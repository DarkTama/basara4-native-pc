"""Tests for Launcher Game update and DLC integration (Part 2 B and C)."""
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(REPO / "launcher"))
sys.path.insert(0, str(REPO / "scripts"))

import basara4_launcher as L
import game_update
import dlc
import patches


def make_sfo(title_id: str = "CUSA01159", app_ver: str = "01.02") -> bytes:
    """Builds a minimal valid PARAM.SFO with TITLE_ID and APP_VER."""
    # Key table
    keys = b"APP_VER\0TITLE_ID\0"
    # Value table: APP_VER at 0 (8 bytes padded), TITLE_ID at 8 (16 bytes padded)
    val_ver = app_ver.encode("utf-8").ljust(8, b"\0")
    val_tid = title_id.encode("utf-8").ljust(16, b"\0")
    values = val_ver + val_tid

    header_size = 20
    entry_size = 16
    key_offset = header_size + 2 * entry_size
    val_offset = key_offset + len(keys)

    header = struct.pack("<4sIIII", b"\0PSF", 0x00000101, key_offset, val_offset, 2)
    # Entry 0: APP_VER
    entry0 = struct.pack("<HHIII", 0, 0x0204, len(app_ver) + 1, 8, 0)
    # Entry 1: TITLE_ID
    entry1 = struct.pack("<HHIII", 8, 0x0204, len(title_id) + 1, 16, 8)

    return header + entry0 + entry1 + keys + values


class TestLauncherUpdateAndDLC(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="b4_launcher_test_"))
        self.game = self.tmp / "game"
        self.update = self.tmp / "update"
        self.dlc = self.tmp / "dlc"
        self.out = self.tmp / "out"
        self.user = self.tmp / "user"

        for p in (self.game, self.update, self.dlc, self.out, self.user):
            p.mkdir(parents=True, exist_ok=True)

        # Base game structure
        (self.game / "eboot.bin").write_bytes(b"BASE_EBOOT_BIN_DATA")
        (self.game / "sce_sys").mkdir(parents=True, exist_ok=True)
        (self.game / "sce_sys" / "param.sfo").write_bytes(make_sfo("CUSA01159", "01.00"))

        # Update structure
        (self.update / "eboot.bin").write_bytes(b"UPDATE_EBOOT_BIN_NEW")
        (self.update / "sce_sys").mkdir(parents=True, exist_ok=True)
        (self.update / "sce_sys" / "param.sfo").write_bytes(make_sfo("CUSA01159", "01.02"))

        # DLC structure: <root>/CUSA01159/ITEM001
        addon_dir = self.dlc / "CUSA01159" / "ITEM000000000001"
        addon_dir.mkdir(parents=True, exist_ok=True)
        (addon_dir / "data.bin").write_bytes(b"ADDON_DATA")

        # Patch Launcher directories
        self.patches = [
            mock.patch.object(L, "USER", self.user),
            mock.patch.object(L, "OUT", self.out),
            mock.patch.object(L, "CONFIG", self.user / "launcher.json"),
            mock.patch.object(L, "INI", self.user / "basara4.ini"),
            mock.patch.object(L, "STAMP", self.out / "prepared.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_and_row_hints(self):
        self.assertIn("game_update", L.DEFAULTS)
        self.assertIn("dlc_folder", L.DEFAULTS)
        self.assertIn("game_update", L.ROW)
        self.assertIn("dlc_folder", L.ROW)
        self.assertEqual(L.ROW["game_update"][0], "Game update folder")
        self.assertEqual(L.ROW["dlc_folder"][0], "DLC folder")
        self.assertIn("Separate update dump", L.ROW["game_update"][1])
        self.assertIn("Folder holding extracted add-ons", L.ROW["dlc_folder"][1])

    def test_launch_flow_with_update_and_dlc(self):
        class DummyLauncher:
            cfg = {
                "game": "",
                "game_update": "",
                "dlc_folder": "",
                "fps_limit": 60,
                "english_patch": False,
            }
            pending = None
            pending_text = ""
            update_view = None

            def _progress(self, text):
                self.pending_text = text

            def _cleanup_update_view(self):
                if getattr(self, "update_view", None):
                    try:
                        game_update.remove_view(self.update_view)
                    except Exception:
                        pass
                    self.update_view = None

        inst = DummyLauncher()
        inst.cfg["game"] = str(self.game)
        inst.cfg["game_update"] = str(self.update)
        inst.cfg["dlc_folder"] = str(self.dlc)

        dummy_exe = self.tmp / "core.exe"
        dummy_exe.write_bytes(b"DUMMY_EXE")

        with mock.patch("basara4_launcher.image_ready", return_value=True):
            L.Launcher._prepare_then_launch(inst, self.game, dummy_exe)

        self.assertIsNotNone(inst.pending)
        error, game, exe, patch_args, notice, app0 = inst.pending
        self.assertIsNone(error)
        self.assertEqual(game, self.game)
        # app0 must be the update view
        self.assertNotEqual(app0, self.game)
        self.assertTrue((app0 / "eboot.bin").is_file())
        self.assertEqual((app0 / "eboot.bin").read_bytes(), b"UPDATE_EBOOT_BIN_NEW")
        # DLC manifest was generated and passed
        self.assertIn("--addons", patch_args)
        addons_bin_idx = patch_args.index("--addons") + 1
        addons_bin_path = Path(patch_args[addons_bin_idx])
        self.assertTrue(addons_bin_path.is_file())

        inst._cleanup_update_view()

    def test_launch_flow_without_dlc_does_not_pass_addons(self):
        class DummyLauncher:
            cfg = {
                "game": str(self.game),
                "game_update": "",
                "dlc_folder": "",
                "fps_limit": 60,
                "english_patch": False,
            }
            pending = None
            pending_text = ""
            update_view = None

            def _progress(self, text):
                self.pending_text = text

            def _cleanup_update_view(self):
                pass

        inst = DummyLauncher()
        dummy_exe = self.tmp / "core.exe"
        dummy_exe.write_bytes(b"DUMMY_EXE")

        with mock.patch("basara4_launcher.image_ready", return_value=True):
            L.Launcher._prepare_then_launch(inst, self.game, dummy_exe)

        self.assertIsNotNone(inst.pending)
        error, game, exe, patch_args, notice, app0 = inst.pending
        self.assertIsNone(error)
        self.assertNotIn("--addons", patch_args)
        self.assertEqual(app0, self.game)


if __name__ == "__main__":
    unittest.main()
