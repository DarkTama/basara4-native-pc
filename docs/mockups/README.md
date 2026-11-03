# Launcher redesign mockup: Yoshiteru / Hisahide themes

Mockup only. No launcher code was touched.

## Files
- `launcher_redesign.html`: the 1000x650 launcher, all 5 pages, theme switcher, page switcher, pad/keyboard hint switcher, and the dual-monitor confirmation dialog (button "Dual-monitor dialog", or turn on the co-op toggle). Open it in any browser (double-click).
- `yoshiteru_display.png`, `hisahide_display.png`: Display page (focus on Window size, hover on VSync).
- `hisahide_dialog.png`: dialog, keyboard hints.
- URL params for fixed states: `?theme=hisahide&page=tools&device=keyboard&hover=vsync&focus=resolution&dialog=1&playhover=1&shot=1`.

## Layout changes vs current
- Window grid: dock 250 | nav 158 | content 592 wide; bottom strip 28 px tall for button hints (new, full width, under all three columns).
- Rows become **cards**: `bg_card` fill + 1 px `border_card` outline, 4 px gap, instead of ruled lines. Hover = `bg_hover`. Focus (pad/keyboard) = 2 px `accent_primary` outline + 5 px bar on the left edge. (Today only a left bar.)
- Checkboxes become **toggle switches** (40x22) with an On/Off text. Dual-monitor co-op gets an `Experimental` badge (`accent_secondary` fill).
- Display-mode segmented control: selected segment filled `accent_primary` with `bg_main` text.
- Page title gets a short 34x3 accent underline. Group headers (`Translation`, `Cheats`, `Controller Management`, `Keyboard & Mouse Controls`, new `Appearance`) are small caps in `accent_primary` with a theme motif glyph.
- Nav selected item: `bg_card` fill + 4 px accent bar + accent text.
- Dock gets its own darker background `bg_dock`; cover art is a placeholder block (real key visual stays an image).
- New row **Launcher theme** (Tools > Appearance): two-option segmented control with colour swatches. Could also go to Display; Tools was chosen so Display stays about the game.
- Hint footer keeps "?" icon, now ringed in accent colour.
- Dialog: `bg_card` body, `bg_dock` header and footer, accent 1 px border, Yes (default) uses PLAY styling, focused button gets an accent ring, pad/keyboard hints from `get_dialog_hints`.

## Token to Tk mapping
| Token | Applied to |
|---|---|
| bg_main | root `tk.Tk` bg, nav column frame, content area frame, page canvas bg, scrollbar trough |
| bg_dock | left dock frame, bottom hint strip, dialog header/footer |
| bg_card | row frames (`tk.Frame` with `highlightthickness=1`), selected nav item, cover placeholder, dialog body |
| bg_control | `TEntry`/`TCombobox` fieldbackground, `TButton` background, `Seg.Toolbutton` unselected background, combobox popdown listbox bg, dialog No button |
| bg_hover | row hover (bind `<Enter>/<Leave>` swap of row bg and its child labels), `TButton` `active`, nav hover, scrollbar thumb, listbox select bg |
| border_card | row `highlightbackground`, entry/combo/button `bordercolor`/`lightcolor`/`darkcolor`, separators (1 px frames), toggle track outline |
| fg_primary | all primary labels, entry/combo foreground, dialog text |
| fg_muted | hints, subtitle, unselected nav, off-toggle knob, "Stop game", credit text |
| accent_primary | selected nav text + bar, focus outline (`highlightbackground` of a focus-ring frame), group headers, title underline, serial text, selected segment bg, slider fill, combobox arrow, links, "?" ring, hint-key borders on pad |
| accent_secondary | `Experimental` badge bg; (Yoshiteru) also the toggle on-track |
| toggle_knob_on | knob oval fill when on (canvas) |
| toggle_track_off | knob track fill when off, slider trough |
| play_bg / play_hover / play_border | PLAY and dialog-Yes: a `tk.Label`/`tk.Frame` with `highlightthickness=2`, `highlightbackground=play_border`, bg swap on `<Enter>/<Leave>` |

Derived (not in the brief): **toggle track on** = `accent_secondary` (Yoshiteru, violet) / `accent_primary` (Hisahide, blue). On Hisahide the brief's `toggle_knob_on` (#0F131C) is dark so it needs a light track: that is why track-on is the blue. Needs one extra token `toggle_track_on`; or reuse `accent_primary` for Hisahide and `accent_secondary` for Yoshiteru in code. Status dot green `#7da36b` (existing `OK`) and fork credit `#e0a952` (`FORK_ACCENT`) are theme-independent. PLAY colours are the same in both themes per the brief.

