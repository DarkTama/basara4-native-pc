"""Core translation extraction logic for Sengoku Basara 4.
Extracts GMD string tables live from PS3 patch files and matches them with PS4 game archives.
Zero dependencies outside stdlib and gmdarc.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Optional, Union

try:
    from .gmdarc import (
        ArcFile,
        GmdFile,
        classify_string_skip_reason,
        is_english,
    )
except ImportError:
    _LAUNCHER_DIR = Path(__file__).resolve().parent
    if str(_LAUNCHER_DIR) not in sys.path:
        sys.path.insert(0, str(_LAUNCHER_DIR))
    from gmdarc import (
        ArcFile,
        GmdFile,
        classify_string_skip_reason,
        is_english,
    )


def find_default_ps4_root(repo_root: Path) -> Path:
    """Finds the default nativePS4/rom directory in the game/ folder."""
    game_dir = repo_root / "game"
    if game_dir.is_dir():
        if (game_dir / "eboot.bin").is_file():
            return game_dir / "nativePS4" / "rom"
        for child in sorted(game_dir.iterdir()):
            if child.is_dir() and (child / "eboot.bin").is_file():
                cand = child / "nativePS4" / "rom"
                if cand.is_dir():
                    return cand
        cusa = game_dir / "CUSA01159" / "nativePS4" / "rom"
        if cusa.is_dir():
            return cusa
    return repo_root / "game" / "CUSA01159" / "nativePS4" / "rom"


def extract_translation_data(
    ps3_arcs: Union[Path, dict[str, Path]],
    ps4_arcs: Union[Path, dict[str, Path]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Extracts translated English strings from PS3 GMD files against PS4 GMD files.
    Returns (db_payload, mismatched_reports, stats).
    """
    if isinstance(ps3_arcs, (str, Path)):
        ps3_root = Path(ps3_arcs)
        ps3_arc_paths = {
            p.relative_to(ps3_root).as_posix(): p
            for p in ps3_root.rglob("*.arc")
            if p.is_file()
        }
    else:
        ps3_arc_paths = dict(ps3_arcs)

    if isinstance(ps4_arcs, (str, Path)):
        ps4_root = Path(ps4_arcs)
        ps4_arc_paths = {
            p.relative_to(ps4_root).as_posix(): p
            for p in ps4_root.rglob("*.arc")
            if p.is_file()
        }
    else:
        ps4_arc_paths = dict(ps4_arcs)

    pool: list[str] = []
    pool_map: dict[str, int] = {}

    def get_pool_index(s: str) -> int:
        idx = pool_map.get(s)
        if idx is None:
            idx = len(pool)
            pool_map[s] = idx
            pool.append(s)
        return idx

    archives_db: dict[str, Any] = {}
    mismatched_reports: list[dict[str, Any]] = []

    stats = {
        "ps3_arcs_found": len(ps3_arc_paths),
        "ps4_arcs_found": len(ps4_arc_paths),
        "arcs_compared": 0,
        "gmds_matched": 0,
        "gmds_mismatched": 0,
        "gmds_unmatched": 0,
        "strings_evaluated": 0,
        "strings_exported_by_index": 0,
        "strings_exported_by_key": 0,
        "strings_skipped": {
            "empty": 0,
            "identical": 0,
            "has_cjk": 0,
            "no_ascii_run": 0,
        },
        "unique_english_strings": 0,
        "archives_with_translations": 0,
    }

    sorted_ps3_rels = sorted(ps3_arc_paths.keys())

    for rel_arc in sorted_ps3_rels:
        ps3_path = ps3_arc_paths[rel_arc]
        try:
            ps3_arc = ArcFile.from_file(ps3_path)
        except Exception:
            # Skip non-ARC files (e.g. SFH files)
            continue

        ps3_gmd_entries = [e for e in ps3_arc.entries if e.is_gmd]
        if not ps3_gmd_entries:
            continue

        ps4_path = ps4_arc_paths.get(rel_arc)
        if not ps4_path or not ps4_path.is_file():
            stats["gmds_unmatched"] += len(ps3_gmd_entries)
            continue

        stats["arcs_compared"] += 1
        try:
            ps4_arc = ArcFile.from_file(ps4_path)
        except Exception as e:
            print(f"Warning: failed reading PS4 arc {rel_arc}: {e}", file=sys.stderr)
            continue

        arc_export: dict[str, Any] = {}

        for ps3_entry in ps3_gmd_entries:
            ps4_entry = ps4_arc.get_entry(ps3_entry.path)
            if not ps4_entry:
                stats["gmds_unmatched"] += 1
                continue

            try:
                gmd3 = GmdFile.from_bytes(ps3_entry.decompress())
                gmd4 = GmdFile.from_bytes(ps4_entry.decompress())
            except Exception as e:
                print(f"Warning: error parsing GMD {rel_arc}:{ps3_entry.path}: {e}", file=sys.stderr)
                continue

            is_matched = (
                gmd3.keys == gmd4.keys
                and len(gmd3.strings) == len(gmd4.strings)
            )

            if is_matched:
                stats["gmds_matched"] += 1
                by_index_map: dict[str, int] = {}
                for i in range(len(gmd3.strings)):
                    stats["strings_evaluated"] += 1
                    s3 = gmd3.strings[i]
                    s4 = gmd4.strings[i]
                    reason = classify_string_skip_reason(s3, s4)
                    if reason is not None:
                        stats["strings_skipped"][reason] += 1
                        continue
                    pool_idx = get_pool_index(s3)
                    by_index_map[str(i)] = pool_idx
                    stats["strings_exported_by_index"] += 1

                if by_index_map:
                    arc_export[ps3_entry.path] = {"by_index": by_index_map}

            else:
                stats["gmds_mismatched"] += 1
                keys3_set = set(gmd3.keys)
                keys4_set = set(gmd4.keys)
                common_keys = [k for k in gmd3.keys if k in keys4_set]
                only_ps3 = [k for k in gmd3.keys if k not in keys4_set]
                only_ps4 = [k for k in gmd4.keys if k not in keys3_set]

                by_key_map: dict[str, int] = {}
                exported_keys: list[str] = []

                if gmd3.key_count > 0 and gmd4.key_count > 0:
                    for k in common_keys:
                        stats["strings_evaluated"] += 1
                        idx3 = gmd3.key_to_index[k]
                        idx4 = gmd4.key_to_index[k]
                        s3 = gmd3.strings[idx3] if idx3 < len(gmd3.strings) else ""
                        s4 = gmd4.strings[idx4] if idx4 < len(gmd4.strings) else ""
                        reason = classify_string_skip_reason(s3, s4)
                        if reason is not None:
                            stats["strings_skipped"][reason] += 1
                            continue
                        pool_idx = get_pool_index(s3)
                        by_key_map[k] = pool_idx
                        exported_keys.append(k)
                        stats["strings_exported_by_key"] += 1

                if by_key_map:
                    arc_export[ps3_entry.path] = {"by_key": by_key_map}

                unkeyed3 = gmd3.strings[gmd3.key_count :]
                unkeyed4 = gmd4.strings[gmd4.key_count :]

                mismatched_reports.append(
                    {
                        "arc": rel_arc,
                        "entry": ps3_entry.path,
                        "ps3_key_count": gmd3.key_count,
                        "ps4_key_count": gmd4.key_count,
                        "ps3_string_count": len(gmd3.strings),
                        "ps4_string_count": len(gmd4.strings),
                        "keys_only_in_ps3": only_ps3,
                        "keys_only_in_ps4": only_ps4,
                        "keys_exported": exported_keys,
                        "unkeyed_strings": {
                            "ps3": unkeyed3,
                            "ps4": unkeyed4,
                        },
                    }
                )

        if arc_export:
            archives_db[rel_arc] = arc_export
            stats["archives_with_translations"] += 1

    stats["unique_english_strings"] = len(pool)

    db_payload = {
        "meta": {
            "format": 1,
            "version": "0.85-matabe-fix",
            "credits": ["Mystechry", "MikoMango", "indrasundanese"],
            "source_endianness": "big",
            "generated_by": "launcher/basara4_translation_extract.py",
            "stats": stats,
        },
        "pool": pool,
        "archives": archives_db,
    }

    return db_payload, mismatched_reports, stats


