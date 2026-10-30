"""Unit tests for tools/ai_translate_gmd.py.

Verifies:
1. Cache load/save, deduplication, and resume filtering.
2. Batch chunking and control code extraction/validation.
3. Mismatched GMD special handling and index calculation.
4. Mock patching of GMD string tables and ARC containers using cache entries.
5. Translation DB updating with pool indexing.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

try:
    from paths import ROOT
except ImportError:
    from tests.paths import ROOT

if str(ROOT / "launcher") not in sys.path:
    sys.path.insert(0, str(ROOT / "launcher"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from gmdarc import ArcFile, GmdFile
from test_translation import make_synthetic_gmd, make_synthetic_arc
from ai_translate_gmd import (
    MISMATCHED_TARGET_KEYWORDS,
    apply_cache_to_arc,
    apply_cache_to_gmd,
    apply_cache_to_string_list,
    chunk_strings,
    create_batches,
    deduplicate_strings,
    export_batches,
    extract_control_codes,
    extract_mismatched_report_strings,
    filter_pending_strings,
    get_cache_stats,
    get_mismatched_index_map_from_report,
    import_batch_translation,
    is_mismatched_target,
    load_cache,
    save_cache,
    update_translation_db,
    validate_control_codes,
)


class TestAiTranslateCache(unittest.TestCase):
    """Tests for Cache Architecture and Resume Filtering."""

    def test_cache_load_save(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "test_cache.json"

            # Loading non-existent cache should return empty dict
            self.assertEqual(load_cache(cache_file), {})

            # Save sample cache
            sample = {
                "合戦開始": "Battle Start",
                "難易度": "Difficulty",
                "勝利条件": "Victory Condition",
            }
            save_cache(sample, cache_file)
            self.assertTrue(cache_file.is_file())

            # Load back and verify
            loaded = load_cache(cache_file)
            self.assertEqual(loaded, sample)

    def test_deduplicate_strings(self):
        inputs = ["合戦開始", "難易度", "合戦開始", "", "勝利条件", "難易度", "  "]
        result = deduplicate_strings(inputs)
        # Empty string filtered out, first-seen order preserved, duplicates removed
        self.assertEqual(result, ["合戦開始", "難易度", "勝利条件", "  "])

    def test_filter_pending_strings_resume_capability(self):
        strings = ["String A", "String B", "String C", "String D"]
        cache = {
            "String A": "Translation A",
            "String B": "",  # Empty translation should NOT count as completed
        }
        pending = filter_pending_strings(strings, cache)
        # String A is completed -> excluded
        # String B is empty -> pending
        # String C, D not in cache -> pending
        self.assertEqual(pending, ["String B", "String C", "String D"])

    def test_get_cache_stats(self):
        cache = {
            "Str 1": "Trans 1",
            "Str 2": "Trans 2",
            "Str 3": "",
        }
        targets = ["Str 1", "Str 2", "Str 3", "Str 4"]
        stats = get_cache_stats(cache, targets)

        self.assertEqual(stats["cached_entries"], 3)
        self.assertEqual(stats["valid_translations"], 2)
        self.assertEqual(stats["empty_entries"], 1)
        self.assertEqual(stats["total_target_strings"], 4)
        self.assertEqual(stats["pending_strings"], 2)  # Str 3 and Str 4
        self.assertEqual(stats["completed_target_strings"], 2)
        self.assertEqual(stats["coverage_percent"], 50.0)


class TestAiTranslateBatching(unittest.TestCase):
    """Tests for Batch chunking and control code preservation."""

    def test_chunk_strings(self):
        items = [f"Item {i}" for i in range(125)]
        chunks = chunk_strings(items, batch_size=50)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(len(chunks[0]), 50)
        self.assertEqual(len(chunks[1]), 50)
        self.assertEqual(len(chunks[2]), 25)

    def test_chunk_strings_invalid_batch_size(self):
        with self.assertRaises(ValueError):
            chunk_strings(["a", "b"], batch_size=0)

    def test_extract_control_codes(self):
        text = (
            "(略)享楽の祭典号\r\n"
            "<SIZE 13>（きょうらくのさいてんごう）</SIZE>\r\n"
            "獲得戦功: <COL FFBF40FF>%02d</COL> pts (計: %d)\n"
            "ボーナス: %s%%"
        )
        codes = extract_control_codes(text)

        expected_tags = ["<SIZE 13>", "</SIZE>", "<COL FFBF40FF>", "</COL>"]
        self.assertEqual(codes["tags"], expected_tags)

        # Expected format specifiers: %02d, %d, %s, %%
        expected_formats = ["%02d", "%d", "%s", "%%"]
        self.assertEqual(codes["formats"], expected_formats)

        self.assertEqual(codes["newline_count"], 3)
        self.assertEqual(codes["newlines"], ["\r\n", "\r\n", "\n"])

    def test_validate_control_codes_valid(self):
        src = "報酬: <COL FF0000>%d</COL>両\r\nプレイヤー: %s"
        dst = "Reward: <COL FF0000>%d</COL> Ryo\r\nPlayer: %s"

        is_valid, errors = validate_control_codes(src, dst)
        self.assertTrue(is_valid)
        self.assertEqual(errors, [])

    def test_validate_control_codes_mismatched_tags(self):
        src = "Lv.<SIZE 15>%d</SIZE>"
        dst = "Lv.<SIZE 14>%d</SIZE>"  # Size altered

        is_valid, errors = validate_control_codes(src, dst)
        self.assertFalse(is_valid)
        self.assertTrue(any("Tags mismatch" in e for e in errors))

    def test_validate_control_codes_mismatched_formats(self):
        src = "Hit Count: %02d (Max: %d)"
        dst = "Hit Count: %d (Max: %d)"  # %02d altered to %d

        is_valid, errors = validate_control_codes(src, dst)
        self.assertFalse(is_valid)
        self.assertTrue(any("Format specifiers mismatch" in e for e in errors))

    def test_validate_control_codes_mismatched_newlines(self):
        src = "Line 1\r\nLine 2\r\nLine 3"
        dst = "Line 1 Line 2\r\nLine 3"  # One newline dropped

        is_valid, errors = validate_control_codes(src, dst)
        self.assertFalse(is_valid)
        self.assertTrue(any("Newline count mismatch" in e for e in errors))

    def test_create_and_export_batches(self):
        sample_strings = [
            "撃破数: %d",
            "武将: <COL FF0000>%s</COL>",
            "合戦準備\r\n出陣する",
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_files = export_batches(sample_strings, tmp_dir, batch_size=2)
            self.assertEqual(len(out_files), 2)  # 2 in batch 0, 1 in batch 1

            with open(out_files[0], "r", encoding="utf-8") as f:
                b0 = json.load(f)

            self.assertEqual(b0["batch_index"], 0)
            self.assertEqual(b0["total_batches"], 2)
            self.assertEqual(len(b0["items"]), 2)
            self.assertEqual(b0["items"][0]["src"], "撃破数: %d")
            self.assertEqual(b0["items"][0]["control_codes"]["formats"], ["%d"])
            self.assertIn("system_instruction", b0)

    def test_import_batch_translation(self):
        cache: dict[str, str] = {}
        source_batch = {
            "items": [
                {"id": 0, "src": "撃破数: %d"},
                {"id": 1, "src": "合戦開始"},
            ]
        }
        # Format 1: id map
        translated_payload = {
            "translations": {
                "0": "Defeats: %d",
                "1": "Battle Start",
            }
        }
        imported, warnings = import_batch_translation(
            translated_payload,
            cache,
            source_batch=source_batch,
        )
        self.assertEqual(imported, 2)
        self.assertEqual(cache["撃破数: %d"], "Defeats: %d")
        self.assertEqual(cache["合戦開始"], "Battle Start")
        self.assertEqual(len(warnings), 0)

        # Format 2: direct jp -> en dict with format warning
        bad_payload = {
            "撃破数: %d": "Defeats: %s",  # bad format specifier
        }
        imported_bad, bad_warnings = import_batch_translation(bad_payload, cache, validate=True)
        self.assertEqual(imported_bad, 1)
        self.assertEqual(len(bad_warnings), 1)


class TestAiTranslateMismatchedGmd(unittest.TestCase):
    """Tests for Mismatched GMD Special Handling."""

    def test_is_mismatched_target(self):
        for kw in MISMATCHED_TARGET_KEYWORDS:
            entry = f"ui/msg/{kw}_jpn"
            self.assertTrue(is_mismatched_target(entry))
            # Test backslash path
            entry_win = f"ui\\msg\\{kw}_jpn"
            self.assertTrue(is_mismatched_target(entry_win))

        self.assertFalse(is_mismatched_target("ui/msg/msg_other_list_jpn"))
        self.assertFalse(is_mismatched_target("system/common.gmd"))

    def test_extract_mismatched_report_strings(self):
        mock_report = [
            {
                "arc": "ui/pause.arc",
                "entry": "ui\\msg\\msg_tact_achieve_jpn",
                "ps4_key_count": 1,
                "unkeyed_strings": {
                    "ps4": ["戦功1達成", "戦功2達成", "Already English"],
                },
            },
            {
                "arc": "ui/config.arc",
                "entry": "ui\\msg\\msg_config_jpn",  # Not a mismatched target
                "unkeyed_strings": {"ps4": ["設定項目"]},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            report_file = Path(tmp_dir) / "report.json"
            report_file.write_text(json.dumps(mock_report), encoding="utf-8")

            extracted = extract_mismatched_report_strings(report_file)
            # Only strings from msg_tact_achieve_jpn, and "Already English" should be excluded
            self.assertEqual(extracted, ["戦功1達成", "戦功2達成"])

    def test_get_mismatched_index_map_from_report(self):
        mock_report = [
            {
                "arc": "ui/pause.arc",
                "entry": "ui\\msg\\msg_bonus_jpn",
                "ps4_key_count": 5,
                "unkeyed_strings": {
                    "ps4": ["ボーナスA", "ボーナスB", "ボーナスC"],
                },
            }
        ]
        cache = {
            "ボーナスA": "Bonus A",
            "ボーナスC": "Bonus C",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            report_file = Path(tmp_dir) / "report.json"
            report_file.write_text(json.dumps(mock_report), encoding="utf-8")

            index_maps = get_mismatched_index_map_from_report(report_file, cache)
            self.assertIn("ui/pause.arc", index_maps)
            entry_map = index_maps["ui/pause.arc"]["ui\\msg\\msg_bonus_jpn"]

            # Key count is 5, so string 0 is index 5, string 2 is index 7
            self.assertEqual(entry_map[5], "Bonus A")
            self.assertEqual(entry_map[7], "Bonus C")
            self.assertNotIn(6, entry_map)  # ボーナスB not in cache


class TestAiTranslateApplyEngine(unittest.TestCase):
    """Tests for GMD and Translation DB Patching."""

    def test_apply_cache_to_string_list(self):
        strings = ["合戦開始", "難易度設定", "出陣", "終了"]
        cache = {
            "合戦開始": "Battle Start",
            "出陣": "Deploy",
        }
        index_map = {
            1: "Custom Difficulty",
        }

        replaced, new_strings = apply_cache_to_string_list(
            strings,
            cache,
            index_map=index_map,
        )

        self.assertEqual(replaced, 3)
        self.assertEqual(new_strings[0], "Battle Start")
        self.assertEqual(new_strings[1], "Custom Difficulty")
        self.assertEqual(new_strings[2], "Deploy")
        self.assertEqual(new_strings[3], "終了")  # untouched

    def test_apply_cache_to_gmd(self):
        keys = ["KEY_START", "KEY_EXIT"]
        original_strings = ["合戦開始", "ゲーム終了"]
        raw_gmd = make_synthetic_gmd("msg_sample", keys, original_strings, endian="<")
        gmd = GmdFile.from_bytes(raw_gmd)

        cache = {
            "合戦開始": "Battle Start",
            "ゲーム終了": "Quit Game",
        }

        replaced, updated_gmd = apply_cache_to_gmd(gmd, cache)
        self.assertEqual(replaced, 2)
        self.assertEqual(updated_gmd.strings, ["Battle Start", "Quit Game"])
        self.assertEqual(updated_gmd.keys, keys)

        # Re-parse serialized binary to ensure binary integrity
        reparsed = GmdFile.from_bytes(updated_gmd.to_bytes())
        self.assertEqual(reparsed.strings, ["Battle Start", "Quit Game"])
        self.assertEqual(reparsed.keys, keys)

    def test_apply_cache_to_arc(self):
        # Entry 1: target mismatched GMD
        gmd1_bytes = make_synthetic_gmd(
            "msg_tact_achieve_jpn",
            ["K1"],
            ["敵将を撃破せよ", "Unkeyed Text"],
            endian="<",
        )
        # Entry 2: non-target GMD
        gmd2_bytes = make_synthetic_gmd(
            "msg_other_jpn",
            [],
            ["その他情報"],
            endian="<",
        )

        arc_bytes = make_synthetic_arc([
            ("ui/msg/msg_tact_achieve_jpn", 0x242BB29A, gmd1_bytes, len(gmd1_bytes)),
            ("ui/msg/msg_other_jpn", 0x242BB29A, gmd2_bytes, len(gmd2_bytes)),
        ])

        arc = ArcFile.from_bytes(arc_bytes)
        cache = {
            "敵将を撃破せよ": "Defeat the enemy general",
            "Unkeyed Text": "Tactical Achievement",
            "その他情報": "Other Information",
        }

        # Apply only to mismatched targets
        gmds_mod, strings_rep = apply_cache_to_arc(
            arc,
            cache,
            target_filter=is_mismatched_target,
        )

        self.assertEqual(gmds_mod, 1)
        self.assertEqual(strings_rep, 2)

        # Verify entry 1 modified
        e1 = arc.get_entry("ui/msg/msg_tact_achieve_jpn")
        self.assertIsNotNone(e1)
        g1 = GmdFile.from_bytes(e1.decompress())
        self.assertEqual(g1.strings[0], "Defeat the enemy general")
        self.assertEqual(g1.strings[1], "Tactical Achievement")

        # Verify entry 2 untouched
        e2 = arc.get_entry("ui/msg/msg_other_jpn")
        self.assertIsNotNone(e2)
        g2 = GmdFile.from_bytes(e2.decompress())
        self.assertEqual(g2.strings[0], "その他情報")

    def test_update_translation_db(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "en_translation_db.json"
            report_path = Path(tmp_dir) / "report.json"

            initial_db = {
                "meta": {"format": 1},
                "pool": ["Existing String 0", "Existing String 1"],
                "archives": {
                    "game_common.arc": {
                        "ui\\msg\\msg_mode_jpn": {"by_index": {"0": 0}}
                    }
                },
            }
            db_path.write_text(json.dumps(initial_db), encoding="utf-8")

            report_data = [
                {
                    "arc": "ui/battle_prepare.arc",
                    "entry": "ui\\msg\\msg_battle_prepare_jpn",
                    "ps4_key_count": 2,
                    "unkeyed_strings": {
                        "ps4": ["出陣前の準備", "武器換装"],
                    },
                }
            ]
            report_path.write_text(json.dumps(report_data), encoding="utf-8")

            cache = {
                "出陣前の準備": "Pre-Battle Preparation",
                "武器換装": "Weapon Equipment Change",
            }

            stats = update_translation_db(
                db_path=db_path,
                cache=cache,
                report_path=report_path,
            )

            self.assertEqual(stats["strings_added_to_db"], 2)
            self.assertEqual(stats["archives_affected"], 1)

            with open(db_path, "r", encoding="utf-8") as f:
                updated_db = json.load(f)

            pool = updated_db["pool"]
            self.assertIn("Pre-Battle Preparation", pool)
            self.assertIn("Weapon Equipment Change", pool)

            prep_idx = pool.index("Pre-Battle Preparation")
            equip_idx = pool.index("Weapon Equipment Change")

            by_idx = updated_db["archives"]["ui/battle_prepare.arc"]["ui\\msg\\msg_battle_prepare_jpn"]["by_index"]
            # ps4_key_count was 2, so unkeyed strings are indices 2 and 3
            self.assertEqual(by_idx["2"], prep_idx)
            self.assertEqual(by_idx["3"], equip_idx)


if __name__ == "__main__":
    unittest.main()
