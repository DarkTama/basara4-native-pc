"""Basara 4 PC launcher theming system (Yoshiteru / Hisahide).

Provides color palettes, live ttk/tk widget restyling, and widget registry.
"""
import weakref
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, Optional

THEMES: Dict[str, Dict[str, str]] = {
    "yoshiteru": {
        "name": "Ashikaga Yoshiteru",
        "motif": "\u25c6",
        "bg_main": "#0D0A14",
        "bg_dock": "#110D1B",
        "bg_card": "#151022",
        "bg_control": "#1C152E",
        "bg_hover": "#271D40",
        "border_card": "#261E3A",
        "fg_primary": "#F2ECE1",
        "fg_muted": "#8A829E",
        "accent_primary": "#E8B949",
        "accent_secondary": "#9D4EDD",
        "toggle_knob_on": "#E8B949",
        "toggle_track_off": "#2A2438",
        "toggle_track_on": "#9D4EDD",
        "play_bg": "#9E192E",
        "play_hover": "#C41E3A",
        "play_border": "#E8B949",
        "ok": "#7da36b",
        "fork_accent": "#e0a952",
    },
    "hisahide": {
        "name": "Matsunaga Hisahide",
        "motif": "\u25b8",
        "bg_main": "#0B0E14",
        "bg_dock": "#0F131C",
        "bg_card": "#131822",
        "bg_control": "#19202E",
        "bg_hover": "#222C40",
        "border_card": "#212A3A",
        "fg_primary": "#E6EDF5",
        "fg_muted": "#7D8B9E",
        "accent_primary": "#4DA6FF",
        "accent_secondary": "#E09F3E",
        "toggle_knob_on": "#0F131C",
        "toggle_track_off": "#1E2636",
        "toggle_track_on": "#4DA6FF",
        "play_bg": "#9E192E",
        "play_hover": "#C41E3A",
        "play_border": "#E8B949",
        "ok": "#7da36b",
        "fork_accent": "#e0a952",
    },
}

DEFAULT_THEME = "yoshiteru"


def get_theme(name: Optional[str] = None) -> Dict[str, str]:
    """Returns the theme tokens dictionary for the given theme name, defaulting to yoshiteru."""
    if not name or name not in THEMES:
        name = DEFAULT_THEME
    return dict(THEMES[name])


