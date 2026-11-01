"""Unit tests for GMD string_block_size header repair and gmdarc serialization."""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import pytest

from gmdarc import ArcEntry, ArcFile, GmdFile, GMD_TYPE_HASH, GMD_MAGIC_LE, ARC_MAGIC_LE
from basara4_gmd_fix import (
    check_and_repair_arc,
    ensure_gmd_header_fix,
    find_candidate_arcs,
    is_exact,
    repair_arc_file,
    repair_layer_in_place,
)


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
    magic = GMD_MAGIC_LE
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
        info = b""
        k_off = 0
        for i, k in enumerate(keys):
            kb = k.encode("utf-8")
            info += struct.pack("<IIIIQQ", i, 0, 0, len(kb), k_off, 0)
            k_off += len(kb) + 1
        buckets = b"\x00" * 2048
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
    magic = ARC_MAGIC_LE
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


class TestGmdFileHelper(unittest.TestCase):
    def test_with_exact_string_block_size_instance(self):
        keys = ["KEY_A", "KEY_B"]
        strings = ["String 1", "String 2 longer text"]
        real_len = sum(len(s.encode("utf-8")) + 1 for s in strings)

        # Inconsistent header: 50 while real length is real_len
        raw = make_synthetic_gmd("test_helper", keys, strings, sbsz_override=50)
        gmd = GmdFile.from_bytes(raw)
        self.assertEqual(gmd.string_block_size, 50)
        self.assertEqual(gmd.orig_string_block_len, real_len)
        self.assertTrue(gmd.sbs_was_garbage)

        repaired_bytes = gmd.with_exact_string_block_size()
        repaired_gmd = GmdFile.from_bytes(repaired_bytes)

        self.assertEqual(repaired_gmd.string_block_size, real_len)
        self.assertEqual(repaired_gmd.orig_string_block_len, real_len)
        self.assertFalse(repaired_gmd.sbs_was_garbage)

        # Confirm all bytes except the 4-byte sbsz at offset 0x20..0x24 are identical
        self.assertEqual(raw[:0x20], repaired_bytes[:0x20])
        self.assertEqual(raw[0x24:], repaired_bytes[0x24:])
        self.assertEqual(
            struct.pack("<I", real_len), repaired_bytes[0x20:0x24]
        )

    def test_with_exact_string_block_size_classmethod(self):
        keys = ["KEY_1"]
        strings = ["Sample text here"]
        real_len = sum(len(s.encode("utf-8")) + 1 for s in strings)

        raw = make_synthetic_gmd("classmethod_test", keys, strings, sbsz_override=999)
        repaired_bytes = GmdFile.with_exact_string_block_size(raw)
        repaired_gmd = GmdFile.from_bytes(repaired_bytes)

        self.assertEqual(repaired_gmd.string_block_size, real_len)
        self.assertEqual(raw[:0x20], repaired_bytes[:0x20])
        self.assertEqual(raw[0x24:], repaired_bytes[0x24:])

    def test_to_bytes_sbs_exact_rule(self):
        keys = ["KEY_1"]
        orig_strings = ["Initial text"]
        new_longer_strings = ["Much longer replacement text for the message"]

        # Base header was exact -> to_bytes writes exact new length
        raw_exact = make_synthetic_gmd("exact_base", keys, orig_strings)
        gmd_exact = GmdFile.from_bytes(raw_exact)
        self.assertEqual(gmd_exact.string_block_size, gmd_exact.orig_string_block_len)

        out_exact = gmd_exact.to_bytes(new_strings=new_longer_strings)
        gmd_out = GmdFile.from_bytes(out_exact)
        expected_len = sum(len(s.encode("utf-8")) + 1 for s in new_longer_strings)
        self.assertEqual(gmd_out.string_block_size, expected_len)

        # Explicit base_exact=True override
        raw_inexact = make_synthetic_gmd("inexact", keys, orig_strings, sbsz_override=10)
        gmd_inexact = GmdFile.from_bytes(raw_inexact)
        out_forced = gmd_inexact.to_bytes(new_strings=new_longer_strings, base_exact=True)
        self.assertEqual(GmdFile.from_bytes(out_forced).string_block_size, expected_len)


