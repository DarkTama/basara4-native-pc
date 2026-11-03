"""Tests for launcher theme system (basara4_theme.py)."""

import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(REPO / "launcher"))

import basara4_theme as theme_mod  # noqa: E402
import basara4_launcher as launcher  # noqa: E402


class TestBasara4Theme(unittest.TestCase):
    def test_token_completeness_for_both_themes(self):
        """Both yoshiteru and hisahide must define all required tokens."""
        required_tokens = {
            "name", "motif",
            "bg_main", "bg_dock", "bg_card", "bg_control", "bg_hover", "border_card",
            "fg_primary", "fg_muted",
            "accent_primary", "accent_secondary",
            "toggle_knob_on", "toggle_track_off", "toggle_track_on",
            "play_bg", "play_hover", "play_border",
            "ok", "fork_accent",
        }
        for th_name in ("yoshiteru", "hisahide"):
            self.assertIn(th_name, theme_mod.THEMES)
            tokens = theme_mod.THEMES[th_name]
            for tok in required_tokens:
                self.assertIn(tok, tokens, f"Token '{tok}' missing in theme '{th_name}'")
                self.assertTrue(isinstance(tokens[tok], str) and tokens[tok])

        # Test specific token values from brief
        y = theme_mod.THEMES["yoshiteru"]
        self.assertEqual(y["bg_main"], "#0D0A14")
        self.assertEqual(y["accent_primary"], "#E8B949")
        self.assertEqual(y["accent_secondary"], "#9D4EDD")
        self.assertEqual(y["toggle_track_on"], y["accent_secondary"])

        h = theme_mod.THEMES["hisahide"]
        self.assertEqual(h["bg_main"], "#0B0E14")
        self.assertEqual(h["accent_primary"], "#4DA6FF")
        self.assertEqual(h["accent_secondary"], "#E09F3E")
        self.assertEqual(h["toggle_track_on"], h["accent_primary"])

    def test_get_theme_fallback(self):
        """get_theme should fall back to yoshiteru if invalid or None."""
        self.assertEqual(theme_mod.get_theme("yoshiteru")["name"], "Ashikaga Yoshiteru")
        self.assertEqual(theme_mod.get_theme("hisahide")["name"], "Matsunaga Hisahide")
        self.assertEqual(theme_mod.get_theme("nonexistent")["name"], "Ashikaga Yoshiteru")
        self.assertEqual(theme_mod.get_theme(None)["name"], "Ashikaga Yoshiteru")

    def test_config_default_and_migration(self):
        """Verifies that DEFAULTS includes 'launcher_theme'='yoshiteru' and loads correctly."""
        self.assertIn("launcher_theme", launcher.DEFAULTS)
        self.assertEqual(launcher.DEFAULTS["launcher_theme"], "yoshiteru")

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "launcher.json"
            orig_cfg = launcher.CONFIG
            try:
                launcher.CONFIG = cfg_file
                # 1. Fresh file without theme defaults to yoshiteru
                cfg_file.write_text(json.dumps({"language": "en"}), encoding="utf-8")
                loaded = launcher.load_config()
                self.assertEqual(loaded.get("launcher_theme"), "yoshiteru")

                # 2. Existing valid hisahide persists
                cfg_file.write_text(json.dumps({"launcher_theme": "hisahide"}), encoding="utf-8")
                loaded = launcher.load_config()
                self.assertEqual(loaded.get("launcher_theme"), "hisahide")

                # 3. Invalid theme value migrates/falls back to yoshiteru
                cfg_file.write_text(json.dumps({"launcher_theme": "invalid_theme"}), encoding="utf-8")
                loaded = launcher.load_config()
                self.assertEqual(loaded.get("launcher_theme"), "yoshiteru")
            finally:
                launcher.CONFIG = orig_cfg

    def test_theme_switch_applies(self):
        """Test ThemeRegistry widget update and callback execution on live switch."""
        reg = theme_mod.ThemeRegistry()

        class MockWidget:
            def __init__(self):
                self.attrs = {}
            def configure(self, **kwargs):
                self.attrs.update(kwargs)
            def winfo_exists(self):
                return True

        w = MockWidget()
        reg.register(w, bg="bg_card", fg="fg_primary")
        self.assertEqual(w.attrs["bg"], theme_mod.THEMES["yoshiteru"]["bg_card"])
        self.assertEqual(w.attrs["fg"], theme_mod.THEMES["yoshiteru"]["fg_primary"])

        # Switch to hisahide
        reg.apply_theme("hisahide")
        self.assertEqual(w.attrs["bg"], theme_mod.THEMES["hisahide"]["bg_card"])
        self.assertEqual(w.attrs["fg"], theme_mod.THEMES["hisahide"]["fg_primary"])

        # Callback test
        received = []
        reg.register_callback(lambda t: received.append(t["name"]))
        self.assertEqual(received, ["Matsunaga Hisahide"])

        reg.apply_theme("yoshiteru")
        self.assertEqual(received[-1], "Ashikaga Yoshiteru")
        self.assertEqual(w.attrs["bg"], theme_mod.THEMES["yoshiteru"]["bg_card"])


if __name__ == "__main__":
    unittest.main()
