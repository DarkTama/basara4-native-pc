"""Launcher layout B: string tables, fork URLs, cheat keys and the hint wiring."""
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "launcher"))

import basara4_launcher as L  # noqa: E402


class Strings(unittest.TestCase):
    def test_fork_urls(self):
        self.assertEqual(L.GITHUB_REPO, "DarkTama/basara4-native-pc")
        for path in (REPO / "launcher").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("razzconnect", text, path.name)
            self.assertNotIn("discord.gg", text, path.name)

    def test_every_page_and_row_has_a_one_line_hint(self):
        for key, _ in L.NAV:
            self.assertIn(key, L.PAGE_HINT)
        for text in list(L.PAGE_HINT.values()) + [hint for _, hint in L.ROW.values()]:
            self.assertLessEqual(len(text), 90, text)  # the hint bar is one line

    def test_cheat_keys_are_the_ones_the_runtime_reads(self):
        cpp = (REPO / "gpu" / "shim" / "spartanport_settings.cpp").read_text(encoding="utf-8")
        for key in L.CHEAT_KEYS:
            self.assertIn(f'"{key}"', cpp)
            self.assertIn(key, L.ROW)

    def test_core_command_line_has_cheats(self):
        self.assertIn('"--no-game-hooks", "--cheats"', (REPO / "launcher" / "basara4_launcher.py").read_text(
            encoding="utf-8"))


class Window(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="b4_launcher_ui_"))
        for name, value in (("USER", self.tmp), ("CONFIG", self.tmp / "launcher.json"),
                            ("INI", self.tmp / "basara4.ini"), ("OLD_INI", self.tmp / "spartanport.ini")):
            patcher = mock.patch.object(L, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            self.app = L.Launcher()
        except Exception as exc:  # no display
            self.skipTest(f"no Tk window: {exc}")
        self.addCleanup(self._close)
        self.app.update()

    def _close(self):
        for job in self.app.tk.splitlist(self.app.tk.call("after", "info")):
            self.app.after_cancel(job)  # the poll and update-check timers
        self.app.destroy()

    def test_cheats_are_off_by_default_and_round_trip_through_the_ini(self):
        self.assertFalse(any(var.get() for var in self.app.cheat_vars.values()))
        self.app.cheat_vars["cheat_coins"].set(True)
        self.app._collect()
        ini = L.read_ini(L.INI)
        self.assertEqual({k: ini[k] for k in L.CHEAT_KEYS},
                         {k: "1" if k == "cheat_coins" else "0" for k in L.CHEAT_KEYS})
        L.write_ini(L.INI, {"cheat_gold": 1, "cheat_coins": 0})  # what the in-game menu does
        self.app._load_ini_into_ui()
        self.assertTrue(self.app.cheat_vars["cheat_gold"].get())
        self.assertFalse(self.app.cheat_vars["cheat_coins"].get())

    def test_hint_follows_enter_and_focus(self):
        self.app.show_page("display")
        self.app.update()
        rows = self.app.pages["display"].winfo_children()  # title, mode, gpu, resolution, fps, ...
        box = next(w for w in _walk(rows[4]) if w.winfo_class() == "TCombobox")
        box.event_generate("<Enter>")
        self.assertEqual(self.app.hint.cget("text"), L.ROW["fps"][1])
        self.app.show_page("tools")
        self.assertEqual(self.app.hint.cget("text"), L.PAGE_HINT["tools"])
        self.app.show_page("display")
        box.event_generate("<FocusIn>")
        self.assertEqual(self.app.hint.cget("text"), L.ROW["fps"][1])


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


if __name__ == "__main__":
    unittest.main()
