"""PS3 English patch repatcher for Sengoku Basara 4 Sumeragi.
Live-extracts GMD strings and translates UI textures (BC3/DXT5) from user-supplied
PS3 patch folder, converting them into repacked native PS4 Little-Endian ARCs.

Zero dependencies on tools/. Works as standalone CLI or imported library.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time
from typing import Any, Callable, Optional, Union
import zipfile
import zlib

from PIL import Image, ImageDraw, ImageFont

# Local launcher imports
try:
    from .gmdarc import ArcFile, GmdFile, is_english, TEX_TYPE_HASH, GMD_TYPE_HASH
    from .basara4_tex import (
        classify,
        convert_ps3_to_ps4,
        decode_bc3_to_rgba,
        deswizzle_bc3,
        is_ui_text_texture,
        parse_tex_header,
        swizzle_bc3,
    )
    from .basara4_translation_extract import extract_translation_data
except ImportError:
    _LAUNCHER_DIR = Path(__file__).resolve().parent
    if str(_LAUNCHER_DIR) not in sys.path:
        sys.path.insert(0, str(_LAUNCHER_DIR))
    from gmdarc import ArcFile, GmdFile, is_english, TEX_TYPE_HASH, GMD_TYPE_HASH
    from basara4_tex import (
        classify,
        convert_ps3_to_ps4,
        decode_bc3_to_rgba,
        deswizzle_bc3,
        is_ui_text_texture,
        parse_tex_header,
        swizzle_bc3,
    )
    from basara4_translation_extract import extract_translation_data


def resolve_game_paths(game_dir_input: Path) -> tuple[Path, Path]:
    """Resolves (game_root, rom_dir) generically from game_dir_input."""
    game_dir_input = Path(game_dir_input).resolve()
    if (game_dir_input / "eboot.bin").is_file():
        return game_dir_input, game_dir_input / "nativePS4" / "rom"

    if game_dir_input.name == "rom" and game_dir_input.parent.name == "nativePS4":
        return game_dir_input.parent.parent, game_dir_input

    if game_dir_input.is_dir():
        for child in sorted(game_dir_input.iterdir()):
            if child.is_dir() and (child / "eboot.bin").is_file():
                return child, child / "nativePS4" / "rom"

        cusa = game_dir_input / "CUSA01159"
        if (cusa / "nativePS4" / "rom").is_dir():
            return cusa, cusa / "nativePS4" / "rom"

    return game_dir_input, game_dir_input / "nativePS4" / "rom"


def safe_extract_zip(zip_path: Path, target_dir: Path) -> Path:
    """Safely extracts zip archive preventing path traversal."""
    zip_path = Path(zip_path).resolve()
    target_dir = Path(target_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            filename = member.filename
            if filename.startswith(("/", "\\")) or ":" in filename:
                raise ValueError(f"Dangerous path in zip archive: {filename}")
            parts = Path(filename).parts
            if ".." in parts:
                raise ValueError(f"Path traversal detected in zip archive: {filename}")
            dest_path = (target_dir / filename).resolve()
            if not dest_path.is_relative_to(target_dir):
                raise ValueError(f"Path traversal out of target: {filename}")
            zf.extract(member, target_dir)
    return target_dir


def detect_ps3_patch(source_path: Path) -> tuple[Path, Optional[Path]]:
    """Detects base rom directory and optional matabe fix directory inside source_path up to depth 4."""
    source_path = Path(source_path).resolve()
    if source_path.is_file() and source_path.suffix.lower() == ".zip":
        repo_root = Path(__file__).resolve().parent.parent
        cache_dir = repo_root / "user" / "ps3_patch_cache"
        source_path = safe_extract_zip(source_path, cache_dir)

    base_rom = None
    matabe_dir = None

    for dirpath, dirnames, filenames in os.walk(source_path):
        p = Path(dirpath)
        try:
            rel = p.relative_to(source_path)
            depth = len(rel.parts)
        except ValueError:
            depth = 0
        if depth > 4:
            continue

        if base_rom is None:
            if p.name == "rom" and ((p / "ui").is_dir() or (p / "st").is_dir()):
                base_rom = p
            elif (p / "rom").is_dir() and ((p / "rom" / "ui").is_dir() or (p / "rom" / "st").is_dir()):
                base_rom = p / "rom"

        if p.name == "pl035" and any(f.startswith("pl035_") and f.endswith(".arc") for f in filenames):
            if not ("rom" in p.parts and "pl" in p.parts):
                matabe_dir = p

    if base_rom is None:
        raise ValueError(
            f"Could not find a valid PS3 (BLJM61248) patch rom directory containing ui/ or st/ inside: {source_path}"
        )

    return base_rom, matabe_dir


def verify_patch_manifest(
    base_rom: Path,
    matabe_dir: Optional[Path],
    manifest_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Verifies files against data/ps3_patch_manifest.json (hashes only)."""
    if manifest_path is None:
        repo_root = Path(__file__).resolve().parent.parent
        manifest_path = repo_root / "data" / "ps3_patch_manifest.json"

    if not manifest_path.is_file():
        return {
            "verified": False,
            "warning": f"Manifest not found: {manifest_path}",
            "patches": {},
            "total_matched": 0,
        }

    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    patches = manifest_data.get("patches", {})
    summary: dict[str, Any] = {"verified": True, "patches": {}, "total_matched": 0}

    # Verify 0.85
    expected_085 = patches.get("0.85", {})
    matched_085 = 0
    mismatched_085 = 0
    missing_085 = 0
    extra_085 = 0

    found_085_files = set()
    for p in base_rom.rglob("*"):
        if p.is_file():
            rel = p.relative_to(base_rom).as_posix()
            found_085_files.add(rel)

    for rel, exp_sha in expected_085.items():
        fp = base_rom / rel
        if not fp.is_file():
            missing_085 += 1
        else:
            h = hashlib.sha256(fp.read_bytes()).hexdigest()
            if h == exp_sha:
                matched_085 += 1
            else:
                mismatched_085 += 1

    extra_085 = len(found_085_files - set(expected_085.keys()))
    summary["patches"]["0.85"] = {
        "matched": matched_085,
        "mismatched": mismatched_085,
        "missing": missing_085,
        "extra": extra_085,
    }

    # Verify matabe fix
    expected_matabe = patches.get("matabe_fix", {})
    matched_matabe = 0
    mismatched_matabe = 0
    missing_matabe = 0
    extra_matabe = 0

    if matabe_dir and matabe_dir.is_dir():
        found_matabe = set()
        for p in matabe_dir.glob("*.arc"):
            if p.is_file():
                rel = f"pl/pl035/{p.name}"
                found_matabe.add(rel)
                exp_sha = expected_matabe.get(rel)
                if not exp_sha:
                    extra_matabe += 1
                else:
                    h = hashlib.sha256(p.read_bytes()).hexdigest()
                    if h == exp_sha:
                        matched_matabe += 1
                    else:
                        mismatched_matabe += 1
        missing_matabe = len(set(expected_matabe.keys()) - found_matabe)
    elif expected_matabe:
        missing_matabe = len(expected_matabe)

    summary["patches"]["matabe_fix"] = {
        "matched": matched_matabe,
        "mismatched": mismatched_matabe,
        "missing": missing_matabe,
        "extra": extra_matabe,
    }

    total_matched = matched_085 + matched_matabe
    summary["total_matched"] = total_matched

    if total_matched == 0:
        raise ValueError(
            "This does not look like the Sengoku Basara 4 Sumeragi PS3 (BLJM61248) English patch: "
            "0 files matched the known patch manifest."
        )

    return summary


