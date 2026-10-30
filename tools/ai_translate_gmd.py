#!/usr/bin/env python3
"""AI Translation Pipeline and Cache Engine for Sengoku Basara 4.

Supports:
1. Cache architecture: data/ai_translation_cache.json ({jp_text: en_text}) with
   automatic resume filtering.
2. Batching: chunking target strings (default 50/batch) with control code
   extraction & validation (%d, %s, %i, %02d, \\r\\n, \\n, <SIZE ...>, <COLOR ...>, etc.).
3. Mismatched GMD special handling: targets msg_tact_achieve, msg_mission_name,
   msg_mission_panel, msg_battle_prepare, msg_bonus.
4. Apply Engine: applies translations from cache directly to GMD string tables
   in mods/english_patch/ or updates data/en_translation_db.json.

Zero external dependencies outside stdlib and gmdarc.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Optional, Sequence, Union

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "launcher") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "launcher"))
if str(_REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "tools"))

try:
    from gmdarc import ArcFile, ArcEntry, GmdFile, is_english
except ImportError:
    # Fallback to local import if needed
    from launcher.gmdarc import ArcFile, ArcEntry, GmdFile, is_english  # type: ignore


# Default file locations
DEFAULT_TIER1_PATH = _REPO_ROOT / "data" / "tier1_unique_strings.json"
DEFAULT_CACHE_PATH = _REPO_ROOT / "data" / "ai_translation_cache.json"
DEFAULT_DB_PATH = _REPO_ROOT / "data" / "en_translation_db.json"
DEFAULT_REPORT_PATH = _REPO_ROOT / "data" / "mismatched_gmd_report.json"
DEFAULT_MODS_DIR = _REPO_ROOT / "mods" / "english_patch"
DEFAULT_BATCH_SIZE = 50

# Mismatched GMD keywords specifically targeted
MISMATCHED_TARGET_KEYWORDS = [
    "msg_tact_achieve",
    "msg_mission_name",
    "msg_mission_panel",
    "msg_battle_prepare",
    "msg_bonus",
]

# Regex patterns for control codes
TAG_PATTERN = re.compile(r"<[^>]+>")
FORMAT_PATTERN = re.compile(r"%(?:%|[-+0 #]*\d*(?:\.\d+)?[diuoxXfFeEgGaAcspn])")
NEWLINE_PATTERN = re.compile(r"\r\n|\n|\r")


# ============================================================================
# 1. Cache Architecture & Deduplication
# ============================================================================

def load_cache(cache_path: Union[Path, str]) -> dict[str, str]:
    """Loads translation cache from JSON file. Returns empty dict if not found."""
    cache_path = Path(cache_path)
    if not cache_path.is_file():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if v is not None}
    except Exception as e:
        print(f"Warning: failed loading cache from {cache_path}: {e}", file=sys.stderr)
    return {}


def save_cache(cache: dict[str, str], cache_path: Union[Path, str]) -> None:
    """Atomically saves translation cache to JSON file with UTF-8 encoding."""
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp")
    data_str = json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True)
    tmp_path.write_text(data_str, encoding="utf-8")
    tmp_path.replace(cache_path)


def deduplicate_strings(strings: Sequence[str]) -> list[str]:
    """Deduplicates strings while strictly preserving original first-seen order.
    Filters out empty strings.
    """
    seen: set[str] = set()
    result: list[str] = []
    for s in strings:
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    return result


def filter_pending_strings(
    strings: Sequence[str],
    cache: dict[str, str],
) -> list[str]:
    """Filters target strings to only those needing translation (resume capability).
    A string is pending if it is not in the cache or its cached translation is empty.
    """
    unique = deduplicate_strings(strings)
    pending: list[str] = []
    for s in unique:
        cached_val = cache.get(s)
        if cached_val is None or not cached_val.strip():
            pending.append(s)
    return pending


def get_cache_stats(
    cache: dict[str, str],
    target_strings: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Computes status statistics for the translation cache."""
    total_cached = len(cache)
    translated_count = sum(1 for v in cache.values() if v and v.strip())
    empty_count = total_cached - translated_count

    stats: dict[str, Any] = {
        "cached_entries": total_cached,
        "valid_translations": translated_count,
        "empty_entries": empty_count,
    }

    if target_strings is not None:
        unique_targets = deduplicate_strings(target_strings)
        pending = filter_pending_strings(unique_targets, cache)
        stats["total_target_strings"] = len(unique_targets)
        stats["pending_strings"] = len(pending)
        stats["completed_target_strings"] = len(unique_targets) - len(pending)
        if unique_targets:
            pct = ((len(unique_targets) - len(pending)) / len(unique_targets)) * 100.0
            stats["coverage_percent"] = round(pct, 2)
        else:
            stats["coverage_percent"] = 100.0

    return stats


