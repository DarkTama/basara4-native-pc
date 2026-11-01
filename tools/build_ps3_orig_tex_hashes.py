"""Extracts original Japanese PS3 textures from the game RAR archive,
computes their SHA-256 hashes, detects translated regions in modified
textures from the PS3 English patch, and saves the hash & rectangle database.

Usage:
    python tools/build_ps3_orig_tex_hashes.py
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np

# Repo imports
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LAUNCHER_DIR = _REPO_ROOT / "launcher"
if str(_LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(_LAUNCHER_DIR))

from gmdarc import ArcFile, TEX_TYPE_HASH
from basara4_tex import decode_bc3_to_rgba, parse_tex_header, ycbcr_to_rgb


def get_changed_rects(do: bytes, dp: bytes) -> list[list[int]]:
    """Compares PS3-original vs PS3-patch decompressed TEX entries and returns
    a list of changed 4x4-block rectangles [x0, y0, x1, y1] (PS3 block coordinates, inclusive).
    """
    try:
        ho = parse_tex_header(do, endian=">")
        hp = parse_tex_header(dp, endian=">")
    except Exception:
        return [[0, 0, 0, 0]]

    w, h = ho["width"], ho["height"]
    bw, bh = w // 4, h // 4
    if ho["format"] != 42 or ho["mips"] != 1 or bw <= 0 or bh <= 0:
        return [[0, 0, max(0, bw - 1), max(0, bh - 1)]]

    try:
        imo = decode_bc3_to_rgba(do[ho["pixel_offset"]:], w, h)
        imp = decode_bc3_to_rgba(dp[hp["pixel_offset"]:], w, h)
    except Exception:
        return [[0, 0, max(0, bw - 1), max(0, bh - 1)]]

    ro = ycbcr_to_rgb(np.array(imo, dtype=np.float32))
    rp = ycbcr_to_rgb(np.array(imp, dtype=np.float32))

    vis_orig = ro[:, :, :3] * (ro[:, :, 3:4] / 255.0)
    vis_patch = rp[:, :, :3] * (rp[:, :, 3:4] / 255.0)
    vis_diff = np.max(np.abs(vis_orig - vis_patch), axis=-1)

    px_ch = vis_diff > 35.0
    block_px_cnt = px_ch.reshape(bh, 4, bw, 4).sum(axis=(1, 3))
    raw_mask = block_px_cnt >= 2

    if not np.any(raw_mask):
        return []

    if np.count_nonzero(raw_mask) > 0.7 * bw * bh:
        return [[0, 0, bw - 1, bh - 1]]

    # Dilate by 1 block (3x3 neighborhood)
    dilated = raw_mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            y_src = slice(max(0, -dy), min(bh, bh - dy))
            x_src = slice(max(0, -dx), min(bw, bw - dx))
            y_dst = slice(max(0, dy), min(bh, bh + dy))
            x_dst = slice(max(0, dx), min(bw, bw + dx))
            dilated[y_dst, x_dst] |= raw_mask[y_src, x_src]

    # Label connected components
    try:
        from scipy import ndimage
        lbl, num_features = ndimage.label(dilated)
    except ImportError:
        # Simple BFS component labeling fallback
        lbl = np.zeros((bh, bw), dtype=int)
        num_features = 0
        for y in range(bh):
            for x in range(bw):
                if dilated[y, x] and lbl[y, x] == 0:
                    num_features += 1
                    queue = [(y, x)]
                    lbl[y, x] = num_features
                    while queue:
                        cy, cx = queue.pop(0)
                        for n_dy, n_dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            ny, nx = cy + n_dy, cx + n_dx
                            if 0 <= ny < bh and 0 <= nx < bw and dilated[ny, nx] and lbl[ny, nx] == 0:
                                lbl[ny, nx] = num_features
                                queue.append((ny, nx))

    rects = []
    for i in range(1, num_features + 1):
        by, bx = np.where(lbl == i)
        rects.append([int(bx.min()), int(by.min()), int(bx.max()), int(by.max())])

    # Merge overlapping rectangles
    changed = True
    while changed:
        changed = False
        merged = []
        skip = set()
        for i in range(len(rects)):
            if i in skip:
                continue
            r1 = rects[i]
            for j in range(i + 1, len(rects)):
                if j in skip:
                    continue
                r2 = rects[j]
                if not (r1[2] < r2[0] or r2[2] < r1[0] or r1[3] < r2[1] or r2[3] < r1[1]):
                    r1 = [min(r1[0], r2[0]), min(r1[1], r2[1]), max(r1[2], r2[2]), max(r1[3], r2[3])]
                    skip.add(j)
                    changed = True
            merged.append(r1)
        rects = merged

    return rects


def build_hashes(
    rar_path: Path,
    patch_rom: Path,
    out_path: Path,
    unrar_exe: Path = Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
    temp_dir: Path | None = None,
    keep_temp: bool = False,
) -> Path:
    rar_path = Path(rar_path).resolve()
    patch_rom = Path(patch_rom).resolve()
    out_path = Path(out_path).resolve()
    unrar_exe = Path(unrar_exe).resolve()

    if not patch_rom.is_dir():
        raise FileNotFoundError(f"PS3 patch rom directory not found: {patch_rom}")

    # Identify patch arcs
    patch_arcs = {
        p.relative_to(patch_rom).as_posix().lower(): p
        for p in patch_rom.rglob("*.arc")
        if p.is_file()
    }
    print(f"Found {len(patch_arcs)} ARC files in PS3 patch rom: {patch_rom}")

    created_temp = False
    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="sb4s_ps3_orig_"))
        created_temp = True
    else:
        temp_dir = Path(temp_dir).resolve()
        temp_dir.mkdir(parents=True, exist_ok=True)

    extracted_rom_dir = temp_dir / "BLJM61248-[BASARA4Sumeragi ]" / "PS3_GAME" / "USRDIR" / "nativePS3" / "rom"

    # Check if already extracted
    needs_extract = True
    if extracted_rom_dir.is_dir():
        existing_arcs = list(extracted_rom_dir.rglob("*.arc"))
        if len(existing_arcs) >= len(patch_arcs) * 0.95:
            print(f"Found {len(existing_arcs)} existing extracted ARCs in {extracted_rom_dir}, skipping UnRAR.")
            needs_extract = False

    if needs_extract:
        if not rar_path.is_file():
            raise FileNotFoundError(f"PS3 original RAR not found: {rar_path}")
        if not unrar_exe.is_file():
            raise FileNotFoundError(f"UnRAR executable not found: {unrar_exe}")

        print(f"Listing files in RAR: {rar_path}...")
        cmd_lb = [str(unrar_exe), "lb", "-v", str(rar_path)]
        out_lb = subprocess.check_output(cmd_lb, text=True, errors="replace")
        rar_files = out_lb.splitlines()

        prefix = "bljm61248-[basara4sumeragi ]/ps3_game/usrdir/nativeps3/rom/"
        to_extract = []
        for rf in rar_files:
            rf_norm = rf.replace("\\", "/").lower()
            if rf_norm.startswith(prefix):
                rel = rf_norm[len(prefix):]
                if rel in patch_arcs:
                    to_extract.append(rf)

        print(f"Matched {len(to_extract)} ARCs to extract from RAR.")
        list_file = temp_dir / "extract_list.txt"
        list_file.write_text("\n".join(to_extract) + "\n", encoding="utf-8")

        print(f"Extracting matching ARCs to {temp_dir}...")
        t_extract0 = time.time()
        cmd_extract = [
            str(unrar_exe),
            "x",
            "-v",
            "-y",
            "-inul",
            str(rar_path),
            f"@{list_file}",
            f"{temp_dir}\\",
        ]
        res = subprocess.run(cmd_extract)
        if res.returncode != 0:
            raise RuntimeError(f"UnRAR extraction failed with exit code {res.returncode}")
        print(f"Extraction completed in {time.time() - t_extract0:.1f}s.")

    # Process all ARCs
    print("Hashing TEX entries and computing changed rectangles...")
    t_hash0 = time.time()
    hashes: dict[str, str] = {}
    changed_rects: dict[str, list[list[int]]] = {}
    pair_cache: dict[tuple[str, str], list[list[int]]] = {}

    total_arcs = 0
    total_tex = 0
    unchanged_tex = 0
    changed_tex = 0

    patch_arc_paths = sorted(patch_rom.rglob("*.arc"))
    for idx, p_arc in enumerate(patch_arc_paths):
        rel = p_arc.relative_to(patch_rom).as_posix()
        o_arc = extracted_rom_dir / rel
        if not o_arc.exists():
            continue

        try:
            ao = ArcFile.from_file(o_arc)
            ap = ArcFile.from_file(p_arc)
        except Exception:
            continue

        total_arcs += 1
        for eo in ao.entries:
            if eo.type_hash != TEX_TYPE_HASH:
                continue

            total_tex += 1
            norm_entry_path = eo.path.replace("\\", "/")
            key = f"{rel}|{norm_entry_path}"

            try:
                do = eo.decompress()
                ho = hashlib.sha256(do).hexdigest()
            except Exception:
                continue

            hashes[key] = ho

            ep = ap.get_entry(eo.path)
            if ep:
                try:
                    dp = ep.decompress()
                    hp = hashlib.sha256(dp).hexdigest()
                except Exception:
                    continue

                if ho == hp:
                    unchanged_tex += 1
                else:
                    changed_tex += 1
                    pair_key = (ho, hp)
                    if pair_key in pair_cache:
                        r = pair_cache[pair_key]
                    else:
                        r = get_changed_rects(do, dp)
                        pair_cache[pair_key] = r

                    if r:
                        changed_rects[key] = r

        if (idx + 1) % 500 == 0 or (idx + 1) == len(patch_arc_paths):
            print(f"Processed {idx + 1}/{len(patch_arc_paths)} ARCs ({total_tex} TEX hashed)...")

    print(
        f"Hashing complete in {time.time() - t_hash0:.1f}s:\n"
        f"  Total ARCs processed:    {total_arcs}\n"
        f"  Total TEX entries:       {total_tex}\n"
        f"  Unchanged in PS3 patch:  {unchanged_tex} ({unchanged_tex / max(1, total_tex) * 100:.1f}%)\n"
        f"  Changed in PS3 patch:    {changed_tex} ({changed_tex / max(1, total_tex) * 100:.1f}%)\n"
        f"  Entries with rects:      {len(changed_rects)}"
    )

    # Serialize output
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {"hashes": hashes, "changed_rects": changed_rects}
    raw_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    if len(raw_json) > 5 * 1024 * 1024:
        # If > 5MB, save as .json.gz
        final_out_path = out_path if out_path.name.endswith(".gz") else out_path.with_name(out_path.name + ".gz")
        gz_data = gzip.compress(raw_json, compresslevel=9)
        final_out_path.write_bytes(gz_data)
        print(f"Saved gzipped hashes ({len(gz_data) / (1024**2):.2f} MB) to {final_out_path}")
    else:
        final_out_path = out_path
        final_out_path.write_bytes(raw_json)
        print(f"Saved JSON hashes ({len(raw_json) / (1024**2):.2f} MB) to {final_out_path}")

    if created_temp and not keep_temp:
        print(f"Cleaning up temporary directory: {temp_dir}")
        shutil.rmtree(temp_dir, ignore_errors=True)

    return final_out_path


def main():
    parser = argparse.ArgumentParser(description="Extract original PS3 texture hashes and changed regions.")
    parser.add_argument(
        "--rar-path",
        type=Path,
        default=Path(r"D:\Games\PS3\Basara 4\Complete Game Archives\SB4S.part01.rar"),
        help="Path to SB4S.part01.rar",
    )
    parser.add_argument(
        "--unrar-exe",
        type=Path,
        default=Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
        help="Path to UnRAR.exe",
    )
    parser.add_argument(
        "--patch-rom",
        type=Path,
        default=Path(r"D:\Games\basara4-windows\ps3 patch\rom"),
        help="Path to PS3 patch rom directory",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / "data" / "ps3_orig_tex_hashes.json",
        help="Path to output hashes JSON or JSON.gz",
    )
    parser.add_argument(
        "--temp-dir",
        type=Path,
        default=Path(r"C:\Temp\sb4s_ps3_orig"),
        help="Directory to extract original PS3 files into (reuses existing if present)",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        default=True,
        help="Keep extracted files after processing",
    )
    args = parser.parse_args()

    build_hashes(
        rar_path=args.rar_path,
        patch_rom=args.patch_rom,
        out_path=args.out,
        unrar_exe=args.unrar_exe,
        temp_dir=args.temp_dir,
        keep_temp=args.keep_temp,
    )


if __name__ == "__main__":
    main()