def _peek_header(raw: bytes, endian: str) -> Optional[dict[str, int]]:
    """Quickly peek 16-24 bytes of TEX header without full entry decompression."""
    if raw.startswith(b"\x00XET") or raw.startswith(b"TEX\x00"):
        hdr = raw[:24]
    else:
        try:
            hdr = zlib.decompressobj().decompress(raw, 64)
        except Exception:
            return None
    if len(hdr) < 16:
        return None
    v2, v3, v4 = struct.unpack(f"{endian}III", hdr[4:16])
    return {
        "w": (v3 >> 6) & 0x1FFF,
        "h": v3 >> 19,
        "mips": v3 & 0x3F,
        "fmt": (v4 >> 8) & 0xFF,
    }


def repatch(
    ps3_source: Union[str, Path],
    game_dir: Union[str, Path],
    out_dir: Union[str, Path],
    progress: Optional[Callable[[int, int, str], None]] = None,
    tex: bool = True,
    gmd: bool = True,
    threshold: float = 12.0,
    prefer_ps4_english: bool = False,
    workers: Optional[int] = None,
    manifest_path: Optional[Union[str, Path]] = None,
) -> dict[str, Any]:
    """Repatches PS4 game archives using translations and textures from a PS3 English patch folder."""
    t_start = time.time()
    ps3_source = Path(ps3_source).resolve()
    game_dir = Path(game_dir).resolve()
    out_dir = Path(out_dir).resolve()
    manifest_path = Path(manifest_path).resolve() if manifest_path else None

    base_rom, matabe_dir = detect_ps3_patch(ps3_source)
    _, rom_dir = resolve_game_paths(game_dir)
    out_rom_dir = out_dir / "nativePS4" / "rom"

    # Verify against manifest
    verification = verify_patch_manifest(base_rom, matabe_dir, manifest_path=manifest_path)

    # Build PS3 merged view
    ps3_arcs_map: dict[str, Path] = {}
    for p in base_rom.rglob("*.arc"):
        if p.is_file():
            ps3_arcs_map[p.relative_to(base_rom).as_posix()] = p
    if matabe_dir and matabe_dir.is_dir():
        for p in matabe_dir.glob("*.arc"):
            if p.is_file():
                ps3_arcs_map[f"pl/pl035/{p.name}"] = p

    # Build PS4 game arcs map
    ps4_arcs_map: dict[str, Path] = {}
    ps4_arcs_lower: dict[str, Path] = {}
    for p in rom_dir.rglob("*.arc"):
        if p.is_file():
            rel = p.relative_to(rom_dir).as_posix()
            ps4_arcs_map[rel] = p
            ps4_arcs_lower[rel.lower()] = p

    if not ps4_arcs_map:
        raise ValueError(f"No .arc files found in PS4 game directory ({rom_dir}).")

    # Identify candidate arcs (only those present on both sides)
    candidate_rels = sorted(set(ps3_arcs_map.keys()) & set(ps4_arcs_map.keys()))
    if not candidate_rels:
        raise ValueError("No common .arc files found between PS3 patch and PS4 game.")

    # Live GMD extraction
    pool: list[str] = []
    archives_db: dict[str, Any] = {}
    if gmd:
        if progress:
            progress(0, 100, "Extracting GMD translations from PS3 patch…")
        db_payload, _, _ = extract_translation_data(ps3_arcs_map, ps4_arcs_map)
        pool = db_payload.get("pool", [])
        archives_db = db_payload.get("archives", {})

    # Stats tracking
    stats: dict[str, Any] = {
        "elapsed_seconds": 0.0,
        "arcs_written": 0,
        "arcs_unmodified": 0,
        "gmd_strings_replaced": 0,
        "gmd_strings_kept_ps4_english": 0,
        "gmd_modified": 0,
        "textures_translated": 0,
        "textures_kept": 0,
        "textures_skipped": {
            "fmt_unsupported": 0,
            "mips_unsupported": 0,
            "size_mismatch": 0,
            "tiling_unsupported": 0,
            "unmatched_ps4": 0,
            "decode_error": 0,
            "non_text_texture": 0,
        },
        "translated_size_histogram": {},
    }

    # In-memory texture conversion cache: (ps3_raw_hash, ps4_raw_hash) -> cache dict
    tex_cache: dict[tuple[bytes, bytes], dict[str, Any]] = {}

    written_arcs_manifest: list[dict[str, Any]] = []

    total_candidates = len(candidate_rels)

    # Progress helper
    def report(cur: int, tot: int, msg: str):
        if progress:
            try:
                progress(cur, tot, msg)
            except TypeError:
                try:
                    progress(f"[{cur}/{tot}] {msg}")  # type: ignore
                except Exception:
                    pass

    report(0, total_candidates, "Starting archive patching…")

    # Sequential archive repacking (with internal multithreading when converting)
    for idx, rel_arc in enumerate(candidate_rels):
        ps3_path = ps3_arcs_map[rel_arc]
        ps4_path = ps4_arcs_map[rel_arc]

        # Fast check: does this arc have GMD entries in archives_db or TEX in PS3?
        has_gmd_to_patch = (gmd and rel_arc in archives_db)
        if not has_gmd_to_patch and not tex:
            stats["arcs_unmodified"] += 1
            continue

        try:
            a3 = ArcFile.from_file(ps3_path)
            a4 = ArcFile.from_file(ps4_path)
        except Exception:
            # Skip non-ARC or unreadable files
            stats["arcs_unmodified"] += 1
            continue

        arc_changed = False

        # 1. Apply GMD translations
        if has_gmd_to_patch:
            arc_spec = archives_db[rel_arc]
            for entry_name, patch_spec in arc_spec.items():
                e4 = a4.get_entry(entry_name)
                if not e4:
                    continue
                try:
                    gmd_obj = GmdFile.from_bytes(e4.decompress())
                except Exception:
                    continue

                gmd_changed = False
                curr_strings = list(gmd_obj.strings)

                if "by_key" in patch_spec:
                    for k, pool_idx in patch_spec["by_key"].items():
                        if k in gmd_obj.key_to_index:
                            str_idx = gmd_obj.key_to_index[k]
                            if str_idx < len(curr_strings):
                                target = pool[pool_idx]
                                curr = curr_strings[str_idx]
                                if prefer_ps4_english and is_english(curr):
                                    stats["gmd_strings_kept_ps4_english"] += 1
                                elif curr != target:
                                    curr_strings[str_idx] = target
                                    stats["gmd_strings_replaced"] += 1
                                    gmd_changed = True

                if "by_index" in patch_spec:
                    for str_idx_s, pool_idx in patch_spec["by_index"].items():
                        str_idx = int(str_idx_s)
                        if str_idx < len(curr_strings):
                            target = pool[pool_idx]
                            curr = curr_strings[str_idx]
                            if prefer_ps4_english and is_english(curr):
                                stats["gmd_strings_kept_ps4_english"] += 1
                            elif curr != target:
                                curr_strings[str_idx] = target
                                stats["gmd_strings_replaced"] += 1
                                gmd_changed = True

                if gmd_changed:
                    stats["gmd_modified"] += 1
                    new_gmd_bytes = gmd_obj.to_bytes(new_strings=curr_strings)
                    e4.set_uncompressed(new_gmd_bytes, level=6)
                    arc_changed = True

        # 2. Apply TEX translations
        if tex:
            for e3 in a3.entries:
                if e3.type_hash != TEX_TYPE_HASH:
                    continue

                e4 = a4.get_entry(e3.path + "_ps4") or a4.get_entry(e3.path)
                if not e4:
                    stats["textures_skipped"]["unmatched_ps4"] += 1
                    continue

                pair_key = (
                    hashlib.sha256(e3.raw_data).digest(),
                    hashlib.sha256(e4.raw_data).digest(),
                )
                cached = tex_cache.get(pair_key)

                if cached is None:
                    if not is_ui_text_texture(e3.path):
                        cached = {"translated": False, "reason": "non_text_texture"}
                    else:
                        h3 = _peek_header(e3.raw_data, ">")
                        h4 = _peek_header(e4.raw_data, "<")
                        if not h3 or not h4:
                            cached = {"translated": False, "reason": "decode_error"}
                        elif h3["fmt"] != 42 or h4["fmt"] != 42:
                            cached = {"translated": False, "reason": "fmt_unsupported"}
                        elif h3["mips"] != 1 or h4["mips"] != 1:
                            cached = {"translated": False, "reason": "mips_unsupported"}
                        else:
                            d3 = e3.decompress()
                            d4 = e4.decompress()
                            cls = classify(d3, d4, threshold=threshold, texture_path=e3.path)
                            if cls["translated"]:
                                conv = convert_ps3_to_ps4(d3, d4)
                                cached = {
                                    "translated": True,
                                    "converted": conv,
                                    "cls": cls,
                                    "ps4_dims": cls.get("ps4_dims", (h4["w"], h4["h"])),
                                }
                            else:
                                cached = {
                                    "translated": False,
                                    "reason": cls.get("reason", "not_translated"),
                                    "cls": cls,
                                }
                    tex_cache[pair_key] = cached

                if cached["translated"]:
                    stats["textures_translated"] += 1
                    dims_key = f"{cached['ps4_dims'][0]}x{cached['ps4_dims'][1]}"
                    stats["translated_size_histogram"][dims_key] = (
                        stats["translated_size_histogram"].get(dims_key, 0) + 1
                    )
                    e4.set_uncompressed(cached["converted"], level=6)
                    arc_changed = True
                elif cached.get("reason") == "not_translated":
                    stats["textures_kept"] += 1
                else:
                    reason = cached.get("reason", "unknown")
                    stats["textures_skipped"][reason] = (
                        stats["textures_skipped"].get(reason, 0) + 1
                    )

        # 3. Repack and write if modified
        if arc_changed:
            repacked_bytes = a4.to_bytes()
            out_arc_path = out_rom_dir / rel_arc
            out_arc_path.parent.mkdir(parents=True, exist_ok=True)

            tmp = tempfile.NamedTemporaryFile(
                dir=out_arc_path.parent,
                delete=False,
                prefix="tmp_repatch_",
                suffix=".arc",
            )
            tmp_path = Path(tmp.name)
            try:
                tmp.write(repacked_bytes)
                tmp.flush()
                tmp.close()
                os.replace(tmp_path, out_arc_path)
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise

            stats["arcs_written"] += 1
            src_stat = ps4_path.stat()
            written_arcs_manifest.append(
                {
                    "relative_path": rel_arc,
                    "source_size": src_stat.st_size,
                    "source_mtime": src_stat.st_mtime,
                    "output_size": len(repacked_bytes),
                    "output_sha256": hashlib.sha256(repacked_bytes).hexdigest(),
                }
            )
        else:
            stats["arcs_unmodified"] += 1

        if (idx + 1) % 50 == 0 or (idx + 1) == total_candidates:
            report(idx + 1, total_candidates, f"Processed {idx + 1}/{total_candidates} archives…")

    # Clear stale arcs
    current_arcs_rels = {e["relative_path"] for e in written_arcs_manifest}
    if out_rom_dir.is_dir():
        for existing in list(out_rom_dir.rglob("*.arc")):
            rel = existing.relative_to(out_rom_dir).as_posix()
            if rel not in current_arcs_rels:
                try:
                    existing.unlink()
                except OSError:
                    pass
        for root, dirs, files in os.walk(out_rom_dir, topdown=False):
            for d in dirs:
                dp = Path(root) / d
                try:
                    dp.rmdir()
                except OSError:
                    pass

    elapsed = time.time() - t_start
    stats["elapsed_seconds"] = round(elapsed, 2)

    # Write manifest.json
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_payload = {
        "mode": "ps3_import",
        "meta": {
            "source_game": "BLJM61248",
        },
        "source_game": "BLJM61248",
        "source_path": str(ps3_source),
        "verification": verification,
        "stats": stats,
        "written_arcs": written_arcs_manifest,
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    report(total_candidates, total_candidates, f"Repatch complete in {stats['elapsed_seconds']}s.")
    return {
        "stats": stats,
        "verification": verification,
        "manifest_path": manifest_path,
        "tex_cache": tex_cache,
    }


def build_contact_sheets(
    ps3_source: Union[str, Path],
    game_dir: Union[str, Path],
    out_dir: Union[str, Path],
    tex_cache: Optional[dict[tuple[bytes, bytes], dict[str, Any]]] = None,
    output_dir: Optional[Path] = None,
) -> tuple[Path, Path]:
    """Generates contact_translated.png and contact_kept.png contact sheets."""
    repo_root = Path(__file__).resolve().parent.parent
    if output_dir is None:
        output_dir = repo_root / "user" / "tex_spike"
    output_dir.mkdir(parents=True, exist_ok=True)

    base_rom, matabe_dir = detect_ps3_patch(Path(ps3_source))
    _, rom_dir = resolve_game_paths(Path(game_dir))

    # Sample up to 48 translated and up to 24 kept
    translated_samples: list[tuple[str, Image.Image, Image.Image]] = []
    kept_samples: list[tuple[str, Image.Image, Image.Image]] = []

    # Scan ui arcs first for high-quality visuals
    ui_arcs = sorted(base_rom.glob("ui/*.arc"))
    for arc_path in ui_arcs:
        rel = arc_path.relative_to(base_rom).as_posix()
        ps4_path = rom_dir / rel
        if not ps4_path.is_file():
            continue
        try:
            a3 = ArcFile.from_file(arc_path)
            a4 = ArcFile.from_file(ps4_path)
        except Exception:
            continue

        for e3 in a3.entries:
            if e3.type_hash != TEX_TYPE_HASH:
                continue
            e4 = a4.get_entry(e3.path + "_ps4") or a4.get_entry(e3.path)
            if not e4:
                continue
            if not is_ui_text_texture(e3.path):
                continue

            h3 = _peek_header(e3.raw_data, ">")
            h4 = _peek_header(e4.raw_data, "<")
            if not h3 or not h4 or h3["fmt"] != 42 or h4["fmt"] != 42 or h3["mips"] != 1 or h4["mips"] != 1:
                continue

            d3 = e3.decompress()
            d4 = e4.decompress()
            cls = classify(d3, d4, threshold=12.0, texture_path=e3.path)

            name = e3.path.replace("\\", "/").split("/")[-1]
            if cls["translated"] and len(translated_samples) < 48:
                # Decoded PS3 source
                im3 = decode_bc3_to_rgba(d3[h3.get("pixel_offset", 20):], h3["w"], h3["h"])
                # Converted PS4 output decoded back
                conv_tex = convert_ps3_to_ps4(d3, d4)
                h_conv = parse_tex_header(conv_tex, endian="<")
                deswz = deswizzle_bc3(conv_tex[h_conv["pixel_offset"]:], h_conv["width"], h_conv["height"])
                im_conv = decode_bc3_to_rgba(deswz, h_conv["width"], h_conv["height"])
                translated_samples.append((name, im3, im_conv))
            elif not cls["translated"] and cls.get("reason") == "not_translated" and len(kept_samples) < 24:
                im3 = decode_bc3_to_rgba(d3[h3.get("pixel_offset", 20):], h3["w"], h3["h"])
                deswz4 = deswizzle_bc3(d4[h4.get("pixel_offset", 24):], h4["w"], h4["h"])
                im4 = decode_bc3_to_rgba(deswz4, h4["w"], h4["h"])
                kept_samples.append((name, im3, im4))

            if len(translated_samples) >= 48 and len(kept_samples) >= 24:
                break
        if len(translated_samples) >= 48 and len(kept_samples) >= 24:
            break

    def make_sheet(samples: list[tuple[str, Image.Image, Image.Image]], cols: int, title: str) -> Image.Image:
        tile_w, tile_h = 240, 120
        label_h = 18
        card_w = tile_w * 2 + 10
        card_h = tile_h + label_h + 10

        rows = (len(samples) + cols - 1) // cols
        sheet_w = cols * card_w + 20
        sheet_h = rows * card_h + 40

        sheet = Image.new("RGBA", (sheet_w, sheet_h), (34, 34, 34, 255))
        draw = ImageDraw.Draw(sheet)

        font = None
        try:
            font = ImageFont.load_default()
        except Exception:
            pass

        draw.text((10, 10), title, fill=(255, 255, 255, 255), font=font)

        for i, (name, left_im, right_im) in enumerate(samples):
            r = i // cols
            c = i % cols
            x0 = 10 + c * card_w
            y0 = 35 + r * card_h

            draw.rectangle([x0, y0, x0 + card_w - 6, y0 + card_h - 6], fill=(45, 45, 45, 255), outline=(70, 70, 70, 255))
            draw.text((x0 + 4, y0 + 2), name[:32], fill=(220, 220, 160, 255), font=font)

            # Left image: composite on dark grey
            l_thumb = left_im.resize((tile_w, tile_h), resample=Image.Resampling.LANCZOS)
            bg_l = Image.new("RGBA", (tile_w, tile_h), (25, 25, 25, 255))
            bg_l.alpha_composite(l_thumb)
            sheet.paste(bg_l, (x0 + 4, y0 + label_h))

            # Right image: composite on dark grey
            r_thumb = right_im.resize((tile_w, tile_h), resample=Image.Resampling.LANCZOS)
            bg_r = Image.new("RGBA", (tile_w, tile_h), (25, 25, 25, 255))
            bg_r.alpha_composite(r_thumb)
            sheet.paste(bg_r, (x0 + tile_w + 6, y0 + label_h))

        return sheet

    trans_sheet = make_sheet(translated_samples, cols=4, title=f"Translated Textures (PS3 Source | Converted PS4 Decoded) - Count: {len(translated_samples)}")
    trans_path = output_dir / "contact_translated.png"
    trans_sheet.save(trans_path)

    kept_sheet = make_sheet(kept_samples, cols=4, title=f"Kept Native PS4 Textures (PS3 Source | PS4 Original) - Count: {len(kept_samples)}")
    kept_path = output_dir / "contact_kept.png"
    kept_sheet.save(kept_path)

    return trans_path, kept_path


def main():
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Repatch PS4 Basara 4 archives using PS3 English patch (GMD + TEX)."
    )
    default_game = repo_root / "game"
    default_ps3 = repo_root / "mods" / "ps3 english patch"
    default_out = repo_root / "mods" / "english_patch"

    parser.add_argument(
        "ps3_source",
        type=Path,
        nargs="?",
        default=default_ps3,
        help="Path to PS3 patch directory or zip archive",
    )
    parser.add_argument(
        "--game",
        type=Path,
        default=default_game,
        help="Path to game folder (containing eboot.bin or CUSA01159)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=default_out,
        help="Path to output patch folder (e.g. mods/english_patch)",
    )
    parser.add_argument(
        "--no-tex",
        action="store_true",
        default=False,
        help="Disable texture repatching",
    )
    parser.add_argument(
        "--no-gmd",
        action="store_true",
        default=False,
        help="Disable GMD string repatching",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=12.0,
        help="Classification score threshold for translated textures (default: 12)",
    )
    parser.add_argument(
        "--prefer-ps4",
        action="store_true",
        default=False,
        help="Keep PS4 strings if they are already English",
    )
    parser.add_argument(
        "--contact-sheets",
        action="store_true",
        default=False,
        help="Generate contact sheet images after repatching",
    )

    args = parser.parse_args()

    def _prog(cur, tot, msg):
        pct = (cur / max(1, tot)) * 100
        print(f"[{cur:4d}/{tot:4d}] ({pct:5.1f}%) {msg}", flush=True)

    res = repatch(
        ps3_source=args.ps3_source,
        game_dir=args.game,
        out_dir=args.out,
        progress=_prog,
        tex=not args.no_tex,
        gmd=not args.no_gmd,
        threshold=args.threshold,
        prefer_ps4_english=args.prefer_ps4,
    )

    s = res["stats"]
    v = res["verification"]

    print("\n================== Repatch Summary ==================")
    print(f"Elapsed time:              {s['elapsed_seconds']} s")
    print(f"Archives written:          {s['arcs_written']}")
    print(f"Archives unmodified:       {s['arcs_unmodified']}")
    print(f"GMD strings replaced:      {s['gmd_strings_replaced']}")
    print(f"GMD strings kept:          {s['gmd_strings_kept_ps4_english']}")
    print(f"GMD files modified:        {s['gmd_modified']}")
    print(f"Textures translated:       {s['textures_translated']}")
    print(f"Textures kept (native):    {s['textures_kept']}")
    print(f"Textures skipped:")
    for reason, count in s["textures_skipped"].items():
        print(f"  - {reason:22s}: {count}")
    print(f"Translated size histogram:")
    for dims, count in sorted(s["translated_size_histogram"].items(), key=lambda x: -x[1]):
        print(f"  - {dims:12s}: {count}")
    print(f"Manifest written to:       {res['manifest_path']}")
    print("====================================================\n")

    if args.contact_sheets:
        print("Generating contact sheets...")
        t_path, k_path = build_contact_sheets(args.ps3_source, args.game, args.out, res["tex_cache"])
        print(f"Translated contact sheet: {t_path}")
        print(f"Kept contact sheet:       {k_path}")


if __name__ == "__main__":
    main()
