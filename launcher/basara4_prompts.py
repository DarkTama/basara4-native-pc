# Basara 4 PC - by Razz. Fork by DarkTama.
# Controller button prompt / glyph patcher (Xbox & Keyboard/Mouse).
from __future__ import annotations

import io
from pathlib import Path
import urllib.request

from PIL import Image
import etcpak

from gmdarc import ArcFile
import basara4_tex as tex

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "prompts"
XBOX_CACHE = ASSETS_DIR / "xbox"
KBM_CACHE = ASSETS_DIR / "kbm"

KENNEY_XBOX_BASE = (
    "https://raw.githubusercontent.com/tanuki-billie/kenney-input-prompts/main/"
    "addons/kenney_input_prompts/Xbox%20Series/Default/"
)

KENNEY_KBM_BASE = (
    "https://raw.githubusercontent.com/tanuki-billie/kenney-input-prompts/main/"
    "addons/kenney_input_prompts/Keyboard%20%26%20Mouse/Default/"
)

XBOX_FILES = [
    "xbox_button_color_a.png",
    "xbox_button_color_b.png",
    "xbox_button_color_x.png",
    "xbox_button_color_y.png",
    "xbox_lb.png",
    "xbox_rb.png",
    "xbox_lt.png",
    "xbox_rt.png",
    "xbox_dpad.png",
    "xbox_button_view.png",
    "xbox_button_menu.png",
    "xbox_ls.png",
    "xbox_rs.png",
]

KBM_FILES = [
    "mouse_left.png",
    "mouse_right.png",
    "keyboard_space.png",
    "keyboard_shift.png",
    "keyboard_e.png",
    "keyboard_q.png",
    "keyboard_r.png",
    "keyboard_f.png",
    "keyboard_tab.png",
    "keyboard_escape.png",
    "keyboard_z.png",
    "keyboard_c.png",
    "keyboard_arrows.png",
]


def ensure_xbox_assets() -> bool:
    XBOX_CACHE.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for fname in XBOX_FILES:
        dest = XBOX_CACHE / fname
        if not dest.is_file():
            url = KENNEY_XBOX_BASE + fname
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    dest.write_bytes(resp.read())
            except Exception:
                all_ok = False
    return all_ok


def ensure_kbm_assets() -> bool:
    KBM_CACHE.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for fname in KBM_FILES:
        dest = KBM_CACHE / fname
        if not dest.is_file():
            url = KENNEY_KBM_BASE + fname
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    dest.write_bytes(resp.read())
            except Exception:
                all_ok = False
    return all_ok


def compose_xbox_atlas(orig_atlas: Image.Image | None = None) -> Image.Image:
    """Composites Xbox button glyphs into a 512x512 RGBA grid (4x4 tiles of 128x128)."""
    ensure_xbox_assets()
    atlas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))

    def place(img_name: str, r: int, c: int, scale: int = 108):
        p = XBOX_CACHE / img_name
        if not p.is_file():
            return
        im = Image.open(p).convert("RGBA")
        im.thumbnail((scale, scale), Image.Resampling.LANCZOS)
        x = c * 128 + (128 - im.width) // 2
        y = r * 128 + (128 - im.height) // 2
        atlas.paste(im, (x, y), im)

    # Row 0: D-Pad, B, A, +
    place("xbox_dpad.png", 0, 0, scale=110)
    place("xbox_button_color_b.png", 0, 1, scale=110)
    place("xbox_button_color_a.png", 0, 2, scale=110)
    if orig_atlas is not None:
        plus = orig_atlas.crop((3 * 128, 0, 4 * 128, 128))
        atlas.paste(plus, (3 * 128, 0), plus)

    # Row 1: B (Circle), A (Cross), Y (Triangle), X (Square)
    place("xbox_button_color_b.png", 1, 0, scale=110)
    place("xbox_button_color_a.png", 1, 1, scale=110)
    place("xbox_button_color_y.png", 1, 2, scale=110)
    place("xbox_button_color_x.png", 1, 3, scale=110)

    # Row 2: LB, RB, LT, RT
    place("xbox_lb.png", 2, 0, scale=110)
    place("xbox_rb.png", 2, 1, scale=110)
    place("xbox_lt.png", 2, 2, scale=110)
    place("xbox_rt.png", 2, 3, scale=110)

    # Row 3: View (Touchpad), Menu (Options), LS, RS
    place("xbox_button_view.png", 3, 0, scale=100)
    place("xbox_button_menu.png", 3, 1, scale=100)
    place("xbox_ls.png", 3, 2, scale=100)
    place("xbox_rs.png", 3, 3, scale=100)

    return atlas