# ============================================================================
# 2. Control Codes & Batching
# ============================================================================

def extract_control_codes(text: str) -> dict[str, Any]:
    """Extracts all tags (<...>), format specifiers (%d, %s, etc.), and newlines."""
    tags = TAG_PATTERN.findall(text)
    formats = FORMAT_PATTERN.findall(text)
    newlines = NEWLINE_PATTERN.findall(text)
    return {
        "tags": tags,
        "formats": formats,
        "newline_count": len(newlines),
        "newlines": newlines,
    }


def validate_control_codes(src: str, dst: str) -> tuple[bool, list[str]]:
    """Validates that all control codes in `src` are preserved correctly in `dst`.
    Checks:
    - Tags (<SIZE ...>, </SIZE>, <COLOR ...>, etc.) match in count and content.
    - Format specifiers (%d, %s, %i, %02d, etc.) match in count and type.
    - Newlines (\\r\\n, \\n) are preserved with identical count.
    Returns (is_valid, list_of_errors).
    """
    errors: list[str] = []

    src_codes = extract_control_codes(src)
    dst_codes = extract_control_codes(dst)

    # 1. Tags check
    if src_codes["tags"] != dst_codes["tags"]:
        errors.append(
            f"Tags mismatch: expected {src_codes['tags']}, got {dst_codes['tags']}"
        )

    # 2. Format specifiers check
    if src_codes["formats"] != dst_codes["formats"]:
        errors.append(
            f"Format specifiers mismatch: expected {src_codes['formats']}, got {dst_codes['formats']}"
        )

    # 3. Newline count check
    if src_codes["newline_count"] != dst_codes["newline_count"]:
        errors.append(
            f"Newline count mismatch: expected {src_codes['newline_count']}, got {dst_codes['newline_count']}"
        )

    return len(errors) == 0, errors


def chunk_strings(strings: Sequence[str], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[str]]:
    """Splits a sequence of strings into chunks of size `batch_size`."""
    if batch_size <= 0:
        raise ValueError(f"batch_size must be positive, got {batch_size}")
    return [list(strings[i : i + batch_size]) for i in range(0, len(strings), batch_size)]


def build_batch_payload(
    chunk: Sequence[str],
    batch_idx: int,
    total_batches: int,
    start_item_id: int = 0,
) -> dict[str, Any]:
    """Generates a structured JSON batch dictionary ready for LLM translation."""
    items: list[dict[str, Any]] = []
    for j, s in enumerate(chunk):
        items.append({
            "id": start_item_id + j,
            "src": s,
            "control_codes": extract_control_codes(s),
        })

    return {
        "batch_index": batch_idx,
        "total_batches": total_batches,
        "batch_size": len(chunk),
        "items": items,
        "system_instruction": (
            "Translate the following Sengoku Basara 4 game text strings from Japanese to natural English.\n"
            "CRITICAL LOCALIZATION RULES:\n"
            "1. NEVER alter, translate, or remove game control codes:\n"
            "   - Tags such as <SIZE ...>, </SIZE>, <COLOR ...>, </COLOR>, <COL ...>, </COL>, <TAG ...>\n"
            "   - Format specifiers such as %d, %s, %i, %02d, %u\n"
            "2. Preserve all newline characters (\\r\\n and \\n) in the exact relative positions.\n"
            "3. Output JSON with a 'translations' dictionary mapping item 'id' to translated string."
        ),
    }


