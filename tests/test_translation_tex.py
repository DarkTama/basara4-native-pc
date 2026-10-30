"""Unit tests for MT Framework TEX parser, BC3 decoding, PS4 swizzle/deswizzle,
texture classification, end-to-end repatcher, and manifest verification.
Zero Capcom assets; uses purely synthetic binary fixtures.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

import numpy as np
from PIL import Image

try:
    from paths import ROOT
except ImportError:
    from tests.paths import ROOT

if str(ROOT / "launcher") not in sys.path:
    sys.path.insert(0, str(ROOT / "launcher"))

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
from basara4_tex import (
    classify,
    convert_ps3_to_ps4,
    decode_bc3_to_rgba,
    deswizzle_bc3,
    get_swizzle_indices,
    make_synthetic_tex_header,
    morton2d_8x8,
    parse_tex_header,
    swizzle_bc3,
)
from basara4_repatch import (
    build_contact_sheets,
    detect_ps3_patch,
    repatch,
    verify_patch_manifest,
)
import etcpak


def make_synthetic_gmd(
    name: str,
    keys: list[str],
    strings: list[str],
    endian: str = "<",
) -> bytes:
    """Builds a complete synthetic GMD binary buffer."""
    magic = GMD_MAGIC_LE if endian == "<" else GMD_MAGIC_BE
    ver = 0x00010302
    kcnt = len(keys)
    scnt = len(strings)

    k_block = b"".join(k.encode("utf-8") + b"\x00" for k in keys)
    s_block = b"".join(s.encode("utf-8") + b"\x00" for s in strings)
    name_b = name.encode("utf-8")

    hdr = struct.pack(
        f"{endian}4sIIQIIIII",
        magic, ver, 0, 0, kcnt, scnt, len(k_block), len(s_block), len(name_b),
    )
    name_block = name_b + b"\x00"

    if kcnt > 0:
        if endian == "<":
            info = b""
            k_off = 0
            for i, k in enumerate(keys):
                kb = k.encode("utf-8")
                info += struct.pack("<IIIIQQ", i, 0, 0, len(kb), k_off, 0)
                k_off += len(kb) + 1
            buckets = b"\x00" * 2048
        else:
            info = b""
            k_off = 0
            for i, k in enumerate(keys):
                kb = k.encode("utf-8")
                info += struct.pack(">IIIII", i, 0, 0, k_off, 0)
                k_off += len(kb) + 1
            buckets = b"\x00" * 1024
        mid = info + buckets
    else:
        mid = b""

    return hdr + name_block + mid + k_block + s_block


def make_synthetic_tex(
    w: int,
    h: int,
    rgba_image: Image.Image,
    endian: str = "<",
    tiled: bool = False,
) -> bytes:
    """Builds a complete synthetic TEX binary (header + BC3 payload)."""
    hdr = make_synthetic_tex_header(w, h, fmt=42, mips=1, endian=endian)
    rgba_bytes = np.array(rgba_image.convert("RGBA")).tobytes()
    bc3_data = etcpak.compress_bc3(rgba_bytes, w, h)
    if tiled and endian == "<":
        bc3_data = swizzle_bc3(bc3_data, w, h)
    return hdr + bc3_data


class TestTexBasics(unittest.TestCase):
    def test_tex_header_parse_roundtrip(self):
        # PS4 Little Endian
        hdr4 = make_synthetic_tex_header(1024, 512, fmt=42, mips=1, endian="<", version=0x9D)
        p4 = parse_tex_header(hdr4)
        self.assertEqual(p4["magic"], b"TEX\x00")
        self.assertEqual(p4["endian"], "<")
        self.assertEqual(p4["width"], 1024)
        self.assertEqual(p4["height"], 512)
        self.assertEqual(p4["format"], 42)
        self.assertEqual(p4["mips"], 1)
        self.assertEqual(p4["pixel_offset"], 24)

        # PS3 Big Endian
        hdr3 = make_synthetic_tex_header(512, 256, fmt=42, mips=1, endian=">", version=0x9D)
        p3 = parse_tex_header(hdr3)
        self.assertEqual(p3["magic"], b"\x00XET")
        self.assertEqual(p3["endian"], ">")
        self.assertEqual(p3["width"], 512)
        self.assertEqual(p3["height"], 256)
        self.assertEqual(p3["format"], 42)
        self.assertEqual(p3["mips"], 1)
        self.assertEqual(p3["pixel_offset"], 20)

    def test_morton_mapping_hand_computed(self):
        # Test specific known coordinates
        bx = np.array([0, 1, 0, 1, 2, 3, 0, 7])
        by = np.array([0, 0, 1, 1, 0, 0, 2, 7])
        m = morton2d_8x8(bx, by)
        expected = [0, 1, 2, 3, 4, 5, 8, 63]
        np.testing.assert_array_equal(m, expected)

        # Verify all 64 coordinates form a full permutation 0..63
        all_x, all_y = np.indices((8, 8))
        all_m = morton2d_8x8(all_x.ravel(), all_y.ravel())
        self.assertEqual(len(set(all_m)), 64)
        self.assertEqual(min(all_m), 0)
        self.assertEqual(max(all_m), 63)

    def test_swizzle_deswizzle_inverse(self):
        sizes = [
            (8, 8),    # 32x32 px
            (16, 4),   # 64x16 px
            (128, 32), # 512x128 px
        ]
        for bw, bh in sizes:
            w, h = bw * 4, bh * 4
            num_blocks = bw * bh
            # Distinct 16-byte blocks
            raw_blocks = np.arange(num_blocks * 4, dtype=np.uint32).tobytes()

            swizzled = swizzle_bc3(raw_blocks, w, h)
            self.assertEqual(len(swizzled), len(raw_blocks))

            deswizzled = deswizzle_bc3(swizzled, w, h)
            self.assertEqual(deswizzled, raw_blocks)

            # Round trip idempotence
            reswizzled = swizzle_bc3(deswizzled, w, h)
            self.assertEqual(reswizzled, swizzled)

    def test_bc3_gradient_and_premultiplied_resize(self):
        w3, h3 = 64, 32
        arr = np.zeros((h3, w3, 4), dtype=np.uint8)
        for y in range(h3):
            for x in range(w3):
                arr[y, x] = [x * 4, y * 8, 128, 255]

        # Put an alpha-0 region on the left half with loud magenta color
        arr[:, :32, 3] = 0
        arr[:, :32, :3] = [123, 0, 123]  # PS3 atlas padding filler

        im3 = Image.fromarray(arr, mode="RGBA")
        ps3_tex = make_synthetic_tex(w3, h3, im3, endian=">")

        # Corresponding 2x PS4 texture
        w4, h4 = 128, 64
        im4_dummy = Image.new("RGBA", (w4, h4), (0, 0, 0, 0))
        ps4_tex = make_synthetic_tex(w4, h4, im4_dummy, endian="<", tiled=True)

        converted_ps4_tex = convert_ps3_to_ps4(ps3_tex, ps4_tex)
        self.assertEqual(len(converted_ps4_tex), len(ps4_tex))

        # Decode back converted PS4
        p4 = parse_tex_header(converted_ps4_tex, endian="<")
        deswz = deswizzle_bc3(converted_ps4_tex[p4["pixel_offset"]:], w4, h4)
        im_out = decode_bc3_to_rgba(deswz, w4, h4)
        arr_out = np.array(im_out)

        # Region corresponding to original alpha-0:
        # x in 0..64 should be strictly alpha 0 and RGB 0 (no neighbor bleeding)
        left_half_alpha = arr_out[:, :60, 3]
        left_half_rgb = arr_out[:, :60, :3]
        self.assertTrue(np.all(left_half_alpha == 0), f"Max alpha in zero region: {np.max(left_half_alpha)}")
        self.assertTrue(np.all(left_half_rgb == 0), f"Max RGB in zero region: {np.max(left_half_rgb)}")

    def test_classify_scores(self):
        w3, h3 = 64, 64
        w4, h4 = 128, 128

        # Image A: opaque circle
        im_a = Image.new("RGBA", (w3, h3), (0, 0, 0, 0))
        for y in range(h3):
            for x in range(w3):
                if (x - 32) ** 2 + (y - 32) ** 2 < 20 ** 2:
                    im_a.putpixel((x, y), (200, 200, 200, 255))

        im_a_2x = im_a.resize((w4, h4), resample=Image.Resampling.NEAREST)

        tex3_a = make_synthetic_tex(w3, h3, im_a, endian=">")
        tex4_a = make_synthetic_tex(w4, h4, im_a_2x, endian="<", tiled=True)

        # Test identical images: score should be very low
        cls_same = classify(tex3_a, tex4_a, threshold=40.0)
        self.assertFalse(cls_same["translated"])
        self.assertLess(cls_same["score"], 20.0)

        # Image B: translated texture with text glyphs (different alpha mask)
        im_b = Image.new("RGBA", (w3, h3), (0, 0, 0, 0))
        # Add diagonal stripes / different shape
        for y in range(h3):
            for x in range(w3):
                if (x + y) % 12 < 6:
                    im_b.putpixel((x, y), (255, 255, 0, 255))

        tex3_b = make_synthetic_tex(w3, h3, im_b, endian=">")
        cls_diff = classify(tex3_b, tex4_a, threshold=40.0)
        self.assertTrue(cls_diff["translated"])
        self.assertGreater(cls_diff["score"], 40.0)


class TestManifestVerification(unittest.TestCase):
    def test_verification_counts_and_validation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            base_rom = td / "rom"
            base_rom.mkdir()

            f_match = base_rom / "match.bin"
            f_match.write_bytes(b"content_match")
            h_match = hashlib.sha256(b"content_match").hexdigest()

            f_mismatch = base_rom / "mismatch.bin"
            f_mismatch.write_bytes(b"content_modified")
            h_orig = hashlib.sha256(b"content_orig").hexdigest()

            f_extra = base_rom / "extra.bin"
            f_extra.write_bytes(b"untracked")

            manifest_payload = {
                "patches": {
                    "0.85": {
                        "match.bin": h_match,
                        "mismatch.bin": h_orig,
                        "missing.bin": "0000000000000000000000000000000000000000000000000000000000000000",
                    },
                    "matabe_fix": {}
                }
            }
            manifest_file = td / "manifest.json"
            manifest_file.write_text(json.dumps(manifest_payload), encoding="utf-8")

            res = verify_patch_manifest(base_rom, None, manifest_path=manifest_file)
            p085 = res["patches"]["0.85"]
            self.assertEqual(p085["matched"], 1)
            self.assertEqual(p085["mismatched"], 1)
            self.assertEqual(p085["missing"], 1)
            self.assertEqual(p085["extra"], 1)
            self.assertEqual(res["total_matched"], 1)

            # Test failure when total matched == 0
            empty_base = td / "empty_rom"
            empty_base.mkdir()
            with self.assertRaises(ValueError) as ctx:
                verify_patch_manifest(empty_base, None, manifest_path=manifest_file)
            self.assertIn("0 files matched the known patch manifest", str(ctx.exception))


class TestRepatchEndToEnd(unittest.TestCase):
    def test_repatch_end_to_end_synthetic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            fake_ps3_dir = td / "ps3_patch"
            fake_ps4_dir = td / "ps4_game"
            out_dir = td / "out_patch"

            # 1. PS3 patch layout
            ps3_rom = fake_ps3_dir / "patch 0.85" / "rom"
            (ps3_rom / "ui").mkdir(parents=True)

            # 2. PS4 game layout
            ps4_rom = fake_ps4_dir / "CUSA01159" / "nativePS4" / "rom"
            (ps4_rom / "ui").mkdir(parents=True)
            (fake_ps4_dir / "CUSA01159" / "eboot.bin").write_bytes(b"\x7fELFfake")

            # Synthetic GMD: PS4 has Japanese, PS3 has English
            gmd3_bytes = make_synthetic_gmd(
                "title_menu", ["kPlay", "kExit"], ["Start Game", "Quit"], endian=">"
            )
            gmd4_bytes = make_synthetic_gmd(
                "title_menu", ["kPlay", "kExit"], ["ゲーム開始", "終了"], endian="<"
            )

            # Translated texture: PS3 button text
            w3, h3 = 64, 32
            w4, h4 = 128, 64

            im_ps4_btn = Image.new("RGBA", (w4, h4), (0, 0, 0, 0))
            for y in range(h4):
                for x in range(w4):
                    if (x - 64) ** 2 + (y - 32) ** 2 < 20 ** 2:
                        im_ps4_btn.putpixel((x, y), (200, 200, 200, 255))

            im_ps3_btn = Image.new("RGBA", (w3, h3), (0, 0, 0, 0))
            for y in range(h3):
                for x in range(w3):
                    if (x + y) % 8 < 4:
                        im_ps3_btn.putpixel((x, y), (255, 255, 0, 255))

            tex3_btn_bytes = make_synthetic_tex(w3, h3, im_ps3_btn, endian=">")
            tex4_btn_bytes = make_synthetic_tex(w4, h4, im_ps4_btn, endian="<", tiled=True)

            # Unchanged texture: identical circle
            tex3_static_bytes = make_synthetic_tex(w3, h3, im_ps3_btn, endian=">")
            im_ps4_static = im_ps3_btn.resize((w4, h4), resample=Image.Resampling.NEAREST)
            tex4_static_bytes = make_synthetic_tex(w4, h4, im_ps4_static, endian="<", tiled=True)

            # Unrelated raw file with specific flags
            unrelated_bytes = b"Capcom Unrelated Raw Data 12345678"
            unrelated_flags = 0x40000000

            # Build PS3 ARC (Big Endian)
            ps3_entries = [
                ArcEntry("ui/msg/title", GMD_TYPE_HASH, 0, 0, 0, zlib.compress(gmd3_bytes, 6), endian=">"),
                ArcEntry("ui/tex/btn_play", TEX_TYPE_HASH, 0, 0, 0, zlib.compress(tex3_btn_bytes, 6), endian=">"),
                ArcEntry("ui/tex/icon_static", TEX_TYPE_HASH, 0, 0, 0, zlib.compress(tex3_static_bytes, 6), endian=">"),
            ]
            ps3_arc = ArcFile(magic=ARC_MAGIC_BE, version=7, entries=ps3_entries, padding=b"\x00" * 16, endian=">")
            (ps3_rom / "ui" / "title.arc").write_bytes(ps3_arc.to_bytes())

            # Build PS4 ARC (Little Endian)
            comp_unrelated = zlib.compress(unrelated_bytes, 9)
            ps4_entries = [
                ArcEntry("ui/msg/title", GMD_TYPE_HASH, 0, 0, 0, zlib.compress(gmd4_bytes, 6), endian="<"),
                ArcEntry("ui/tex/btn_play_ps4", TEX_TYPE_HASH, 0, 0, 0, zlib.compress(tex4_btn_bytes, 6), endian="<"),
                ArcEntry("ui/tex/icon_static_ps4", TEX_TYPE_HASH, 0, 0, 0, zlib.compress(tex4_static_bytes, 6), endian="<"),
                ArcEntry(
                    "ui/misc/unrelated.dat", 0x11223344, len(comp_unrelated),
                    unrelated_flags | (len(unrelated_bytes) & 0x1FFFFFFF), 0, comp_unrelated, endian="<"
                ),
            ]
            ps4_arc = ArcFile(magic=ARC_MAGIC_LE, version=7, entries=ps4_entries, padding=b"\xcc" * 16, endian="<")
            (ps4_rom / "ui" / "title.arc").write_bytes(ps4_arc.to_bytes())

            # Manifest for verification
            manifest_file = td / "manifest.json"
            h_ps3_arc = hashlib.sha256((ps3_rom / "ui" / "title.arc").read_bytes()).hexdigest()
            manifest_file.write_text(
                json.dumps({"patches": {"0.85": {"ui/title.arc": h_ps3_arc}, "matabe_fix": {}}}),
                encoding="utf-8",
            )

            # Run repatch
            res = repatch(
                ps3_source=fake_ps3_dir,
                game_dir=fake_ps4_dir / "CUSA01159",
                out_dir=out_dir,
                threshold=40.0,
                manifest_path=manifest_file,
            )

            stats = res["stats"]
            self.assertEqual(stats["arcs_written"], 1)
            self.assertEqual(stats["gmd_strings_replaced"], 2)
            self.assertEqual(stats["textures_translated"], 1)
            self.assertEqual(stats["textures_kept"], 1)

            # Inspect written output arc
            out_arc_path = out_dir / "nativePS4" / "rom" / "ui" / "title.arc"
            self.assertTrue(out_arc_path.is_file())
            out_arc = ArcFile.from_file(out_arc_path)

            self.assertEqual(len(out_arc.entries), 4)

            # 1. GMD entry verified
            e_gmd = out_arc.get_entry("ui/msg/title")
            self.assertIsNotNone(e_gmd)
            out_gmd = GmdFile.from_bytes(e_gmd.decompress())
            self.assertEqual(out_gmd.strings[0], "Start Game")
            self.assertEqual(out_gmd.strings[1], "Quit")

            # 2. Translated TEX entry verified
            e_tex = out_arc.get_entry("ui/tex/btn_play_ps4")
            self.assertIsNotNone(e_tex)
            out_tex_bytes = e_tex.decompress()
            self.assertEqual(len(out_tex_bytes), len(tex4_btn_bytes))
            p_out = parse_tex_header(out_tex_bytes, endian="<")
            self.assertEqual(p_out["width"], 128)
            self.assertEqual(p_out["height"], 64)

            # 3. Unchanged TEX entry kept verbatim
            e_static = out_arc.get_entry("ui/tex/icon_static_ps4")
            self.assertIsNotNone(e_static)
            self.assertEqual(e_static.raw_data, ps4_entries[2].raw_data)

            # 4. Unrelated entry kept byte-identical with flags preserved
            e_unrel = out_arc.get_entry("ui/misc/unrelated.dat")
            self.assertIsNotNone(e_unrel)
            self.assertEqual(e_unrel.raw_data, comp_unrelated)
            self.assertEqual(e_unrel.flags, unrelated_flags)


if __name__ == "__main__":
    unittest.main()
