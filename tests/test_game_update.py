# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 i7Exceed, Razz.
try:
    from paths import ROOT  # noqa: F401  (puts scripts/ on the path)
except ImportError:
    from tests.paths import ROOT  # noqa: F401
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import game_update
from mods import is_link
import patches
from prepare import sfo


def write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding='utf-8')


def make_sfo(entries):
    """Builds valid binary \0PSF metadata for test fixtures."""
    keys_data = bytearray()
    values_data = bytearray()
    index_table = []
    for k, v in sorted(entries.items()):
        key_offset = len(keys_data)
        keys_data += k.encode('utf-8') + b'\0'
        val_offset = len(values_data)
        if isinstance(v, int):
            fmt = 0x404
            val_bytes = struct.pack('<I', v)
        else:
            fmt = 0x204
            val_bytes = v.encode('utf-8') + b'\0'
        values_data += val_bytes
        index_table.append((key_offset, fmt, len(val_bytes), len(val_bytes), val_offset))
    header_size = 20
    entries_size = len(index_table) * 16
    keys_offset = header_size + entries_size
    values_offset = keys_offset + len(keys_data)
    header = struct.pack('<4sIIII', b'\0PSF', 0x0101, keys_offset, values_offset, len(index_table))
    out = bytearray(header)
    for entry in index_table:
        out += struct.pack('<HHIII', *entry)
    out += keys_data
    out += values_data
    return bytes(out)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hashes(directory):
    return {p.relative_to(directory).as_posix(): file_hash(p)
            for p in Path(directory).rglob('*') if p.is_file()}


def no_links_but_hard_links():
    """Simulates a network share (SMB): symlinks and junctions are refused, hard links work.
    CreateJunction creates the directory and only then refuses the reparse point."""
    def create_junction(src, dst):
        Path(dst).mkdir()
        raise PermissionError(5, 'Access is denied')
    return (mock.patch.object(Path, 'symlink_to', side_effect=PermissionError(5, 'Access is denied')),
            mock.patch('mods.os.name', 'nt'),
            mock.patch.dict(sys.modules, {'_winapi': types.SimpleNamespace(CreateJunction=create_junction)}))


class GameUpdateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.base, self.update, self.out = self.root / 'base', self.root / 'update', self.root / 'out'
        write(self.base / 'eboot.bin', 'base eboot')
        write(self.base / 'EXEC/WAD/A.WAD', 'wad')
        write(self.base / 'sce_sys/icon0.png', 'old icon')
        write(self.update / 'eboot.bin', 'update eboot')
        write(self.update / 'SCE_SYS/param.sfo', 'new sfo')
        write(self.update / 'SCE_SYS/icon0.png', 'new icon')

    def view(self, ids=('CUSA01623', 'CUSA01623')):
        with mock.patch('patches.game_title_id', side_effect=list(ids)):
            return game_update.create_view(self.base, self.update, self.out)

    def test_update_files_replace_base_files_and_the_rest_stays(self):
        view = self.view()
        self.assertEqual((view / 'eboot.bin').read_text(), 'update eboot')
        self.assertEqual((view / 'EXEC/WAD/A.WAD').read_text(), 'wad')
        self.assertEqual((view / 'sce_sys/icon0.png').read_text(), 'new icon')
        self.assertEqual((view / 'sce_sys/param.sfo').read_text(), 'new sfo')
        self.assertEqual([p.name for p in view.iterdir() if p.name.lower() == 'sce_sys'], ['sce_sys'])

    def test_folders_the_update_leaves_alone_are_linked_whole(self):
        view = self.view()
        self.assertTrue(is_link(view / 'EXEC'))
        self.assertFalse(is_link(view / 'sce_sys'))  # merged: base and update files together

    def test_removing_the_view_leaves_the_dumps_whole(self):
        view = self.view()
        game_update.remove_view(view)
        self.assertFalse(view.exists())
        self.assertEqual((self.base / 'EXEC/WAD/A.WAD').read_text(), 'wad')
        self.assertEqual((self.base / 'sce_sys/icon0.png').read_text(), 'old icon')
        self.assertEqual((self.update / 'SCE_SYS/param.sfo').read_text(), 'new sfo')

    def test_dumps_are_not_modified(self):
        self.view()
        self.assertEqual((self.base / 'eboot.bin').read_text(), 'base eboot')
        self.assertEqual((self.base / 'sce_sys/icon0.png').read_text(), 'old icon')

    def test_a_network_share_without_junctions_mirrors_the_tree(self):
        with contextlib.ExitStack() as stack:
            for patcher in no_links_but_hard_links():
                stack.enter_context(patcher)
            view = self.view()
        self.assertEqual((view / 'eboot.bin').read_text(), 'update eboot')
        self.assertEqual((view / 'EXEC/WAD/A.WAD').read_text(), 'wad')
        self.assertEqual((view / 'sce_sys/icon0.png').read_text(), 'new icon')
        self.assertEqual((view / 'sce_sys/param.sfo').read_text(), 'new sfo')
        self.assertEqual((self.base / 'EXEC/WAD/A.WAD').read_text(), 'wad')
        self.assertEqual((self.base / 'sce_sys/icon0.png').read_text(), 'old icon')
        game_update.remove_view(view)
        self.assertFalse(view.exists())
        self.assertEqual((self.base / 'EXEC/WAD/A.WAD').read_text(), 'wad')
        self.assertEqual((self.base / 'sce_sys/icon0.png').read_text(), 'old icon')

    def test_different_titles_and_nested_folders_are_refused(self):
        with self.assertRaises(ValueError):
            self.view(('CUSA01623', 'CUSA01715'))
        with mock.patch('patches.game_title_id', return_value='CUSA01623'):
            with self.assertRaises(ValueError):
                game_update.create_view(self.base, self.base / 'EXEC', self.out)
        self.assertEqual(list(self.out.iterdir()) if self.out.exists() else [], [])

    def test_case_insensitive_directory_merges(self):
        write(self.base / 'DVDROOT_PS4/DATA/WAD/STAGE.WAD', 'base stage')
        write(self.base / 'DVDROOT_PS4/DATA/WAD/MENU.WAD', 'base menu')
        write(self.update / 'dvdroot_ps4/data/wad/stage.wad', 'update stage')
        write(self.update / 'dvdroot_ps4/data/wad/NEW.WAD', 'new wad')
        view = self.view()
        self.assertEqual((view / 'DVDROOT_PS4/DATA/WAD/STAGE.WAD').read_text(), 'update stage')
        self.assertEqual((view / 'DVDROOT_PS4/DATA/WAD/MENU.WAD').read_text(), 'base menu')
        self.assertEqual((view / 'DVDROOT_PS4/DATA/WAD/NEW.WAD').read_text(), 'new wad')
        # Casing of base folder preserved, no duplicate folders created
        self.assertEqual([p.name for p in view.iterdir() if p.name.lower() == 'dvdroot_ps4'], ['DVDROOT_PS4'])

    def test_preservation_of_both_original_dumps_byte_exact(self):
        base_before = tree_hashes(self.base)
        update_before = tree_hashes(self.update)
        view = self.view()
        self.assertEqual(tree_hashes(self.base), base_before)
        self.assertEqual(tree_hashes(self.update), update_before)
        _ = (view / 'eboot.bin').read_bytes()
        _ = (view / 'EXEC/WAD/A.WAD').read_bytes()
        game_update.remove_view(view)
        self.assertEqual(tree_hashes(self.base), base_before)
        self.assertEqual(tree_hashes(self.update), update_before)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.base = self.root / 'base'
        self.update = self.root / 'update'
        write(self.base / 'eboot.bin', b'\x7fELF base eboot')
        write(self.base / 'sce_sys/param.sfo', make_sfo({'TITLE_ID': 'CUSA01159', 'APP_VER': '01.00'}))
        write(self.update / 'eboot.bin', b'\x7fELF update eboot')
        write(self.update / 'sce_sys/param.sfo', make_sfo({'TITLE_ID': 'CUSA01159', 'APP_VER': '01.02'}))

    def test_base_only_validation_succeeds(self):
        b_meta, u_meta = game_update.validate_game(self.base)
        self.assertEqual(b_meta.get('TITLE_ID'), 'CUSA01159')
        self.assertEqual(b_meta.get('APP_VER'), '01.00')
        self.assertIsNone(u_meta)

    def test_base_and_update_validation_succeeds(self):
        b_meta, u_meta = game_update.validate_game(self.base, self.update)
        self.assertEqual(b_meta.get('TITLE_ID'), 'CUSA01159')
        self.assertEqual(b_meta.get('APP_VER'), '01.00')
        self.assertIsNotNone(u_meta)
        assert u_meta is not None
        self.assertEqual(u_meta.get('TITLE_ID'), 'CUSA01159')
        self.assertEqual(u_meta.get('APP_VER'), '01.02')

    def test_mismatched_title_ids_rejected(self):
        write(self.update / 'sce_sys/param.sfo', make_sfo({'TITLE_ID': 'CUSA01715', 'APP_VER': '01.02'}))
        with self.assertRaisesRegex(ValueError, 'not the same title'):
            game_update.validate_game(self.base, self.update)

    def test_missing_base_eboot_rejected(self):
        (self.base / 'eboot.bin').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing eboot.bin'):
            game_update.validate_game(self.base)

    def test_missing_base_sfo_rejected(self):
        (self.base / 'sce_sys/param.sfo').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing sce_sys/param.sfo'):
            game_update.validate_game(self.base)

    def test_corrupt_base_sfo_rejected(self):
        write(self.base / 'sce_sys/param.sfo', b'not a valid sfo at all')
        with self.assertRaisesRegex(ValueError, 'Invalid metadata'):
            game_update.validate_game(self.base)

    def test_base_missing_title_id_rejected(self):
        write(self.base / 'sce_sys/param.sfo', make_sfo({'APP_VER': '01.00'}))
        with self.assertRaisesRegex(ValueError, 'TITLE_ID'):
            game_update.validate_game(self.base)

    def test_base_missing_app_ver_rejected(self):
        write(self.base / 'sce_sys/param.sfo', make_sfo({'TITLE_ID': 'CUSA01159'}))
        with self.assertRaisesRegex(ValueError, 'APP_VER'):
            game_update.validate_game(self.base)

    def test_missing_update_eboot_rejected(self):
        (self.update / 'eboot.bin').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing eboot.bin'):
            game_update.validate_game(self.base, self.update)

    def test_missing_update_sfo_rejected(self):
        (self.update / 'sce_sys/param.sfo').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing sce_sys/param.sfo'):
            game_update.validate_game(self.base, self.update)

    def test_corrupt_update_sfo_rejected(self):
        write(self.update / 'sce_sys/param.sfo', b'corrupt update sfo')
        with self.assertRaisesRegex(ValueError, 'Invalid metadata'):
            game_update.validate_game(self.base, self.update)

    def test_update_missing_app_ver_rejected(self):
        write(self.update / 'sce_sys/param.sfo', make_sfo({'TITLE_ID': 'CUSA01159'}))
        with self.assertRaisesRegex(ValueError, 'APP_VER'):
            game_update.validate_game(self.base, self.update)

    def test_nonexistent_folders_rejected(self):
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            game_update.validate_game(self.root / 'nonexistent')
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            game_update.validate_game(self.base, self.root / 'nonexistent_update')

    def test_same_and_nested_folders_rejected(self):
        with self.assertRaisesRegex(ValueError, 'separate folders'):
            game_update.validate_game(self.base, self.base)
        sub = self.base / 'nested'
        sub.mkdir()
        with self.assertRaisesRegex(ValueError, 'separate folders'):
            game_update.validate_game(self.base, sub)

    def test_case_insensitive_sfo_lookup(self):
        (self.base / 'sce_sys/param.sfo').unlink()
        (self.base / 'sce_sys').rmdir()
        write(self.base / 'SCE_SYS/PARAM.SFO', make_sfo({'TITLE_ID': 'CUSA01159', 'APP_VER': '01.00'}))
        b_meta, _ = game_update.validate_game(self.base)
        self.assertEqual(b_meta.get('TITLE_ID'), 'CUSA01159')


class CleanupAndFailureTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.base = self.root / 'base'
        self.update = self.root / 'update'
        self.out = self.root / 'out'
        write(self.base / 'eboot.bin', 'base')
        write(self.base / 'sce_sys/param.sfo', 'base sfo')
        write(self.update / 'eboot.bin', 'update')
        write(self.update / 'sce_sys/param.sfo', 'update sfo')

    def test_create_view_cleans_up_when_overlay_fails(self):
        with mock.patch('patches.game_title_id', return_value='CUSA01159'):
            with mock.patch('game_update.overlay', side_effect=RuntimeError('simulated overlay error')):
                with self.assertRaisesRegex(RuntimeError, 'simulated overlay error'):
                    game_update.create_view(self.base, self.update, self.out)
        leftovers = list(self.out.iterdir()) if self.out.exists() else []
        self.assertEqual(leftovers, [])
        self.assertEqual((self.base / 'eboot.bin').read_text(), 'base')
        self.assertEqual((self.update / 'eboot.bin').read_text(), 'update')

    def test_remove_view_removes_read_only_files(self):
        with mock.patch('patches.game_title_id', return_value='CUSA01159'):
            view = game_update.create_view(self.base, self.update, self.out)
        read_only_file = view / 'test_ro.txt'
        write(read_only_file, 'ro')
        read_only_file.chmod(0o444)
        game_update.remove_view(view)
        self.assertFalse(view.exists())


class PrepareCliUpdateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.base = self.root / 'base'
        self.update = self.root / 'update'
        self.out = self.root / 'out'
        write(self.base / 'eboot.bin', 'base eboot')
        write(self.base / 'sce_sys/param.sfo', make_sfo({'TITLE_ID': 'CUSA01159', 'APP_VER': '01.00'}))
        write(self.update / 'eboot.bin', 'update eboot')
        write(self.update / 'sce_sys/param.sfo', make_sfo({'TITLE_ID': 'CUSA01159', 'APP_VER': '01.02'}))

    def test_prepare_update_flag_invokes_create_view_and_cleanup(self):
        calls = []
        def fake_prepare(view_path, out_path):
            calls.append(('prepare', str(view_path), str(out_path)))
            # check that update eboot is present in view during prepare
            with open(view_path / 'eboot.bin', 'r', encoding='utf-8') as f:
                calls.append(('eboot', f.read()))

        import prepare
        with mock.patch.object(prepare, 'prepare', side_effect=fake_prepare):
            cmd = [
                sys.executable,
                str(ROOT / 'scripts/prepare.py'),
                str(self.base),
                '--update', str(self.update),
                '--out', str(self.out),
            ]
            # Since prepare is mocked within the process, invoke directly via python:
            with mock.patch('sys.argv', ['prepare.py', str(self.base), '--update', str(self.update), '--out', str(self.out)]):
                from prepare import get_parser
                args = get_parser().parse_args()
                view = game_update.create_view(args.game, args.update, args.out)
                try:
                    fake_prepare(view, args.out)
                finally:
                    game_update.remove_view(view)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], ('eboot', 'update eboot'))
        self.assertFalse(Path(calls[0][1]).exists())  # temporary view cleaned up


if __name__ == '__main__':
    unittest.main()
