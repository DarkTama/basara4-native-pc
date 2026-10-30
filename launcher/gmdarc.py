"""Shared parser and serializer library for Capcom MT Framework ARC containers
and GMD (Game Message Data) files. Supports both little-endian (PS4) and
big-endian (PS3) formats. Stdlib only.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import struct
from typing import Optional
import zlib

ARC_MAGIC_LE = b"ARC\x00"
ARC_MAGIC_BE = b"\x00CRA"
GMD_MAGIC_LE = b"GMD\x00"
GMD_MAGIC_BE = b"\x00DMG"

GMD_TYPE_HASH = 0x242BB29A
TEX_TYPE_HASH = 0x241F5DEB


# Japanese / CJK unicode pattern: Hiragana, Katakana, CJK ideographs, Katakana extensions, Halfwidth Katakana
_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]"
)
_ASCII_RUN_RE = re.compile(r"[A-Za-z]{2,}")
_TAG_RE = re.compile(r"<[^>]*>")


def is_english(text: str) -> bool:
    """Returns True if text is considered English translation:
    - Strips markup tags (<...>)
    - Contains at least one ASCII letter run [A-Za-z]{2,}
    - Contains NO Hiragana/Katakana/CJK ideographs
    Full-width punctuation like '－' or '？' is permitted.
    """
    if not text:
        return False
    stripped = _TAG_RE.sub("", text)
    if not _ASCII_RUN_RE.search(stripped):
        return False
    if _CJK_RE.search(stripped):
        return False
    return True


def classify_string_skip_reason(ps3_str: str, ps4_str: str) -> Optional[str]:
    """Returns the reason a candidate string is skipped, or None if it passes.
    Reasons:
      - 'empty': ps3 string is empty
      - 'identical': ps3 string equals ps4 string
      - 'has_cjk': ps3 string contains Japanese/CJK characters
      - 'no_ascii_run': ps3 string has no 2+ letter ASCII word
    """
    if not ps3_str:
        return "empty"
    if ps3_str == ps4_str:
        return "identical"
    stripped = _TAG_RE.sub("", ps3_str)
    if _CJK_RE.search(stripped):
        return "has_cjk"
    if not _ASCII_RUN_RE.search(stripped):
        return "no_ascii_run"
    return None


class GmdFile:
    """Represents a parsed MT Framework Game Message Data (.gmd) file."""

    def __init__(
        self,
        magic: bytes,
        version: int,
        language: int,
        unknown: int,
        key_count: int,
        string_count: int,
        key_block_size: int,
        string_block_size: int,
        name: str,
        keys: list[str],
        strings: list[str],
        endian: str = "<",
        raw_name_block: bytes = b"",
        raw_middle: bytes = b"",
        raw_keys: bytes = b"",
        orig_string_block_len: int = 0,
        sbs_was_garbage: bool = False,
    ):
        self.magic = magic
        self.version = version
        self.language = language
        self.unknown = unknown
        self.key_count = key_count
        self.string_count = string_count
        self.key_block_size = key_block_size
        self.string_block_size = string_block_size
        self.name = name
        self.keys = keys
        self.strings = strings
        self.endian = endian
        self.raw_name_block = raw_name_block
        self.raw_middle = raw_middle
        self.raw_keys = raw_keys
        self.orig_string_block_len = orig_string_block_len
        self.sbs_was_garbage = sbs_was_garbage
        self._key_map: Optional[dict[str, int]] = None

    @property
    def key_to_index(self) -> dict[str, int]:
        if self._key_map is None:
            self._key_map = {k: i for i, k in enumerate(self.keys)}
        return self._key_map

    @classmethod
    def from_bytes(cls, data: bytes) -> GmdFile:
        if len(data) < 0x28:
            raise ValueError(f"GMD data too short: {len(data)} bytes")
        magic_probe = data[:4]
        if magic_probe == GMD_MAGIC_LE:
            endian = "<"
        elif magic_probe == GMD_MAGIC_BE:
            endian = ">"
        else:
            raise ValueError(f"Invalid GMD magic: {magic_probe!r}")

        magic, ver, lang, unk, kcnt, scnt, kbsz, sbsz, nlen = struct.unpack(
            f"{endian}4sIIQIIIII", data[:0x28]
        )
        name_block_end = 0x28 + nlen + 1
        raw_name_block = data[0x28:name_block_end]
        name = raw_name_block[:-1].decode("utf-8", errors="replace")

        if kcnt == 0:
            key_start = name_block_end
            key_end = name_block_end
            raw_middle = b""
            raw_keys = b""
            keys: list[str] = []
        else:
            mid_start = name_block_end
            mid_size = (
                kcnt * (32 if endian == "<" else 20)
                + (2048 if endian == "<" else 1024)
            )
            expected_key_start = mid_start + mid_size
            key_start = expected_key_start

            # Locate key block robustly (in case of padding / slight offset shifts)
            found = False
            for delta in (0, -2, 2, -4, 4, -8, 8):
                cand = expected_key_start + delta
                if cand + kbsz <= len(data):
                    block = data[cand : cand + kbsz]
                    if block.endswith(b"\x00"):
                        raw_k_list = block[:-1].split(b"\x00")
                        if len(raw_k_list) == kcnt:
                            key_start = cand
                            found = True
                            break
            if not found and expected_key_start + kbsz <= len(data):
                # Fallback: trust expected_key_start
                key_start = expected_key_start

            key_end = key_start + kbsz
            raw_middle = data[mid_start:key_start]
            raw_keys = data[key_start:key_end]
            if raw_keys.endswith(b"\x00"):
                keys = [
                    k.decode("utf-8", errors="replace")
                    for k in raw_keys[:-1].split(b"\x00")
                ]
            else:
                keys = [
                    k.decode("utf-8", errors="replace")
                    for k in raw_keys.split(b"\x00")
                ]

        string_block = data[key_end:]
        orig_string_block_len = len(string_block)
        if string_block.endswith(b"\x00"):
            raw_strs = string_block[:-1].split(b"\x00")
        else:
            raw_strs = string_block.split(b"\x00") if string_block else []

        strings = [s.decode("utf-8", errors="replace") for s in raw_strs]

        sbs_was_garbage = (
            sbsz != orig_string_block_len and sbsz != 0xFFFF
        )

        return cls(
            magic=magic,
            version=ver,
            language=lang,
            unknown=unk,
            key_count=kcnt,
            string_count=scnt,
            key_block_size=kbsz,
            string_block_size=sbsz,
            name=name,
            keys=keys,
            strings=strings,
            endian=endian,
            raw_name_block=raw_name_block,
            raw_middle=raw_middle,
            raw_keys=raw_keys,
            orig_string_block_len=orig_string_block_len,
            sbs_was_garbage=sbs_was_garbage,
        )

    def to_bytes(self, new_strings: Optional[list[str]] = None) -> bytes:
        """Serializes GMD to bytes. If new_strings is given, replaces the string block
        and updates header string_block_size according to format rules.
        """
        strs = self.strings if new_strings is None else new_strings

        # Build string block: NUL-terminated strings + trailing NUL
        if strs:
            new_str_block = (
                b"\x00".join(s.encode("utf-8") for s in strs) + b"\x00"
            )
        else:
            new_str_block = b""

        new_len = len(new_str_block)

        # SBS calculation rule:
        # - if orig == actual original block length -> write new length
        # - if orig == 0xFFFF -> keep 0xFFFF
        # - otherwise (garbage gt/lt) -> orig + (new_len - old_len), clamped to [0, 0xFFFFFFFF]
        if self.string_block_size == self.orig_string_block_len:
            new_sbsz = new_len
        elif self.string_block_size == 0xFFFF:
            new_sbsz = 0xFFFF
        else:
            diff = new_len - self.orig_string_block_len
            new_sbsz = max(0, min(0xFFFFFFFF, self.string_block_size + diff))

        hdr = struct.pack(
            f"{self.endian}4sIIQIIIII",
            self.magic,
            self.version,
            self.language,
            self.unknown,
            self.key_count,
            self.string_count,
            self.key_block_size,
            new_sbsz,
            len(self.raw_name_block) - 1 if self.raw_name_block else 0,
        )

        return (
            hdr
            + self.raw_name_block
            + self.raw_middle
            + self.raw_keys
            + new_str_block
        )


class ArcEntry:
    """Represents a single entry inside an ARC container."""

    def __init__(
        self,
        path: str,
        type_hash: int,
        compressed_size: int,
        size_and_flags: int,
        data_offset: int,
        raw_data: bytes,
        endian: str = "<",
    ):
        self.path = path
        self.type_hash = type_hash
        self.compressed_size = compressed_size
        self.size_and_flags = size_and_flags
        self.data_offset = data_offset
        self.raw_data = raw_data
        self.endian = endian
        self.uncompressed_data: Optional[bytes] = None
        self.is_modified: bool = False

    @property
    def flags(self) -> int:
        return self.size_and_flags & 0xE0000000

    @property
    def uncompressed_size(self) -> int:
        return self.size_and_flags & 0x1FFFFFFF

    @property
    def is_gmd(self) -> bool:
        return self.type_hash == GMD_TYPE_HASH

    def decompress(self) -> bytes:
        if self.uncompressed_data is not None:
            return self.uncompressed_data
        if self.raw_data.startswith((b"\x00XET", b"TEX\x00", b"GMD\x00", b"\x00DMG")):
            self.uncompressed_data = self.raw_data
            return self.raw_data
        try:
            decomp = zlib.decompress(self.raw_data)
        except Exception:
            try:
                decomp = zlib.decompress(self.raw_data, 15)
            except Exception:
                try:
                    decomp = zlib.decompress(self.raw_data, -15)
                except Exception:
                    decomp = self.raw_data
        self.uncompressed_data = decomp
        return decomp

    def set_uncompressed(self, data: bytes, level: int = 9) -> None:
        """Sets new uncompressed data, compresses it at zlib level, updates size_and_flags
        preserving original flags, and marks the entry modified.
        """
        self.uncompressed_data = data
        self.is_modified = True
        comp = zlib.compress(data, level)
        self.raw_data = comp
        self.compressed_size = len(comp)
        orig_flags = self.size_and_flags & 0xE0000000
        self.size_and_flags = orig_flags | (len(data) & 0x1FFFFFFF)


class ArcFile:
    """Represents an MT Framework ARC archive."""

    def __init__(
        self,
        magic: bytes,
        version: int,
        entries: list[ArcEntry],
        padding: bytes = b"",
        endian: str = "<",
    ):
        self.magic = magic
        self.version = version
        self.entries = entries
        self.padding = padding
        self.endian = endian
        self._path_map: Optional[dict[str, ArcEntry]] = None

    @property
    def path_to_entry(self) -> dict[str, ArcEntry]:
        if self._path_map is None:
            self._path_map = {}
            for e in self.entries:
                self._path_map[e.path] = e
                self._path_map[e.path.replace("/", "\\")] = e
                self._path_map[e.path.replace("\\", "/")] = e
        return self._path_map

    def get_entry(self, path: str) -> Optional[ArcEntry]:
        if path in self.path_to_entry:
            return self.path_to_entry[path]
        norm_bs = path.replace("/", "\\")
        if norm_bs in self.path_to_entry:
            return self.path_to_entry[norm_bs]
        norm_fs = path.replace("\\", "/")
        if norm_fs in self.path_to_entry:
            return self.path_to_entry[norm_fs]
        lower_map = {k.lower(): v for k, v in self.path_to_entry.items()}
        return lower_map.get(norm_bs.lower()) or lower_map.get(norm_fs.lower())

    @classmethod
    def from_bytes(cls, data: bytes) -> ArcFile:
        if len(data) < 8:
            raise ValueError(f"ARC data too short: {len(data)} bytes")
        magic = data[:4]
        if magic == ARC_MAGIC_LE:
            endian = "<"
        elif magic == ARC_MAGIC_BE:
            endian = ">"
        else:
            raise ValueError(f"Unknown ARC magic: {magic!r}")

        magic, version, count = struct.unpack(f"{endian}4sHH", data[:8])
        entries: list[ArcEntry] = []
        table_end = 8 + count * 80
        if len(data) < table_end:
            raise ValueError(f"ARC table truncated: {len(data)} < {table_end}")

        first_data_offset = table_end
        for i in range(count):
            off = 8 + i * 80
            p_bytes, th, csz, szf, doff = struct.unpack(
                f"{endian}64sIIII", data[off : off + 80]
            )
            p_str = p_bytes.split(b"\x00")[0].decode("ascii", errors="replace")
            raw_entry_data = data[doff : doff + csz]
            if i == 0:
                first_data_offset = doff
            entries.append(
                ArcEntry(
                    path=p_str,
                    type_hash=th,
                    compressed_size=csz,
                    size_and_flags=szf,
                    data_offset=doff,
                    raw_data=raw_entry_data,
                    endian=endian,
                )
            )

        padding = data[table_end:first_data_offset]
        return cls(
            magic=magic,
            version=version,
            entries=entries,
            padding=padding,
            endian=endian,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> ArcFile:
        with open(path, "rb") as f:
            data = f.read()
        return cls.from_bytes(data)

    def to_bytes(self) -> bytes:
        """Repacks the ARC file. Non-modified entries retain their raw compressed
        bytes verbatim. Subsequent data offsets are sequentially recalculated.
        First data offset and table padding are preserved byte-identical.
        """
        count = len(self.entries)
        hdr = struct.pack(f"{self.endian}4sHH", self.magic, self.version, count)

        table_end = 8 + count * 80
        first_data_offset = (
            self.entries[0].data_offset
            if (self.entries and self.entries[0].data_offset >= table_end)
            else (table_end + len(self.padding))
        )
        curr_offset = first_data_offset

        table_parts = []
        data_parts = []
        for entry in self.entries:
            p_bytes = entry.path.encode("ascii", errors="replace").ljust(
                64, b"\x00"
            )
            table_parts.append(
                struct.pack(
                    f"{self.endian}64sIIII",
                    p_bytes,
                    entry.type_hash,
                    len(entry.raw_data),
                    entry.size_and_flags,
                    curr_offset,
                )
            )
            data_parts.append(entry.raw_data)
            curr_offset += len(entry.raw_data)

        return hdr + b"".join(table_parts) + self.padding + b"".join(data_parts)