def apply_ttk_theme(style: ttk.Style, theme_name: str, root: Optional[tk.Tk] = None) -> None:
    """Configures ttk styles for the clam theme using tokens from theme_name."""
    theme = get_theme(theme_name)
    bg_main = theme["bg_main"]
    bg_dock = theme["bg_dock"]
    bg_card = theme["bg_card"]
    bg_control = theme["bg_control"]
    bg_hover = theme["bg_hover"]
    border_card = theme["border_card"]
    fg_primary = theme["fg_primary"]
    fg_muted = theme["fg_muted"]
    accent_primary = theme["accent_primary"]
    play_bg = theme["play_bg"]
    play_hover = theme["play_hover"]
    play_border = theme["play_border"]

    style.theme_use("clam")

    # Global fallback
    style.configure(
        ".",
        background=bg_main,
        foreground=fg_primary,
        fieldbackground=bg_control,
        bordercolor=border_card,
        lightcolor=border_card,
        darkcolor=border_card,
        font=("Segoe UI", 10),
    )

    # Frame styles
    style.configure("TFrame", background=bg_main)
    style.configure("Card.TFrame", background=bg_card)
    style.configure("Dock.TFrame", background=bg_dock)
    style.configure("Panel.TFrame", background=bg_card)

    # Label styles
    style.configure("TLabel", background=bg_main, foreground=fg_primary)
    style.configure("Card.TLabel", background=bg_card, foreground=fg_primary)
    style.configure("Dock.TLabel", background=bg_dock, foreground=fg_primary)
    style.configure("Panel.TLabel", background=bg_card, foreground=fg_primary)
    style.configure("Muted.TLabel", background=bg_card, foreground=fg_muted, font=("Segoe UI", 9))
    style.configure("Head.TLabel", background=bg_card, foreground=accent_primary, font=("Segoe UI Semibold", 11))

    # Radio & Check buttons (ttk fallbacks)
    style.configure("TRadiobutton", background=bg_card, foreground=fg_primary)
    style.configure(
        "TCheckbutton",
        background=bg_card,
        foreground=fg_primary,
        indicatorbackground=bg_control,
        indicatorforeground=accent_primary,
        upperbordercolor=border_card,
        lowerbordercolor=border_card,
    )
    style.map("TRadiobutton", background=[("active", bg_hover)], foreground=[("active", accent_primary)])
    style.map(
        "TCheckbutton",
        background=[("active", bg_hover)],
        foreground=[("active", accent_primary)],
        indicatorbackground=[("active", bg_control)],
    )

    # Buttons
    style.configure(
        "TButton",
        background=bg_control,
        foreground=fg_primary,
        padding=(12, 4),
        borderwidth=1,
        bordercolor=border_card,
        lightcolor=border_card,
        darkcolor=border_card,
    )
    btn_edge = [("focus", accent_primary), ("disabled", border_card), ("active", accent_primary)]
    style.map(
        "TButton",
        background=[("active", bg_hover), ("disabled", bg_card)],
        foreground=[("disabled", fg_muted)],
        bordercolor=btn_edge,
        lightcolor=btn_edge,
        darkcolor=btn_edge,
    )

    # Play Button
    style.configure(
        "Play.TButton",
        background=play_bg,
        foreground="#ffffff",
        font=("Segoe UI Semibold", 15),
        padding=(10, 10),
        borderwidth=2,
        bordercolor=play_border,
        lightcolor=play_border,
        darkcolor=play_border,
    )
    play_edge = [("focus", play_border), ("active", play_border)]
    style.map(
        "Play.TButton",
        background=[("active", play_hover), ("disabled", bg_control)],
        bordercolor=play_edge,
        lightcolor=play_edge,
        darkcolor=play_edge,
    )

    # Stop Button
    style.configure(
        "Stop.TButton",
        background=bg_control,
        foreground=fg_muted,
        padding=(10, 4),
        borderwidth=1,
        bordercolor=border_card,
        lightcolor=border_card,
        darkcolor=border_card,
        font=("Segoe UI", 9),
    )
    style.map(
        "Stop.TButton",
        background=[("active", bg_hover), ("disabled", bg_control)],
        foreground=[("disabled", fg_muted)],
        bordercolor=[("disabled", border_card), ("active", accent_primary), ("focus", accent_primary)],
        lightcolor=[("disabled", border_card), ("active", accent_primary), ("focus", accent_primary)],
        darkcolor=[("disabled", border_card), ("active", accent_primary), ("focus", accent_primary)],
    )

    # Segmented controls (toolbutton style)
    style.configure(
        "Seg.Toolbutton",
        background=bg_control,
        foreground=fg_primary,
        padding=(10, 5),
        borderwidth=1,
        bordercolor=border_card,
        lightcolor=border_card,
        darkcolor=border_card,
        anchor="center",
    )
    seg_edge = [("focus", accent_primary), ("active", accent_primary)]
    style.map(
        "Seg.Toolbutton",
        background=[("selected", accent_primary), ("active", bg_hover)],
        foreground=[("selected", bg_main), ("active", fg_primary)],
        bordercolor=seg_edge,
        lightcolor=seg_edge,
        darkcolor=seg_edge,
    )

    # Combobox
    style.configure(
        "TCombobox",
        arrowcolor=accent_primary,
        bordercolor=border_card,
        lightcolor=border_card,
        darkcolor=border_card,
        padding=(6, 4),
        selectbackground=bg_control,
        selectforeground=fg_primary,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", bg_control)],
        foreground=[("readonly", fg_primary)],
        selectbackground=[("readonly", bg_control)],
        selectforeground=[("readonly", fg_primary)],
        bordercolor=[("focus", accent_primary), ("active", accent_primary)],
        lightcolor=[("focus", accent_primary), ("active", accent_primary)],
        darkcolor=[("focus", accent_primary), ("active", accent_primary)],
        arrowcolor=[("active", accent_primary)],
    )

    # Entry
    style.configure(
        "TEntry",
        fieldbackground=bg_control,
        foreground=fg_primary,
        insertcolor=fg_primary,
        bordercolor=border_card,
        lightcolor=border_card,
        darkcolor=border_card,
        padding=(6, 4),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", accent_primary)],
        lightcolor=[("focus", accent_primary)],
        darkcolor=[("focus", accent_primary)],
    )

    # Vertical Scrollbar
    style.configure(
        "Vertical.TScrollbar",
        background=bg_hover,
        troughcolor=bg_main,
        bordercolor=bg_main,
        arrowcolor=fg_muted,
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", accent_primary), ("pressed", accent_primary)],
    )

    if root is not None:
        root.option_add("*TCombobox*Listbox.background", bg_control)
        root.option_add("*TCombobox*Listbox.foreground", fg_primary)
        root.option_add("*TCombobox*Listbox.selectBackground", bg_hover)
        root.option_add("*TCombobox*Listbox.selectForeground", accent_primary)


class ThemeRegistry:
    """Registry of widgets and callbacks for live theme switching without restart."""

    def __init__(self):
        self._entries: list[tuple[weakref.ref, dict[str, str]]] = []
        self._callbacks: list[Callable[[dict[str, str]], None]] = []
        self.current_theme: str = DEFAULT_THEME

    def register(self, widget: Any, **token_bindings: str) -> Any:
        """
        Registers a tk widget to automatically receive configure(attr=theme[token])
        when the theme changes.
        Example: registry.register(label, bg="bg_card", fg="fg_primary")
        """
        ref = weakref.ref(widget)
        self._entries.append((ref, token_bindings))
        theme = get_theme(self.current_theme)
        self._apply_to_entry(ref, token_bindings, theme)
        return widget

    def register_callback(self, callback: Callable[[dict[str, str]], None]) -> None:
        """Registers a function to be invoked with the current theme dictionary upon theme switch."""
        self._callbacks.append(callback)
        callback(get_theme(self.current_theme))

    def _apply_to_entry(
        self, ref: weakref.ref, token_bindings: dict[str, str], theme: dict[str, str]
    ) -> bool:
        w = ref()
        if w is None:
            return False
        try:
            if not w.winfo_exists():
                return False
        except Exception:
            return False

        kwargs = {}
        for prop, token in token_bindings.items():
            kwargs[prop] = theme.get(token, token)
        try:
            w.configure(**kwargs)
        except Exception:
            pass
        return True

    def apply_theme(
        self,
        theme_name: str,
        root: Optional[tk.Tk] = None,
        style: Optional[ttk.Style] = None,
    ) -> None:
        """Switches active theme and updates all registered widgets, ttk styles, and callbacks."""
        self.current_theme = theme_name if theme_name in THEMES else DEFAULT_THEME
        theme = get_theme(self.current_theme)

        if style is not None:
            apply_ttk_theme(style, self.current_theme, root)

        alive_entries = []
        for ref, bindings in self._entries:
            if self._apply_to_entry(ref, bindings, theme):
                alive_entries.append((ref, bindings))
        self._entries = alive_entries

        for cb in self._callbacks:
            try:
                cb(theme)
            except Exception:
                pass


# Global singleton registry instance
REGISTRY = ThemeRegistry()