class TestGmdHeaderRepairRules(unittest.TestCase):
    def setUp(self):
        self.keys = ["K1", "K2"]
        self.base_strings = ["Short", "Base text"]
        self.longer_strings = ["Much longer replacement string 1", "Much longer replacement string 2"]
        self.base_len = sum(len(s.encode("utf-8")) + 1 for s in self.base_strings)
        self.longer_len = sum(len(s.encode("utf-8")) + 1 for s in self.longer_strings)

    def test_exact_header_longer_strings_repaired(self):
        """Base GMD exact header + layered GMD with longer strings & inconsistent header -> repaired."""
        # Base: exact header
        base_gmd = make_synthetic_gmd("msg_a", self.keys, self.base_strings)
        # Layered: longer strings, but sbsz was not updated (set to base_len + 1, e.g. 5631 instead of 5809)
        layered_gmd = make_synthetic_gmd(
            "msg_a", self.keys, self.longer_strings, sbsz_override=self.base_len + 1
        )

        entries_base = [("ui\\msg\\msg_a", GMD_TYPE_HASH, base_gmd, 0)]
        entries_layered = [("ui\\msg\\msg_a", GMD_TYPE_HASH, layered_gmd, 0)]

        arc_base = ArcFile.from_bytes(make_synthetic_arc(entries_base))
        arc_layered = ArcFile.from_bytes(make_synthetic_arc(entries_layered))

        repaired = check_and_repair_arc(arc_layered, arc_base)
        self.assertEqual(len(repaired), 1)
        path, old_sbsz, new_sbsz = repaired[0]
        self.assertEqual(path, "ui\\msg\\msg_a")
        self.assertEqual(old_sbsz, self.base_len + 1)
        self.assertEqual(new_sbsz, self.longer_len)

        # Verify the entry inside arc_layered was updated
        entry = arc_layered.get_entry("ui\\msg\\msg_a")
        repaired_entry_gmd = GmdFile.from_bytes(entry.decompress())
        self.assertEqual(repaired_entry_gmd.string_block_size, self.longer_len)

    def test_0xffff_header_untouched(self):
        """Layered GMD with 0xFFFF header -> untouched."""
        base_gmd = make_synthetic_gmd("msg_b", self.keys, self.base_strings)
        # Layered has 0xFFFF header
        layered_gmd = make_synthetic_gmd(
            "msg_b", self.keys, self.longer_strings, sbsz_override=0xFFFF
        )

        entries_base = [("ui\\msg\\msg_b", GMD_TYPE_HASH, base_gmd, 0)]
        entries_layered = [("ui\\msg\\msg_b", GMD_TYPE_HASH, layered_gmd, 0)]

        arc_base = ArcFile.from_bytes(make_synthetic_arc(entries_base))
        arc_layered = ArcFile.from_bytes(make_synthetic_arc(entries_layered))

        repaired = check_and_repair_arc(arc_layered, arc_base)
        self.assertEqual(repaired, [])
        entry = arc_layered.get_entry("ui\\msg\\msg_b")
        self.assertEqual(
            GmdFile.from_bytes(entry.decompress()).string_block_size, 0xFFFF
        )

    def test_base_non_exact_untouched(self):
        """Base GMD non-exact header -> layered GMD untouched."""
        # Base has non-exact header (e.g. 500 when real is base_len)
        base_gmd = make_synthetic_gmd(
            "msg_c", self.keys, self.base_strings, sbsz_override=500
        )
        layered_gmd = make_synthetic_gmd(
            "msg_c", self.keys, self.longer_strings, sbsz_override=600
        )

        entries_base = [("ui\\msg\\msg_c", GMD_TYPE_HASH, base_gmd, 0)]
        entries_layered = [("ui\\msg\\msg_c", GMD_TYPE_HASH, layered_gmd, 0)]

        arc_base = ArcFile.from_bytes(make_synthetic_arc(entries_base))
        arc_layered = ArcFile.from_bytes(make_synthetic_arc(entries_layered))

        repaired = check_and_repair_arc(arc_layered, arc_base)
        self.assertEqual(repaired, [])
        entry = arc_layered.get_entry("ui\\msg\\msg_c")
        self.assertEqual(
            GmdFile.from_bytes(entry.decompress()).string_block_size, 600
        )


