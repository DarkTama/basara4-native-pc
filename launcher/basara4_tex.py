"""Texture (TEX) parser, swizzler/deswizzler, classifier, and repatcher
for Sengoku Basara 4 Sumeragi (PS3 BE and PS4 LE).

Handles Capcom MT Framework TEX containers (type hash 0x241F5DEB).
Specialized for format 42 (BC3 / DXT5) single-mip textures.
"""
from __future__ import annotations

import io
import struct
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageFilter

# Global precomputed swizzle index cache: (w, h) -> (lin_to_tiled, tiled_to_lin)
_SWIZZLE_CACHE: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}


def parse_tex_header(data: bytes, endian: Optional[str] = None) -> dict[str, Any]:
    """Parses a PS3 (Big-Endian) or PS4 (Little-Endian) MT Framework TEX header."""
    if len(data) < 20:
        raise ValueError(f"TEX data too short: {len(data)} bytes")

    magic = data[:4]
    if endian is None:
        if magic == b"\x00XET":
            endian = ">"
        elif magic == b"TEX\x00":
            endian = "<"
        else:
            raise ValueError(f"Unknown TEX magic: {magic!r}")

    d2, d3, d4 = struct.unpack(f"{endian}III", data[4:16])
    ver = d2 & 0xFFF
    dim = d2 >> 28
    mips = d3 & 0x3F
    w = (d3 >> 6) & 0x1FFF
    h = d3 >> 19
    fmt = (d4 >> 8) & 0xFF

    if endian == ">":
        pix_off = struct.unpack(">I", data[16:20])[0]
    else:
        if len(data) < 24:
            raise ValueError(f"PS4 TEX header truncated: {len(data)} < 24 bytes")
        pix_off = struct.unpack("<Q", data[16:24])[0]

    return {
        "magic": magic,
        "version": ver,
        "dimension": dim,
        "mips": mips,
        "width": w,
        "height": h,
        "format": fmt,
        "pixel_offset": pix_off,
        "endian": endian,
    }


def extract_tex_payload(data: bytes, endian: Optional[str] = None) -> tuple[dict[str, Any], bytes]:
    """Parses TEX header and returns (header_dict, pixel_data_bytes)."""
    hdr = parse_tex_header(data, endian=endian)
    return hdr, data[hdr["pixel_offset"]:]


def make_synthetic_tex_header(
    w: int,
    h: int,
    fmt: int = 42,
    mips: int = 1,
    endian: str = "<",
    version: int = 0x9D,
    dimension: int = 2,
) -> bytes:
    """Constructs a valid MT Framework TEX header prefix up to pixel_offset."""
    d2 = (dimension << 28) | (version & 0xFFF)
    d3 = (h << 19) | ((w & 0x1FFF) << 6) | (mips & 0x3F)
    d4 = (fmt & 0xFF) << 8

    if endian == ">":
        pix_off = 16 + 4 * mips
        # Pad up to 20 for mips=1
        hdr = struct.pack(">4sIIII", b"\x00XET", d2, d3, d4, pix_off)
    else:
        pix_off = 16 + 8 * mips
        # Pad up to 24 for mips=1
        hdr = struct.pack("<4sIIIQ", b"TEX\x00", d2, d3, d4, pix_off)

    return hdr


