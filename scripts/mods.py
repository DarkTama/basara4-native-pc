#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 i7Exceed.
"""Filesystem link helpers for game update overlay and mod views.

Supports symlinks, Windows NTFS junctions (without symlink privilege),
and hard links as fallback, tracking created reparse points so temporary views
can be deleted cleanly without descending into the original game files.
"""
import os
from pathlib import Path
import shutil

# Windows without symlink privileges: track links created via junctions/hard links
LINKED = {}


def make_link(link, target):
    """Creates a link from link to target.
    Tries symlinks first. On Windows without symlink privileges, uses junctions for directories
    and hard links (or cross-volume copies) for files.
    """
    link, target = Path(link), Path(target)
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
        return
    except OSError:
        if os.name != 'nt':
            raise
    if target.is_dir():
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except OSError:
            # Mirror the folder when junctions are refused (e.g. SMB network shares)
            link.mkdir(exist_ok=True)
            for entry in target.iterdir():
                make_link(link / entry.name, entry)
            return
    else:
        try:
            os.link(target, link)
        except OSError:
            shutil.copy2(target, link)
    LINKED[str(link)] = target


def is_link(path):
    """Returns True if path is a symlink, tracked reparse point, or Windows junction."""
    path = Path(path)
    return path.is_symlink() or str(path) in LINKED or (hasattr(path, 'is_junction') and path.is_junction())


def link_target(path):
    """Returns the target of the link/junction."""
    path = Path(path)
    return LINKED.get(str(path)) or path.resolve(strict=True)


def remove_link(path):
    """Removes a link or junction without touching its target."""
    path = Path(path)
    if hasattr(path, 'is_junction') and path.is_junction():
        try:
            os.rmdir(path)
        except PermissionError:
            path.chmod(0o777)
            os.rmdir(path)
    else:
        try:
            path.unlink()
        except PermissionError:
            path.chmod(0o666)
            path.unlink()
    LINKED.pop(str(path), None)


def child(folder, name):
    """Finds an entry inside folder case-insensitively, returning folder / name if not found."""
    folder = Path(folder)
    for entry in (folder.iterdir() if folder.is_dir() else ()):
        if entry.name.casefold() == name.casefold():
            return entry
    return folder / name
