"""Unit tests for launcher gamepad & keyboard navigation pure logic."""

import sys
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "launcher"))

import basara4_nav as nav  # noqa: E402


class TestButtonBitmasks(unittest.TestCase):
    def test_empty_mask(self):
        decoded = nav.decode_xinput_buttons(0)
        self.assertFalse(any(decoded.values()))

    def test_single_buttons(self):
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_DPAD_UP)["up"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_DPAD_DOWN)["down"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_DPAD_LEFT)["left"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_DPAD_RIGHT)["right"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_START)["start"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_BACK)["back"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_LEFT_SHOULDER)["lb"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_RIGHT_SHOULDER)["rb"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_A)["a"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_B)["b"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_X)["x"])
        self.assertTrue(nav.decode_xinput_buttons(nav.XINPUT_GAMEPAD_Y)["y"])

    def test_button_combinations(self):
        mask = (
            nav.XINPUT_GAMEPAD_A
            | nav.XINPUT_GAMEPAD_DPAD_UP
            | nav.XINPUT_GAMEPAD_RIGHT_SHOULDER
        )
        decoded = nav.decode_xinput_buttons(mask)
        self.assertTrue(decoded["a"])
        self.assertTrue(decoded["up"])
        self.assertTrue(decoded["rb"])
        self.assertFalse(decoded["b"])
        self.assertFalse(decoded["down"])
        self.assertFalse(decoded["lb"])


class TestStickDeadzone(unittest.TestCase):
    def test_inside_deadzone(self):
        # Coordinates inside deadzone
        up, down, left, right = nav.stick_to_directions(0, 0, deadzone=7849)
        self.assertFalse(any([up, down, left, right]))

        up, down, left, right = nav.stick_to_directions(5000, -6000, deadzone=7849)
        self.assertFalse(any([up, down, left, right]))

        up, down, left, right = nav.stick_to_directions(-7849, 7849, deadzone=7849)
        self.assertFalse(any([up, down, left, right]))

    def test_pure_directions(self):
        up, down, left, right = nav.stick_to_directions(0, 20000, deadzone=7849)
        self.assertEqual((up, down, left, right), (True, False, False, False))

        up, down, left, right = nav.stick_to_directions(0, -20000, deadzone=7849)
        self.assertEqual((up, down, left, right), (False, True, False, False))

        up, down, left, right = nav.stick_to_directions(-20000, 0, deadzone=7849)
        self.assertEqual((up, down, left, right), (False, False, True, False))

        up, down, left, right = nav.stick_to_directions(20000, 0, deadzone=7849)
        self.assertEqual((up, down, left, right), (False, False, False, True))

    def test_dominant_axis(self):
        # Slightly angled up-right, but Y is dominant
        up, down, left, right = nav.stick_to_directions(10000, 25000, deadzone=7849)
        self.assertEqual((up, down, left, right), (True, False, False, False))

        # Slightly angled up-right, but X is dominant
        up, down, left, right = nav.stick_to_directions(25000, 10000, deadzone=7849)
        self.assertEqual((up, down, left, right), (False, False, False, True))


class TestKeyRepeatTiming(unittest.TestCase):
    def test_initial_press_and_repeats(self):
        tracker = nav.KeyRepeatTracker(initial_delay_s=0.350, repeat_delay_s=0.120)

        # First press at t=1.0: triggers immediately
        self.assertTrue(tracker.update("down", True, 1.000))

        # Held before 350ms initial delay: does not trigger
        self.assertFalse(tracker.update("down", True, 1.100))
        self.assertFalse(tracker.update("down", True, 1.349))

        # Held at t=1.350 (1.000 + 0.350): triggers first repeat
        self.assertTrue(tracker.update("down", True, 1.350))

        # Held before repeat delay (0.120s): does not trigger
        self.assertFalse(tracker.update("down", True, 1.400))

        # Held at t=1.470 (1.350 + 0.120): triggers second repeat
        self.assertTrue(tracker.update("down", True, 1.470))

    def test_release_and_repress(self):
        tracker = nav.KeyRepeatTracker(initial_delay_s=0.350, repeat_delay_s=0.120)

        self.assertTrue(tracker.update("up", True, 0.0))
        self.assertFalse(tracker.update("up", True, 0.1))

        # Released at t=0.2
        self.assertFalse(tracker.update("up", False, 0.2))

        # Re-pressed at t=0.25: triggers immediately because key was released
        self.assertTrue(tracker.update("up", True, 0.25))

    def test_multiple_keys_independent(self):
        tracker = nav.KeyRepeatTracker(initial_delay_s=0.350, repeat_delay_s=0.120)

        self.assertTrue(tracker.update("left", True, 0.0))
        self.assertTrue(tracker.update("down", True, 0.1))

        # "left" repeats at 0.350
        self.assertTrue(tracker.update("left", True, 0.350))
        # "down" should NOT repeat yet at 0.350 (needs 0.1 + 0.350 = 0.450)
        self.assertFalse(tracker.update("down", True, 0.350))
        self.assertTrue(tracker.update("down", True, 0.450))