def create_batches(
    strings: Sequence[str],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Creates all structured batch payloads for a sequence of strings."""
    chunks = chunk_strings(strings, batch_size=batch_size)
    total_batches = len(chunks)
    batches: list[dict[str, Any]] = []
    item_offset = 0

    for i, ch in enumerate(chunks):
        batch = build_batch_payload(ch, i, total_batches, start_item_id=item_offset)
        batches.append(batch)
        item_offset += len(ch)

    return batches


def export_batches(
    strings: Sequence[str],
    output_dir: Union[Path, str],
    batch_size: int = DEFAULT_BATCH_SIZE,
    cache: Optional[dict[str, str]] = None,
) -> list[Path]:
    """Filters pending strings (using cache if provided), generates structured batch
    JSON files in output_dir, and returns the written file paths.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pending = filter_pending_strings(strings, cache) if cache is not None else deduplicate_strings(strings)
    batches = create_batches(pending, batch_size=batch_size)

    written: list[Path] = []
    for b in batches:
        b_idx = b["batch_index"]
        out_file = out_dir / f"batch_{b_idx:04d}.json"
        out_file.write_text(
            json.dumps(b, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(out_file)

    return written


def import_batch_translation(
    batch_data_or_file: Union[Path, str, dict[str, Any], list[Any]],
    cache: dict[str, str],
    source_batch: Optional[dict[str, Any]] = None,
    validate: bool = True,
) -> tuple[int, list[str]]:
    """Imports translations from a translated batch file or payload into the cache.
    Accepts:
    - {"translations": {"<id>": "en", ...}} or {"<id>": "en"}
    - [{"id": <id>, "src": "jp", "translation": "en"}, ...]
    - {"<jp_text>": "<en_text>", ...}

    Returns (imported_count, list_of_validation_warnings).
    """
    data: Any = batch_data_or_file
    if isinstance(batch_data_or_file, (str, Path)):
        p = Path(batch_data_or_file)
        if not p.is_file():
            raise FileNotFoundError(f"Batch file not found: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

    # Build reference map id -> src if source_batch provided
    id_to_src: dict[int, str] = {}
    if source_batch and "items" in source_batch:
        for it in source_batch["items"]:
            id_to_src[int(it["id"])] = it["src"]

    imported = 0
    warnings: list[str] = []

    # Format 1: dictionary
    if isinstance(data, dict):
        trans_dict = data.get("translations", data)
        for k, v in trans_dict.items():
            if not isinstance(v, str):
                continue
            jp_src: Optional[str] = None
            if str(k).isdigit() and int(k) in id_to_src:
                jp_src = id_to_src[int(k)]
            elif str(k) not in ("system_instruction", "batch_index", "total_batches", "batch_size"):
                jp_src = str(k)

            if jp_src:
                if validate:
                    valid, errs = validate_control_codes(jp_src, v)
                    if not valid:
                        warnings.append(f"Validation warning for '{jp_src[:20]}...': {errs}")
                cache[jp_src] = v
                imported += 1

    # Format 2: list of items
    elif isinstance(data, list):
        for it in data:
            if isinstance(it, dict):
                src = it.get("src")
                trans = it.get("translation") or it.get("en") or it.get("dst")
                item_id = it.get("id")
                if not src and item_id is not None and int(item_id) in id_to_src:
                    src = id_to_src[int(item_id)]
                if src and isinstance(trans, str):
                    if validate:
                        valid, errs = validate_control_codes(src, trans)
                        if not valid:
                            warnings.append(f"Validation warning for '{src[:20]}...': {errs}")
                    cache[src] = trans
                    imported += 1

    return imported, warnings


# ============================================================================
# 3. Mismatched GMD Special Handling
# ============================================================================

def is_mismatched_target(
    path_or_name: str,
    keywords: Optional[Sequence[str]] = None,
) -> bool:
    """Checks if a GMD entry or file path matches one of the target mismatched GMD keywords."""
    norm = path_or_name.replace("\\", "/").lower()
    targets = keywords if keywords is not None else MISMATCHED_TARGET_KEYWORDS
    return any(kw.lower() in norm for kw in targets)


def extract_mismatched_report_strings(
    report_path: Union[Path, str],
    keywords: Optional[Sequence[str]] = None,
) -> list[str]:
    """Extracts unique untranslated Japanese strings from mismatched_gmd_report.json
    matching the specified keywords (or default mismatched targets).
    """
    report_file = Path(report_path)
    if not report_file.is_file():
        return []

    with open(report_file, "r", encoding="utf-8") as f:
        report = json.load(f)

    extracted: list[str] = []
    for item in report:
        entry = item.get("entry", "")
        if not is_mismatched_target(entry, keywords):
            continue
        unkeyed_ps4 = item.get("unkeyed_strings", {}).get("ps4", [])
        for s in unkeyed_ps4:
            if s and not is_english(s):
                extracted.append(s)

    return deduplicate_strings(extracted)


def get_mismatched_index_map_from_report(
    report_path: Union[Path, str],
    cache: dict[str, str],
    keywords: Optional[Sequence[str]] = None,
) -> dict[str, dict[str, dict[int, str]]]:
    """Generates an index replacement map from mismatched_gmd_report.json.
    Returns: { rel_arc: { gmd_entry: { string_index: translated_text } } }
    Since unkeyed strings start at index ps4_key_count in PS4 GMDs, string j
    has index ps4_key_count + j.
    """
    report_file = Path(report_path)
    if not report_file.is_file():
        return {}

    with open(report_file, "r", encoding="utf-8") as f:
        report = json.load(f)

    result: dict[str, dict[str, dict[int, str]]] = {}

    for item in report:
        arc_rel = item.get("arc", "")
        entry = item.get("entry", "")
        if not is_mismatched_target(entry, keywords):
            continue

        kcnt = item.get("ps4_key_count", 0)
        unkeyed_ps4 = item.get("unkeyed_strings", {}).get("ps4", [])

        idx_map: dict[int, str] = {}
        for j, s in enumerate(unkeyed_ps4):
            idx = kcnt + j
            if s in cache and cache[s].strip() and cache[s] != s:
                idx_map[idx] = cache[s]

        if idx_map:
            result.setdefault(arc_rel, {})[entry] = idx_map

    return result


# ============================================================================
# 4. Apply Engine
# ============================================================================

def apply_cache_to_string_list(
    strings: Sequence[str],
    cache: dict[str, str],
    index_map: Optional[dict[int, str]] = None,
) -> tuple[int, list[str]]:
    """Applies cached translations to a string list by index or exact text match.
    Returns (replaced_count, new_strings_list).
    """
    new_strings = list(strings)
    replaced = 0

    for i, s in enumerate(new_strings):
        # 1. Match by index if explicit index mapping provided
        if index_map is not None and i in index_map:
            val = index_map[i]
            if val != new_strings[i]:
                new_strings[i] = val
                replaced += 1
        # 2. Match by exact text in translation cache
        elif s in cache:
            cached_trans = cache[s]
            if cached_trans.strip() and cached_trans != s:
                new_strings[i] = cached_trans
                replaced += 1

    return replaced, new_strings


def apply_cache_to_gmd(
    gmd: GmdFile,
    cache: dict[str, str],
    index_map: Optional[dict[int, str]] = None,
) -> tuple[int, GmdFile]:
    """Applies cached translations to an in-memory GmdFile.
    Returns (replaced_count, updated_GmdFile).
    """
    replaced, new_strings = apply_cache_to_string_list(gmd.strings, cache, index_map=index_map)
    if replaced == 0:
        return 0, gmd

    new_bytes = gmd.to_bytes(new_strings=new_strings)
    updated_gmd = GmdFile.from_bytes(new_bytes)
    return replaced, updated_gmd


def apply_cache_to_arc(
    arc: ArcFile,
    cache: dict[str, str],
    target_filter: Optional[Callable[[str], bool]] = None,
    index_maps: Optional[dict[str, dict[int, str]]] = None,
) -> tuple[int, int]:
    """Applies cached translations to all matching GMD entries in an ArcFile.
    Returns (gmds_modified, strings_replaced).
    """
    gmds_modified = 0
    strings_replaced = 0

    for entry in arc.entries:
        if not entry.is_gmd:
            continue
        if target_filter is not None and not target_filter(entry.path):
            continue

        try:
            gmd = GmdFile.from_bytes(entry.decompress())
        except Exception as e:
            print(f"Warning: failed reading GMD entry {entry.path}: {e}", file=sys.stderr)
            continue

        entry_idx_map = index_maps.get(entry.path) if index_maps else None
        rep_count, updated_gmd = apply_cache_to_gmd(gmd, cache, index_map=entry_idx_map)
        if rep_count > 0:
            entry.set_uncompressed(updated_gmd.to_bytes())
            gmds_modified += 1
            strings_replaced += rep_count

    return gmds_modified, strings_replaced


def apply_cache_to_mods_directory(
    mods_dir: Union[Path, str],
    cache: dict[str, str],
    target_keywords: Optional[Sequence[str]] = None,
    report_path: Optional[Union[Path, str]] = None,
) -> dict[str, Any]:
    """Applies translation cache directly into repacked ARC files in mods/english_patch.
    Targets mismatched GMDs or all GMDs if specified.
    """
    mods_root = Path(mods_dir).resolve()
    rom_dir = mods_root / "nativePS4" / "rom" if (mods_root / "nativePS4" / "rom").is_dir() else mods_root

    stats = {
        "arcs_found": 0,
        "arcs_modified": 0,
        "gmds_modified": 0,
        "strings_replaced": 0,
    }

    if not rom_dir.is_dir():
        print(f"Mods ROM directory does not exist: {rom_dir}", file=sys.stderr)
        return stats

    # Build report index map if report is provided
    report_index_maps: dict[str, dict[str, dict[int, str]]] = {}
    if report_path and Path(report_path).is_file():
        report_index_maps = get_mismatched_index_map_from_report(report_path, cache, target_keywords)

    arc_files = [p for p in rom_dir.rglob("*.arc") if p.is_file()]
    stats["arcs_found"] = len(arc_files)

    for arc_path in arc_files:
        try:
            rel_arc = arc_path.relative_to(rom_dir).as_posix()
        except ValueError:
            rel_arc = arc_path.name

        try:
            arc = ArcFile.from_file(arc_path)
        except Exception as e:
            print(f"Warning: failed reading ARC {arc_path}: {e}", file=sys.stderr)
            continue

        filter_fn: Optional[Callable[[str], bool]] = None
        if target_keywords is not None:
            filter_fn = lambda p: is_mismatched_target(p, target_keywords)

        arc_idx_maps = report_index_maps.get(rel_arc)

        g_mod, s_rep = apply_cache_to_arc(
            arc,
            cache,
            target_filter=filter_fn,
            index_maps=arc_idx_maps,
        )

        if g_mod > 0:
            repacked_bytes = arc.to_bytes()
            tmp_path = arc_path.with_suffix(".tmp_patch")
            tmp_path.write_bytes(repacked_bytes)
            tmp_path.replace(arc_path)
            stats["arcs_modified"] += 1
            stats["gmds_modified"] += g_mod
            stats["strings_replaced"] += s_rep

    return stats


def update_translation_db(
    db_path: Union[Path, str],
    cache: dict[str, str],
    report_path: Optional[Union[Path, str]] = None,
    ps4_rom_dir: Optional[Union[Path, str]] = None,
    out_path: Optional[Union[Path, str]] = None,
) -> dict[str, Any]:
    """Updates data/en_translation_db.json with new AI translations.
    Injects translations into pool and updates 'by_index' mappings for mismatched
    GMDs (from report) and/or scanned PS4 GMDs.
    """
    db_file = Path(db_path)
    out_file = Path(out_path) if out_path else db_file

    if db_file.is_file():
        with open(db_file, "r", encoding="utf-8") as f:
            db_data = json.load(f)
    else:
        db_data = {
            "meta": {
                "format": 1,
                "version": "ai-tier1-patch",
                "generated_by": "tools/ai_translate_gmd.py",
            },
            "pool": [],
            "archives": {},
        }

    pool: list[str] = db_data.setdefault("pool", [])
    pool_map: dict[str, int] = {s: i for i, s in enumerate(pool)}
    archives: dict[str, Any] = db_data.setdefault("archives", {})

    def get_or_add_pool(text: str) -> int:
        idx = pool_map.get(text)
        if idx is None:
            idx = len(pool)
            pool_map[text] = idx
            pool.append(text)
        return idx

    stats = {
        "initial_pool_size": len(pool),
        "strings_added_to_db": 0,
        "archives_affected": 0,
        "final_pool_size": 0,
    }

    affected_arcs: set[str] = set()

    # 1. Update from mismatched report
    if report_path and Path(report_path).is_file():
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)

        for item in report:
            arc_rel = item.get("arc", "")
            entry = item.get("entry", "")
            kcnt = item.get("ps4_key_count", 0)
            unkeyed = item.get("unkeyed_strings", {}).get("ps4", [])

            for j, s in enumerate(unkeyed):
                if s in cache and cache[s].strip() and cache[s] != s:
                    en_trans = cache[s]
                    pidx = get_or_add_pool(en_trans)
                    gmd_idx = kcnt + j

                    arc_entry = archives.setdefault(arc_rel, {}).setdefault(entry, {})
                    by_idx = arc_entry.setdefault("by_index", {})
                    by_idx[str(gmd_idx)] = pidx
                    stats["strings_added_to_db"] += 1
                    affected_arcs.add(arc_rel)

    # 2. Update from PS4 base game ARCs if directory provided
    if ps4_rom_dir and Path(ps4_rom_dir).is_dir():
        rom_p = Path(ps4_rom_dir)
        for arc_path in rom_p.rglob("*.arc"):
            if not arc_path.is_file():
                continue
            rel_arc = arc_path.relative_to(rom_p).as_posix()
            try:
                arc = ArcFile.from_file(arc_path)
            except Exception:
                continue

            for entry in arc.entries:
                if not entry.is_gmd:
                    continue
                try:
                    gmd = GmdFile.from_bytes(entry.decompress())
                except Exception:
                    continue

                for i, s in enumerate(gmd.strings):
                    if s in cache and cache[s].strip() and cache[s] != s:
                        en_trans = cache[s]
                        pidx = get_or_add_pool(en_trans)
                        arc_entry = archives.setdefault(rel_arc, {}).setdefault(entry.path, {})
                        by_idx = arc_entry.setdefault("by_index", {})
                        if by_idx.get(str(i)) != pidx:
                            by_idx[str(i)] = pidx
                            stats["strings_added_to_db"] += 1
                            affected_arcs.add(rel_arc)

    stats["final_pool_size"] = len(pool)
    stats["archives_affected"] = len(affected_arcs)

    # Save updated DB
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(db_data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return stats


# ============================================================================
# 5. CLI Runner
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sengoku Basara 4 - AI Translation Pipeline & Cache Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Paths
    parser.add_argument(
        "--tier1-file",
        type=Path,
        default=DEFAULT_TIER1_PATH,
        help="Path to Tier 1 unique strings JSON file (default: data/tier1_unique_strings.json)",
    )
    parser.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help="Path to translation cache JSON file (default: data/ai_translation_cache.json)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to English translation database JSON (default: data/en_translation_db.json)",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Path to mismatched GMD report JSON (default: data/mismatched_gmd_report.json)",
    )
    parser.add_argument(
        "--mods-dir",
        type=Path,
        default=DEFAULT_MODS_DIR,
        help="Path to english patch mods directory (default: mods/english_patch)",
    )
    parser.add_argument(
        "--ps4-root",
        type=Path,
        default=None,
        help="Optional path to PS4 ROM directory for scanning base game strings",
    )

    # Batching & Export
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Number of strings per batch (default: 50)",
    )
    parser.add_argument(
        "--export-batches",
        type=Path,
        default=None,
        metavar="DIR",
        help="Export pending untranslated strings as batch JSON files in DIR",
    )
    parser.add_argument(
        "--dump-batch",
        type=int,
        default=None,
        metavar="INDEX",
        help="Dump prompt payload for a specific batch index to stdout",
    )
    parser.add_argument(
        "--import-batch",
        type=Path,
        default=None,
        metavar="FILE",
        help="Import a translated batch JSON file into the translation cache",
    )
    parser.add_argument(
        "--import-json",
        type=Path,
        default=None,
        metavar="FILE",
        help="Import a JSON dictionary of translations {jp: en} into the cache",
    )
    parser.add_argument(
        "--validate-cache",
        action="store_true",
        help="Validate control code preservation across all cached entries",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print translation cache coverage and pending statistics",
    )

    # Mismatched GMD options
    parser.add_argument(
        "--mismatched-only",
        action="store_true",
        help="Filter operations to strings from mismatched GMD files only",
    )
    parser.add_argument(
        "--extract-mismatched",
        action="store_true",
        help="Extract unique Japanese strings from mismatched GMD report",
    )

    # Apply Engine
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run Apply Engine: inject cached translations into mods or translation DB",
    )
    parser.add_argument(
        "--apply-target",
        choices=["all", "mods", "db"],
        default="all",
        help="Target for Apply Engine: 'all', 'mods', or 'db' (default: all)",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    cache = load_cache(args.cache_file)

    # 1. Handle Import JSON / Batch
    if args.import_json:
        if not args.import_json.is_file():
            print(f"Error: import file not found: {args.import_json}", file=sys.stderr)
            return 1
        with open(args.import_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        imported, warnings = import_batch_translation(data, cache)
        save_cache(cache, args.cache_file)
        print(f"Imported {imported} translations into {args.cache_file}")
        if warnings:
            print(f"Warnings ({len(warnings)}):")
            for w in warnings[:10]:
                print(f"  {w}")
        return 0

    if args.import_batch:
        imported, warnings = import_batch_translation(args.import_batch, cache)
        save_cache(cache, args.cache_file)
        print(f"Imported {imported} translations from {args.import_batch} into {args.cache_file}")
        if warnings:
            print(f"Warnings ({len(warnings)}):")
            for w in warnings[:10]:
                print(f"  {w}")
        return 0

    # 2. Handle Validate Cache
    if args.validate_cache:
        total_errors = 0
        print(f"Validating {len(cache)} entries in {args.cache_file}...")
        for src, dst in cache.items():
            valid, errs = validate_control_codes(src, dst)
            if not valid:
                total_errors += 1
                print(f"[FAIL] {repr(src[:30])} -> {errs}")
        if total_errors == 0:
            print("All cached translations passed control code validation!")
        else:
            print(f"Validation finished with {total_errors} errors/warnings.")
        return 0 if total_errors == 0 else 1

    # Load target strings
    target_strings: list[str] = []
    if args.mismatched_only or args.extract_mismatched:
        target_strings = extract_mismatched_report_strings(args.report_path)
        if args.extract_mismatched:
            print(f"Extracted {len(target_strings)} unique Japanese strings from mismatched report.")
            for s in target_strings[:20]:
                print(f"  {repr(s)}")
            if len(target_strings) > 20:
                print(f"  ... and {len(target_strings) - 20} more.")
            return 0
    elif args.tier1_file.is_file():
        with open(args.tier1_file, "r", encoding="utf-8") as f:
            target_strings = json.load(f)

    # 3. Handle Apply Engine
    if args.apply:
        print(f"Running Apply Engine (target: {args.apply_target})...")
        print(f"Using translation cache with {len(cache)} entries.")

        if args.apply_target in ("all", "db"):
            db_stats = update_translation_db(
                db_path=args.db_path,
                cache=cache,
                report_path=args.report_path,
                ps4_rom_dir=args.ps4_root,
            )
            print(f"Translation DB updated: {args.db_path}")
            print(f"  Strings mapped: {db_stats['strings_added_to_db']}")
            print(f"  Total pool size: {db_stats['final_pool_size']}")
            print(f"  Archives affected: {db_stats['archives_affected']}")

        if args.apply_target in ("all", "mods"):
            if args.mods_dir.is_dir():
                mod_stats = apply_cache_to_mods_directory(
                    mods_dir=args.mods_dir,
                    cache=cache,
                    target_keywords=MISMATCHED_TARGET_KEYWORDS if args.mismatched_only else None,
                    report_path=args.report_path,
                )
                print(f"Mod ARCs patched in {args.mods_dir}:")
                print(f"  ARCs checked:     {mod_stats['arcs_found']}")
                print(f"  ARCs modified:    {mod_stats['arcs_modified']}")
                print(f"  GMDs modified:    {mod_stats['gmds_modified']}")
                print(f"  Strings replaced: {mod_stats['strings_replaced']}")
            else:
                print(f"Note: mods directory not found: {args.mods_dir} (skipped)")

        return 0

    # 4. Handle Export Batches
    if args.export_batches:
        pending = filter_pending_strings(target_strings, cache)
        written = export_batches(pending, args.export_batches, batch_size=args.batch_size)
        print(f"Exported {len(written)} batch files ({len(pending)} pending strings) to {args.export_batches}")
        return 0

    # 5. Handle Dump Single Batch
    if args.dump_batch is not None:
        pending = filter_pending_strings(target_strings, cache)
        batches = create_batches(pending, batch_size=args.batch_size)
        idx = args.dump_batch
        if 0 <= idx < len(batches):
            print(json.dumps(batches[idx], ensure_ascii=False, indent=2))
            return 0
        else:
            print(f"Error: batch index {idx} out of range (0..{len(batches)-1})", file=sys.stderr)
            return 1

    # 6. Default or --stats: Print Status
    stats = get_cache_stats(cache, target_strings)
    print("=== Sengoku Basara 4 - AI Translation Status ===")
    print(f"Cache File:             {args.cache_file}")
    print(f"Target Strings File:    {args.tier1_file}")
    print(f"Total Target Strings:   {stats.get('total_target_strings', len(target_strings))}")
    print(f"Completed Strings:      {stats.get('completed_target_strings', 0)}")
    print(f"Pending Strings:        {stats.get('pending_strings', len(target_strings))}")
    print(f"Translation Coverage:   {stats.get('coverage_percent', 0.0)}%")
    print(f"Total Cached Entries:   {stats['cached_entries']}")

    batches = create_batches(filter_pending_strings(target_strings, cache), batch_size=args.batch_size)
    print(f"Batches Required:       {len(batches)} (at {args.batch_size} strings/batch)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
