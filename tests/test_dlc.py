#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 i7Exceed.
"""Unit tests for Basara 4 PC Local DLC Support."""

try:
    from paths import ROOT  # noqa: F401  (puts scripts/ on sys.path)
except ImportError:
    from tests.paths import ROOT  # noqa: F401
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

import importlib.util
_spec = importlib.util.spec_from_file_location("make_package", ROOT / "packaging" / "basara4" / "make_package.py")
make_package = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_package)

from dlc import (
    MAGIC,
    MANIFEST_VERSION,
    HEADER_SIZE,
    ENTRY_SIZE,
    MAX_ADDONS,
    STATUS_INSTALLED,
    DLCValidationError,
    is_valid_entitlement_id,
    discover_addons,
    build_binary_manifest,
    generate_manifest,
)


class TestDLCValidationAndDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="b4_dlc_test_"))
        self.dlc_root = self.tmp / "dlc_root"
        self.dlc_root.mkdir()
        self.out_dir = self.tmp / "out"
        self.out_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_entitlement_id_validation(self):
        """Validates alphanumeric, hyphen, underscore, 1-16 chars."""
        self.assertTrue(is_valid_entitlement_id("COSTUME001"))
        self.assertTrue(is_valid_entitlement_id("DLC_01-EX"))
        self.assertTrue(is_valid_entitlement_id("1234567890123456"))  # 16 chars

        self.assertFalse(is_valid_entitlement_id(""))  # empty
        self.assertFalse(is_valid_entitlement_id("12345678901234567"))  # 17 chars (too long)
        self.assertFalse(is_valid_entitlement_id("COSTUME/01"))  # path traversal slash
        self.assertFalse(is_valid_entitlement_id(".."))  # path traversal
        self.assertFalse(is_valid_entitlement_id("COSTUME.01"))  # dots
        self.assertFalse(is_valid_entitlement_id("COSTUME 01"))  # space

    def test_no_dlc_directory(self):
        """None or empty string returns empty list without error."""
        self.assertEqual(discover_addons(None), [])
        self.assertEqual(discover_addons(""), [])

    def test_nonexistent_dlc_directory_returns_empty(self):
        """Missing DLC root means no add-ons, never a failure."""
        nonexistent = self.dlc_root / "does_not_exist"
        self.assertEqual(discover_addons(nonexistent), [])

    def test_valid_folder_discovery_and_enumeration(self):
        """Auto-discovers valid add-on folders under <DLC_ROOT>/CUSA01159/."""
        title_dir = self.dlc_root / "CUSA01159"
        addon1 = title_dir / "COSTUME000000001"
        addon2 = title_dir / "WEAPON0000000001"
        addon1.mkdir(parents=True)
        addon2.mkdir(parents=True)

        (addon1 / "data.bin").write_bytes(b"DATA1")
        (addon2 / "data.bin").write_bytes(b"DATA2")

        addons = discover_addons(self.dlc_root, title_id="CUSA01159")
        self.assertEqual(len(addons), 2)
        labels = [a["entitlement_label"] for a in addons]
        self.assertIn("COSTUME000000001", labels)
        self.assertIn("WEAPON0000000001", labels)
        self.assertEqual(addons[0]["status"], STATUS_INSTALLED)
        self.assertEqual(addons[0]["service_label"], 0)

    def test_wrong_title_id_ignored(self):
        """Folders for other titles are ignored if expected title folder is missing."""
        wrong_dir = self.dlc_root / "CUSA99999"
        wrong_addon = wrong_dir / "COSTUME001"
        wrong_addon.mkdir(parents=True)
        (wrong_addon / "data.bin").write_bytes(b"WRONG")

        addons = discover_addons(self.dlc_root, title_id="CUSA01159")
        self.assertEqual(addons, [])

    def test_empty_or_invalid_addon_folder_skipped_with_log(self):
        """Unknown or invalid add-on folders are skipped with a log line without aborting discovery."""
        title_dir = self.dlc_root / "CUSA01159"
        empty_addon = title_dir / "EMPTY001"
        empty_addon.mkdir(parents=True)
        valid_addon = title_dir / "VALID001"
        valid_addon.mkdir(parents=True)
        (valid_addon / "data.bin").write_bytes(b"DATA")

        addons = discover_addons(self.dlc_root, title_id="CUSA01159")
        self.assertEqual(len(addons), 1)
        self.assertEqual(addons[0]["entitlement_label"], "VALID001")

    def test_duplicate_entitlement_ids_skipped(self):
        """Duplicate entitlement IDs in user manifest or auto-discovery are safely handled."""
        title_dir = self.dlc_root / "CUSA01159"
        addon1 = title_dir / "ADDON1"
        addon1.mkdir(parents=True)
        (addon1 / "file.bin").write_bytes(b"A")

        manifest = {
            "version": 1,
            "title_id": "CUSA01159",
            "addons": [
                {"entitlement_label": "SAME_LABEL", "path": "ADDON1"},
                {"entitlement_label": "SAME_LABEL", "path": "ADDON1"}
            ]
        }
        (title_dir / "dlc_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        addons = discover_addons(self.dlc_root, title_id="CUSA01159")
        self.assertEqual(len(addons), 1)
        self.assertEqual(addons[0]["entitlement_label"], "SAME_LABEL")

    def test_unsafe_path_traversal_in_manifest_skipped(self):
        """Manifest paths attempting to escape DLC root are skipped."""
        title_dir = self.dlc_root / "CUSA01159"
        title_dir.mkdir(parents=True)
        valid_addon = title_dir / "VALID001"
        valid_addon.mkdir(parents=True)
        (valid_addon / "data.bin").write_bytes(b"DATA")

        manifest = {
            "version": 1,
            "title_id": "CUSA01159",
            "addons": [
                {"entitlement_label": "ESCAPED01", "path": "../../windows"},
                {"entitlement_label": "VALID001", "path": "VALID001"}
            ]
        }
        (title_dir / "dlc_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        addons = discover_addons(self.dlc_root, title_id="CUSA01159")
        self.assertEqual(len(addons), 1)
        self.assertEqual(addons[0]["entitlement_label"], "VALID001")

    def test_binary_and_json_manifest_generation(self):
        """generate_manifest produces valid addons.json and binary B4ADD001."""
        title_dir = self.dlc_root / "CUSA01159"
        addon = title_dir / "COSTUME001"
        addon.mkdir(parents=True)
        (addon / "asset.arc").write_bytes(b"ARC_DATA")

        json_path, bin_path, addons = generate_manifest(self.dlc_root, self.out_dir, title_id="CUSA01159")
        self.assertTrue(json_path.is_file())
        self.assertTrue(bin_path.is_file())
        self.assertEqual(len(addons), 1)

        # Validate JSON content
        manifest = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["title_id"], "CUSA01159")
        self.assertEqual(manifest["count"], 1)
        self.assertEqual(manifest["addons"][0]["entitlement_label"], "COSTUME001")

        # Validate binary layout
        bin_data = bin_path.read_bytes()
        self.assertEqual(len(bin_data), HEADER_SIZE + ENTRY_SIZE)
        magic, ver, count, title_b = struct.unpack("<8sII16s", bin_data[:HEADER_SIZE])
        self.assertEqual(magic, MAGIC)
        self.assertEqual(ver, MANIFEST_VERSION)
        self.assertEqual(count, 1)
        self.assertEqual(title_b.split(b"\x00")[0].decode(), "CUSA01159")

        # Entry layout
        srv, ent_b, status, host_b, flags = struct.unpack("<I20sI512sI", bin_data[HEADER_SIZE:])
        self.assertEqual(srv, 0)
        self.assertEqual(ent_b.split(b"\x00")[0].decode(), "COSTUME001")
        self.assertEqual(status, STATUS_INSTALLED)
        self.assertEqual(flags, 0)
        self.assertEqual(host_b.split(b"\x00")[0].decode(), str(addon.resolve()))

    def test_generate_manifest_with_missing_dlc_root_produces_empty_manifest(self):
        """Missing DLC root produces an empty manifest rather than failing."""
        nonexistent = self.dlc_root / "does_not_exist"
        json_path, bin_path, addons = generate_manifest(nonexistent, self.out_dir, title_id="CUSA01159")
        self.assertEqual(addons, [])
        self.assertTrue(json_path.is_file())
        self.assertTrue(bin_path.is_file())
        manifest = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["count"], 0)
        self.assertEqual(manifest["addons"], [])

    def test_cli_root_option(self):
        """dlc.py accepts --root <dir> and --out <out dir> entry points."""
        title_dir = self.dlc_root / "CUSA01159"
        addon = title_dir / "COSTUME001"
        addon.mkdir(parents=True)
        (addon / "asset.arc").write_bytes(b"ARC_DATA")

        out_cli = self.tmp / "out_cli"
        cmd = [
            sys.executable,
            str(ROOT / "scripts/dlc.py"),
            "--root", str(self.dlc_root),
            "--out", str(out_cli),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertIn("Discovered 1 add-on(s)", result.stdout)
        self.assertTrue((out_cli / "addons.json").is_file())
        self.assertTrue((out_cli / "addons.bin").is_file())

    def test_packaging_includes_ported_scripts(self):
        """Packaging script includes dlc.py and update scripts in PREPARE_SCRIPTS."""
        for script in ("dlc.py", "game_update.py", "mods.py", "patches.py"):
            self.assertIn(script, make_package.PREPARE_SCRIPTS)
            self.assertTrue((ROOT / "scripts" / script).is_file())


if __name__ == "__main__":
    unittest.main()
