#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 i7Exceed.
"""Read-only combined view of a dumped base game and its separate update folder.

The update (eboot.bin, sce_sys, ...) is laid over the base game in a temporary folder, so
neither dump is modified. Whatever the update does not touch is linked as a whole (a symlink,
or a junction / hard link on Windows without the symlink privilege, see mods.make_link), so
large game assets cost a handful of links. Names are matched without regard to case.
"""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile

from mods import is_link, link_target, make_link, remove_link
from patches import find_sfo, game_title_id, game_app_ver


def overlay(source, dest):
    """Lays source over dest: its files replace dest's, folders are merged, names are
    case-insensitive. dest holds links to the base; a folder both have is made a real one."""
    source, dest = Path(source), Path(dest)
    entries = {entry.name.casefold(): entry for entry in dest.iterdir()}
    for item in source.iterdir():
        match = entries.get(item.name.casefold(), dest / item.name)
        if item.is_dir() and match.exists() and match.is_dir():
            if is_link(match):  # a linked folder of the base: replace the link by a merged copy
                inner = link_target(match)
                remove(match)
                match.mkdir()
                for child in inner.iterdir():
                    make_link(match / child.name, child)
            overlay(item, match)
        else:
            if match.exists() or is_link(match):
                remove(match)
            make_link(match, item)


def remove(path):
    """Removes a file, link or folder of a view without following links into the dumps."""
    path = Path(path)
    if is_link(path):
        remove_link(path)
    elif path.is_dir():
        for child in list(path.iterdir()):
            remove(child)
        try:
            path.rmdir()
        except PermissionError:
            path.chmod(0o777)
            path.rmdir()
    else:
        try:
            path.unlink()
        except PermissionError:
            path.chmod(0o666)
            path.unlink()


def create_view(base, update, out):
    """The folder holding base with update over it, made in out; the caller removes it."""
    from patches import game_title_id
    base, update = Path(base).resolve(), Path(update).resolve()
    base_id, update_id = game_title_id(base), game_title_id(update)
    if not base_id or base_id != update_id:
        raise ValueError(f'Game and update are not the same title: {base_id} / {update_id}')
    if base == update or base in update.parents or update in base.parents:
        raise ValueError('Choose two separate folders for the game and its update')
    if not (update / 'eboot.bin').is_file():
        raise ValueError('The update folder needs eboot.bin and sce_sys/param.sfo')
    Path(out).mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix='game-update-', dir=out))
    try:
        for entry in base.iterdir():
            make_link(target / entry.name, entry)
        overlay(update, target)
    except BaseException:
        remove_view(target)
        raise
    return target


def remove_view(path):
    """Deletes a view made by create_view (the dumps stay untouched)."""
    p = Path(path)
    if p.exists() or is_link(p):
        remove(p)


def validate_game(base, update=None):
    """Validates the base game folder and optional update folder before launch.
    Both must contain the required executable (eboot.bin) and metadata (sce_sys/param.sfo).
    Reads TITLE_ID and APP_VER from sce_sys/param.sfo; rejects mismatched title IDs and invalid metadata.
    Returns (base_metadata, update_metadata).
    """
    from prepare import load_sfo

    if not base:
        raise ValueError("Choose the game folder (the one with eboot.bin).")
    base_path = Path(base).resolve()
    if not base_path.is_dir():
        raise ValueError(f"Game folder does not exist: {base}")
    if not (base_path / "eboot.bin").is_file():
        raise ValueError(f"Missing eboot.bin in game folder: {base}")

    base_sfo = find_sfo(base_path)
    if not base_sfo.is_file():
        raise ValueError(f"Missing sce_sys/param.sfo in game folder: {base}")
    try:
        base_meta = load_sfo(base_sfo)
    except Exception as err:
        raise ValueError(f"Invalid metadata in game folder {base_sfo}: {err}") from err

    base_title_id = base_meta.get("TITLE_ID")
    base_app_ver = base_meta.get("APP_VER")
    if not base_title_id or not isinstance(base_title_id, str) or not base_title_id.strip():
        raise ValueError(f"Missing or invalid TITLE_ID in game folder: {base_sfo}")
    if not base_app_ver or not isinstance(base_app_ver, str) or not base_app_ver.strip():
        raise ValueError(f"Missing or invalid APP_VER in game folder: {base_sfo}")

    update_meta = None
    if update:
        update_path = Path(update).resolve()
        if not update_path.is_dir():
            raise ValueError(f"Update folder does not exist: {update}")
        if base_path == update_path or base_path in update_path.parents or update_path in base_path.parents:
            raise ValueError("Choose two separate folders for the game and its update")
        if not (update_path / "eboot.bin").is_file():
            raise ValueError(f"Missing eboot.bin in update folder: {update}")

        update_sfo = find_sfo(update_path)
        if not update_sfo.is_file():
            raise ValueError(f"Missing sce_sys/param.sfo in update folder: {update}")
        try:
            update_meta = load_sfo(update_sfo)
        except Exception as err:
            raise ValueError(f"Invalid metadata in update folder {update_sfo}: {err}") from err

        update_title_id = update_meta.get("TITLE_ID")
        update_app_ver = update_meta.get("APP_VER")
        if not update_title_id or not isinstance(update_title_id, str) or not update_title_id.strip():
            raise ValueError(f"Missing or invalid TITLE_ID in update folder: {update_sfo}")
        if not update_app_ver or not isinstance(update_app_ver, str) or not update_app_ver.strip():
            raise ValueError(f"Missing or invalid APP_VER in update folder: {update_sfo}")

        if base_title_id.strip() != update_title_id.strip():
            raise ValueError(f"Game and update are not the same title: {base_title_id.strip()} / {update_title_id.strip()}")

    return base_meta, update_meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path, help='Base game dump folder')
    parser.add_argument('update', type=Path, help='Extracted game update folder')
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parent.parent / 'out')
    args = parser.parse_args()
    view = create_view(args.base, args.update, args.out)
    print(view)