class TestGmdFixLayerAndCache(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.base_dir = self.root / "base"
        self.update_dir = self.root / "update"
        self.out_fix_dir = self.root / "mods" / "gmd_header_fix"

        self.base_dir.mkdir(parents=True)
        self.update_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_ensure_gmd_header_fix_and_cache(self):
        # Create candidate ARC in both base and update: nativePS4/rom/ui/battle_prepare.arc
        keys = ["K1"]
        base_strs = ["Base Japanese string"]
        update_strs = ["Very long English replacement string that caused stack corruption"]

        base_gmd = make_synthetic_gmd("msg_win_require_jpn", keys, base_strs)
        # update has sbsz 5631, real is longer
        update_gmd = make_synthetic_gmd(
            "msg_win_require_jpn", keys, update_strs, sbsz_override=5631
        )
        real_len = sum(len(s.encode("utf-8")) + 1 for s in update_strs)

        arc_rel = Path("nativePS4") / "rom" / "ui" / "battle_prepare.arc"
        base_arc_path = self.base_dir / arc_rel
        update_arc_path = self.update_dir / arc_rel
        base_arc_path.parent.mkdir(parents=True, exist_ok=True)
        update_arc_path.parent.mkdir(parents=True, exist_ok=True)

        base_arc_path.write_bytes(
            make_synthetic_arc([("ui\\msg\\msg_win_require_jpn", GMD_TYPE_HASH, base_gmd, 0)])
        )
        update_arc_path.write_bytes(
            make_synthetic_arc([("ui\\msg\\msg_win_require_jpn", GMD_TYPE_HASH, update_gmd, 0)])
        )

        orig_base_bytes = base_arc_path.read_bytes()
        orig_update_bytes = update_arc_path.read_bytes()

        # 1. Run ensure_gmd_header_fix
        repaired_arcs = ensure_gmd_header_fix(self.base_dir, self.update_dir, self.out_fix_dir)
        self.assertEqual(len(repaired_arcs), 1)
        out_arc = self.out_fix_dir / arc_rel
        self.assertTrue(out_arc.is_file())

        # Verify repaired GMD header
        repaired_arc = ArcFile.from_file(out_arc)
        repaired_gmd = GmdFile.from_bytes(repaired_arc.get_entry("ui\\msg\\msg_win_require_jpn").decompress())
        self.assertEqual(repaired_gmd.string_block_size, real_len)

        # Confirm base and update folders were NEVER modified
        self.assertEqual(base_arc_path.read_bytes(), orig_base_bytes)
        self.assertEqual(update_arc_path.read_bytes(), orig_update_bytes)

        # Stamp file must exist
        stamp_file = self.out_fix_dir / ".gmd_fix_stamp.json"
        self.assertTrue(stamp_file.is_file())

        # 2. Second run: must hit cache and return repaired arcs without error
        repaired_arcs_2 = ensure_gmd_header_fix(self.base_dir, self.update_dir, self.out_fix_dir)
        self.assertEqual(len(repaired_arcs_2), 1)
        self.assertEqual(repaired_arcs_2[0], out_arc)

    def test_repair_layer_in_place(self):
        keys = ["K1"]
        base_strs = ["Base Japanese string"]
        patch_strs = ["English patch string"]
        real_len = sum(len(s.encode("utf-8")) + 1 for s in patch_strs)

        base_gmd = make_synthetic_gmd("msg_test", keys, base_strs)
        patch_gmd = make_synthetic_gmd("msg_test", keys, patch_strs, sbsz_override=10)

        arc_rel = Path("nativePS4") / "rom" / "ui" / "test.arc"
        base_arc_path = self.base_dir / arc_rel
        base_arc_path.parent.mkdir(parents=True, exist_ok=True)
        base_arc_path.write_bytes(
            make_synthetic_arc([("ui\\msg\\msg_test", GMD_TYPE_HASH, base_gmd, 0)])
        )

        patch_dir = self.root / "mods" / "english_patch"
        patch_arc_path = patch_dir / arc_rel
        patch_arc_path.parent.mkdir(parents=True, exist_ok=True)
        patch_arc_path.write_bytes(
            make_synthetic_arc([("ui\\msg\\msg_test", GMD_TYPE_HASH, patch_gmd, 0)])
        )

        res = repair_layer_in_place(patch_dir, self.base_dir)
        self.assertEqual(len(res), 1)

        repaired_arc = ArcFile.from_file(patch_arc_path)
        gmd = GmdFile.from_bytes(repaired_arc.get_entry("ui\\msg\\msg_test").decompress())
        self.assertEqual(gmd.string_block_size, real_len)
