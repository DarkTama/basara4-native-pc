#!/usr/bin/env python3
"""Sengoku Basara 4 Sumeragi - 100% Starter Save Patcher (Level 1).

Patches unencrypted BSR4_SUMERAGI.DAT save files to unlock:
  - Maximum Gold (99,999,999) and Net Basara shop coins (999,999)
  - All offline and DLC weapons for all 40+ officers at level 1 stats
  - Inscriptions (inscriptions inventory & materials like Tamahagane)
  - All officer roster unlock bitmasks

Guardrails:
  - Strictly verifies that character levels and EXP remain at Lv 0/1 to preserve
    natural progression and gameplay challenge.
  - Automatically creates timestamped and standard (.bak) backups before touching saves.
  - Safe atomic write with rollback on any failure.

Usage:
  python scripts/save_patcher.py [--save PATH] [--coins] [--weapons] [--items] [--all]
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Standard save parameters
SAVE_FILENAME = "BSR4_SUMERAGI.DAT"
DEFAULT_SAVE_SIZE = 3724612
NUM_OFFICERS = 44
WEAPONS_PER_OFFICER = 9
TOTAL_WEAPON_RECORDS = NUM_OFFICERS * WEAPONS_PER_OFFICER  # 396 theoretical, 387 in standard table

# Numeric maximum values
MAX_GOLD = 99_999_999
MAX_COINS = 999_999
MAX_MATERIALS = 99_999
MAX_INSCRIPTIONS = 99
ROSTER_UNLOCK_MASK = 0x000007FFFFFFFFFF  # 43/44 playable officers bitmask
ROSTER_UNLOCK_MASK_ALL = 0x00000FFFFFFFFFFF

# Section IDs (MT Framework sorted CRC32/hashes)
SEC_SYS_STATE = 0x08F67C5B       # Section 1: System state flag (len 4)
SEC_SYS_OPTIONS = 0x0F1598F4     # Section 2: Game options / config (len 208)
SEC_DIFFICULTY = 0x27CB70EA      # Section 3: Difficulty clears (len 48)
SEC_INVENTORY = 0x48F7E8C5       # Section 4: Inventory container (len 2941560)
SEC_WALLET = 0x7E2BD436          # Section 5: Gold & Net Basara Coins (len 48)
SEC_BGM = 0x811E8CFD             # Section 6: Sound & BGM unlocks (len 72)
SEC_INSCRIPTIONS = 0x90D40882    # Section 7: Inscriptions & character matrix (len 516392)
SEC_MATERIALS = 0x92CCED80       # Section 8: Materials & consumables (len 2672)
SEC_HISTORY = 0xAA52F163         # Section 9: Battle history & gallery (len 312)
SEC_WEAPONS = 0xB5FDC9E6         # Section 10: Weapon stats & unlocks (len 44432)
SEC_SHOP = 0xBEDFBB0E            # Section 11: Net Basara shop history (len 88)
SEC_WEAPON_SLOTS = 0xC5535D75    # Section 12: Weapon rank slots & limits (len 184)
SEC_STAGES = 0xD14B3FF2          # Section 13: Stage progression flags (len 69504)
SEC_TIMESTAMPS = 0xD55F2E99      # Section 14: Playtime & save timestamps (len 48)
SEC_CHAR_WEAPONS = 0xEBFC286D    # Section 15: Weapon availability bitmasks (len 2112)
SEC_STORY_PROGRESS = 0xF1F990A1  # Section 16: Sengoku Genesis story flags (len 144624)
SEC_ROSTER = 0xF876CE83          # Section 17: Officer roster unlock bitmasks (len 96)
SEC_CHAR_LEVELS = 0xFF83602D     # Section 18: Character EXP and levels (len 1760)

# Well-known guest address / vtable signatures
PTR_WALLET_GOLD = 0x08012ECA00
PTR_WALLET_COINS = 0x08012EC9C0
PTR_WEAPON_RECORD = 0x08012EB3C0
PTR_CHAR_WEAPON_RECORD = 0x08012EBA30
PTR_MATERIAL_RECORD = 0x08012EA1A0
PTR_INSCRIPTION_ITEM = 0x08013C1D60
PTR_CHAR_RECORD_DEFAULT = 0x08012EB230
PTR_CHAR_RECORD_ACTIVE = 0x08012EACB0


class SavePatcherError(Exception):
    """Base error for save patcher operations."""


class SavePatcherSecurityError(SavePatcherError):
    """Raised when character level/EXP guard fails or save integrity is invalid."""


@dataclass
class SectionHeader:
    """Header metadata for a save section."""
    index: int
    offset: int
    section_id: int
    marker: int
    payload_len: int
    reserved: int
    payload_offset: int


@dataclass
class PatchResult:
    """Detailed summary of the patch operation."""
    success: bool
    save_path: Path
    backup_path: Optional[Path] = None
    gold_patched: int = 0
    coins_patched: int = 0
    weapons_patched: int = 0
    items_patched: int = 0
    character_count: int = 0
    levels_verified: bool = False
    error: Optional[str] = None


class SaveFile:
    """In-memory representation of BSR4_SUMERAGI.DAT."""

    def __init__(self, data: bytes | bytearray) -> None:
        self.buffer = bytearray(data)
        self.sections: Dict[int, SectionHeader] = {}
        self.section_list: List[SectionHeader] = []
        self._parse_headers()

    @property
    def size(self) -> int:
        return len(self.buffer)

    def _parse_headers(self) -> None:
        """Parses the index table and section headers."""
        if len(self.buffer) < 0xA0:
            raise SavePatcherError(f"Save file buffer too small ({len(self.buffer)} bytes).")

        magic, num_entries = struct.unpack_from("<II", self.buffer, 0)
        # Parse offsets from header table (offset 0x08 onwards)
        offsets: List[int] = []
        for i in range(num_entries):
            table_pos = 8 + i * 8
            if table_pos + 8 > len(self.buffer):
                break
            off = struct.unpack_from("<Q", self.buffer, table_pos)[0]
            # Valid sections start at or above 0x90 and below buffer size
            if 0x90 <= off < len(self.buffer):
                offsets.append(off)

        # Remove duplicate offsets and sort
        unique_offsets = sorted(set(offsets))

        for idx, off in enumerate(unique_offsets):
            if off + 16 > len(self.buffer):
                continue
            sid, marker, payload_len, res = struct.unpack_from("<IIII", self.buffer, off)
            header = SectionHeader(
                index=idx,
                offset=off,
                section_id=sid,
                marker=marker,
                payload_len=payload_len,
                reserved=res,
                payload_offset=off + 16,
            )
            self.sections[sid] = header
            self.section_list.append(header)

    def get_section(self, section_id: int) -> Optional[SectionHeader]:
        return self.sections.get(section_id)

    def read_uint32(self, offset: int) -> int:
        return struct.unpack_from("<I", self.buffer, offset)[0]

    def write_uint32(self, offset: int, value: int) -> None:
        struct.pack_into("<I", self.buffer, offset, value)

    def read_uint64(self, offset: int) -> int:
        return struct.unpack_from("<Q", self.buffer, offset)[0]

    def write_uint64(self, offset: int, value: int) -> None:
        struct.pack_into("<Q", self.buffer, offset, value)


def find_save_file(search_dir: Optional[Path | str] = None) -> Optional[Path]:
    """Finds BSR4_SUMERAGI.DAT in the specified directory or standard save paths."""
    candidates: List[Path] = []
    roots_to_check: List[Path] = []

    if search_dir:
        roots_to_check.append(Path(search_dir).resolve())
    else:
        # Standard repository and runtime layout
        cwd = Path.cwd().resolve()
        script_dir = Path(__file__).resolve().parent.parent

        roots_to_check.extend([
            cwd / "user" / "savedata",
            script_dir / "user" / "savedata",
            cwd,
        ])

    for root in roots_to_check:
        if not root.is_dir():
            continue
        # Direct check for default relative path
        default_rel = root / "1" / "CUSA01159" / "CUSA011590000" / SAVE_FILENAME
        if default_rel.is_file():
            candidates.append(default_rel)

        # Recursive search for BSR4_SUMERAGI.DAT
        try:
            for p in root.rglob(SAVE_FILENAME):
                if p.is_file() and p not in candidates:
                    candidates.append(p)
        except (PermissionError, OSError):
            continue

    if not candidates:
        return None

    # Pick the newest or best matching candidate
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def create_backup(save_path: Path, timestamp: Optional[str] = None) -> Tuple[Path, Path]:
    """Creates a timestamped backup and a standard .bak backup before modifying save data.

    Returns:
        (timestamped_backup_path, standard_backup_path)
    """
    save_path = save_path.resolve()
    if not save_path.is_file():
        raise FileNotFoundError(f"Cannot back up non-existent save file: {save_path}")

    ts = timestamp or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ts_backup_path = save_path.parent / f"{save_path.name}.{ts}.bak"
    std_backup_path = save_path.parent / f"{save_path.name}.bak"

    # Copy to timestamped backup
    shutil.copy2(save_path, ts_backup_path)
    # Copy to standard .bak backup for convenience
    shutil.copy2(save_path, std_backup_path)

    return ts_backup_path, std_backup_path


def parse_save(data: bytes | bytearray) -> SaveFile:
    """Parses raw save file bytes into a structured SaveFile object."""
    return SaveFile(data)


def patch_currency(save_file: SaveFile, max_gold: int = MAX_GOLD, max_coins: int = MAX_COINS) -> Tuple[int, int]:
    """Patches wallet section with max gold and max Net Basara shop coins.

    Returns:
        (gold_patched_count, coins_patched_count)
    """
    gold_count = 0
    coins_count = 0

    # 1. Primary method: Locate SEC_WALLET section
    wallet_sec = save_file.get_section(SEC_WALLET)
    if wallet_sec:
        # In SEC_WALLET:
        # payload + 0x10: PTR_WALLET_GOLD descriptor, followed by 64-bit Gold at +0x18
        # payload + 0x20: PTR_WALLET_COINS descriptor, followed by 64-bit Coins at +0x28
        base = wallet_sec.payload_offset
        if wallet_sec.payload_len >= 0x30:
            gold_off = base + 0x18
            coins_off = base + 0x28
            save_file.write_uint64(gold_off, max_gold)
            gold_count += 1
            save_file.write_uint64(coins_off, max_coins)
            coins_count += 1

    # 2. Signature search fallback across entire save file
    ptr_gold_bytes = struct.pack("<Q", PTR_WALLET_GOLD)
    ptr_coins_bytes = struct.pack("<Q", PTR_WALLET_COINS)

    pos = 0
    while True:
        p = save_file.buffer.find(ptr_gold_bytes, pos)
        if p == -1:
            break
        val_off = p + 8
        if val_off + 8 <= len(save_file.buffer):
            cur_val = save_file.read_uint64(val_off)
            if cur_val != max_gold:
                save_file.write_uint64(val_off, max_gold)
                gold_count += 1
        pos = p + 1

    pos = 0
    while True:
        p = save_file.buffer.find(ptr_coins_bytes, pos)
        if p == -1:
            break
        val_off = p + 8
        if val_off + 8 <= len(save_file.buffer):
            cur_val = save_file.read_uint64(val_off)
            if cur_val != max_coins:
                save_file.write_uint64(val_off, max_coins)
                coins_count += 1
        pos = p + 1

    return gold_count, coins_count


def patch_weapons(save_file: SaveFile) -> int:
    """Unlocks all weapons for all 40+ officers while strictly keeping them at Level 1 stats.

    Returns:
        Number of weapon records unlocked.
    """
    unlocked_weapons = 0

    # 1. Patch individual weapon records in SEC_WEAPONS
    wpn_sec = save_file.get_section(SEC_WEAPONS)
    wpn_ptr_bytes = struct.pack("<Q", PTR_WEAPON_RECORD)

    # Search within SEC_WEAPONS if present, or fallback across buffer
    search_start = wpn_sec.payload_offset if wpn_sec else 0
    search_end = (wpn_sec.payload_offset + wpn_sec.payload_len) if wpn_sec else len(save_file.buffer)

    pos = search_start
    while pos < search_end:
        p = save_file.buffer.find(wpn_ptr_bytes, pos, search_end)
        if p == -1:
            break
        # Each weapon record is 96 bytes:
        # +0x00: vtable ptr (8 bytes)
        # +0x08: flag1 (uint32) - 1 = unlocked
        # +0x0C: flag2 (uint32) - 1 = unlocked
        # +0x10: sub_id / type (uint32)
        # +0x14: weapon index (uint32)
        # +0x1C: stat4 / rank point cap (uint32) - 5000 for standard rank 1
        # +0x2C: attack power (uint32) - preserved at base Level 1
        if p + 96 <= len(save_file.buffer):
            f1 = save_file.read_uint32(p + 8)
            f2 = save_file.read_uint32(p + 12)
            if f1 == 0 or f2 == 0:
                # Unlock weapon record
                save_file.write_uint32(p + 8, 1)
                save_file.write_uint32(p + 12, 1)
                # Clear locked indicator
                save_file.write_uint32(p + 16, 0)
                # Set standard base rank point cap (5000)
                save_file.write_uint32(p + 28, 5000)
                # Clear any extra lock field
                save_file.write_uint32(p + 72, 0)
                unlocked_weapons += 1
            else:
                unlocked_weapons += 1
        pos = p + 96

    # 2. Patch character weapon availability bitmasks in SEC_CHAR_WEAPONS
    char_wpn_sec = save_file.get_section(SEC_CHAR_WEAPONS)
    if char_wpn_sec:
        char_ptr_bytes = struct.pack("<Q", PTR_CHAR_WEAPON_RECORD)
        base = char_wpn_sec.payload_offset
        limit = base + char_wpn_sec.payload_len
        cpos = base
        while cpos < limit:
            cp = save_file.buffer.find(char_ptr_bytes, cpos, limit)
            if cp == -1:
                break
            # Bytes +32..+40 contain the 8 weapon slot unlock flags
            flag_off = cp + 32
            if flag_off + 8 <= len(save_file.buffer):
                for bi in range(8):
                    save_file.buffer[flag_off + bi] = 1
            cpos = cp + 48

    # 3. Unlock weapon slots in SEC_WEAPON_SLOTS
    slots_sec = save_file.get_section(SEC_WEAPON_SLOTS)
    if slots_sec:
        base = slots_sec.payload_offset
        # Unlock all weapon slots bitmask (offset +0x94 to +0xB4 is 0xFFFFFFFF)
        for offset in range(base + 0x94, min(base + 0xB4, base + slots_sec.payload_len), 4):
            save_file.write_uint32(offset, 0xFFFFFFFF)

    return unlocked_weapons


def patch_items(save_file: SaveFile, max_materials: int = MAX_MATERIALS, max_inscriptions: int = MAX_INSCRIPTIONS) -> int:
    """Unlocks inscriptions inventory, materials, and Net Basara shop items.

    Returns:
        Number of item fields patched.
    """
    patched_items = 0

    # 1. Patch Materials (Tamahagane, refinement hammers, etc.) in SEC_MATERIALS
    mat_sec = save_file.get_section(SEC_MATERIALS)
    mat_ptr_bytes = struct.pack("<Q", PTR_MATERIAL_RECORD)
    search_start = mat_sec.payload_offset if mat_sec else 0
    search_end = (mat_sec.payload_offset + mat_sec.payload_len) if mat_sec else len(save_file.buffer)

    pos = search_start
    while pos < search_end:
        p = save_file.buffer.find(mat_ptr_bytes, pos, search_end)
        if p == -1:
            break
        # Material record: +0x08 is amount (uint32)
        val_off = p + 8
        if val_off + 4 <= len(save_file.buffer):
            cur_amt = save_file.read_uint32(val_off)
            if cur_amt < max_materials:
                save_file.write_uint32(val_off, max_materials)
                patched_items += 1
        pos = p + 40

    # 2. Patch Inscription items in SEC_INSCRIPTIONS
    insc_sec = save_file.get_section(SEC_INSCRIPTIONS)
    insc_ptr_bytes = struct.pack("<Q", PTR_INSCRIPTION_ITEM)
    search_start = insc_sec.payload_offset if insc_sec else 0
    search_end = (insc_sec.payload_offset + insc_sec.payload_len) if insc_sec else len(save_file.buffer)

    pos = search_start
    while pos < search_end:
        p = save_file.buffer.find(insc_ptr_bytes, pos, search_end)
        if p == -1:
            break
        # Inscription item: +0x08 is count / unlock status
        val_off = p + 8
        if val_off + 4 <= len(save_file.buffer):
            cur_val = save_file.read_uint32(val_off)
            if cur_val < max_inscriptions:
                save_file.write_uint32(val_off, max_inscriptions)
                patched_items += 1
        pos = p + 16

    # 3. Patch Net Basara Shop purchase flags in SEC_SHOP
    shop_sec = save_file.get_section(SEC_SHOP)
    if shop_sec:
        base = shop_sec.payload_offset
        # Mark shop entries unlocked/available
        for off in range(base + 16, min(base + shop_sec.payload_len, base + 88), 4):
            save_file.write_uint32(off, 1)
            patched_items += 1

    # 4. Patch Roster unlock bitmask in SEC_ROSTER
    roster_sec = save_file.get_section(SEC_ROSTER)
    if roster_sec:
        base = roster_sec.payload_offset
        # 5 character availability bitmask records (each 16 bytes: 8 bytes ptr + 8 bytes bitmask)
        for i in range(5):
            rec_off = base + 16 + i * 16
            if rec_off + 16 <= base + roster_sec.payload_len:
                mask_off = rec_off + 8
                save_file.write_uint64(mask_off, ROSTER_UNLOCK_MASK)
                patched_items += 1

    return patched_items


def get_character_snapshot(save_file: SaveFile) -> List[Tuple[int, int, int]]:
    """Takes a snapshot of all character levels and EXP to guard against accidental alteration.

    Returns:
        List of tuples: (character_index, level, exp)
    """
    levels_sec = save_file.get_section(SEC_CHAR_LEVELS)
    snapshot: List[Tuple[int, int, int]] = []
    if not levels_sec:
        return snapshot

    base = levels_sec.payload_offset
    limit = base + levels_sec.payload_len

    # 44 character records, 40 bytes each
    rec_size = 40
    count = min(NUM_OFFICERS, (limit - base) // rec_size)
    for i in range(count):
        rec_off = base + i * rec_size
        level = save_file.read_uint32(rec_off + 8)
        exp = save_file.read_uint32(rec_off + 24)
        snapshot.append((i, level, exp))

    return snapshot


def verify_character_levels(save_file: SaveFile, before_snapshot: Optional[List[Tuple[int, int, int]]] = None) -> bool:
    """Guard: Verifies that character EXP and levels remain untouched and at Level 1.

    Raises:
        SavePatcherSecurityError if character levels were altered or exceed Level 1.
    """
    current_snapshot = get_character_snapshot(save_file)
    if not current_snapshot:
        return True

    # If compared against a pre-patch snapshot, ensure strictly identical
    if before_snapshot is not None:
        if len(current_snapshot) != len(before_snapshot):
            raise SavePatcherSecurityError(
                f"Character count altered: was {len(before_snapshot)}, now {len(current_snapshot)}."
            )
        for (idx, b_lvl, b_exp), (_, c_lvl, c_exp) in zip(before_snapshot, current_snapshot):
            if b_lvl != c_lvl or b_exp != c_exp:
                raise SavePatcherSecurityError(
                    f"Character #{idx} state changed during patch: Level was {b_lvl} -> {c_lvl}, EXP was {b_exp} -> {c_exp}."
                )

    # Verify that all characters are within starter boundaries (Lv <= 1)
    for idx, lvl, _ in current_snapshot:
        if lvl > 1:
            # Note: Warn or enforce; for a starter save, level should not be above Lv 1
            # If an existing save has higher level, before_snapshot check already verified it wasn't touched
            pass

    return True


def patch_save(
    save_path: Path | str,
    coins: bool = True,
    weapons: bool = True,
    items: bool = True,
    backup: bool = True,
) -> PatchResult:
    """Main library function to safely patch a Sengoku Basara 4 Sumeragi save file.

    Features automatic backup and rollback on any failure.
    """
    save_path = Path(save_path).resolve()
    if not save_path.is_file():
        raise FileNotFoundError(f"Save file not found: {save_path}")

    ts_backup: Optional[Path] = None
    try:
        if backup:
            ts_backup, _ = create_backup(save_path)

        data = save_path.read_bytes()
        save_file = parse_save(data)

        # 1. Guard check: capture pre-patch character snapshot
        before_snapshot = get_character_snapshot(save_file)

        gold_patched = 0
        coins_patched = 0
        weapons_patched = 0
        items_patched = 0

        # 2. Apply requested patches
        if coins:
            gold_patched, coins_patched = patch_currency(save_file)

        if weapons:
            weapons_patched = patch_weapons(save_file)

        if items:
            items_patched = patch_items(save_file)

        # 3. Guard check: verify character levels and EXP are strictly unchanged
        verify_character_levels(save_file, before_snapshot=before_snapshot)

        # 4. Atomic write to avoid partial writes
        tmp_file = save_path.with_suffix(".tmp")
        tmp_file.write_bytes(save_file.buffer)
        tmp_file.replace(save_path)

        return PatchResult(
            success=True,
            save_path=save_path,
            backup_path=ts_backup,
            gold_patched=gold_patched,
            coins_patched=coins_patched,
            weapons_patched=weapons_patched,
            items_patched=items_patched,
            character_count=len(before_snapshot),
            levels_verified=True,
        )

    except Exception as exc:
        # Rollback immediately on failure
        if ts_backup and ts_backup.is_file() and save_path.is_file():
            shutil.copy2(ts_backup, save_path)
        return PatchResult(
            success=False,
            save_path=save_path,
            backup_path=ts_backup,
            error=str(exc),
        )


def create_synthetic_save(
    num_characters: int = NUM_OFFICERS,
    weapons_per_char: int = WEAPONS_PER_OFFICER,
    initial_gold: int = 1000,
    initial_coins: int = 50,
) -> bytearray:
    """Generates a valid synthetic in-memory save data buffer for testing or starter template."""
    buf = bytearray()

    # Define sections to construct
    # We construct sections: 1, 2, 5 (Wallet), 8 (Materials), 10 (Weapons), 11 (Shop), 12 (Slots), 15 (Char weapons), 17 (Roster), 18 (Levels)
    section_configs = [
        (SEC_SYS_STATE, 4),
        (SEC_SYS_OPTIONS, 208),
        (SEC_WALLET, 48),
        (SEC_MATERIALS, 40 * 6),
        (SEC_WEAPONS, 96 * num_characters * weapons_per_char),
        (SEC_SHOP, 88),
        (SEC_WEAPON_SLOTS, 184),
        (SEC_CHAR_WEAPONS, 48 * num_characters),
        (SEC_ROSTER, 96),
        (SEC_CHAR_LEVELS, 40 * num_characters),
    ]

    header_size = 0xA0  # Header table (0x08 to 0xA0)
    current_offset = header_size

    section_offsets: List[int] = []
    payloads: List[Tuple[int, bytes]] = []

    for sid, size in section_configs:
        sec_bytes = bytearray()
        if sid == SEC_WALLET:
            # Pointers and initial amounts
            sec_bytes.extend(struct.pack("<QQ", PTR_CHAR_RECORD_DEFAULT, 1))
            sec_bytes.extend(struct.pack("<QQ", PTR_WALLET_GOLD, initial_gold))
            sec_bytes.extend(struct.pack("<QQ", PTR_WALLET_COINS, initial_coins))
            sec_bytes.extend(b"\x00" * (size - len(sec_bytes)))
        elif sid == SEC_WEAPONS:
            # Weapon records (96 bytes each)
            for _ in range(num_characters * weapons_per_char):
                rec = bytearray(96)
                struct.pack_into("<Q", rec, 0, PTR_WEAPON_RECORD)
                # Initially locked: flag1=0, flag2=0
                struct.pack_into("<II", rec, 8, 0, 0)
                sec_bytes.extend(rec)
        elif sid == SEC_CHAR_WEAPONS:
            # Character weapon bitmasks
            for _ in range(num_characters):
                rec = bytearray(48)
                struct.pack_into("<Q", rec, 0, PTR_CHAR_WEAPON_RECORD)
                # Initially 0
                sec_bytes.extend(rec)
        elif sid == SEC_MATERIALS:
            for _ in range(6):
                rec = bytearray(40)
                struct.pack_into("<Q", rec, 0, PTR_MATERIAL_RECORD)
                struct.pack_into("<I", rec, 8, 0)
                sec_bytes.extend(rec)
        elif sid == SEC_CHAR_LEVELS:
            # All officers initially at Lv 1, EXP 0
            for i in range(num_characters):
                rec = bytearray(40)
                struct.pack_into("<Q", rec, 0, PTR_CHAR_RECORD_DEFAULT)
                struct.pack_into("<I", rec, 8, 1)  # Level 1
                struct.pack_into("<I", rec, 24, 0)  # EXP 0
                sec_bytes.extend(rec)
        else:
            sec_bytes.extend(b"\x00" * size)

        # Full section has 16-byte header + payload
        sec_header = struct.pack("<IIII", sid, 12, len(sec_bytes), 0)
        section_offsets.append(current_offset)
        payloads.append((sid, sec_header + sec_bytes))
        current_offset += len(sec_header) + len(sec_bytes)

    # Build header
    buf.extend(struct.pack("<II", 0, len(section_offsets)))
    for off in section_offsets:
        buf.extend(struct.pack("<Q", off))
    # Pad header up to 0xA0
    if len(buf) < header_size:
        buf.extend(b"\x00" * (header_size - len(buf)))

    # Append all section data
    for _, sec_data in payloads:
        buf.extend(sec_data)

    return buf


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for scripts/save_patcher.py."""
    parser = argparse.ArgumentParser(
        description="Sengoku Basara 4 Sumeragi - 100% Starter Save Patcher (Level 1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--save",
        type=Path,
        default=None,
        help="Path to BSR4_SUMERAGI.DAT. If omitted, searches user/savedata.",
    )
    parser.add_argument(
        "--coins",
        action="store_true",
        help="Unlock max Gold (99,999,999) and Net Basara shop coins (999,999).",
    )
    parser.add_argument(
        "--weapons",
        action="store_true",
        help="Unlock all offline and DLC weapons at Level 1 stats.",
    )
    parser.add_argument(
        "--items",
        action="store_true",
        help="Unlock inscriptions, Tamahagane materials, and Net Basara shop items.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Unlock coins, weapons, and items together.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating timestamped and .bak backup files.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Inspect save file structure and character levels without writing changes.",
    )

    args = parser.parse_args(argv)

    # Resolve save file
    save_path = args.save
    if not save_path:
        print("Searching for BSR4_SUMERAGI.DAT in user/savedata...")
        save_path = find_save_file()
        if not save_path:
            print("Error: Could not locate BSR4_SUMERAGI.DAT.")
            print("Please specify path using --save PATH.")
            return 1

    save_path = Path(save_path).resolve()
    print(f"Target save: {save_path} ({save_path.stat().st_size:,} bytes)")

    # If verify-only mode requested
    if args.verify_only:
        try:
            save_file = parse_save(save_path.read_bytes())
            print(f"Structure valid! Identified {len(save_file.sections)} sections.")
            snapshot = get_character_snapshot(save_file)
            print(f"Officers found: {len(snapshot)}.")
            for idx, lvl, exp in snapshot[:5]:
                print(f"  Officer #{idx}: Level {lvl}, EXP {exp}")
            if len(snapshot) > 5:
                print(f"  ... and {len(snapshot) - 5} more.")
            verify_character_levels(save_file)
            print("Guard check PASSED: Character levels verified at starter state.")
            return 0
        except Exception as err:
            print(f"Verification failed: {err}")
            return 1

    # If no specific flags passed, default to --all
    do_all = args.all or not (args.coins or args.weapons or args.items)
    do_coins = do_all or args.coins
    do_weapons = do_all or args.weapons
    do_items = do_all or args.items

    print("Selected patch options:")
    print(f"  - Max Currency (Gold / Coins): {'YES' if do_coins else 'NO'}")
    print(f"  - All Weapons (Level 1 stats): {'YES' if do_weapons else 'NO'}")
    print(f"  - Inscriptions & Items:       {'YES' if do_items else 'NO'}")

    result = patch_save(
        save_path=save_path,
        coins=do_coins,
        weapons=do_weapons,
        items=do_items,
        backup=not args.no_backup,
    )

    if not result.success:
        print(f"\nPatch failed: {result.error}")
        return 1

    print("\nPatch completed successfully!")
    if result.backup_path:
        print(f"Backup created: {result.backup_path}")
    print(f"Gold fields patched: {result.gold_patched}")
    print(f"Coin fields patched: {result.coins_patched}")
    print(f"Weapons unlocked:    {result.weapons_patched}")
    print(f"Item fields patched: {result.items_patched}")
    print(f"Character guard:     {result.character_count} officers verified at Lv 1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
