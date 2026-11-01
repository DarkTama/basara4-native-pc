# Basara 4 PC - by Razz. Fork by DarkTama.
# GMD header repair tool and layer generator.
# Repairs inconsistent GMD string_block_size headers across active game layers.
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Optional, Union

try:
    from .gmdarc import ArcFile, ArcEntry, GmdFile
except ImportError:
    from gmdarc import ArcFile, ArcEntry, GmdFile

STAMP_NAME = ".gmd_fix_stamp.json"
STAMP_VERSION = 1


def is_exact(gmd: GmdFile) -> bool:
    """Returns True if the GMD header string_block_size matches its actual string block length."""
    return gmd.string_block_size == gmd.orig_string_block_len


def find_candidate_arcs(root: Path | str) -> list[tuple[Path, Path]]:
    """Returns list of (relative_path, absolute_path) for candidate arcs in root.
    Only scans arcs under nativePS4/rom/ui/ (or rom/ui/) and rom/game_common.arc.
    """
    root = Path(root)
    candidates: list[tuple[Path, Path]] = []
    seen: set[str] = set()

    for ui_rel in (Path("nativePS4") / "rom" / "ui", Path("rom") / "ui"):
        ui_dir = root / ui_rel
        if ui_dir.is_dir():
            for arc_path in sorted(ui_dir.rglob("*.arc")):
                if arc_path.is_file():
                    rel = arc_path.relative_to(root)
                    rel_norm = rel.as_posix().lower()
                    if rel_norm not in seen:
                        seen.add(rel_norm)
                        candidates.append((rel, arc_path))

    for common_rel in (
        Path("nativePS4") / "rom" / "game_common.arc",
        Path("rom") / "game_common.arc",
    ):
        full_cand = root / common_rel
        if full_cand.is_file():
            rel_norm = common_rel.as_posix().lower()
            if rel_norm not in seen:
                seen.add(rel_norm)
                candidates.append((common_rel, full_cand))

    return candidates


def check_and_repair_arc(
    layered_arc: ArcFile,
    base_arc: ArcFile,
) -> list[tuple[str, int, int]]:
    """Checks GMD entries in layered_arc against base_arc.
    Rule: If the base GMD's string_block_size equals its real string block length
    (an 'exact' header), the layered GMD must also be exact. If it is not (and its
    header is not 0xFFFF), rewrites string_block_size to the real length of the
    layered GMD's string block. Leaves every other case untouched.
    Returns list of (gmd_path, old_sbsz, new_sbsz) for repaired entries.
    """
    repaired: list[tuple[str, int, int]] = []
    for entry in layered_arc.entries:
        if not entry.is_gmd:
            continue
        base_entry = base_arc.get_entry(entry.path)
        if not base_entry or not base_entry.is_gmd:
            continue

        raw_layered = entry.decompress()
        gmd_layered = GmdFile.from_bytes(raw_layered)
        if gmd_layered.string_block_size == 0xFFFF:
            continue

        raw_base = base_entry.decompress()
        gmd_base = GmdFile.from_bytes(raw_base)

        base_exact = is_exact(gmd_base)
        layered_exact = is_exact(gmd_layered)

        if base_exact and not layered_exact:
            old_sbsz = gmd_layered.string_block_size
            new_bytes = gmd_layered.with_exact_string_block_size(raw_layered)
            entry.set_uncompressed(new_bytes)
            repaired.append((entry.path, old_sbsz, gmd_layered.orig_string_block_len))

    return repaired


def repair_arc_file(
    layered_arc_path: Path | str,
    base_arc_path: Path | str,
    out_arc_path: Optional[Path | str] = None,
) -> list[tuple[str, int, int]]:
    """Inspects layered_arc_path against base_arc_path.
    If any GMD headers need repair, repacks the arc and writes to out_arc_path
    (or layered_arc_path in-place if out_arc_path is None).
    Never writes if no entries were repaired.
    Returns list of (gmd_path, old_sbsz, new_sbsz).
    """
    layered_arc_path = Path(layered_arc_path)
    base_arc_path = Path(base_arc_path)
    if not layered_arc_path.is_file() or not base_arc_path.is_file():
        return []

    layered_arc = ArcFile.from_file(layered_arc_path)
    base_arc = ArcFile.from_file(base_arc_path)

    repaired = check_and_repair_arc(layered_arc, base_arc)
    if not repaired:
        return []

    dst = Path(out_arc_path) if out_arc_path is not None else layered_arc_path
    dst.parent.mkdir(parents=True, exist_ok=True)

    repacked_bytes = layered_arc.to_bytes()
    tmp = tempfile.NamedTemporaryFile(
        dir=dst.parent,
        delete=False,
        prefix="tmp_gmd_fix_",
        suffix=".arc",
    )
    tmp_path = Path(tmp.name)
    try:
        tmp.write(repacked_bytes)
        tmp.flush()
        tmp.close()
        os.replace(tmp_path, dst)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    return repaired


def _arc_stamp(src_arc: Path, base_arc: Path, repaired_entries: list[tuple[str, int, int]]) -> dict:
    st_src = src_arc.stat()
    st_base = base_arc.stat()
    return {
        "src_size": st_src.st_size,
        "src_mtime": st_src.st_mtime,
        "base_size": st_base.st_size,
        "base_mtime": st_base.st_mtime,
        "repaired": [list(item) for item in repaired_entries],
    }


