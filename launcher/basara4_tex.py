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
from PIL import Image

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


def classify(
    ps3_tex: bytes,
    ps4_tex: bytes,
    threshold: float = 40.0,
) -> dict[str, Any]:
    """Classifies whether the PS3 texture contains an English translation compared
    to the native PS4 original.

    Returns dict with:
      - translated: bool
      - score: float (mean alpha difference over mask)
      - rgb_diff: float (mean RGB difference over mask)
      - reason: Optional[str] (skip reason if not translated or unsupported)
      - ps3_dims: (w, h)
      - ps4_dims: (w, h)
    """
    try:
        h3 = parse_tex_header(ps3_tex, endian=">")
        h4 = parse_tex_header(ps4_tex, endian="<")
    except Exception as e:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
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
            "reason": "mips_unsupported",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
        }

    if w3 <= 0 or h3_px <= 0 or w4 <= 0 or h4_px <= 0:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
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
                "reason": "tiling_unsupported",
                "ps3_dims": (w3, h3_px),
                "ps4_dims": (w4, h4_px),
            }
    except Exception:
        return {
            "translated": False,
            "score": 0.0,
            "rgb_diff": 0.0,
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
            "reason": f"decode_error: {e}",
            "ps3_dims": (w3, h3_px),
            "ps4_dims": (w4, h4_px),
        }

    # Downscale PS4 to PS3 size with a box filter
    box_filter = getattr(Image.Resampling, "BOX", getattr(Image, "BOX", None))
    im4_down = im4.resize((w3, h3_px), resample=box_filter)

    arr3 = np.array(im3)
    arr4 = np.array(im4_down)

    # mask = PS4 alpha > 32
    mask = arr4[:, :, 3] > 32
    if np.any(mask):
        score = float(np.mean(np.abs(arr3[mask, 3].astype(float) - arr4[mask, 3].astype(float))))
        rgb_diff = float(np.mean(np.abs(arr3[mask, :3].astype(float) - arr4[mask, :3].astype(float))))
    else:
        score = 0.0
        rgb_diff = 0.0

    is_translated = (score >= threshold)
    return {
        "translated": is_translated,
        "score": score,
        "rgb_diff": rgb_diff,
        "reason": None if is_translated else "not_translated",
        "ps3_dims": (w3, h3_px),
        "ps4_dims": (w4, h4_px),
    }


def convert_ps3_to_ps4(ps3_tex: bytes, ps4_tex: bytes) -> bytes:
    """Converts a translated PS3 BC3 texture to the matching PS4 format.
    Upscales to PS4 resolution using premultiplied Lanczos interpolation,
    applies alpha cleanup to avoid atlas neighbour bleed, compresses to BC3
    using etcpak, tiles (swizzles) into PS4 order, and splices into the original
    PS4 header bytes.
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

    # 1. Decode PS3 linear BC3 to RGBA
    ps3_payload = ps3_tex[h3["pixel_offset"]:]
    im3 = decode_bc3_to_rgba(ps3_payload, w3, h3_px)
    arr3 = np.array(im3, dtype=np.float32)

    # 2. Premultiply alpha before resize to eliminate color halos
    alpha3 = arr3[:, :, 3] / 255.0
    rgb_pre = (arr3[:, :, :3] * alpha3[..., None]).astype(np.uint8)
    rgba_pre = np.dstack([rgb_pre, arr3[:, :, 3].astype(np.uint8)])

    # 3. Lanczos resize
    lanczos = getattr(Image.Resampling, "LANCZOS", getattr(Image, "LANCZOS", None))
    im_pre = Image.fromarray(rgba_pre, mode="RGBA")
    im_resized = im_pre.resize((w4, h4_px), resample=lanczos)
    arr_resized = np.array(im_resized, dtype=np.float32)

    # 4. Unpremultiply
    alpha_res = arr_resized[:, :, 3]
    alpha_norm = np.maximum(alpha_res / 255.0, 1e-6)
    rgb_unpre = np.clip(np.round(arr_resized[:, :, :3] / alpha_norm[..., None]), 0, 255)

    # 5. Zero-alpha mask: wherever PS3 source alpha (nearest-upscaled) is 0, force output alpha to 0
    nearest = getattr(Image.Resampling, "NEAREST", getattr(Image, "NEAREST", None))
    orig_alpha_im = Image.fromarray(arr3[:, :, 3].astype(np.uint8), mode="L")
    im_alpha_nearest = orig_alpha_im.resize((w4, h4_px), resample=nearest)
    zero_mask = (np.array(im_alpha_nearest) == 0)
    alpha_res[zero_mask] = 0
    rgb_unpre[zero_mask] = 0

    # 6. For texels where resized alpha < 8, zero RGB to suppress outline noise
    low_alpha = (alpha_res < 8)
    rgb_unpre[low_alpha] = 0

    final_rgba = np.dstack([rgb_unpre, alpha_res]).astype(np.uint8)

    # 7. Compress to linear BC3
    comp_bc3 = etcpak.compress_bc3(final_rgba.tobytes(), w4, h4_px)

    # 8. Swizzle to PS4 tiled order
    swizzled_payload = swizzle_bc3(comp_bc3, w4, h4_px)

    # 9. Splice into original PS4 header verbatim
    converted_tex = ps4_tex[:pix_off4] + swizzled_payload
    if len(converted_tex) != len(ps4_tex):
        raise ValueError(
            f"Converted TEX length mismatch: got {len(converted_tex)}, expected {len(ps4_tex)}"
        )

    return converted_tex
