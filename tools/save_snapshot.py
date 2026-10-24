#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Save snapshots: keep named copies of the game's save data and put one back, so a bug that takes 20 minutes of play to reach can be
reproduced from a save made just before it.

  python tools/save_snapshot.py list
  python tools/save_snapshot.py save <name>      copy the current save data into a snapshot
  python tools/save_snapshot.py load <name>      put a snapshot back (the current save is backed up first)
  python tools/save_snapshot.py --user D:\\path\\to\\user ...     (default: G:\\basara4-windows\\user, or ./user)

Only the game's save data is touched (user/savedata).  Close the game first; it refuses to run while the game is open.
"""
import argparse
import datetime
import shutil
import subprocess
import sys
from pathlib import Path


def default_user():
    for candidate in (Path(r"G:\basara4-windows\user"), Path("user")):
        if candidate.is_dir():
            return candidate
    return Path("user")


def game_running():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-Process basara4-core -ErrorAction SilentlyContinue | Measure-Object).Count"],
                         capture_output=True, text=True).stdout.strip()
    return out not in ("", "0")


def size_of(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def describe(path):
    files = [f for f in path.rglob("*") if f.is_file()]
    newest = max((f.stat().st_mtime for f in files), default=0)
    when = datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M:%S") if newest else "-"
    return f"{len(files)} files, {size_of(path) / 1048576:.1f} MiB, last save {when}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", type=Path, default=default_user())
    parser.add_argument("command", choices=["list", "save", "load"])
    parser.add_argument("name", nargs="?")
    args = parser.parse_args()

    save = args.user / "savedata"
    root = args.user / "save_snapshots"

    if args.command == "list":
        print(f"current save: {describe(save) if save.is_dir() else 'none'}")
        snaps = sorted(p for p in root.glob("*") if p.is_dir()) if root.is_dir() else []
        if not snaps:
            print("no snapshots yet")
        for snap in snaps:
            print(f"  {snap.name}: {describe(snap)}")
        return 0

    if not args.name or any(c in args.name for c in '\\/:*?"<>|'):
        sys.exit("give a plain snapshot name (no slashes or special characters)")
    if game_running():
        sys.exit("the game is running - close it first so the save is not changed under it")

    target = root / args.name
    if args.command == "save":
        if not save.is_dir():
            sys.exit(f"no save data at {save}")
        if target.exists():
            sys.exit(f"snapshot '{args.name}' exists already (choose another name, or delete {target})")
        shutil.copytree(save, target)
        print(f"saved snapshot '{args.name}': {describe(target)}")
        return 0

    if not target.is_dir():
        sys.exit(f"no snapshot '{args.name}' (see: list)")
    if save.is_dir():
        backup = root / ("backup-before-load-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(save, backup)
        print(f"current save kept as '{backup.name}'")
        shutil.rmtree(save)
    shutil.copytree(target, save)
    print(f"loaded snapshot '{args.name}': {describe(save)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
