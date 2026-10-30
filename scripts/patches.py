#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 i7Exceed.
"""Game metadata and title ID helpers.

Reads TITLE_ID, APP_VER and other attributes from sce_sys/param.sfo,
matching paths case-insensitively.
"""
from pathlib import Path
from prepare import read_sfo, load_sfo


def find_sfo(game):
    """Case-insensitive search for sce_sys/param.sfo inside a game folder."""
    game = Path(game)
    for sub in ('sce_sys', 'SCE_SYS'):
        for sfo_name in ('param.sfo', 'PARAM.SFO'):
            p = game / sub / sfo_name
            if p.is_file():
                return p
    if game.is_dir():
        for entry in game.iterdir():
            if entry.is_dir() and entry.name.casefold() == 'sce_sys':
                for f in entry.iterdir():
                    if f.is_file() and f.name.casefold() == 'param.sfo':
                        return f
    return game / 'sce_sys/param.sfo'


def game_sfo(game):
    """The values of the game folder's param.sfo, or {} when it cannot be read."""
    if not game:
        return {}
    sfo_path = find_sfo(game)
    return read_sfo(sfo_path) or {}


def game_title_id(game):
    """The TITLE_ID from the game folder's param.sfo, or None."""
    return game_sfo(game).get('TITLE_ID')


def game_app_ver(game):
    """The APP_VER from the game folder's param.sfo, or None."""
    ver = game_sfo(game).get('APP_VER')
    return ver.strip() if isinstance(ver, str) and ver.strip() else None
