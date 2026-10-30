"""Standalone CLI tool for MT Framework ARC archives and GMD string tables.

Usage:
    python tools/arc_tool.py list <file.arc>
        Print one line per entry (uncompressed size, type, path) and a summary footer.

    python tools/arc_tool.py extract <file.arc> <out_dir>
        Extract all decompressed entries to <out_dir>/<path><ext> and generate
        arc_manifest.json with container metadata and entry order.

    python tools/arc_tool.py repack <original.arc> <dir> <out.arc>
        Load original ARC as template, replace entries whose files in <dir> differ,
        keep unmodified entries byte-identical, and write out.arc.

    python tools/arc_tool.py gmd <file.gmd|file.arc> [--json out.json]
        Print GMD strings (index and text) to stdout. For ARC files, prints
        strings for all GMD entries. Optionally export to JSON.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "launcher") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "launcher"))

from gmdarc import (
    ARC_MAGIC_BE,
    ARC_MAGIC_LE,
    GMD_MAGIC_BE,
    GMD_MAGIC_LE,
    GMD_TYPE_HASH,
    TEX_TYPE_HASH,
    ArcEntry,
    ArcFile,
    GmdFile,
)

EXT_BY_HASH: dict[int, str] = {
    GMD_TYPE_HASH: ".gmd",
    TEX_TYPE_HASH: ".tex",
}

TYPE_BY_HASH: dict[int, str] = {
    GMD_TYPE_HASH: "gmd",
    TEX_TYPE_HASH: "tex",
}


def get_type_name(type_hash: int) -> str:
    """Return friendly type name (gmd/tex) or 8-char lowercase hex hash."""
    return TYPE_BY_HASH.get(type_hash, f"{type_hash:08x}")


def get_extension(type_hash: int) -> str:
    """Return file extension: .gmd, .tex, or .<8-hex type hash>."""
    return EXT_BY_HASH.get(type_hash, f".{type_hash:08x}")


def validate_entry_path(rel_path: str) -> list[str]:
    """Validate entry relative path and split into parts.

    Refuses paths that are absolute or contain '..' directory traversal.
    """
    norm = rel_path.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[a-zA-Z]:", norm):
        raise ValueError(f"Entry path is absolute: {rel_path}")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        raise ValueError(f"Entry path is empty: {rel_path}")
    if ".." in parts or any(p.startswith("..") for p in parts):
        raise ValueError(f"Entry path contains '..': {rel_path}")
    return parts


def get_safe_dest(base_dir: Path, rel_path: str) -> Path:
    """Resolve a destination file path under base_dir, refusing directory escape."""
    parts = validate_entry_path(rel_path)
    base_resolved = base_dir.resolve()
    dest_resolved = base_dir.joinpath(*parts).resolve()
    try:
        dest_resolved.relative_to(base_resolved)
    except ValueError:
        raise ValueError(f"Entry path escapes output directory: {rel_path}")
    return dest_resolved


def cmd_list(args: argparse.Namespace) -> int:
    arc_path = Path(args.arc_file)
    if not arc_path.is_file():
        raise FileNotFoundError(f"File not found: {arc_path}")

    arc = ArcFile.from_file(arc_path)
    for entry in arc.entries:
        t_name = get_type_name(entry.type_hash)
        print(f"{entry.uncompressed_size:>10}  {t_name:<8}  {entry.path}")

    endian_str = "BE" if arc.endian == ">" else "LE"
    print(f"{len(arc.entries)} entries ({endian_str}, v{arc.version})")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    arc_path = Path(args.arc_file)
    if not arc_path.is_file():
        raise FileNotFoundError(f"File not found: {arc_path}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    arc = ArcFile.from_file(arc_path)

    # Validate all entry paths first before writing any file
    prepared_entries: list[tuple[ArcEntry, str, Path]] = []
    for entry in arc.entries:
        validate_entry_path(entry.path)
        ext = get_extension(entry.type_hash)
        rel_path_with_ext = entry.path.replace("\\", "/") + ext
        dest = get_safe_dest(out_dir, rel_path_with_ext)
        prepared_entries.append((entry, rel_path_with_ext, dest))

    manifest_entries: list[dict] = []
    for idx, (entry, rel_path_with_ext, dest) in enumerate(prepared_entries):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(entry.decompress())

        manifest_entries.append({
            "order": idx,
            "path": entry.path,
            "file": rel_path_with_ext,
            "type_hash": entry.type_hash,
            "flags": entry.flags,
        })

    manifest = {
        "endian": arc.endian,
        "version": arc.version,
        "entries": manifest_entries,
    }
    manifest_path = out_dir / "arc_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


def cmd_repack(args: argparse.Namespace) -> int:
    orig_path = Path(args.original_arc).resolve()
    if not orig_path.is_file():
        raise FileNotFoundError(f"Original ARC not found: {args.original_arc}")

    out_path = Path(args.out_arc).resolve()
    if orig_path == out_path:
        raise ValueError("Output ARC cannot overwrite original ARC template")

    in_dir = Path(args.dir)
    if not in_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {args.dir}")

    arc = ArcFile.from_file(orig_path)

    # Consult manifest if present to preserve exact mapped files and order
    manifest_path = in_dir / "arc_manifest.json"
    manifest_by_order: dict[int, str] = {}
    if manifest_path.is_file():
        try:
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in mdata.get("entries", []):
                if "order" in item and "file" in item:
                    manifest_by_order[item["order"]] = item["file"]
        except Exception:
            pass

    replaced_count = 0
    for idx, entry in enumerate(arc.entries):
        file_rel = manifest_by_order.get(idx)
        if not file_rel:
            ext = get_extension(entry.type_hash)
            file_rel = entry.path.replace("\\", "/").strip("/") + ext

        parts = validate_entry_path(file_rel)
        entry_file = in_dir.joinpath(*parts)
        if entry_file.is_file():
            new_data = entry_file.read_bytes()
            orig_data = entry.decompress()
            if new_data != orig_data:
                entry.set_uncompressed(new_data)
                replaced_count += 1
                print(f"Replaced: {entry.path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(arc.to_bytes())
    return 0


def cmd_gmd(args: argparse.Namespace) -> int:
    target_path = Path(args.file)
    if not target_path.is_file():
        raise FileNotFoundError(f"File not found: {target_path}")

    data = target_path.read_bytes()
    magic = data[:4]

    if magic in (ARC_MAGIC_LE, ARC_MAGIC_BE):
        arc = ArcFile.from_bytes(data)
        gmd_entries = [e for e in arc.entries if e.is_gmd]
        json_dict: dict[str, list[dict]] = {}

        for entry in gmd_entries:
            gmd = GmdFile.from_bytes(entry.decompress())
            print(f"=== {entry.path} ({len(gmd.strings)} strings) ===")
            entry_strings: list[dict] = []
            for idx, s in enumerate(gmd.strings):
                print(f"[{idx}] {s}")
                entry_strings.append({"index": idx, "text": s})
            json_dict[entry.path] = entry_strings

        if args.json:
            out_json = Path(args.json)
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(
                json.dumps(json_dict, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return 0

    if magic in (GMD_MAGIC_LE, GMD_MAGIC_BE):
        gmd = GmdFile.from_bytes(data)
        json_list: list[dict] = []
        for idx, s in enumerate(gmd.strings):
            print(f"[{idx}] {s}")
            json_list.append({"index": idx, "text": s})

        if args.json:
            out_json = Path(args.json)
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(
                json.dumps(json_list, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return 0

    raise ValueError(f"Unrecognized file format (expected ARC or GMD magic): {target_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arc_tool.py",
        description="MT Framework ARC archive and GMD string table tool.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List ARC entries")
    p_list.add_argument("arc_file", help="Path to ARC file")

    # extract
    p_extract = subparsers.add_parser("extract", help="Extract ARC entries")
    p_extract.add_argument("arc_file", help="Path to ARC file")
    p_extract.add_argument("out_dir", help="Directory to extract files into")

    # repack
    p_repack = subparsers.add_parser("repack", help="Repack ARC entries from directory")
    p_repack.add_argument("original_arc", help="Path to original template ARC file")
    p_repack.add_argument("dir", help="Directory containing extracted/modified files")
    p_repack.add_argument("out_arc", help="Path to output ARC file")

    # gmd
    p_gmd = subparsers.add_parser("gmd", help="Print strings from GMD or ARC file")
    p_gmd.add_argument("file", help="Path to GMD or ARC file")
    p_gmd.add_argument("--json", dest="json", help="Path to output JSON file")

    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.subcommand == "list":
            return cmd_list(args)
        elif args.subcommand == "extract":
            return cmd_extract(args)
        elif args.subcommand == "repack":
            return cmd_repack(args)
        elif args.subcommand == "gmd":
            return cmd_gmd(args)
        return 0
    except (FileNotFoundError, ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