def extract_translation(
    ps3_root: Path,
    ps4_root: Path,
    out_db_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """CLI/Tools entry point that writes DB and report files."""
    print(f"Scanning PS3 root: {ps3_root}")
    print(f"Scanning PS4 root: {ps4_root}")

    db_payload, mismatched_reports, stats = extract_translation_data(ps3_root, ps4_root)

    out_db_path.parent.mkdir(parents=True, exist_ok=True)
    db_json_str = json.dumps(
        db_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    out_db_path.write_text(db_json_str, encoding="utf-8")
    db_size = out_db_path.stat().st_size

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_json_str = json.dumps(
        mismatched_reports,
        ensure_ascii=False,
        indent=2,
    )
    report_path.write_text(report_json_str, encoding="utf-8")

    print("\n--- Phase 2 Extraction Summary ---")
    print(f"PS3 Arcs found:              {stats['ps3_arcs_found']}")
    print(f"PS4 Arcs found:              {stats['ps4_arcs_found']}")
    print(f"Arcs compared:               {stats['arcs_compared']}")
    print(f"GMDs matched (Rule 1):       {stats['gmds_matched']}")
    print(f"GMDs mismatched (Rule 2):    {stats['gmds_mismatched']}")
    print(f"GMDs unmatched (Rule 3):     {stats['gmds_unmatched']}")
    print(f"Strings evaluated:           {stats['strings_evaluated']}")
    print(f"Strings exported by index:   {stats['strings_exported_by_index']}")
    print(f"Strings exported by key:     {stats['strings_exported_by_key']}")
    print(f"Strings skipped:             {stats['strings_skipped']}")
    print(f"Unique strings in pool:      {stats['unique_english_strings']}")
    print(f"Archives with translations:  {stats['archives_with_translations']}")
    print(f"Database written to:         {out_db_path} ({db_size:,} bytes)")
    print(f"Mismatched report written:   {report_path} ({len(mismatched_reports)} GMDs)")

    return stats
