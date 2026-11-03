"""Gamepad and keyboard navigation pure logic and XInput ctypes binding for Basara 4 PC launcher."""

import ctypes
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# XInput button bitmasks
XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_DPAD_DOWN = 0x0002
XINPUT_GAMEPAD_DPAD_LEFT = 0x0004
XINPUT_GAMEPAD_DPAD_RIGHT = 0x0008
XINPUT_GAMEPAD_START = 0x0010
XINPUT_GAMEPAD_BACK = 0x0020
XINPUT_GAMEPAD_LEFT_THUMB = 0x0040
XINPUT_GAMEPAD_RIGHT_THUMB = 0x0080
XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100  # LB
XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200  # RB
XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
XINPUT_GAMEPAD_X = 0x4000
XINPUT_GAMEPAD_Y = 0x8000

DEFAULT_DEADZONE = 7849

BUTTON_HINTS_EN = {
    "pad": "[A] Select   [B] Back   [LB/RB] Page   [Start] Play",
    "keyboard": "Enter Select   Esc Back   Q/E Page   F5 Play",
}

BUTTON_HINTS_ID = {
    "pad": "[A] Pilih   [B] Kembali   [LB/RB] Halaman   [Start] Main",
    "keyboard": "Enter Pilih   Esc Kembali   Q/E Halaman   F5 Main",
}

DIALOG_HINTS_EN = {
    "pad": "[A] Confirm   [B] Cancel   [Left/Right] Choose",
    "keyboard": "Enter Confirm   Esc Cancel   Left/Right Choose",
}

DIALOG_HINTS_ID = {
    "pad": "[A] Konfirmasi   [B] Batal   [Kiri/Kanan] Pilih",
    "keyboard": "Enter Konfirmasi   Esc Batal   Kiri/Kanan Pilih",
}


def get_button_hints(device: str = "keyboard", lang: str = "en") -> str:
    """Returns the button hints string for the given input device and language."""
    hints = BUTTON_HINTS_ID if lang == "id" else BUTTON_HINTS_EN
    return hints.get(device, hints["keyboard"])


def get_dialog_hints(device: str = "keyboard", lang: str = "en") -> str:
    """Returns dialog button hints for the given device and language."""
    hints = DIALOG_HINTS_ID if lang == "id" else DIALOG_HINTS_EN
    return hints.get(device, hints["keyboard"])


def decode_xinput_buttons(wButtons: int) -> Dict[str, bool]:
    """Decodes XInput wButtons bitmask into a dictionary of named booleans."""
    return {
        "up": bool(wButtons & XINPUT_GAMEPAD_DPAD_UP),
        "down": bool(wButtons & XINPUT_GAMEPAD_DPAD_DOWN),
        "left": bool(wButtons & XINPUT_GAMEPAD_DPAD_LEFT),
        "right": bool(wButtons & XINPUT_GAMEPAD_DPAD_RIGHT),
        "start": bool(wButtons & XINPUT_GAMEPAD_START),
        "back": bool(wButtons & XINPUT_GAMEPAD_BACK),
        "lb": bool(wButtons & XINPUT_GAMEPAD_LEFT_SHOULDER),
        "rb": bool(wButtons & XINPUT_GAMEPAD_RIGHT_SHOULDER),
        "a": bool(wButtons & XINPUT_GAMEPAD_A),
        "b": bool(wButtons & XINPUT_GAMEPAD_B),
        "x": bool(wButtons & XINPUT_GAMEPAD_X),
        "y": bool(wButtons & XINPUT_GAMEPAD_Y),
    }


def stick_to_directions(
    lx: int, ly: int, deadzone: int = DEFAULT_DEADZONE
) -> Tuple[bool, bool, bool, bool]:
    """
    Maps analog thumbstick coordinates to (up, down, left, right) booleans
    using deadzone threshold and dominant axis filtering to prevent accidental
    diagonal jumps in UI navigation.
    """
    abs_x = abs(lx)
    abs_y = abs(ly)
    if abs_x <= deadzone and abs_y <= deadzone:
        return False, False, False, False

    if abs_y >= abs_x:
        return (ly > deadzone, ly < -deadzone, False, False)
    else:
        return (False, False, lx < -deadzone, lx > deadzone)


class KeyRepeatTracker:
    """
    Tracks press and repeat timing for directional inputs.
    First press triggers immediately at t=0.
    Holding for initial_delay_s triggers repeat, then repeats every repeat_delay_s.
    """

    def __init__(
        self, initial_delay_s: float = 0.350, repeat_delay_s: float = 0.120
    ):
        self.initial_delay_s = initial_delay_s
        self.repeat_delay_s = repeat_delay_s
        self.states: Dict[str, Dict[str, float]] = {}

    def update(self, key: str, is_pressed: bool, current_time: float) -> bool:
        """
        Updates key state at current_time.
        Returns True if a key-press or repeat event should be generated.
        """
        state = self.states.get(key)
        if not is_pressed:
            if state is not None:
                del self.states[key]
            return False

        if state is None:
            self.states[key] = {
                "next_fire": current_time + self.initial_delay_s
            }
            return True

        if current_time >= state["next_fire"] - 1e-5:
            state["next_fire"] = current_time + self.repeat_delay_s
            return True

        return False

    def reset(self) -> None:
        """Clears all held keys."""
        self.states.clear()


def compute_next_row_index(
    current_row: int, total_rows: int, delta: int, clamp: bool = True
) -> int:
    """Computes next row index given delta (-1 or +1)."""
    if total_rows <= 0:
        return 0
    target = current_row + delta
    if clamp:
        return max(0, min(total_rows - 1, target))
    return target % total_rows


def compute_next_column_index(
    current_col: int, row_len: int, delta: int, clamp: bool = True
) -> int:
    """Computes next column index within a row given delta."""
    if row_len <= 0:
        return 0
    target = current_col + delta
    if clamp:
        return max(0, min(row_len - 1, target))
    return target % row_len


def find_closest_column(
    target_row_len: int, preferred_col: int
) -> int:
    """Finds the closest valid column index when moving vertically between rows."""
    if target_row_len <= 0:
        return 0
    return max(0, min(target_row_len - 1, preferred_col))


# --- XInput ctypes loading ---
class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


class XInputManager:
    """Loads XInput DLL dynamically with graceful fallbacks."""

    def __init__(self):
        self.dll = None
        self.get_state_func = None
        self._load()

    def _load(self):
        if sys.platform != "win32":
            return
        dll_candidates = ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll")
        for name in dll_candidates:
            try:
                dll = ctypes.windll.LoadLibrary(name)
                if hasattr(dll, "XInputGetState"):
                    func = dll.XInputGetState
                    func.argtypes = [ctypes.c_ulong, ctypes.POINTER(XINPUT_STATE)]
                    func.restype = ctypes.c_ulong
                    self.dll = dll
                    self.get_state_func = func
                    break
            except Exception:
                continue

    @property
    def is_available(self) -> bool:
        return self.get_state_func is not None

    def poll_slot(self, slot: int) -> Optional[Tuple[Dict[str, bool], int, int]]:
        """
        Polls a specific XInput slot (0..3).
        Returns None if not connected or error, otherwise:
        (buttons_dict, sThumbLX, sThumbLY)
        """
        if not self.get_state_func:
            return None
        state = XINPUT_STATE()
        res = self.get_state_func(ctypes.c_ulong(slot), ctypes.byref(state))
        if res == 0:  # ERROR_SUCCESS
            gp = state.Gamepad
            buttons = decode_xinput_buttons(gp.wButtons)
            return buttons, gp.sThumbLX, gp.sThumbLY
        return None