## Fonts (Tk-safe)
| Use | Mockup | Tk |
|---|---|---|
| UI text | Segoe UI 13/12/11 px | `("Segoe UI", 9..10)` (same as now); fallback chain: Segoe UI > Tahoma > TkDefaultFont |
| Page title | Segoe UI 600 22 px | `("Segoe UI Semibold", 16)` |
| Game title (JP) | Yu Mincho bold 25 px | `("Yu Mincho", 17, "bold")`; fallback MS Mincho > Yu Gothic > Segoe UI. Pick the first present via `tkinter.font.families()` |
| Button keys, badge | Segoe UI 9.5-10.5 px | `("Segoe UI", 7..8, "bold")` |
No web fonts are used, so nothing needs bundling.
Tk sizes are in points; at 96 DPI px = pt x 1.33. The launcher is DPI-scaled already; keep using `tk scaling`.

## Spacing scale
4 / 8 / 12 / 16 / 22 px (mockup). Row min height 40, gap 4, row inner pad 14 left / 10 right, content pad 22, nav item 40, strip 28. In Tk: `pady=2` per side for the gap, `ipady` for min height, `padx=(14,10)`.

## What is hard in Tk and how to approximate
- **Toggle switch**: no ttk equivalent. Use a small `tk.Canvas` (40x22): `create_oval` x2 + `create_rectangle` for a pill track, knob oval moved on click. Needs keyboard focus handling (`takefocus=1`, Space/Enter, pad A) and must be redrawn on theme change. Anti-aliasing is absent on Canvas; at 22 px height edges look slightly jagged. Alternative: pre-rendered 4 PNG states (on/off x theme) as `PhotoImage`, which is smooth and simple.
- **Rounded corners**: not available. Mockup uses square cards (only the toggle and the pad key chips round). Pad key chips can be `Label` with a pre-rendered PNG or just square.
- **Focus ring**: Tk `highlightthickness` ring is per-widget and cannot sit outside. Use an outer 2 px `tk.Frame` whose bg is the accent when focused and `border_card` otherwise (changing only its `highlightbackground`/bg), and a 5 px accent `Frame` at its left.
- **Row hover**: no CSS `:hover`. Bind `<Enter>`/`<Leave>` on the row and all descendants (children steal Enter/Leave); on Leave check `winfo_containing` to avoid flicker.
- **ttk theming**: ttk `clam` supports colouring `TCombobox`, `TEntry`, `TButton`, scrollbar. The combobox popdown listbox needs `option_add("*TCombobox*Listbox.background", ...)` etc. The arrow button colour needs `arrowcolor`. Changing theme live requires re-running `style.configure/map` and walking `tk.*` widgets; simplest is a `ThemePalette` dict plus `apply_theme()` that recolours registered widgets (keep a registry list), or rebuild the pages.
- **Segmented control** uses existing `Seg.Toolbutton` radiobuttons; selected state via `style.map(... background=[("selected", accent)])`.
- **Gradients / blur / box-shadow / dimmed scrim**: none used. The dialog shadow is a 4 px `bg_main` border frame. Scrim: not possible with true alpha on all Windows setups; the real dialog is a modal `Toplevel` without dimming (or `-alpha` on a black borderless toplevel over the main window, which works on Windows). Mockup shows dimming only for clarity.
- **Theme-coloured native dialog**: `messagebox` cannot be themed. Needs a custom `Toplevel` (the launcher already has `ask_yes_no`; it would be replaced by a themed version with Left/Right/Enter/Esc and pad handling).
- **Window title bar** stays native (white/dark by OS). Optional: DWM `DWMWA_USE_IMMERSIVE_DARK_MODE` via ctypes for a dark title bar.
- **Cover art placeholder**: real image stays a `PhotoImage`; unchanged.
- **Keycap/pad chips** in the strip: approximate with `tk.Label(relief="solid", bd=1)` or one label with text only if too fiddly.
- **Scrolling rows** (Controls page) already uses canvas; the dark scrollbar needs `ttk.Style` `Vertical.TScrollbar` colours (clam).

## Theme persistence
`cfg["theme"] = "yoshiteru" | "hisahide"` in the same config as the other settings, default yoshiteru. Apply at startup and on change (no restart needed if the registry approach is used).
