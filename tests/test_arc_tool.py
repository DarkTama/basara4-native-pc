"""Unit tests for tools/arc_tool.py using synthetic fixtures with no Capcom data.
Follows the style of tests/paths.py and tests/test_translation.py.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

try:
    from paths import ROOT
except ImportError:
    from tests.paths import ROOT

# Ensure launcher and tools are in sys.path
if str(ROOT / "launcher") not in sys.path:
    sys.path.insert(0, str(ROOT / "launcher"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from gmdarc import (
    ARC_MAGIC_BE,
    ARC_MAGIC_LE,
    GMD_MAGIC_BE,
    GMD_MAGIC_LE,
    GMD_TYPE_HASH,
    TEX_TYPE_HASH,
    ArcEntry,
    ArcFile,
    GmdFile,
)
from tests.test_translation import make_synthetic_arc, make_synthetic_gmd
import arc_tool


class TestArcTool(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temp_dir.name)

        # Create synthetic GMD data
        self.gmd_data = make_synthetic_gmd(
            "test_menu",
            ["KEY_START", "KEY_OPTIONS"],
            ["Start Game", "Options Menu"],
            endian="<",
        )
        self.tex_data = b"DUMMY_TEXTURE_DATA_12345"
        self.custom_data = b"CUSTOM_BINARY_PAYLOAD_67890"

        self.entries = [
            ("ui\\msg\\menu", GMD_TYPE_HASH, self.gmd_data, 0x40000000),
            ("texture\\bg01", TEX_TYPE_HASH, self.tex_data, 0x20000000),
            ("model\\custom", 0x12345678, self.custom_data, 0x00000000),
        ]
        self.raw_arc = make_synthetic_arc(
            self.entries,
            endian="<",
            version=7,
            padding=b"\x00" * 16,
        )
        self.arc_path = self.tmp / "sample.arc"
        self.arc_path.write_bytes(self.raw_arc)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_output(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = arc_tool.main(["list", str(self.arc_path)])

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        out = stdout.getvalue()

        # Check entry listings
        self.assertIn("ui\\msg\\menu", out)
        self.assertIn("gmd", out)
        self.assertIn(str(len(self.gmd_data)), out)

        self.assertIn("texture\\bg01", out)
        self.assertIn("tex", out)
        self.assertIn(str(len(self.tex_data)), out)

        self.assertIn("model\\custom", out)
        self.assertIn("12345678", out)
        self.assertIn(str(len(self.custom_data)), out)

        # Check footer: count, endian, version
        self.assertIn("3 entries (LE, v7)", out)

    def test_extract_and_repack_unchanged_identical(self):
        extract_dir = self.tmp / "extracted"
        repacked_arc = self.tmp / "repacked.arc"

        # 1. Extract
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = arc_tool.main(["extract", str(self.arc_path), str(extract_dir)])
        self.assertEqual(code, 0)

        # Verify extracted files exist with normalized paths and extensions
        gmd_file = extract_dir / "ui" / "msg" / "menu.gmd"
        tex_file = extract_dir / "texture" / "bg01.tex"
        custom_file = extract_dir / "model" / "custom.12345678"
        manifest_file = extract_dir / "arc_manifest.json"

        self.assertTrue(gmd_file.is_file())
        self.assertEqual(gmd_file.read_bytes(), self.gmd_data)

        self.assertTrue(tex_file.is_file())
        self.assertEqual(tex_file.read_bytes(), self.tex_data)

        self.assertTrue(custom_file.is_file())
        self.assertEqual(custom_file.read_bytes(), self.custom_data)

        self.assertTrue(manifest_file.is_file())
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest["endian"], "<")
        self.assertEqual(manifest["version"], 7)
        self.assertEqual(len(manifest["entries"]), 3)
        self.assertEqual(manifest["entries"][0]["path"], "ui\\msg\\menu")
        self.assertEqual(manifest["entries"][0]["type_hash"], GMD_TYPE_HASH)
        self.assertEqual(manifest["entries"][0]["flags"], 0x40000000)
        self.assertEqual(manifest["entries"][0]["order"], 0)

        # 2. Repack with no modifications -> must be byte-identical
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = arc_tool.main([
                "repack",
                str(self.arc_path),
                str(extract_dir),
                str(repacked_arc),
            ])
        self.assertEqual(code, 0)
        self.assertEqual(repacked_arc.read_bytes(), self.raw_arc)

    def test_repack_modified_entry(self):
        extract_dir = self.tmp / "extracted"
        repacked_arc = self.tmp / "repacked.arc"

        arc_tool.main(["extract", str(self.arc_path), str(extract_dir)])

        # Modify only the custom binary file
        custom_file = extract_dir / "model" / "custom.12345678"
        new_payload = b"MODIFIED_PAYLOAD_DIFFERENT_BYTES_EXTRA"
        custom_file.write_bytes(new_payload)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = arc_tool.main([
                "repack",
                str(self.arc_path),
                str(extract_dir),
                str(repacked_arc),
            ])

        self.assertEqual(code, 0)
        out = stdout.getvalue()
        self.assertIn("Replaced: model\\custom", out)

        # Result is not identical to original
        repacked_bytes = repacked_arc.read_bytes()
        self.assertNotEqual(repacked_bytes, self.raw_arc)

        # Parse repacked ARC back and verify
        repacked_obj = ArcFile.from_bytes(repacked_bytes)
        self.assertEqual(len(repacked_obj.entries), 3)

        # Unmodified entries keep verbatim raw bytes
        orig_obj = ArcFile.from_bytes(self.raw_arc)
        self.assertEqual(
            repacked_obj.entries[0].raw_data, orig_obj.entries[0].raw_data
        )
        self.assertEqual(
            repacked_obj.entries[1].raw_data, orig_obj.entries[1].raw_data
        )

        # Modified entry has new content and updated sizes
        self.assertEqual(repacked_obj.entries[2].decompress(), new_payload)
        self.assertEqual(repacked_obj.entries[2].uncompressed_size, len(new_payload))

    def test_path_escape_refused(self):
        # ARC containing directory traversal entry path
        bad_entries = [
            ("..\\evil.bin", 0x12345678, b"evil", 0),
        ]
        bad_arc_data = make_synthetic_arc(bad_entries)
        bad_arc_file = self.tmp / "malicious.arc"
        bad_arc_file.write_bytes(bad_arc_data)

        extract_dir = self.tmp / "safe_extract"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = arc_tool.main(["extract", str(bad_arc_file), str(extract_dir)])

        self.assertEqual(code, 1)
        err = stderr.getvalue()
        self.assertIn("Error:", err)
        self.assertTrue(
            "escapes output directory" in err or "contains '..'" in err
        )
        # Ensure no files written outside or inside
        self.assertFalse((self.tmp / "evil.bin").exists())

    def test_absolute_path_escape_refused(self):
        bad_entries = [
            ("/absolute/path/file", 0x12345678, b"payload", 0),
        ]
        bad_arc_data = make_synthetic_arc(bad_entries)
        bad_arc_file = self.tmp / "bad_abs.arc"
        bad_arc_file.write_bytes(bad_arc_data)

        extract_dir = self.tmp / "safe_extract_abs"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = arc_tool.main(["extract", str(bad_arc_file), str(extract_dir)])

        self.assertEqual(code, 1)
        self.assertIn("Error:", stderr.getvalue())

    def test_refuse_overwrite_original(self):
        extract_dir = self.tmp / "extracted"
        arc_tool.main(["extract", str(self.arc_path), str(extract_dir)])

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = arc_tool.main([
                "repack",
                str(self.arc_path),
                str(extract_dir),
                str(self.arc_path),
            ])

        self.assertEqual(code, 1)
        self.assertIn("Error:", stderr.getvalue())
        self.assertIn("cannot overwrite original ARC", stderr.getvalue())

    def test_missing_file_error(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = arc_tool.main(["list", str(self.tmp / "nonexistent.arc")])

        self.assertEqual(code, 1)
        self.assertIn("Error:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_bad_magic_error(self):
        corrupt_file = self.tmp / "corrupt.arc"
        corrupt_file.write_bytes(b"INVALID_MAGIC_HEADER_BYTES")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = arc_tool.main(["list", str(corrupt_file)])

        self.assertEqual(code, 1)
        self.assertIn("Error:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_gmd_command_standalone_and_json(self):
        gmd_file = self.tmp / "standalone.gmd"
        gmd_file.write_bytes(self.gmd_data)
        out_json = self.tmp / "strings.json"

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = arc_tool.main(["gmd", str(gmd_file), "--json", str(out_json)])

        self.assertEqual(code, 0)
        out = stdout.getvalue()
        self.assertIn("[0] Start Game", out)
        self.assertIn("[1] Options Menu", out)

        self.assertTrue(out_json.is_file())
        data = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["index"], 0)
        self.assertEqual(data[0]["text"], "Start Game")
        self.assertEqual(data[1]["index"], 1)
        self.assertEqual(data[1]["text"], "Options Menu")

    def test_gmd_command_arc_input(self):
        out_json = self.tmp / "arc_strings.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = arc_tool.main(["gmd", str(self.arc_path), "--json", str(out_json)])

        self.assertEqual(code, 0)
        out = stdout.getvalue()
        self.assertIn("=== ui\\msg\\menu (2 strings) ===", out)
        self.assertIn("[0] Start Game", out)
        self.assertIn("[1] Options Menu", out)

        self.assertTrue(out_json.is_file())
        data = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertIn("ui\\msg\\menu", data)
        self.assertEqual(len(data["ui\\msg\\menu"]), 2)
        self.assertEqual(data["ui\\msg\\menu"][0]["text"], "Start Game")


if __name__ == "__main__":
    unittest.main()
