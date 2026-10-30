"""Generate data/ps3_patch_manifest.json from the PS3 English patch folder.
Contains SHA-256 hashes of known patch files (no game or patch assets).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

_REPO_ROOT = Path(__file__).resolve().parent.parent


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_manifest(src_dir: Path, out_path: Path) -> dict:
    src_dir = Path(src_dir).resolve()
    out_path = Path(out_path).resolve()

    # Find 0.85 base rom
    rom_085 = None
    for cand in [
        src_dir / "patch 0.85" / "rom",
        src_dir / "rom",
    ]:
        if cand.is_dir() and ((cand / "ui").is_dir() or (cand / "st").is_dir()):
            rom_085 = cand
            break

    if rom_085 is None:
        for p in src_dir.rglob("rom"):
            if p.is_dir() and ((p / "ui").is_dir() or (p / "st").is_dir()):
                rom_085 = p
                break

    if rom_085 is None:
        raise FileNotFoundError(f"Could not find patch 0.85 rom folder inside {src_dir}")

    # Find matabe fix
    matabe_dir = None
    for cand in [
        src_dir / "matabe fix" / "pl035",
        src_dir / "pl035",
    ]:
        if cand.is_dir() and any(cand.glob("pl035_*.arc")):
            matabe_dir = cand
            break

    if matabe_dir is None:
        for p in src_dir.rglob("pl035"):
            if p.is_dir() and any(p.glob("pl035_*.arc")):
                if not ("rom" in p.parts and "pl" in p.parts):
                    matabe_dir = p
                    break

    print(f"Base 0.85 rom: {rom_085}")
    print(f"Matabe fix dir: {matabe_dir}")

    t0 = time.time()
    hashes_085: dict[str, str] = {}
    for p in sorted(rom_085.rglob("*")):
        if p.is_file():
            rel = p.relative_to(rom_085).as_posix()
            hashes_085[rel] = hash_file(p)

    hashes_matabe: dict[str, str] = {}
    if matabe_dir and matabe_dir.is_dir():
        for p in sorted(matabe_dir.glob("*.arc")):
            if p.is_file():
                rel = f"pl/pl035/{p.name}"
                hashes_matabe[rel] = hash_file(p)

    manifest = {
        "meta": {
            "game": "Sengoku Basara 4 Sumeragi",
            "ps3_title_id": "BLJM61248",
            "patches": ["English patch v0.85", "Matabe fix"],
            "credits": ["Mystechry", "MikoMango"],
        },
        "patches": {
            "0.85": hashes_085,
            "matabe_fix": hashes_matabe,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    elapsed = time.time() - t0
    print(f"Generated {out_path} in {elapsed:.2f}s:")
    print(f"  0.85 patch files:   {len(hashes_085)}")
    print(f"  matabe fix files:   {len(hashes_matabe)}")
    print(f"  Total files hashed: {len(hashes_085) + len(hashes_matabe)}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Generate PS3 patch manifest.")
    parser.add_argument(
        "--src",
        type=Path,
        default=_REPO_ROOT / "mods" / "ps3 english patch",
        help="Path to PS3 english patch folder",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / "data" / "ps3_patch_manifest.json",
        help="Path to output manifest JSON",
    )
    args = parser.parse_args()
    make_manifest(args.src, args.out)


if __name__ == "__main__":
    main()
