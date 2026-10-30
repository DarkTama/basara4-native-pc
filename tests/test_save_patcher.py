"""Unit tests for the Sengoku Basara 4 Sumeragi 100% Starter Save Patcher."""

import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

import paths  # noqa: F401  (configures sys.path with scripts/)
import save_patcher
from save_patcher import (
    DEFAULT_SAVE_SIZE,
    MAX_COINS,
    MAX_GOLD,
    MAX_MATERIALS,
    NUM_OFFICERS,
    PTR_CHAR_RECORD_DEFAULT,
    PTR_WALLET_COINS,
    PTR_WALLET_GOLD,
    PTR_WEAPON_RECORD,
    SEC_CHAR_LEVELS,
    SEC_WALLET,
    SEC_WEAPONS,
    PatchResult,
    SaveFile,
    SavePatcherSecurityError,
    create_backup,
    create_synthetic_save,
    find_save_file,
    get_character_snapshot,
    main,
    parse_save,
    patch_currency,
    patch_items,
    patch_save,
    patch_weapons,
    verify_character_levels,
)


class SavePatcherTest(unittest.TestCase):
    """Test suite for save patcher using synthetic save buffers and temporary files."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.save_dir = self.root / "user" / "savedata" / "1" / "CUSA01159" / "CUSA011590000"
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.save_path = self.save_dir / "BSR4_SUMERAGI.DAT"

        # Generate a standard synthetic save
        self.synthetic_bytes = create_synthetic_save(
            num_characters=NUM_OFFICERS,
            initial_gold=12345,
            initial_coins=67,
        )
        self.save_path.write_bytes(self.synthetic_bytes)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_synthetic_save_structure_validity(self) -> None:
        """Verifies that synthetic save has valid headers and recognizable sections."""
        save_file = parse_save(self.synthetic_bytes)
        self.assertGreater(len(save_file.sections), 5)
        self.assertIsNotNone(save_file.get_section(SEC_WALLET))
        self.assertIsNotNone(save_file.get_section(SEC_WEAPONS))
        self.assertIsNotNone(save_file.get_section(SEC_CHAR_LEVELS))

    def test_backup_creation(self) -> None:
        """Verifies timestamped and .bak backup files are correctly created."""
        ts_backup, std_backup = create_backup(self.save_path, timestamp="20261030_120000")
        self.assertTrue(ts_backup.is_file())
        self.assertTrue(std_backup.is_file())
        self.assertEqual(ts_backup.name, "BSR4_SUMERAGI.DAT.20261030_120000.bak")
        self.assertEqual(std_backup.name, "BSR4_SUMERAGI.DAT.bak")
        self.assertEqual(ts_backup.read_bytes(), self.synthetic_bytes)
        self.assertEqual(std_backup.read_bytes(), self.synthetic_bytes)

    def test_currency_patch_verification(self) -> None:
        """Verifies that currency patching sets Gold to 99,999,999 and Coins to 999,999."""
        save_file = parse_save(self.save_path.read_bytes())
        gold_patched, coins_patched = patch_currency(save_file)

        self.assertGreaterEqual(gold_patched, 1)
        self.assertGreaterEqual(coins_patched, 1)

        wallet_sec = save_file.get_section(SEC_WALLET)
        self.assertIsNotNone(wallet_sec)
        base = wallet_sec.payload_offset

        # Check values in wallet payload
        gold_val = save_file.read_uint64(base + 0x18)
        coins_val = save_file.read_uint64(base + 0x28)

        self.assertEqual(gold_val, MAX_GOLD)
        self.assertEqual(coins_val, MAX_COINS)

    def test_weapons_patch_verification(self) -> None:
        """Verifies weapons are unlocked while preserving base Level 1 stats."""
        save_file = parse_save(self.save_path.read_bytes())
        count = patch_weapons(save_file)
        self.assertGreater(count, 0)

        wpn_sec = save_file.get_section(SEC_WEAPONS)
        self.assertIsNotNone(wpn_sec)

        # Inspect first weapon record in SEC_WEAPONS
        ptr = struct.pack("<Q", save_patcher.PTR_WEAPON_RECORD)
        p = save_file.buffer.find(ptr, wpn_sec.payload_offset)
        self.assertNotEqual(p, -1)

        f1 = save_file.read_uint32(p + 8)
        f2 = save_file.read_uint32(p + 12)
        stat4 = save_file.read_uint32(p + 28)

        # Both unlock flags must be set to 1
        self.assertEqual(f1, 1)
        self.assertEqual(f2, 1)
        # Rank point limit set to 5000 (standard level 1)
        self.assertEqual(stat4, 5000)

    def test_items_patch_verification(self) -> None:
        """Verifies materials and item quantities are maxed."""
        save_file = parse_save(self.save_path.read_bytes())
        items_patched = patch_items(save_file)
        self.assertGreater(items_patched, 0)

        # Verify material record
        mat_sec = save_file.get_section(save_patcher.SEC_MATERIALS)
        self.assertIsNotNone(mat_sec)
        mat_ptr = struct.pack("<Q", save_patcher.PTR_MATERIAL_RECORD)
        mp = save_file.buffer.find(mat_ptr, mat_sec.payload_offset)
        self.assertNotEqual(mp, -1)
        amt = save_file.read_uint32(mp + 8)
        self.assertEqual(amt, MAX_MATERIALS)

    def test_guard_character_levels_unchanged(self) -> None:
        """Guardrail test: verifies character EXP and levels are untouched and verified at Lv 1."""
        save_file = parse_save(self.save_path.read_bytes())
        snapshot_before = get_character_snapshot(save_file)
        self.assertEqual(len(snapshot_before), NUM_OFFICERS)

        for _, lvl, exp in snapshot_before:
            self.assertEqual(lvl, 1)
            self.assertEqual(exp, 0)

        # Apply patches
        patch_currency(save_file)
        patch_weapons(save_file)
        patch_items(save_file)

        # Guard must pass
        self.assertTrue(verify_character_levels(save_file, before_snapshot=snapshot_before))

    def test_guard_detects_altered_character_levels(self) -> None:
        """Guardrail test: ensures tampering with character level triggers a security error."""
        save_file = parse_save(self.save_path.read_bytes())
        snapshot_before = get_character_snapshot(save_file)

        levels_sec = save_file.get_section(SEC_CHAR_LEVELS)
        self.assertIsNotNone(levels_sec)
        # Artificially alter character #0 level to 50
        save_file.write_uint32(levels_sec.payload_offset + 8, 50)

        with self.assertRaises(SavePatcherSecurityError):
            verify_character_levels(save_file, before_snapshot=snapshot_before)

    def test_full_patch_save_workflow(self) -> None:
        """End-to-end test of patch_save API with backup and verification."""
        result = patch_save(self.save_path, coins=True, weapons=True, items=True, backup=True)
        self.assertTrue(result.success)
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(result.backup_path.is_file())
        self.assertGreater(result.gold_patched, 0)
        self.assertGreater(result.weapons_patched, 0)
        self.assertTrue(result.levels_verified)

        # Check patched file on disk
        patched = parse_save(self.save_path.read_bytes())
        wallet_sec = patched.get_section(SEC_WALLET)
        self.assertEqual(patched.read_uint64(wallet_sec.payload_offset + 0x18), MAX_GOLD)

    def test_rollback_on_failure(self) -> None:
        """Verifies that an error during patching automatically restores the original file."""
        original_data = self.save_path.read_bytes()

        # Simulate failure by corrupting the save after backup in memory or monkeypatching
        def failing_patch(_sf):
            raise RuntimeError("Simulated failure during write")

        orig_patch = save_patcher.patch_currency
        try:
            save_patcher.patch_currency = failing_patch
            result = patch_save(self.save_path, backup=True)
            self.assertFalse(result.success)
            self.assertIn("Simulated failure", result.error or "")
            # Ensure file content was rolled back to original
            self.assertEqual(self.save_path.read_bytes(), original_data)
        finally:
            save_patcher.patch_currency = orig_patch

    def test_find_save_file(self) -> None:
        """Verifies automatic search finds BSR4_SUMERAGI.DAT in nested paths."""
        found = find_save_file(search_dir=self.root)
        self.assertIsNotNone(found)
        self.assertEqual(found.resolve(), self.save_path.resolve())

    def test_cli_invocation_all(self) -> None:
        """Tests CLI execution with --all flag."""
        rc = main(["--save", str(self.save_path), "--all"])
        self.assertEqual(rc, 0)

        # Re-check patched data
        save_file = parse_save(self.save_path.read_bytes())
        wallet_sec = save_file.get_section(SEC_WALLET)
        self.assertEqual(save_file.read_uint64(wallet_sec.payload_offset + 0x18), MAX_GOLD)

    def test_cli_verify_only(self) -> None:
        """Tests CLI execution with --verify-only flag."""
        rc = main(["--save", str(self.save_path), "--verify-only"])
        self.assertEqual(rc, 0)

    def test_real_save_smoke_if_present(self) -> None:
        """Smoke test on workspace save file if present, running on an isolated copy."""
        real_save = Path("user/savedata/1/CUSA01159/CUSA011590000/BSR4_SUMERAGI.DAT")
        if not real_save.is_file():
            self.skipTest("No local real save file in workspace.")

        isolated_copy = self.root / "REAL_BSR4_SUMERAGI.DAT"
        shutil.copy2(real_save, isolated_copy)

        # Verify parsing and patching
        res = patch_save(isolated_copy, coins=True, weapons=True, items=True, backup=True)
        self.assertTrue(res.success, f"Failed patching real save copy: {res.error}")
        self.assertGreater(res.gold_patched, 0)
        self.assertGreater(res.weapons_patched, 0)
        self.assertTrue(res.levels_verified)


if __name__ == "__main__":
    unittest.main()
