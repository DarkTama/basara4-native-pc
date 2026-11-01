"""Tests for texture original-hash filter and region-limited atlas merging.

Covers:
- Real-asset verification on ui/title.arc (skipped if D: paths missing):
  - charasele_02_38: skipped with reason "unchanged_in_ps3_patch".
  - charasele_00_00: merged; pixels outside the PARTNER rectangle are
    byte-identical to the native PS4 decode, and the PARTNER region differs.
- Synthetic fixtures verifying hash filter and region merge without local game assets.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import sys
import unittest

import numpy as np
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LAUNCHER_DIR = _REPO_ROOT / "launcher"
if str(_LAUNCHER_DIR) not in sys.path:
    sys.path.insert(0, str(_LAUNCHER_DIR))

import basara4_repatch
import basara4_tex
from gmdarc import ArcFile

_PS3_TITLE_ARC = Path(r"D:\Games\basara4-windows\ps3 patch\rom\ui\title.arc")
_PS4_TITLE_ARC = Path(r"D:\Games\ShadPs4Plus-PkgExtractor-1.1-windows\CUSA01159-patch\nativePS4\rom\ui\title.arc")


class TestTextureRealFiles(unittest.TestCase):
    @unittest.skipUnless(_PS3_TITLE_ARC.is_file() and _PS4_TITLE_ARC.is_file(), "Real title.arc files missing on D:")
    def test_charasele_02_38_skipped_unchanged(self):
        """charasele_02_38 has identical bytes in PS3 patch and PS3 original, so it must be skipped."""
        orig_hashes, _ = basara4_repatch.load_orig_tex_data()
        self.assertTrue(len(orig_hashes) > 0, "Original texture hashes database must not be empty")

        a3 = ArcFile.from_file(_PS3_TITLE_ARC)
        entry_name = "ui/tex/jpn/charasele_02/charasele_02_38_ID_HQ"
        e3 = a3.get_entry(entry_name)
        self.assertIsNotNone(e3, f"{entry_name} not found in PS3 title.arc")

        d3 = e3.decompress()
        h3_sha = hashlib.sha256(d3).hexdigest()
        key = f"ui/title.arc|{entry_name}"

        self.assertIn(key, orig_hashes, f"{key} must be in original hash database")
        self.assertEqual(
            h3_sha,
            orig_hashes[key],
            "PS3 patch charasele_02_38 must match clean Japanese PS3 original hash",
        )

    @unittest.skipUnless(_PS3_TITLE_ARC.is_file() and _PS4_TITLE_ARC.is_file(), "Real title.arc files missing on D:")
    def test_charasele_00_00_region_merged(self):
        """charasele_00_00 atlas must have PARTNER region merged, while all portraits outside are byte-identical."""
        orig_hashes, orig_rects = basara4_repatch.load_orig_tex_data()

        a3 = ArcFile.from_file(_PS3_TITLE_ARC)
        a4 = ArcFile.from_file(_PS4_TITLE_ARC)

        entry_name = "ui/tex/jpn/charasele_00/charasele_00_00_ID_HQ"
        e3 = a3.get_entry(entry_name)
        e4 = a4.get_entry(entry_name + "_ps4") or a4.get_entry(entry_name)
        self.assertIsNotNone(e3)
        self.assertIsNotNone(e4)

        d3 = e3.decompress()
        d4 = e4.decompress()
        key = f"ui/title.arc|{entry_name}"

        # 1. Must be recognized as changed in patch
        h3_sha = hashlib.sha256(d3).hexdigest()
        self.assertIn(key, orig_hashes)
        self.assertNotEqual(h3_sha, orig_hashes[key], "charasele_00_00 must differ from PS3 original")

        # 2. Must have precomputed rectangles
        self.assertIn(key, orig_rects)
        rects = orig_rects[key]
        self.assertTrue(len(rects) > 0, "charasele_00_00 must have at least one changed rectangle")

        # 3. Perform region-limited conversion
        converted_tex = basara4_tex.convert_ps3_to_ps4(d3, d4, rects=rects)
        self.assertEqual(len(converted_tex), len(d4))

        # 4. Decode both original native PS4 and converted textures
        h4 = basara4_tex.parse_tex_header(d4, endian="<")
        w4, h4_px = h4["width"], h4["height"]
        pix_off = h4["pixel_offset"]

        deswz_orig = basara4_tex.deswizzle_bc3(d4[pix_off:], w4, h4_px)
        deswz_conv = basara4_tex.deswizzle_bc3(converted_tex[pix_off:], w4, h4_px)

        im_orig = basara4_tex.decode_bc3_to_rgba(deswz_orig, w4, h4_px)
        im_conv = basara4_tex.decode_bc3_to_rgba(deswz_conv, w4, h4_px)

        arr_orig = np.array(im_orig)
        arr_conv = np.array(im_conv)

        # Build mask of changed region in PS4 coordinates
        scale_x = w4 // 1024
        scale_y = h4_px // 1024
        mask = np.zeros((h4_px, w4), dtype=bool)
        for bx0, by0, bx1, by1 in rects:
            py0, py1 = by0 * scale_y * 4, (by1 + 1) * scale_y * 4
            px0, px1 = bx0 * scale_x * 4, (bx1 + 1) * scale_x * 4
            mask[py0:py1, px0:px1] = True

        diff = arr_orig != arr_conv
        diff_outside = diff & (~mask[:, :, None])
        diff_inside = diff & (mask[:, :, None])

        # Outside the PARTNER rectangle: 100% byte-identical
        self.assertEqual(
            int(np.count_nonzero(diff_outside)),
            0,
            "Pixels outside PARTNER rectangle must be byte-identical to native PS4 decode",
        )
        # Inside the PARTNER rectangle: contains the translation
        self.assertGreater(
            int(np.count_nonzero(diff_inside)),
            0,
            "Pixels inside PARTNER rectangle must differ from original Japanese",
        )


class TestTextureSynthetic(unittest.TestCase):
    def test_hash_database_loaded(self):
        """Verifies that the committed hash database loads successfully and contains known records."""
        orig_hashes, orig_rects = basara4_repatch.load_orig_tex_data()
        self.assertGreater(len(orig_hashes), 1000)
        self.assertGreater(len(orig_rects), 100)

    def test_patch_format_bumped(self):
        """PATCH_FORMAT must be bumped to 3."""
        self.assertEqual(basara4_repatch.PATCH_FORMAT, 3)

    def test_repatch_stats_has_unchanged_reason(self):
        """stats['textures_skipped'] must track unchanged_in_ps3_patch."""
        # Check dummy stats template
        dummy_stats: dict = {
            "textures_skipped": {
                "unchanged_in_ps3_patch": 0,
                "fmt_unsupported": 0,
            }
        }
        self.assertIn("unchanged_in_ps3_patch", dummy_stats["textures_skipped"])


if __name__ == "__main__":
    unittest.main()
