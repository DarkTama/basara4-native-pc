"""Phase 2: Extract PS3 English translation into data/en_translation_db.json
and generate data/mismatched_gmd_report.json.
Thin wrapper around launcher/basara4_translation_extract.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "launcher") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "launcher"))

from basara4_translation_extract import (
    extract_translation,
    extract_translation_data,
    find_default_ps4_root,
)


def main():
    parser = argparse.ArgumentParser(
        description="Extract PS3 English translation for PS4 ARC patch builder."
    )
    default_ps3 = _REPO_ROOT / "_ps3_source" / "rom"
    default_ps4 = find_default_ps4_root(_REPO_ROOT)
    default_db = _REPO_ROOT / "data" / "en_translation_db.json"
    default_report = _REPO_ROOT / "data" / "mismatched_gmd_report.json"

    parser.add_argument(
        "--ps3-root",
        type=Path,
        default=default_ps3,
        help="Path to PS3 ROM directory",
    )
    parser.add_argument(
        "--ps4-root",
        type=Path,
        default=default_ps4,
        help="Path to PS4 ROM directory",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=default_db,
        help="Path to output en_translation_db.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=default_report,
        help="Path to output mismatched_gmd_report.json",
    )

    args = parser.parse_args()
    extract_translation(args.ps3_root, args.ps4_root, args.out, args.report)


if __name__ == "__main__":
    main()