def _is_arc_stamp_valid(cached: dict, src_arc: Path, base_arc: Path) -> bool:
    try:
        st_src = src_arc.stat()
        st_base = base_arc.stat()
        return (
            cached.get("src_size") == st_src.st_size
            and abs(cached.get("src_mtime", 0) - st_src.st_mtime) < 1e-4
            and cached.get("base_size") == st_base.st_size
            and abs(cached.get("base_mtime", 0) - st_base.st_mtime) < 1e-4
        )
    except OSError:
        return False


def ensure_gmd_header_fix(
    base_game: Path | str,
    effective_game: Path | str,
    out_fix_dir: Path | str,
) -> list[Path]:
    """Ensures repaired ARCs are written to out_fix_dir (e.g. mods/gmd_header_fix)
    for any candidate ARCs in effective_game that have inconsistent GMD headers
    relative to base_game.
    Uses a stamp file in out_fix_dir to cache results keyed by (path, size, mtime).
    Never modifies base_game or effective_game.
    Returns list of repaired ARC paths present in out_fix_dir.
    """
    base_game = Path(base_game)
    effective_game = Path(effective_game)
    out_fix_dir = Path(out_fix_dir)

    out_fix_dir.mkdir(parents=True, exist_ok=True)
    stamp_path = out_fix_dir / STAMP_NAME

    cache: dict[str, dict] = {}
    if stamp_path.is_file():
        try:
            raw_cache = json.loads(stamp_path.read_text(encoding="utf-8"))
            if raw_cache.get("version") == STAMP_VERSION:
                cache = raw_cache.get("arcs", {})
        except (OSError, ValueError):
            cache = {}

    candidates = find_candidate_arcs(effective_game)
    new_cache: dict[str, dict] = {}
    repaired_arcs: list[Path] = []

    for rel_arc, src_arc_path in candidates:
        base_arc_path = base_game / rel_arc
        if not base_arc_path.is_file():
            alt_base = base_game / rel_arc.relative_to("nativePS4") if rel_arc.parts and rel_arc.parts[0] == "nativePS4" else None
            if alt_base and alt_base.is_file():
                base_arc_path = alt_base
            else:
                continue

        rel_key = rel_arc.as_posix()
        dst_arc_path = out_fix_dir / rel_arc
        cached_entry = cache.get(rel_key)

        if cached_entry and _is_arc_stamp_valid(cached_entry, src_arc_path, base_arc_path):
            new_cache[rel_key] = cached_entry
            if cached_entry.get("repaired") and dst_arc_path.is_file():
                repaired_arcs.append(dst_arc_path)
            continue

        repaired = repair_arc_file(src_arc_path, base_arc_path, dst_arc_path)
        new_cache[rel_key] = _arc_stamp(src_arc_path, base_arc_path, repaired)
        if repaired:
            repaired_arcs.append(dst_arc_path)
        elif dst_arc_path.is_file():
            try:
                dst_arc_path.unlink()
            except OSError:
                pass

    stamp_data = {
        "version": STAMP_VERSION,
        "arcs": new_cache,
    }
    stamp_path.write_text(json.dumps(stamp_data, indent=2), encoding="utf-8")

    return repaired_arcs


def repair_layer_in_place(
    layer_dir: Path | str,
    base_game: Path | str,
) -> list[tuple[Path, list[tuple[str, int, int]]]]:
    """Checks and repairs candidate ARCs inside layer_dir in-place against base_game.
    Used for mod layers like mods/english_patch and prompt layers.
    Caches results in layer_dir / .gmd_fix_stamp.json.
    Returns list of (rel_arc, repaired_entries).
    """
    layer_dir = Path(layer_dir)
    base_game = Path(base_game)
    if not layer_dir.is_dir():
        return []

    stamp_path = layer_dir / STAMP_NAME
    cache: dict[str, dict] = {}
    if stamp_path.is_file():
        try:
            raw_cache = json.loads(stamp_path.read_text(encoding="utf-8"))
            if raw_cache.get("version") == STAMP_VERSION:
                cache = raw_cache.get("arcs", {})
        except (OSError, ValueError):
            cache = {}

    candidates = find_candidate_arcs(layer_dir)
    new_cache: dict[str, dict] = {}
    results: list[tuple[Path, list[tuple[str, int, int]]]] = []

    for rel_arc, arc_path in candidates:
        base_arc_path = base_game / rel_arc
        if not base_arc_path.is_file():
            alt_base = base_game / rel_arc.relative_to("nativePS4") if rel_arc.parts and rel_arc.parts[0] == "nativePS4" else None
            if alt_base and alt_base.is_file():
                base_arc_path = alt_base
            else:
                continue

        rel_key = rel_arc.as_posix()
        cached_entry = cache.get(rel_key)

        if cached_entry and _is_arc_stamp_valid(cached_entry, arc_path, base_arc_path):
            new_cache[rel_key] = cached_entry
            continue

        repaired = repair_arc_file(arc_path, base_arc_path, out_arc_path=None)
        new_cache[rel_key] = _arc_stamp(arc_path, base_arc_path, repaired)
        if repaired:
            results.append((rel_arc, repaired))

    stamp_data = {
        "version": STAMP_VERSION,
        "arcs": new_cache,
    }
    stamp_path.write_text(json.dumps(stamp_data, indent=2), encoding="utf-8")

    return results
