"""Unit tests for Phase 4: Launch-time translation overlay.
Tests synthetic directories only; never writes to the real game folder.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
import unittest
from unittest import mock

import sys
REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_DIR = REPO_ROOT / "launcher"
if str(LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHER_DIR))

import basara4_overlay
import basara4_launcher


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestTranslationOverlay(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="b4_overlay_test_"))
        self.game = self.tmp / "game"
        self.patch = self.tmp / "patch"
        self.overlay = self.tmp / "overlay"

        # Create synthetic game layout
        (self.game / "nativePS4/rom/ui").mkdir(parents=True)
        (self.game / "nativePS4/rom/st").mkdir(parents=True)
        (self.game / "sce_sys").mkdir(parents=True)

        (self.game / "eboot.bin").write_text("EBOOT_ORIGINAL_BINARY_DATA", encoding="utf-8")
        (self.game / "sce_sys/icon0.png").write_text("FAKE_PNG_ICON", encoding="utf-8")
        (self.game / "nativePS4/rom/ui/title.arc").write_text("GAME_ORIGINAL_TITLE_ARC", encoding="utf-8")
        (self.game / "nativePS4/rom/ui/sibling.arc").write_text("GAME_ORIGINAL_SIBLING_ARC", encoding="utf-8")
        (self.game / "nativePS4/rom/st/st01.arc").write_text("STAGE_01_ARC_DATA", encoding="utf-8")
        (self.game / "nativePS4/rom/st/st02.arc").write_text("STAGE_02_ARC_DATA", encoding="utf-8")

        # Create synthetic patch layout
        (self.patch / "nativePS4/rom/ui").mkdir(parents=True)
        (self.patch / "nativePS4/rom/ui/title.arc").write_text("PATCHED_TITLE_ARC_ENGLISH", encoding="utf-8")
        manifest = {
            "db_sha256": "dummy_db_sha256_hash",
            "written_arcs": [
                {
                    "relative_path": "ui/title.arc",
                    "source_size": (self.game / "nativePS4/rom/ui/title.arc").stat().st_size,
                    "source_mtime": (self.game / "nativePS4/rom/ui/title.arc").stat().st_mtime,
                }
            ],
        }
        (self.patch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def tearDown(self):
        # Safely remove overlay first if it exists
        if self.overlay.exists() or os.path.islink(self.overlay):
            basara4_overlay._safe_remove_tree(self.overlay, game=self.game)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_overlay_structure_links_and_junctions(self):
        """Verifies replaced files come from patch, siblings are hard links,
        untouched dirs are junctions, and top-level files resolve.
        """
        out = basara4_overlay.build_overlay(self.game, self.patch, self.overlay)
        self.assertEqual(out, self.overlay)

        # 1. Replaced file is from patch
        overlay_title = self.overlay / "nativePS4/rom/ui/title.arc"
        self.assertTrue(overlay_title.is_file())
        self.assertEqual(overlay_title.read_text(encoding="utf-8"), "PATCHED_TITLE_ARC_ENGLISH")

        # 2. Sibling file is hard link to game file
        overlay_sibling = self.overlay / "nativePS4/rom/ui/sibling.arc"
        game_sibling = self.game / "nativePS4/rom/ui/sibling.arc"
        self.assertTrue(overlay_sibling.is_file())
        self.assertEqual(overlay_sibling.read_text(encoding="utf-8"), "GAME_ORIGINAL_SIBLING_ARC")
        self.assertTrue(os.path.samefile(overlay_sibling, game_sibling))

        # 3. Untouched directory (st) is a junction / reparse point
        overlay_st = self.overlay / "nativePS4/rom/st"
        self.assertTrue(overlay_st.is_dir())
        st_lstat = os.lstat(overlay_st)
        is_reparse = bool(
            getattr(st_lstat, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        self.assertTrue(is_reparse, "Untouched directory should be created as a junction/reparse point")

        # Files inside junctioned dir resolve correctly
        self.assertEqual((overlay_st / "st01.arc").read_text(encoding="utf-8"), "STAGE_01_ARC_DATA")
        self.assertEqual((overlay_st / "st02.arc").read_text(encoding="utf-8"), "STAGE_02_ARC_DATA")

        # 4. Top-level eboot.bin resolves and is hard link to game
        overlay_eboot = self.overlay / "eboot.bin"
        game_eboot = self.game / "eboot.bin"
        self.assertTrue(overlay_eboot.is_file())
        self.assertTrue(os.path.samefile(overlay_eboot, game_eboot))

        # Top-level sce_sys is a junction
        overlay_sce = self.overlay / "sce_sys"
        sce_lstat = os.lstat(overlay_sce)
        self.assertTrue(
            bool(getattr(sce_lstat, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        )

    def test_stamp_reuse_does_not_rebuild(self):
        """Verifies that when stamp matches, build_overlay reuses existing overlay without changes."""
        basara4_overlay.build_overlay(self.game, self.patch, self.overlay)
        stamp_path = self.overlay / ".overlay_stamp.json"
        self.assertTrue(stamp_path.is_file())
        first_mtime = stamp_path.stat().st_mtime_ns

        time.sleep(0.02)
        out = basara4_overlay.build_overlay(self.game, self.patch, self.overlay)
        self.assertEqual(out, self.overlay)
        second_mtime = stamp_path.stat().st_mtime_ns
        self.assertEqual(first_mtime, second_mtime, "Overlay stamp was rewritten when it should have been reused")

    def test_safe_remove_and_rebuild_preserves_game_files(self):
        """Safety test: deleting and rebuilding the overlay must leave every game file intact,
        including files inside junctioned directories.
        """
        # Collect baseline hashes and file count of game
        game_snapshot_before: dict[str, str] = {}
        for root, _, files in os.walk(self.game):
            for f in files:
                p = Path(root) / f
                rel = p.relative_to(self.game).as_posix()
                game_snapshot_before[rel] = _sha256(p)

        # First build
        basara4_overlay.build_overlay(self.game, self.patch, self.overlay)

        # Modify patch file to force rebuild
        time.sleep(0.02)
        (self.patch / "nativePS4/rom/ui/title.arc").write_text("PATCHED_TITLE_ARC_ENGLISH_V2", encoding="utf-8")

        # Second build (triggers rebuild)
        basara4_overlay.build_overlay(self.game, self.patch, self.overlay)
        self.assertEqual(
            (self.overlay / "nativePS4/rom/ui/title.arc").read_text(encoding="utf-8"),
            "PATCHED_TITLE_ARC_ENGLISH_V2",
        )

        # Verify all game files are 100% intact
        game_snapshot_after: dict[str, str] = {}
        for root, _, files in os.walk(self.game):
            for f in files:
                p = Path(root) / f
                rel = p.relative_to(self.game).as_posix()
                game_snapshot_after[rel] = _sha256(p)

        self.assertEqual(game_snapshot_before, game_snapshot_after)
        self.assertEqual(len(game_snapshot_before), len(game_snapshot_after))

    def test_empty_patch_returns_game(self):
        """An empty patch directory must return the original game path without creating an overlay."""
        empty_patch = self.tmp / "empty_patch"
        empty_patch.mkdir()
        out = basara4_overlay.build_overlay(self.game, empty_patch, self.overlay)
        self.assertEqual(out, self.game)
        self.assertFalse(self.overlay.exists())

    def test_refuse_nested_paths(self):
        """Overlay refuses paths nested inside game or vice versa."""
        nested_overlay = self.game / "bad_overlay"
        with self.assertRaises(ValueError):
            basara4_overlay.build_overlay(self.game, self.patch, nested_overlay)

        with self.assertRaises(ValueError):
            basara4_overlay.build_overlay(self.game, self.patch, self.game)

        with self.assertRaises(ValueError):
            basara4_overlay.build_overlay(self.game, self.patch, self.tmp)

    def test_launcher_english_patch_defaults_and_toggle(self):
        """Verifies launcher defaults english_patch to False, and _prepare_then_launch
        uses real game when off.
        """
        self.assertFalse(basara4_launcher.DEFAULTS.get("english_patch"))

        class DummyLauncher:
            cfg = {"fps_limit": 60, "english_patch": False}
            pending = None
            pending_text = ""
            def _progress(self, text):
                self.pending_text = text

        inst = DummyLauncher()
        with mock.patch("basara4_launcher.image_ready", return_value=True):
            basara4_launcher.Launcher._prepare_then_launch(inst, self.game, Path("dummy_exe"))

        self.assertIsNotNone(inst.pending)
        error, game, exe, patch_args, notice, app0 = inst.pending
        self.assertIsNone(error)
        self.assertEqual(app0, self.game, "When english_patch is off, app0 must be the real game")

    def test_launcher_decision_ps3_import_without_db(self):
        """When english_patch is enabled, PATCH_DIR has a ps3_import manifest,
        and no DB exists, _prepare_then_launch builds overlay with no DB check."""
        (self.patch / "manifest.json").write_text(
            json.dumps({"mode": "ps3_import", "source_game": "BLJM61248"}),
            encoding="utf-8",
        )
        fake_db = self.tmp / "nonexistent_db.json"

        class DummyLauncher:
            cfg = {"fps_limit": 60, "english_patch": True}
            pending = None
            pending_text = ""
            def _progress(self, text):
                self.pending_text = text

        inst = DummyLauncher()
        with mock.patch("basara4_launcher.image_ready", return_value=True), \
             mock.patch("basara4_launcher.PATCH_DIR", self.patch), \
             mock.patch("basara4_launcher.OVERLAY_DIR", self.overlay), \
             mock.patch("basara4_launcher.TRANSLATION_DB", fake_db):
            basara4_launcher.Launcher._prepare_then_launch(inst, self.game, Path("dummy_exe"))

        self.assertIsNotNone(inst.pending)
        error, game, exe, patch_args, notice, app0 = inst.pending
        self.assertIsNone(error)
        self.assertEqual(app0, self.overlay)

    def test_launcher_decision_no_import_and_no_db(self):
        """When english_patch is enabled and neither ps3_import manifest nor DB exists,
        _prepare_then_launch returns the 'import first' error message."""
        empty_patch_dir = self.tmp / "empty_patch_dir"
        empty_patch_dir.mkdir(parents=True, exist_ok=True)
        fake_db = self.tmp / "nonexistent_db.json"

        class DummyLauncher:
            cfg = {"fps_limit": 60, "english_patch": True}
            pending = None
            pending_text = ""
            def _progress(self, text):
                self.pending_text = text

        inst = DummyLauncher()
        with mock.patch("basara4_launcher.image_ready", return_value=True), \
             mock.patch("basara4_launcher.PATCH_DIR", empty_patch_dir), \
             mock.patch("basara4_launcher.OVERLAY_DIR", self.overlay), \
             mock.patch("basara4_launcher.TRANSLATION_DB", fake_db):
            basara4_launcher.Launcher._prepare_then_launch(inst, self.game, Path("dummy_exe"))

        self.assertIsNotNone(inst.pending)
        error, game, exe, patch_args, notice, app0 = inst.pending
        self.assertIsNotNone(error)
        self.assertIn("BLJM61248", error)
        self.assertEqual(
            error,
            'No English patch yet: click "Import PS3 English Patch…" and select your PS3 (BLJM61248) English patch folder.',
        )


if __name__ == "__main__":
    unittest.main()
