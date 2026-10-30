"""Phase 3: User Patch Builder.
Reads local PS4 game ARC files, injects translated English strings from
data/en_translation_db.json into in-memory GMDs, and writes repacked Little-Endian
ARCs into mods/english_patch/nativePS4/rom/<rel>.arc.

Has a CLI and an importable build_patch() function. Zero dependencies on tools/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Optional

# Import gmdarc from the same launcher directory
try:
    from .gmdarc import ArcFile, GmdFile, is_english
except ImportError:
    _LAUNCHER_DIR = Path(__file__).resolve().parent
    if str(_LAUNCHER_DIR) not in sys.path:
        sys.path.insert(0, str(_LAUNCHER_DIR))
    from gmdarc import ArcFile, GmdFile, is_english


def resolve_game_paths(game_dir_input: Path) -> tuple[Path, Path]:
    """Resolves (game_root, rom_dir) generically from game_dir_input."""
    # Check if game_dir_input is already the game root (holding eboot.bin)
    if (game_dir_input / "eboot.bin").is_file():
        game_root = game_dir_input
        rom_dir = game_root / "nativePS4" / "rom"
        return game_root, rom_dir

    # Check if game_dir_input is nativePS4/rom
    if game_dir_input.name == "rom" and game_dir_input.parent.name == "nativePS4":
        game_root = game_dir_input.parent.parent
        return game_root, game_dir_input

    # Search subdirectories for eboot.bin (e.g. game/CUSA01159)
    if game_dir_input.is_dir():
        for child in sorted(game_dir_input.iterdir()):
            if child.is_dir() and (child / "eboot.bin").is_file():
                game_root = child
                rom_dir = game_root / "nativePS4" / "rom"
                return game_root, rom_dir

        cusa = game_dir_input / "CUSA01159"
        if (cusa / "nativePS4" / "rom").is_dir():
            return cusa, cusa / "nativePS4" / "rom"

    # Fallback: assume game_dir_input / nativePS4 / rom
    return game_dir_input, game_dir_input / "nativePS4" / "rom"


def build_patch(
    game_dir: Path | str,
    db_path: Path | str,
    out_dir: Path | str,
    progress: Optional[Callable[[int, int, str], None]] = None,
    prefer_ps4_english: bool = False,
) -> dict[str, Any]:
    """Builds English patch archives from PS4 base game and translation DB.
    Never modifies anything inside game_dir.
    """
    game_dir = Path(game_dir).resolve()
    db_path = Path(db_path).resolve()
    out_dir = Path(out_dir).resolve()

    game_root, rom_dir = resolve_game_paths(game_dir)
    out_rom_dir = out_dir / "nativePS4" / "rom"

    if not db_path.is_file():
        raise FileNotFoundError(f"Translation DB not found: {db_path}")

    db_bytes = db_path.read_bytes()
    db_hash = hashlib.sha256(db_bytes).hexdigest()
    db_data = json.loads(db_bytes.decode("utf-8"))

    pool: list[str] = db_data.get("pool", [])
    archives_db: dict[str, dict[str, Any]] = db_data.get("archives", {})

    # Pre-index PS4 arcs for fast and case-insensitive lookup
    ps4_arcs_by_norm: dict[str, Path] = {}
    ps4_arcs_by_lower: dict[str, Path] = {}
    if rom_dir.is_dir():
        for p in rom_dir.rglob("*.arc"):
            if p.is_file():
                rel = p.relative_to(rom_dir).as_posix()
                ps4_arcs_by_norm[rel] = p
                ps4_arcs_by_lower[rel.lower()] = p

    stats = {
        "arcs_in_db": len(archives_db),
        "arcs_found": 0,
        "arcs_missing": 0,
        "arcs_written": 0,
        "arcs_unmodified": 0,
        "gmds_processed": 0,
        "gmds_modified": 0,
        "strings_replaced": 0,
        "strings_kept_ps4_english": 0,
        "strings_unchanged": 0,
        "sbs_garbage_adjusted": 0,
    }

    written_arcs_manifest: list[dict[str, Any]] = []

    total_archives = len(archives_db)
    sorted_rels = sorted(archives_db.keys())

    def report(cur: int, tot: int, msg: str):
        if progress:
            try:
                progress(cur, tot, msg)
            except TypeError:
                try:
                    progress(f"[{cur}/{tot}] {msg}")  # type: ignore
                except Exception:
                    pass

    for i, rel_arc in enumerate(sorted_rels):
        report(i, total_archives, f"Processing {rel_arc}")

        # Locate PS4 ARC (exact match, then case-insensitive fallback)
        ps4_arc_path = ps4_arcs_by_norm.get(rel_arc)
        if not ps4_arc_path:
            ps4_arc_path = ps4_arcs_by_lower.get(rel_arc.lower())

        if not ps4_arc_path or not ps4_arc_path.is_file():
            print(f"Warning: PS4 arc not found: {rel_arc}")
            stats["arcs_missing"] += 1
            continue

        stats["arcs_found"] += 1

        try:
            arc = ArcFile.from_file(ps4_arc_path)
        except Exception as e:
            print(f"Warning: failed reading PS4 arc {rel_arc}: {e}")
            continue

        arc_gmd_exports = archives_db[rel_arc]
        arc_changed = False

        for gmd_entry_path, patch_specs in arc_gmd_exports.items():
            arc_entry = arc.get_entry(gmd_entry_path)
            if not arc_entry:
                continue

            stats["gmds_processed"] += 1
            try:
                raw_gmd = arc_entry.decompress()
                gmd = GmdFile.from_bytes(raw_gmd)
            except Exception as e:
                print(f"Warning: failed decompressing GMD {rel_arc}:{gmd_entry_path}: {e}")
                continue

            current_strings = list(gmd.strings)
            gmd_changed = False

            # 1. Apply by_key first (map key name -> index via PS4 key list)
            if "by_key" in patch_specs:
                by_key_map = patch_specs["by_key"]
                for key_name, pool_idx in by_key_map.items():
                    k_idx = gmd.key_to_index.get(key_name)
                    if k_idx is not None and k_idx < len(current_strings):
                        target_str = pool[pool_idx]
                        curr_str = current_strings[k_idx]
                        if prefer_ps4_english and is_english(curr_str):
                            stats["strings_kept_ps4_english"] += 1
                            stats["strings_unchanged"] += 1
                        elif curr_str != target_str:
                            current_strings[k_idx] = target_str
                            stats["strings_replaced"] += 1
                            gmd_changed = True
                        else:
                            stats["strings_unchanged"] += 1

            # 2. Apply by_index
            if "by_index" in patch_specs:
                by_index_map = patch_specs["by_index"]
                for idx_str, pool_idx in by_index_map.items():
                    idx = int(idx_str)
                    if idx < len(current_strings):
                        target_str = pool[pool_idx]
                        curr_str = current_strings[idx]
                        if prefer_ps4_english and is_english(curr_str):
                            stats["strings_kept_ps4_english"] += 1
                            stats["strings_unchanged"] += 1
                        elif curr_str != target_str:
                            current_strings[idx] = target_str
                            stats["strings_replaced"] += 1
                            gmd_changed = True
                        else:
                            stats["strings_unchanged"] += 1

            if gmd_changed:
                stats["gmds_modified"] += 1
                if gmd.sbs_was_garbage:
                    stats["sbs_garbage_adjusted"] += 1
                new_gmd_bytes = gmd.to_bytes(new_strings=current_strings)
                arc_entry.set_uncompressed(new_gmd_bytes)
                arc_changed = True

        if arc_changed:
            repacked_bytes = arc.to_bytes()
            out_arc_path = out_rom_dir / rel_arc
            out_arc_path.parent.mkdir(parents=True, exist_ok=True)

            # Atomic replace via temporary file
            tmp = tempfile.NamedTemporaryFile(
                dir=out_arc_path.parent,
                delete=False,
                prefix="tmp_patch_",
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
            src_stat = ps4_arc_path.stat()
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

    report(total_archives, total_archives, "Done writing patch archives.")

    # Write manifest.json
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_payload = {
        "db_sha256": db_hash,
        "prefer_ps4_english": prefer_ps4_english,
        "stats": stats,
        "written_arcs": written_arcs_manifest,
    }
    manifest_path.write_text(
        json.dumps(manifest_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return stats


def main():
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Build PS4 English translation patch from translation DB."
    )
    default_game = repo_root / "game"
    default_db = repo_root / "data" / "en_translation_db.json"
    default_out = repo_root / "mods" / "english_patch"

    parser.add_argument(
        "--game",
        type=Path,
        default=default_game,
        help="Path to game folder (containing eboot.bin or CUSA01159)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=default_db,
        help="Path to en_translation_db.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=default_out,
        help="Path to output patch folder (e.g. mods/english_patch)",
    )
    parser.add_argument(
        "--prefer-ps4",
        action="store_true",
        default=False,
        help="Keep PS4 strings if they are already English",
    )

    args = parser.parse_args()

    last_pct = -1

    def progress(cur: int, tot: int, msg: str):
        nonlocal last_pct
        pct = int(cur * 100 / tot) if tot > 0 else 100
        if pct != last_pct and pct % 5 == 0:
            last_pct = pct
            print(f"[{pct:3d}%] ({cur}/{tot}) {msg}")

    print("Building English patch...")
    stats = build_patch(
        game_dir=args.game,
        db_path=args.db,
        out_dir=args.out,
        progress=progress,
        prefer_ps4_english=args.prefer_ps4,
    )

    print("\n--- Phase 3 Patch Build Summary ---")
    print(f"Archives in DB:          {stats['arcs_in_db']}")
    print(f"Archives found:          {stats['arcs_found']}")
    print(f"Archives missing:        {stats['arcs_missing']}")
    print(f"Archives written:        {stats['arcs_written']}")
    print(f"Archives unmodified:     {stats['arcs_unmodified']}")
    print(f"GMDs processed:          {stats['gmds_processed']}")
    print(f"GMDs modified:           {stats['gmds_modified']}")
    print(f"Strings replaced:        {stats['strings_replaced']}")
    print(f"Strings kept (PS4 Eng):  {stats['strings_kept_ps4_english']}")
    print(f"Strings unchanged:       {stats['strings_unchanged']}")
    print(f"SBS garbage adjusted:    {stats['sbs_garbage_adjusted']}")


if __name__ == "__main__":
    main()