def decode_bc3_to_rgba(bc3_bytes: bytes, w: int, h: int) -> Image.Image:
    """Decodes raw linear BC3 / DXT5 payload to an RGBA PIL Image using Pillow's DDS loader."""
    pitch = max(1, ((w + 3) // 4)) * 16
    fields = [
        b"DDS ",
        124,
        0x81007,  # DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
        h,
        w,
        pitch,
        0,  # depth
        1,  # mipmaps
        *([0] * 11),  # reserved1
        32,  # pf_size
        0x4,  # DDPF_FOURCC
        b"DXT5",
        0, 0, 0, 0, 0,  # bitcount, masks
        0x1000,  # DDSCAPS_TEXTURE
        0, 0, 0, 0,  # caps2..4, reserved2
    ]
    dds_header = struct.pack("<4s7I11I2I4s5I4II", *fields)
    im = Image.open(io.BytesIO(dds_header + bc3_bytes))
    return im.convert("RGBA")


# ---- Colour space ----
# The game's _ID_HQ UI textures are not RGB: each texel stores YCbCr as R=Cr, G=Y, B=Cb, A=alpha
# (BT.601 full range, chroma centred on 128). A raw decode therefore looks green. Any resampling,
# sharpening or compositing has to happen on real RGB, and the result must be stored back in this
# layout, or chroma noise (green/purple) and tinted glyphs (white -> pink) appear in game.
NEUTRAL_CHROMA = 128.0


def ycbcr_to_rgb(stored: np.ndarray) -> np.ndarray:
    """Stored (H,W,3|4) R=Cr G=Y B=Cb in 0..255 -> real RGB float32 0..255 (alpha passed through)."""
    s = stored.astype(np.float32)
    y = s[..., 1]
    cr = s[..., 0] - NEUTRAL_CHROMA
    cb = s[..., 2] - NEUTRAL_CHROMA
    rgb = np.stack([y + 1.402 * cr, y - 0.344136 * cb - 0.714136 * cr, y + 1.772 * cb], axis=-1)
    rgb = np.clip(rgb, 0.0, 255.0)
    if s.shape[-1] == 4:
        return np.dstack([rgb, s[..., 3]])
    return rgb


def rgb_to_ycbcr(rgb: np.ndarray) -> np.ndarray:
    """Real RGB (H,W,3|4) 0..255 -> stored layout R=Cr G=Y B=Cb, float32 0..255 (alpha passed through)."""
    c = rgb.astype(np.float32)
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = NEUTRAL_CHROMA + (b - y) / 1.772
    cr = NEUTRAL_CHROMA + (r - y) / 1.402
    out = np.clip(np.stack([cr, y, cb], axis=-1), 0.0, 255.0)
    if c.shape[-1] == 4:
        return np.dstack([out, c[..., 3]])
    return out


def rgba_image_to_stored(im: Image.Image) -> np.ndarray:
    """Real-colour RGBA image -> uint8 RGBA array in the game's stored layout. Fully transparent
    texels get black with neutral chroma so filtering never pulls in extreme chroma."""
    arr = np.array(im.convert("RGBA"), dtype=np.float32)
    out = rgb_to_ycbcr(arr)
    clear = arr[..., 3] == 0
    out[clear, 0] = NEUTRAL_CHROMA
    out[clear, 1] = 0.0
    out[clear, 2] = NEUTRAL_CHROMA
    return np.round(out).astype(np.uint8)


def stored_to_rgba_image(stored) -> Image.Image:
    """Stored-layout RGBA -> real-colour RGBA image."""
    arr = np.array(stored, dtype=np.float32)
    return Image.fromarray(np.round(ycbcr_to_rgb(arr)).astype(np.uint8), mode="RGBA")


def morton2d_8x8(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Morton (Z-order) interleaving for 3 bits each (0..7)."""
    return (
        (x & 1)
        | ((y & 1) << 1)
        | ((x & 2) << 1)
        | ((y & 2) << 2)
        | ((x & 4) << 2)
        | ((y & 4) << 3)
    )


def morton2d_16x4(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Morton interleaving for x in 0..15 (4 bits) and y in 0..3 (2 bits)."""
    return (
        (x & 1)
        | ((y & 1) << 1)
        | ((x & 2) << 1)
        | ((y & 2) << 2)
        | ((x & 4) << 2)
        | ((x & 8) << 2)
    )


def get_swizzle_indices(w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Computes or retrieves cached linear_to_tiled and tiled_to_linear index arrays
    for a texture of w x h pixels (where blocks are 4x4 px).
    """
    key = (w, h)
    cached = _SWIZZLE_CACHE.get(key)
    if cached is not None:
        return cached

    bw = w // 4
    bh = h // 4

    if bw == 16 and bh == 4:
        GY, GX = np.indices((bh, bw))
        tiled_idx = morton2d_16x4(GX, GY)
        lin_to_tiled = tiled_idx.ravel()
        tiled_to_lin = np.empty_like(lin_to_tiled)
        tiled_to_lin[lin_to_tiled] = np.arange(bw * bh)
        _SWIZZLE_CACHE[key] = (lin_to_tiled, tiled_to_lin)
        return lin_to_tiled, tiled_to_lin

    if bw % 8 != 0 or bh % 8 != 0:
        raise ValueError(
            f"Unsupported texture block dimensions: {bw}x{bh} blocks ({w}x{h} px). "
            "Must be a multiple of 8x8 blocks."
        )

    tiles_x = bw // 8
    tiles_y = bh // 8
    bx = np.arange(8)
    by = np.arange(8)
    morton_table = morton2d_8x8(bx[None, :], by[:, None])  # shape (8, 8) [by, bx]

    GY, GX = np.indices((bh, bw))
    ty = GY // 8
    tx = GX // 8
    m_idx = morton_table[GY % 8, GX % 8]
    tiled_idx = (ty * tiles_x + tx) * 64 + m_idx

    lin_to_tiled = tiled_idx.ravel()
    tiled_to_lin = np.empty_like(lin_to_tiled)
    tiled_to_lin[lin_to_tiled] = np.arange(bw * bh)

    _SWIZZLE_CACHE[key] = (lin_to_tiled, tiled_to_lin)
    return lin_to_tiled, tiled_to_lin


def deswizzle_bc3(tiled_payload: bytes, w: int, h: int) -> bytes:
    """Converts PS4 tiled BC3 payload to linear row-major BC3 blocks."""
    blocks = np.frombuffer(tiled_payload, dtype=np.dtype("V16"))
    lin_to_tiled, _ = get_swizzle_indices(w, h)
    if len(blocks) != len(lin_to_tiled):
        raise ValueError(
            f"Payload size mismatch: got {len(blocks)} blocks, expected {len(lin_to_tiled)}"
        )
    linear_blocks = blocks[lin_to_tiled]
    return linear_blocks.tobytes()


def swizzle_bc3(linear_payload: bytes, w: int, h: int) -> bytes:
    """Converts linear row-major BC3 blocks to PS4 tiled BC3 payload."""
    blocks = np.frombuffer(linear_payload, dtype=np.dtype("V16"))
    _, tiled_to_lin = get_swizzle_indices(w, h)
    if len(blocks) != len(tiled_to_lin):
        raise ValueError(
            f"Payload size mismatch: got {len(blocks)} blocks, expected {len(tiled_to_lin)}"
        )
    tiled_blocks = blocks[tiled_to_lin]
    return tiled_blocks.tobytes()


WHITELIST_KEYWORDS = (
    "name",
    "text",
    "font",
    "msg",
    "title",
    "button",
    "rule",
    "guide",
    "wep",
    "setup",
    "settei",
)

BLACKLIST_KEYWORDS = (
    "face",
    "bust",
    "stand",
    "cutin",
    "illust",
    "body",
    "model",
    "effect",
    "cos_image",
    "hiface",
    "nenpyo_face",
    "id_face",
    "cp_face",
    "list_face",
    "photo",
    "bg",
    "stage",
    "st_m",
    "st_s",
    "map",
    "pos",
)


def is_ui_text_texture(path: str) -> bool:
    """Checks whether a texture path represents UI text rather than portrait/3D/model/stage art."""
    if not path:
        return True
    p = path.lower()
    if any(kw in p for kw in WHITELIST_KEYWORDS):
        return True
    if any(kw in p for kw in BLACKLIST_KEYWORDS):
        return False
    return True


def find_ps3_ps4_localized_rects(
    im3: Image.Image,
    im4: Image.Image,
    w3: int,
    h3: int,
) -> list[list[int]]:
    """Fallback: finds localized changed 4x4-block rectangles by comparing PS3 against PS4.
    Filters out diffuse differences (e.g. character art like charasele_02_38).
    """
    bw, bh = w3 // 4, h3 // 4
    if bw <= 0 or bh <= 0:
        return []

    box_filter = getattr(Image.Resampling, "BOX", getattr(Image, "BOX", None))
    im4_down = im4.resize((w3, h3), resample=box_filter)

    r3 = ycbcr_to_rgb(np.array(im3, dtype=np.float32))
    r4 = ycbcr_to_rgb(np.array(im4_down, dtype=np.float32))

    vis3 = r3[:, :, :3] * (r3[:, :, 3:4] / 255.0)
    vis4 = r4[:, :, :3] * (r4[:, :, 3:4] / 255.0)
    diff = np.max(np.abs(vis3 - vis4), axis=-1)

    px_ch = diff > 45.0
    b_cnt = px_ch.reshape(bh, 4, bw, 4).sum(axis=(1, 3))
    raw_mask = b_cnt >= 4
    if not np.any(raw_mask):
        return []

    if np.count_nonzero(raw_mask) > 0.7 * bw * bh:
        return [[0, 0, bw - 1, bh - 1]]

    # Dilate by 1 block
    dilated = raw_mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            y_src = slice(max(0, -dy), min(bh, bh - dy))
            x_src = slice(max(0, -dx), min(bw, bw - dx))
            y_dst = slice(max(0, dy), min(bh, bh + dy))
            x_dst = slice(max(0, dx), min(bw, bw + dx))
            dilated[y_dst, x_dst] |= raw_mask[y_src, x_src]

    # Label connected components
    try:
        from scipy import ndimage
        lbl, num_features = ndimage.label(dilated)
    except ImportError:
        lbl = np.zeros((bh, bw), dtype=int)
        num_features = 0
        for y in range(bh):
            for x in range(bw):
                if dilated[y, x] and lbl[y, x] == 0:
                    num_features += 1
                    queue = [(y, x)]
                    lbl[y, x] = num_features
                    while queue:
                        cy, cx = queue.pop(0)
                        for n_dy, n_dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            ny, nx = cy + n_dy, cx + n_dx
                            if 0 <= ny < bh and 0 <= nx < bw and dilated[ny, nx] and lbl[ny, nx] == 0:
                                lbl[ny, nx] = num_features
                                queue.append((ny, nx))

    valid_rects = []
    for i in range(1, num_features + 1):
        by, bx = np.where(lbl == i)
        raw_count = np.count_nonzero(raw_mask[by, bx])
        if raw_count >= 8:
            valid_rects.append([int(bx.min()), int(by.min()), int(bx.max()), int(by.max())])

    # If diffuse across more than 10 components, it is art/downsampling noise, not localized UI text
    if len(valid_rects) > 10:
        return []

    # Merge overlapping
    changed = True
    while changed:
        changed = False
        merged = []
        skip = set()
        for i in range(len(valid_rects)):
            if i in skip:
                continue
            r1 = valid_rects[i]
            for j in range(i + 1, len(valid_rects)):
                if j in skip:
                    continue
                r2 = valid_rects[j]
                if not (r1[2] < r2[0] or r2[2] < r1[0] or r1[3] < r2[1] or r2[3] < r1[1]):
                    r1 = [min(r1[0], r2[0]), min(r1[1], r2[1]), max(r1[2], r2[2]), max(r1[3], r2[3])]
                    skip.add(j)
                    changed = True
            merged.append(r1)
        valid_rects = merged

    return valid_rects


def classify(
    ps3_tex: bytes,
    ps4_tex: bytes,
    threshold: float = 12.0,
    texture_path: Optional[str] = None,
    path: Optional[str] = None,
    rects: Optional[list[list[int]]] = None,
) -> dict[str, Any]:
    """Classifies whether the PS3 texture contains an English translation compared
    to the native PS4 original.

    Returns dict with:
      - translated: bool
      - score: float (mean alpha difference over mask)
      - rgb_diff: float (mean RGB difference over mask)
      - changed_pixels: int (count of significantly changed pixels)
      - reason: Optional[str] (skip reason if not translated or unsupported)
      - ps3_dims: (w, h)
      - ps4_dims: (w, h)
    """
    t_path = texture_path if texture_path is not None else path
    if t_path is not None and not is_ui_text_texture(t_path):
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
            "changed_pixels": 0,
            "reason": "non_text_texture",
            "ps3_dims": (0, 0),
            "ps4_dims": (0, 0),
        }

    try:
        h3 = parse_tex_header(ps3_tex, endian=">")
        h4 = parse_tex_header(ps4_tex, endian="<")
    except Exception as e:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
            "changed_pixels": 0,
            "reason": f"header_error: {e}",
            "ps3_dims": (0, 0),
            "ps4_dims": (0, 0),
        }

    w3, h3_px = h3["width"], h3["height"]
    w4, h4_px = h4["width"], h4["height"]

    # Format check: fmt 42 is BC3
    if h3["format"] != 42 or h4["format"] != 42:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
            "changed_pixels": 0,
            "reason": "fmt_unsupported",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
        }

    # Single mip level only in v1
    if h3["mips"] != 1 or h4["mips"] != 1:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
            "changed_pixels": 0,
            "reason": "mips_unsupported",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
        }

    if w3 <= 0 or h3_px <= 0 or w4 <= 0 or h4_px <= 0:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
            "changed_pixels": 0,
            "reason": "size_mismatch",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
        }

    # Ratio check: usually 2x or 1x
    if not ((w4 == w3 * 2 and h4_px == h3_px * 2) or (w4 == w3 and h4_px == h3_px)):
        if (w4 * h3_px != w3 * h4_px) or (w4 < w3) or (h4_px < h3_px):
            return {
                "translated": False,
                "score": 0.0,
                "rgb_diff": 0.0,
                "changed_pixels": 0,
                "reason": "size_mismatch",
                "ps3_dims": (w3, h3_px),
                "ps4_dims": (w4, h4_px),
            }

    # Tiling support check
    ps4_payload = ps4_tex[h4["pixel_offset"]:]
    tiled_size = (w4 // 4) * (h4_px // 4) * 16
    if len(ps4_payload) != tiled_size:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
            "changed_pixels": 0,
            "reason": "tiling_unsupported",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
        }

    try:
        deswz = deswizzle_bc3(ps4_payload, w4, h4_px)
        reswz = swizzle_bc3(deswz, w4, h4_px)
        if reswz != ps4_payload:
            return {
                "translated": False,
                "score": 0.0,
                "rgb_diff": 0.0,
                "changed_pixels": 0,
                "reason": "tiling_unsupported",
                "ps3_dims": (w3, h3_px),
                "ps4_dims": (w4, h4_px),
            }
    except Exception:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
            "changed_pixels": 0,
            "reason": "tiling_unsupported",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
        }

    # Decode both images
    ps3_payload = ps3_tex[h3["pixel_offset"]:]
    try:
        im3 = decode_bc3_to_rgba(ps3_payload, w3, h3_px)
        im4 = decode_bc3_to_rgba(deswz, w4, h4_px)
    except Exception as e:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
            "changed_pixels": 0,
            "reason": f"decode_error: {e}",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
        }

    # Downscale PS4 to PS3 size with a box filter
    box_filter = getattr(Image.Resampling, "BOX", getattr(Image, "BOX", None))
    im4_down = im4.resize((w3, h3_px), resample=box_filter)

    arr3 = np.array(im3)
    arr4 = np.array(im4_down)

    # Purge transparent chroma-key magenta and low-alpha texels before comparison
    # so transparent metadata does not cause false-positive RGB differences
    purge3 = (
        (arr3[:, :, 3] <= 16)
        | (
            (np.abs(arr3[:, :, 0].astype(int) - 123) <= 15)
            & (np.abs(arr3[:, :, 2].astype(int) - 123) <= 15)
            & (arr3[:, :, 1] <= 25)
        )
    )
    arr3_clean = arr3.copy()
    arr3_clean[purge3, :3] = 0
    arr3_clean[purge3, 3] = 0

    purge4 = (
        (arr4[:, :, 3] <= 16)
        | (
            (np.abs(arr4[:, :, 0].astype(int) - 123) <= 15)
            & (np.abs(arr4[:, :, 2].astype(int) - 123) <= 15)
            & (arr4[:, :, 1] <= 25)
        )
    )
    arr4_clean = arr4.copy()
    arr4_clean[purge4, :3] = 0
    arr4_clean[purge4, 3] = 0

    mask = (arr4_clean[:, :, 3] > 32) | (arr3_clean[:, :, 3] > 32)
    diff_a = np.abs(arr3_clean[:, :, 3].astype(float) - arr4_clean[:, :, 3].astype(float))
    diff_rgb_channels = np.abs(arr3_clean[:, :, :3].astype(float) - arr4_clean[:, :, :3].astype(float))
    diff_rgb_mean = np.mean(diff_rgb_channels, axis=-1)

    if np.any(mask):
        score = float(np.mean(diff_a[mask]))
        rgb_diff = float(np.mean(diff_rgb_channels[mask]))
    else:
        score = 0.0
        rgb_diff = 0.0

    # Count significantly changed pixels (alpha diff > 40 or mean RGB diff > 40 on visible pixels)
    vis_mask = (arr3_clean[:, :, 3] > 16) | (arr4_clean[:, :, 3] > 16)
    changed_mask = (diff_a > 40.0) | ((diff_rgb_mean > 40.0) & vis_mask)
    changed_pixel_count = int(np.count_nonzero(changed_mask))

    total_pixels = w3 * h3_px
    ratio = changed_pixel_count / max(1, total_pixels)

    if rects is not None:
        is_translated = len(rects) > 0
        return {
            "translated": is_translated,
            "score": score,
            "rgb_diff": rgb_diff,
            "changed_pixels": changed_pixel_count,
            "reason": None if is_translated else "not_translated",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
            "rects": rects,
        }

    # Dual condition: global score >= threshold OR localized changed pixel cluster
    candidate_translated = bool(
        (score >= threshold)
        or (changed_pixel_count >= 250 and score >= 2.0)
        or (changed_pixel_count >= 250 and ratio >= 0.0005 and score >= 0.5)
    )

    if candidate_translated:
        fallback_rects = find_ps3_ps4_localized_rects(im3, im4, w3, h3_px)
        is_translated = len(fallback_rects) > 0
    else:
        fallback_rects = []
        is_translated = False

    return {
        "translated": is_translated,
        "score": score,
        "rgb_diff": rgb_diff,
        "changed_pixels": changed_pixel_count,
        "reason": None if is_translated else "not_translated",
        "ps3_dims": (w3, h3_px),
        "ps4_dims": (w4, h4_px),
        "rects": fallback_rects if is_translated else [],
    }


def convert_ps3_to_ps4(
    ps3_tex: bytes,
    ps4_tex: bytes,
    rects: Optional[list[list[int]]] = None,
) -> bytes:
    """Converts a translated PS3 BC3 texture to the matching PS4 format.
    Upscales to PS4 resolution using premultiplied Lanczos interpolation,
    applies halo purging and remaster sharpening (Hermite S-curve and unsharp mask),
    applies alpha cleanup to avoid atlas neighbour bleed, compresses to BC3
    using etcpak, and composites changed regions onto the PS4 original texture.
    """
    try:
        import etcpak
    except ImportError as e:
        raise ImportError("etcpak is required for texture repatching: pip install etcpak") from e

    h3 = parse_tex_header(ps3_tex, endian=">")
    h4 = parse_tex_header(ps4_tex, endian="<")

    w3, h3_px = h3["width"], h3["height"]
    w4, h4_px = h4["width"], h4["height"]
    pix_off4 = h4["pixel_offset"]
    scale_x = max(1, w4 // w3)
    scale_y = max(1, h4_px // h3_px)
    bw4, bh4 = w4 // 4, h4_px // 4

    ps4_payload = ps4_tex[pix_off4:]
    deswz_ps4 = deswizzle_bc3(ps4_payload, w4, h4_px)
    im4 = decode_bc3_to_rgba(deswz_ps4, w4, h4_px)

    # 1. Decode PS3 linear BC3 and move it out of the stored YCbCr layout into real RGB
    ps3_payload = ps3_tex[h3["pixel_offset"]:]
    im3 = decode_bc3_to_rgba(ps3_payload, w3, h3_px)

    if rects is None:
        rects = find_ps3_ps4_localized_rects(im3, im4, w3, h3_px)
        if not rects:
            # No localized text region found: preserve untouched native PS4 texture
            return ps4_tex

    arr3 = ycbcr_to_rgb(np.array(im3, dtype=np.float32))

    # 2. Fully transparent texels carry no colour: make them black so they cannot bleed when
    # resampled (in RGB this is a real black, not the extreme chroma a raw zero would be).
    a3 = arr3[:, :, 3]
    arr3[a3 == 0, :3] = 0.0

    # 3. Premultiply alpha before resize to eliminate color halos
    alpha3 = arr3[:, :, 3] / 255.0
    rgb_pre = (arr3[:, :, :3] * alpha3[..., None]).astype(np.uint8)
    rgba_pre = np.dstack([rgb_pre, arr3[:, :, 3].astype(np.uint8)])

    # 4. Lanczos resize
    lanczos = getattr(Image.Resampling, "LANCZOS", getattr(Image, "LANCZOS", None))
    im_pre = Image.fromarray(rgba_pre, mode="RGBA")
    im_resized = im_pre.resize((w4, h4_px), resample=lanczos)
    arr_resized = np.array(im_resized, dtype=np.float32)

    # 5. Unpremultiply
    alpha_res = arr_resized[:, :, 3]
    alpha_norm = np.maximum(alpha_res / 255.0, 1e-6)
    rgb_unpre = np.clip(np.round(arr_resized[:, :, :3] / alpha_norm[..., None]), 0, 255)

    # 6. Sharpness Enhancement (Remaster Pipeline):
    # Subtle unsharp mask on RGB where alpha > 32 to produce crisp vector-like text edges
    im_rgb = Image.fromarray(rgb_unpre.astype(np.uint8), mode="RGB")
    im_rgb_sharp = im_rgb.filter(ImageFilter.UnsharpMask(radius=1.0, percent=125, threshold=2))
    arr_rgb_sharp = np.array(im_rgb_sharp, dtype=np.float32)
    vis_mask = alpha_res > 32
    rgb_unpre[vis_mask] = arr_rgb_sharp[vis_mask]

    # 7. Zero-alpha mask: wherever PS3 source alpha (nearest-upscaled) is 0, force output alpha to 0
    nearest = getattr(Image.Resampling, "NEAREST", getattr(Image, "NEAREST", None))
    orig_alpha_im = Image.fromarray(arr3[:, :, 3].astype(np.uint8), mode="L")
    im_alpha_nearest = orig_alpha_im.resize((w4, h4_px), resample=nearest)
    zero_mask = (np.array(im_alpha_nearest) == 0)
    alpha_res[zero_mask] = 0.0

    # For anti-bleed: transparent texels are black (neutral chroma once stored)
    rgb_unpre[alpha_res == 0] = 0.0

    upscaled_ps3_rgb = np.dstack([rgb_unpre, alpha_res]).astype(np.float32)

    # 8. Region-limited compositing onto PS4 in RGB
    arr4_rgb = ycbcr_to_rgb(np.array(im4, dtype=np.float32))

    block_mask_4 = np.zeros((bh4, bw4), dtype=bool)
    for bx0, by0, bx1, by1 in rects:
        bx0_4 = max(0, min(bw4, bx0 * scale_x))
        bx1_4 = max(0, min(bw4, (bx1 + 1) * scale_x))
        by0_4 = max(0, min(bh4, by0 * scale_y))
        by1_4 = max(0, min(bh4, (by1 + 1) * scale_y))
        block_mask_4[by0_4:by1_4, bx0_4:bx1_4] = True

    pixel_mask_4 = np.repeat(np.repeat(block_mask_4, 4, axis=0), 4, axis=1)

    composite_rgb = arr4_rgb.copy()
    composite_rgb[pixel_mask_4] = upscaled_ps3_rgb[pixel_mask_4]

    final_rgba = rgba_image_to_stored(Image.fromarray(np.round(composite_rgb).astype(np.uint8), mode="RGBA"))

    # 9. Compress to linear BC3
    comp_bc3 = etcpak.compress_bc3(final_rgba.tobytes(), w4, h4_px)
    comp_blocks = np.frombuffer(comp_bc3, dtype=np.dtype("V16")).reshape((bh4, bw4))

    # Keep native PS4 blocks outside the region mask
    ps4_orig_blocks = np.frombuffer(deswz_ps4, dtype=np.dtype("V16")).reshape((bh4, bw4)).copy()
    ps4_orig_blocks[block_mask_4] = comp_blocks[block_mask_4]

    # 10. Swizzle to PS4 tiled order
    swizzled_payload = swizzle_bc3(ps4_orig_blocks.tobytes(), w4, h4_px)

    # 11. Splice into original PS4 header verbatim
    converted_tex = ps4_tex[:pix_off4] + swizzled_payload
    if len(converted_tex) != len(ps4_tex):
        raise ValueError(
            f"Converted TEX length mismatch: got {len(converted_tex)}, expected {len(ps4_tex)}"
        )

    return converted_tex
