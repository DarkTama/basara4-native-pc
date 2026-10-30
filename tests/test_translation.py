"""Unit tests for the ARC and GMD parser/serializer, translation extraction,
and patch building using purely synthetic fixtures with no Capcom data.
Follows the style of tests/paths.py and tests/test_mods.py.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

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
    ArcEntry,
    ArcFile,
    GmdFile,
    classify_string_skip_reason,
    is_english,
)
from basara4_translation import build_patch


def make_synthetic_gmd(
    name: str,
    keys: list[str],
    strings: list[str],
    endian: str = "<",
    language: int = 0,
    unknown: int = 0,
    sbsz_override: int | None = None,
) -> bytes:
    """Builds a complete, valid synthetic GMD binary buffer."""
    magic = GMD_MAGIC_LE if endian == "<" else GMD_MAGIC_BE
    ver = 0x00010302
    kcnt = len(keys)
    scnt = len(strings)

    k_block = b"".join(k.encode("utf-8") + b"\x00" for k in keys)
    s_block = b"".join(s.encode("utf-8") + b"\x00" for s in strings)

    kbsz = len(k_block)
    sbsz = sbsz_override if sbsz_override is not None else len(s_block)

    name_b = name.encode("utf-8")
    nlen = len(name_b)

    hdr = struct.pack(
        f"{endian}4sIIQIIIII",
        magic,
        ver,
        language,
        unknown,
        kcnt,
        scnt,
        kbsz,
        sbsz,
        nlen,
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


def make_synthetic_arc(
    entries: list[tuple[str, int, bytes, int]],
    endian: str = "<",
    version: int = 7,
    padding: bytes = b"",
) -> bytes:
    """Builds a complete, valid synthetic ARC container binary buffer."""
    count = len(entries)
    magic = ARC_MAGIC_LE if endian == "<" else ARC_MAGIC_BE
    hdr = struct.pack(f"{endian}4sHH", magic, version, count)

    table_parts: list[bytes] = []
    data_parts: list[bytes] = []

    first_offset = 8 + count * 80 + len(padding)
    curr_offset = first_offset

    for path, type_hash, uncompressed, flags in entries:
        comp = zlib.compress(uncompressed, 9)
        csz = len(comp)
        szf = (flags & 0xE0000000) | (len(uncompressed) & 0x1FFFFFFF)
        p_bytes = path.encode("ascii").ljust(64, b"\x00")
        table_parts.append(
            struct.pack(
                f"{endian}64sIIII", p_bytes, type_hash, csz, szf, curr_offset
            )
        )
        data_parts.append(comp)
        curr_offset += csz

    return hdr + b"".join(table_parts) + padding + b"".join(data_parts)


class TestGmdParser(unittest.TestCase):
    def test_gmd_roundtrip_both_endians(self):
        keys = ["MSG_START", "MSG_STOP", "MSG_PAUSE"]
        strings = ["Start Game", "Stop Game", "Pause Game", "Unkeyed string 1"]

        for endian in ("<", ">"):
            with self.subTest(endian=endian):
                raw = make_synthetic_gmd("test_msg", keys, strings, endian=endian)
                gmd = GmdFile.from_bytes(raw)

                self.assertEqual(gmd.endian, endian)
                self.assertEqual(gmd.name, "test_msg")
                self.assertEqual(gmd.keys, keys)
                self.assertEqual(gmd.strings, strings)

                rebuilt = gmd.to_bytes()
                self.assertEqual(rebuilt, raw)
                self.assertEqual(
                    hashlib.sha256(rebuilt).hexdigest(),
                    hashlib.sha256(raw).hexdigest(),
                )

    def test_gmd_roundtrip_keyless(self):
        strings = ["First unkeyed", "Second unkeyed", "Third unkeyed"]
        for endian in ("<", ">"):
            with self.subTest(endian=endian):
                raw = make_synthetic_gmd("keyless_msg", [], strings, endian=endian)
                gmd = GmdFile.from_bytes(raw)
                self.assertEqual(gmd.keys, [])
                self.assertEqual(gmd.strings, strings)
                rebuilt = gmd.to_bytes()
                self.assertEqual(rebuilt, raw)

    def test_string_block_size_rules(self):
        keys = ["K1"]
        orig_strings = ["Short string"]
        new_strings = ["Much longer string that expands the block"]

        # Case 1: orig_sbs == actual original block length -> writes new length
        raw1 = make_synthetic_gmd("case1", keys, orig_strings, endian="<")
        gmd1 = GmdFile.from_bytes(raw1)
        self.assertFalse(gmd1.sbs_was_garbage)
        rebuilt1 = gmd1.to_bytes(new_strings=new_strings)
        gmd1_rebuilt = GmdFile.from_bytes(rebuilt1)
        expected_len1 = len(b"".join(s.encode("utf-8") + b"\x00" for s in new_strings))
        self.assertEqual(gmd1_rebuilt.string_block_size, expected_len1)

        # Case 2: orig_sbs == 0xFFFF -> keeps 0xFFFF
        raw2 = make_synthetic_gmd("case2", keys, orig_strings, endian="<", sbsz_override=0xFFFF)
        gmd2 = GmdFile.from_bytes(raw2)
        self.assertFalse(gmd2.sbs_was_garbage)
        rebuilt2 = gmd2.to_bytes(new_strings=new_strings)
        gmd2_rebuilt = GmdFile.from_bytes(rebuilt2)
        self.assertEqual(gmd2_rebuilt.string_block_size, 0xFFFF)

        # Case 3: orig_sbs is garbage (e.g. 500 when actual was 13)
        actual_len = len(b"".join(s.encode("utf-8") + b"\x00" for s in orig_strings))
        garbage_sbs = 500
        raw3 = make_synthetic_gmd("case3", keys, orig_strings, endian="<", sbsz_override=garbage_sbs)
        gmd3 = GmdFile.from_bytes(raw3)
        self.assertTrue(gmd3.sbs_was_garbage)
        rebuilt3 = gmd3.to_bytes(new_strings=new_strings)
        gmd3_rebuilt = GmdFile.from_bytes(rebuilt3)
        diff = expected_len1 - actual_len
        self.assertEqual(gmd3_rebuilt.string_block_size, garbage_sbs + diff)


class TestArcParser(unittest.TestCase):
    def test_arc_roundtrip(self):
        gmd1 = make_synthetic_gmd("gmd1", ["K1"], ["Str 1"])
        non_gmd = b"Raw binary texture or shader data"
        entries = [
            ("ui\\msg\\test_gmd", GMD_TYPE_HASH, gmd1, 0x40000000),
            ("shader\\test_shd", 0x12345678, non_gmd, 0x00000000),
        ]
        padding = b"\x00" * 32
        raw_arc = make_synthetic_arc(entries, endian="<", version=7, padding=padding)

        arc = ArcFile.from_bytes(raw_arc)
        self.assertEqual(len(arc.entries), 2)
        self.assertEqual(arc.padding, padding)
        self.assertEqual(arc.entries[0].path, "ui\\msg\\test_gmd")
        self.assertTrue(arc.entries[0].is_gmd)
        self.assertEqual(arc.entries[1].path, "shader\\test_shd")
        self.assertFalse(arc.entries[1].is_gmd)

        # Repack without modifications
        rebuilt = arc.to_bytes()
        self.assertEqual(rebuilt, raw_arc)

    def test_flag_preservation(self):
        gmd_data = make_synthetic_gmd("test", ["K"], ["Old value"])
        entries = [("ui\\msg\\test", GMD_TYPE_HASH, gmd_data, 0x40000000)]
        raw_arc = make_synthetic_arc(entries, endian="<", version=7)
        arc = ArcFile.from_bytes(raw_arc)

        entry = arc.entries[0]
        self.assertEqual(entry.flags, 0x40000000)

        # Modify entry data
        new_gmd_data = make_synthetic_gmd("test", ["K"], ["New longer translated value"])
        entry.set_uncompressed(new_gmd_data)

        self.assertEqual(entry.flags, 0x40000000)
        self.assertEqual(entry.uncompressed_size, len(new_gmd_data))
        self.assertEqual(
            entry.size_and_flags, 0x40000000 | (len(new_gmd_data) & 0x1FFFFFFF)
        )

        repacked = arc.to_bytes()
        re_arc = ArcFile.from_bytes(repacked)
        self.assertEqual(re_arc.entries[0].flags, 0x40000000)
        self.assertEqual(re_arc.entries[0].uncompressed_size, len(new_gmd_data))


class TestEnglishFilter(unittest.TestCase):
    def test_english_filter_positive(self):
        self.assertTrue(is_english("Start Battle"))
        self.assertTrue(is_english("<COLOR RED>Victory</COLOR>"))
        self.assertTrue(is_english("<SIZE 13>Sanada Yukimura's Spear</SIZE>"))
        self.assertTrue(is_english("Chapter 1－Prologue"))
        self.assertTrue(is_english("Are you ready？"))

    def test_english_filter_negative(self):
        self.assertFalse(is_english(""))  # empty
        self.assertFalse(is_english("   "))  # whitespace
        self.assertFalse(is_english("<TAG></TAG>"))  # only tags
        self.assertFalse(is_english("A"))  # single letter (no 2+ run)
        self.assertFalse(is_english("12345"))  # numbers only
        self.assertFalse(is_english("こんにちは"))  # Hiragana
        self.assertFalse(is_english("サムライ"))  # Katakana
        self.assertFalse(is_english("戦国時代"))  # CJK Kanji
        self.assertFalse(is_english("Stage 1 勝利"))  # Mixed English + Kanji
        self.assertFalse(is_english("ｱｲｳｴｵ"))  # Halfwidth Katakana

    def test_classify_skip_reasons(self):
        self.assertEqual(classify_string_skip_reason("", "abc"), "empty")
        self.assertEqual(classify_string_skip_reason("Same", "Same"), "identical")
        self.assertEqual(classify_string_skip_reason("日本語", "abc"), "has_cjk")
        self.assertEqual(classify_string_skip_reason("123", "abc"), "no_ascii_run")
        self.assertIsNone(classify_string_skip_reason("English text", "日本語"))


class TestTranslationPatch(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name)

    def test_by_key_on_mismatched_keys(self):
        # PS4 GMD: keys ["A", "B", "C"], strings ["PS4_A", "PS4_B", "PS4_C"]
        ps4_gmd = make_synthetic_gmd(
            "mismatch_msg",
            ["A", "B", "C"],
            ["PS4_A", "PS4_B", "PS4_C"],
            endian="<",
        )
        ps4_arc_bytes = make_synthetic_arc(
            [("ui\\msg\\mismatch_msg", GMD_TYPE_HASH, ps4_gmd, 0x40000000)]
        )

        game_root = self.base / "game" / "CUSA01159"
        rom_dir = game_root / "nativePS4" / "rom"
        rom_dir.mkdir(parents=True)
        (game_root / "eboot.bin").write_bytes(b"synthetic eboot")
        arc_file = rom_dir / "test.arc"
        arc_file.write_bytes(ps4_arc_bytes)
        source_hash_before = hashlib.sha256(arc_file.read_bytes()).hexdigest()

        # DB has by_key mapping only for common keys A and C
        db = {
            "meta": {"format": 1},
            "pool": ["Translated_A", "Translated_C"],
            "archives": {
                "test.arc": {
                    "ui\\msg\\mismatch_msg": {
                        "by_key": {
                            "A": 0,
                            "C": 1,
                        }
                    }
                }
            },
        }
        db_path = self.base / "db.json"
        db_path.write_text(json.dumps(db), encoding="utf-8")

        out_dir = self.base / "mods" / "english_patch"
        stats = build_patch(game_root, db_path, out_dir)

        # Verify source game file was NOT modified
        self.assertEqual(
            hashlib.sha256(arc_file.read_bytes()).hexdigest(),
            source_hash_before,
        )

        # Verify patch was built
        out_arc_path = out_dir / "nativePS4" / "rom" / "test.arc"
        self.assertTrue(out_arc_path.is_file())
        self.assertEqual(stats["strings_replaced"], 2)

        out_arc = ArcFile.from_file(out_arc_path)
        out_gmd = GmdFile.from_bytes(out_arc.entries[0].decompress())
        self.assertEqual(
            out_gmd.strings,
            ["Translated_A", "PS4_B", "Translated_C"],
        )

    def test_unkeyed_strings_never_index_transplanted_in_mismatched_gmds(self):
        # PS4 has 2 keys + 2 unkeyed strings = 4 strings
        ps4_gmd = make_synthetic_gmd(
            "diff_count_msg",
            ["K1", "K2"],
            ["PS4_K1", "PS4_K2", "PS4_Unkeyed_0", "PS4_Unkeyed_1"],
            endian="<",
        )
        ps4_arc_bytes = make_synthetic_arc(
            [("ui\\msg\\diff_count_msg", GMD_TYPE_HASH, ps4_gmd, 0x40000000)]
        )

        game_root = self.base / "game" / "CUSA01159"
        rom_dir = game_root / "nativePS4" / "rom"
        rom_dir.mkdir(parents=True)
        (game_root / "eboot.bin").write_bytes(b"synthetic eboot")
        arc_file = rom_dir / "test.arc"
        arc_file.write_bytes(ps4_arc_bytes)

        # In a mismatched GMD, by rule only by_key is exported.
        # No by_index entries may exist for unkeyed indices 2 and 3.
        db = {
            "meta": {"format": 1},
            "pool": ["Trans_K1", "Trans_K2"],
            "archives": {
                "test.arc": {
                    "ui\\msg\\diff_count_msg": {
                        "by_key": {
                            "K1": 0,
                            "K2": 1,
                        }
                    }
                }
            },
        }
        db_path = self.base / "db.json"
        db_path.write_text(json.dumps(db), encoding="utf-8")

        out_dir = self.base / "mods" / "english_patch"
        build_patch(game_root, db_path, out_dir)

        out_arc = ArcFile.from_file(out_dir / "nativePS4" / "rom" / "test.arc")
        out_gmd = GmdFile.from_bytes(out_arc.entries[0].decompress())

        # Unkeyed strings must remain PS4 original
        self.assertEqual(out_gmd.strings[2], "PS4_Unkeyed_0")
        self.assertEqual(out_gmd.strings[3], "PS4_Unkeyed_1")
        # Keyed strings were replaced
        self.assertEqual(out_gmd.strings[0], "Trans_K1")
        self.assertEqual(out_gmd.strings[1], "Trans_K2")

    def test_prefer_ps4_english_flag(self):
        # PS4 string is already English ("English Title")
        ps4_gmd = make_synthetic_gmd(
            "title_msg",
            ["TITLE"],
            ["English Title"],
            endian="<",
        )
        ps4_arc_bytes = make_synthetic_arc(
            [("ui\\msg\\title_msg", GMD_TYPE_HASH, ps4_gmd, 0x40000000)]
        )

        game_root = self.base / "game" / "CUSA01159"
        rom_dir = game_root / "nativePS4" / "rom"
        rom_dir.mkdir(parents=True, exist_ok=True)
        (game_root / "eboot.bin").write_bytes(b"synthetic eboot")
        arc_file = rom_dir / "title.arc"
        arc_file.write_bytes(ps4_arc_bytes)

        db = {
            "meta": {"format": 1},
            "pool": ["PS3 English Title"],
            "archives": {
                "title.arc": {
                    "ui\\msg\\title_msg": {
                        "by_index": {"0": 0}
                    }
                }
            },
        }
        db_path = self.base / "db_title.json"
        db_path.write_text(json.dumps(db), encoding="utf-8")

        # 1. With prefer_ps4_english=True, PS4 English is kept
        out_prefer = self.base / "mods" / "out_prefer"
        stats_prefer = build_patch(game_root, db_path, out_prefer, prefer_ps4_english=True)
        self.assertEqual(stats_prefer["strings_kept_ps4_english"], 1)
        self.assertEqual(stats_prefer["strings_replaced"], 0)
        self.assertEqual(stats_prefer["arcs_written"], 0)

        # 2. With prefer_ps4_english=False, PS3 replacement wins
        out_overwrite = self.base / "mods" / "out_overwrite"
        stats_overwrite = build_patch(game_root, db_path, out_overwrite, prefer_ps4_english=False)
        self.assertEqual(stats_overwrite["strings_kept_ps4_english"], 0)
        self.assertEqual(stats_overwrite["strings_replaced"], 1)
        self.assertEqual(stats_overwrite["arcs_written"], 1)
        out_arc = ArcFile.from_file(out_overwrite / "nativePS4" / "rom" / "title.arc")
        out_gmd = GmdFile.from_bytes(out_arc.entries[0].decompress())
        self.assertEqual(out_gmd.strings[0], "PS3 English Title")


if __name__ == "__main__":
    unittest.main()