def compose_kbm_atlas(orig_atlas: Image.Image | None = None) -> Image.Image:
    """Composites Keyboard & Mouse button glyphs into a 512x512 RGBA grid (4x4 tiles of 128x128)."""
    ensure_kbm_assets()
    atlas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))

    def place(img_name: str, r: int, c: int, scale: int = 100):
        p = KBM_CACHE / img_name
        if not p.is_file():
            return
        im = Image.open(p).convert("RGBA")
        im.thumbnail((scale, scale), Image.Resampling.LANCZOS)
        x = c * 128 + (128 - im.width) // 2
        y = r * 128 + (128 - im.height) // 2
        atlas.paste(im, (x, y), im)

    # Row 0: Arrows, F, Space, +
    place("keyboard_arrows.png", 0, 0, scale=100)
    place("keyboard_f.png", 0, 1, scale=100)
    place("keyboard_space.png", 0, 2, scale=100)
    if orig_atlas is not None:
        plus = orig_atlas.crop((3 * 128, 0, 4 * 128, 128))
        atlas.paste(plus, (3 * 128, 0), plus)

    # Row 1: F, Space, Mouse Right, Mouse Left
    place("keyboard_f.png", 1, 0, scale=100)
    place("keyboard_space.png", 1, 1, scale=100)
    place("mouse_right.png", 1, 2, scale=95)
    place("mouse_left.png", 1, 3, scale=95)

    # Row 2: Shift, E, R, Q
    place("keyboard_shift.png", 2, 0, scale=100)
    place("keyboard_e.png", 2, 1, scale=100)
    place("keyboard_r.png", 2, 2, scale=100)
    place("keyboard_q.png", 2, 3, scale=100)

    # Row 3: Tab, Esc, Z, C
    place("keyboard_tab.png", 3, 0, scale=100)
    place("keyboard_escape.png", 3, 1, scale=100)
    place("keyboard_z.png", 3, 2, scale=100)
    place("keyboard_c.png", 3, 3, scale=100)

    return atlas


ARC_REL = Path("nativePS4") / "rom" / "game_common.arc"
STAMP_NAME = ".prompts_stamp.json"
STAMP_VERSION = 2  # bump when the atlas or encoding changes


def find_base_arc(game: Path, lower_layers: list[Path] | None = None) -> Path:
    """The game_common.arc the prompt patch must be built on: the topmost lower overlay layer
    that carries one (e.g. the English patch), else the game's own."""
    for layer in reversed(lower_layers or []):
        cand = Path(layer) / ARC_REL
        if cand.is_file():
            return cand
    game = Path(game)
    for cand in (game / ARC_REL, game / "game_common.arc"):
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"Cannot find game_common.arc in {game}")


def _stamp_for(base_arc: Path, style: str) -> dict:
    st = base_arc.stat()
    return {"version": STAMP_VERSION, "style": style, "base": str(base_arc.resolve()),
            "base_size": st.st_size, "base_mtime": st.st_mtime}


def _build_prompts_arc(base_arc: Path, out_patch_dir: Path, style: str = "xbox") -> Path:
    arc = ArcFile.from_file(base_arc)
    entry = arc.get_entry(r"ui\tex\jpn\common\common_001_ID_HQ_ps4")
    if not entry:
        entry = arc.get_entry("ui/tex/jpn/common/common_001_ID_HQ_ps4")
    if not entry:
        raise ValueError("Cannot find common_001_ID_HQ_ps4 entry in game_common.arc")

    orig_raw = entry.decompress()
    hdr, orig_payload = tex.extract_tex_payload(orig_raw, endian="<")
    orig_deswiz = tex.deswizzle_bc3(orig_payload, hdr["width"], hdr["height"])
    orig_atlas = tex.decode_bc3_to_rgba(orig_deswiz, hdr["width"], hdr["height"])

    if style == "kbm":
        new_atlas = compose_kbm_atlas(orig_atlas)
    else:
        new_atlas = compose_xbox_atlas(orig_atlas)

    comp_bc3 = etcpak.compress_bc3(new_atlas.tobytes(), 512, 512)
    swizzled = tex.swizzle_bc3(comp_bc3, 512, 512)
    new_tex = orig_raw[: hdr["pixel_offset"]] + swizzled

    entry.set_uncompressed(new_tex)

    dst_arc = out_patch_dir / "nativePS4" / "rom" / "game_common.arc"
    dst_arc.parent.mkdir(parents=True, exist_ok=True)
    dst_arc.write_bytes(arc.to_bytes())
    return dst_arc


def ensure_prompts(game: Path, out_patch_dir: Path, style: str,
                   lower_layers: list[Path] | None = None) -> Path:
    """Builds (or reuses) the prompt patch. It is layered on top of lower_layers, so it never
    drops the entries another patch (the English patch) changed in game_common.arc."""
    import json
    out_patch_dir = Path(out_patch_dir)
    base_arc = find_base_arc(game, lower_layers)
    dst_arc = out_patch_dir / ARC_REL
    stamp_file = out_patch_dir / STAMP_NAME
    want = _stamp_for(base_arc, style)
    try:
        if dst_arc.is_file() and json.loads(stamp_file.read_text(encoding="utf-8")) == want:
            return dst_arc
    except (OSError, ValueError):
        pass
    _build_prompts_arc(base_arc, out_patch_dir, style=style)
    stamp_file.write_text(json.dumps(want, indent=2), encoding="utf-8")
    return dst_arc


def ensure_xbox_prompts(game: Path, out_patch_dir: Path, lower_layers: list[Path] | None = None) -> Path:
    return ensure_prompts(game, out_patch_dir, "xbox", lower_layers)


def ensure_kbm_prompts(game: Path, out_patch_dir: Path, lower_layers: list[Path] | None = None) -> Path:
    return ensure_prompts(game, out_patch_dir, "kbm", lower_layers)
