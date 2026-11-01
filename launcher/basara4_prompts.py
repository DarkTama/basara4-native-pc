# Basara 4 PC - by Razz. Fork by DarkTama.
# Controller button prompt / glyph patcher.
from __future__ import annotations

import io
from pathlib import Path
import urllib.request

from PIL import Image
import etcpak

from gmdarc import ArcFile
import basara4_tex as tex

PROMPTS_CACHE = Path(__file__).resolve().parent.parent / "assets" / "prompts" / "xbox"

KENNEY_BASE = (
    "https://raw.githubusercontent.com/tanuki-billie/kenney-input-prompts/main/"
    "addons/kenney_input_prompts/Xbox%20Series/Default/"
)

REQUIRED_FILES = [
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


def ensure_prompt_assets() -> bool:
    """Ensures Kenney Xbox prompt png assets exist locally in assets/prompts/xbox."""
    PROMPTS_CACHE.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for fname in REQUIRED_FILES:
        dest = PROMPTS_CACHE / fname
        if not dest.is_file():
            url = KENNEY_BASE + fname
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    dest.write_bytes(resp.read())
            except Exception:
                all_ok = False
    return all_ok


def compose_xbox_atlas(orig_atlas: Image.Image | None = None) -> Image.Image:
    """Composites Xbox button glyphs into a 512x512 RGBA grid (4x4 tiles of 128x128)."""
    ensure_prompt_assets()
    atlas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))

    def place(img_name: str, r: int, c: int, scale: int = 108):
        p = PROMPTS_CACHE / img_name
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


def build_xbox_prompts_arc(game_rom: Path, out_patch_dir: Path) -> Path:
    """Builds patched game_common.arc with Xbox button glyphs inside out_patch_dir."""
    src_arc = game_rom / "game_common.arc"
    if not src_arc.is_file():
        # Maybe game_rom is CUSA01159 directly
        src_arc = game_rom / "nativePS4" / "rom" / "game_common.arc"
    if not src_arc.is_file():
        raise FileNotFoundError(f"Cannot find game_common.arc in {game_rom}")

    arc = ArcFile.from_file(src_arc)
    entry = arc.get_entry(r"ui\tex\jpn\common\common_001_ID_HQ_ps4")
    if not entry:
        entry = arc.get_entry("ui/tex/jpn/common/common_001_ID_HQ_ps4")
    if not entry:
        raise ValueError("Cannot find common_001_ID_HQ_ps4 entry in game_common.arc")

    orig_raw = entry.decompress()
    hdr, orig_payload = tex.extract_tex_payload(orig_raw, endian="<")
    orig_deswiz = tex.deswizzle_bc3(orig_payload, hdr["width"], hdr["height"])
    orig_atlas = tex.decode_bc3_to_rgba(orig_deswiz, hdr["width"], hdr["height"])

    new_atlas = compose_xbox_atlas(orig_atlas)

    comp_bc3 = etcpak.compress_bc3(new_atlas.tobytes(), 512, 512)
    swizzled = tex.swizzle_bc3(comp_bc3, 512, 512)
    new_tex = orig_raw[: hdr["pixel_offset"]] + swizzled

    entry.set_uncompressed(new_tex)

    dst_arc = out_patch_dir / "nativePS4" / "rom" / "game_common.arc"
    dst_arc.parent.mkdir(parents=True, exist_ok=True)
    dst_arc.write_bytes(arc.to_bytes())
    return dst_arc


def ensure_xbox_prompts(game_rom: Path, out_patch_dir: Path) -> Path:
    dst_arc = out_patch_dir / "nativePS4" / "rom" / "game_common.arc"
    if dst_arc.is_file() and dst_arc.stat().st_size > 10 * 1024 * 1024:
        return dst_arc
    return build_xbox_prompts_arc(game_rom, out_patch_dir)