class TestButtonHints(unittest.TestCase):
    def test_english_hints(self):
        pad_hint = nav.get_button_hints("pad", "en")
        self.assertIn("[A]", pad_hint)
        self.assertIn("[B]", pad_hint)
        self.assertIn("[LB/RB]", pad_hint)
        self.assertIn("[Start]", pad_hint)
        self.assertIn("Select", pad_hint)
        self.assertIn("Play", pad_hint)

        kbd_hint = nav.get_button_hints("keyboard", "en")
        self.assertIn("Enter", kbd_hint)
        self.assertIn("Esc", kbd_hint)
        self.assertIn("Q/E", kbd_hint)
        self.assertIn("F5", kbd_hint)
        self.assertIn("Select", kbd_hint)

    def test_indonesian_hints(self):
        pad_hint = nav.get_button_hints("pad", "id")
        self.assertIn("[A]", pad_hint)
        self.assertIn("Pilih", pad_hint)
        self.assertIn("Kembali", pad_hint)
        self.assertIn("Halaman", pad_hint)
        self.assertIn("Main", pad_hint)

        kbd_hint = nav.get_button_hints("keyboard", "id")
        self.assertIn("Enter", kbd_hint)
        self.assertIn("Pilih", kbd_hint)
        self.assertIn("Kembali", kbd_hint)
        self.assertIn("Halaman", kbd_hint)
        self.assertIn("Main", kbd_hint)

    def test_dialog_hints(self):
        self.assertIn("Confirm", nav.get_dialog_hints("pad", "en"))
        self.assertIn("Konfirmasi", nav.get_dialog_hints("pad", "id"))
        self.assertIn("Esc", nav.get_dialog_hints("keyboard", "en"))
        self.assertIn("Batal", nav.get_dialog_hints("keyboard", "id"))


class TestFocusOrderComputation(unittest.TestCase):
    def test_row_navigation_clamping(self):
        total_rows = 5
        # Move down from row 0
        self.assertEqual(nav.compute_next_row_index(0, total_rows, +1), 1)
        # Move down from row 4 (bottom) -> clamped at 4
        self.assertEqual(nav.compute_next_row_index(4, total_rows, +1), 4)
        # Move up from row 0 (top) -> clamped at 0
        self.assertEqual(nav.compute_next_row_index(0, total_rows, -1), 0)

    def test_column_navigation(self):
        row_len = 3
        # Move right from col 0
        self.assertEqual(nav.compute_next_column_index(0, row_len, +1), 1)
        # Move right from col 2 -> clamped at 2
        self.assertEqual(nav.compute_next_column_index(2, row_len, +1), 2)
        # Move left from col 0 -> clamped at 0
        self.assertEqual(nav.compute_next_column_index(0, row_len, -1), 0)

    def test_closest_column(self):
        # Target row has 1 column, preferred was column 2
        self.assertEqual(nav.find_closest_column(1, preferred_col=2), 0)
        # Target row has 4 columns, preferred was column 2
        self.assertEqual(nav.find_closest_column(4, preferred_col=2), 2)


if __name__ == "__main__":
    unittest.main()
