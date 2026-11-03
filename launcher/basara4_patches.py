"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Sengoku Basara 4 Sumeragi (CUSA01159) memory patches for the runtime loader.

Writes patches.bin (G3PATCH2: patch base, count, then offset/length/bytes per write), found by code
signature in the game's eboot.elf so they apply to any build with the same code.

unlimited_fps: the game's own frame limiter (a sleep followed by a spin on the TSC until one frame
period has passed) is skipped. The game measures the real time each frame took and scales its update
by it, so a higher frame rate does not make it run faster.
"""
import argparse
import struct
import sys
from pathlib import Path

EBOOT_BASE = 0x400000

# vsubss/vmulss, vucomiss, jb (sleep only when the wait is long) ... call usleep ... vucomiss, jbe (leave
# the spin loop at once when the period has passed). "??" bytes are displacements.
LIMITER = ("c5 e2 5c d0 c5 f2 59 ca c5 f8 2e 0d ?? ?? ?? ?? 72 20 c4 e1 fa 2c c1 69 f8 e8 03 00 00 "
           "81 c7 18 fc ff ff e8 ?? ?? ?? ?? c5 fa 10 5d cc c5 fa 10 43 60 c5 f8 2e d8 "
           "c5 fb 10 4d d0 0f 86 ?? ?? ?? ??")
SLEEP_JB, SPIN_JBE = 16, 59

# Site 1: sRender constructor resolution default (VA 0xdbd28e, file 0xdc128e)
# mov dword ptr [rbx + 0xb8], 0x780; mov dword ptr [rbx + 0xbc], 0x438
RENDER_CONSTR = "c7 83 b8 00 00 00 80 07 00 00 c7 83 bc 00 00 00 38 04 00 00"
RENDER_CONSTR_IMM_OFFSET = 16

# Site 2: sRender::initResolution active height (VA 0xdbc326, file 0xdc0326)
# mov dword ptr [r13 + 0xb8], 0x780; mov dword ptr [r13 + 0xbc], 0x438
RENDER_INIT_RES = "41 c7 85 b8 00 00 00 80 07 00 00 41 c7 85 bc 00 00 00 38 04 00 00"
RENDER_INIT_RES_IMM_OFFSET = 18

# Site 3: sRender::initResolution fallback height (VA 0xdbc360, file 0xdc0360)
# movabs rax, 0x43800000780; mov qword ptr [r13 + 0xb8], rax
RENDER_FALLBACK = "48 b8 80 07 00 00 38 04 00 00 49 89 85 b8 00 00 00"
RENDER_FALLBACK_IMM_OFFSET = 6

# Site 4: sRender::initDisplayModeTable mode 2 height (VA 0xdbcbe9, file 0xdc0be9)
# movabs rax, 0x43800000780; mov qword ptr [rbx + r13 + 0x107ab80], rax
RENDER_MODE_TABLE = "48 b8 80 07 00 00 38 04 00 00 4a 89 84 2b 80 ab 07 01"
RENDER_MODE_TABLE_IMM_OFFSET = 6

HEIGHT_2160 = struct.pack('<I', 2160)  # b'\x70\x08\x00\x00'


def eboot_image(elf):
    """The PT_LOAD bytes of eboot.elf at their virtual addresses."""
    phoff, = struct.unpack_from('<Q', elf, 0x20)
    phentsize, phnum = struct.unpack_from('<HH', elf, 0x36)
    loads = []
    for i in range(phnum):
        kind, _, offset, vaddr, _, filesz, _, _ = struct.unpack_from('<IIQQQQQQ', elf, phoff + i * phentsize)
        if kind == 1:
            loads.append((vaddr, offset, filesz))
    image = bytearray(max(v + f for v, _, f in loads))
    for vaddr, offset, filesz in loads:
        image[vaddr:vaddr + filesz] = elf[offset:offset + filesz]
    return bytes(image)


def find(image, text):
    pattern = [None if b == '??' else int(b, 16) for b in text.split()]
    # longest run of fixed bytes
    best, run_start = (0, 0), None
    for i, b in enumerate(pattern + [None]):
        if b is not None and run_start is None:
            run_start = i
        if b is None and run_start is not None:
            if i - run_start > best[1] - best[0]:
                best = (run_start, i)
            run_start = None
    a, b = best
    anchor = bytes(pattern[a:b])
    found, at = [], image.find(anchor)
    while at >= 0:
        s = at - a
        if s >= 0 and s + len(pattern) <= len(image) and all(
                p is None or image[s + i] == p for i, p in enumerate(pattern)):
            found.append(s)
        at = image.find(anchor, at + 1)
    return found


def unlimited_fps(image):
    """[(image offset, bytes)] that remove the game's frame wait, or raise LookupError."""
    sites = find(image, LIMITER)
    if len(sites) != 1:
        raise LookupError('frame limiter code not found' if not sites else 'frame limiter code found twice')
    base = sites[0]
    jbe = base + SPIN_JBE
    disp, = struct.unpack_from('<i', image, jbe + 2)
    return [(base + SLEEP_JB, b'\xeb\x20'),
            (jbe, b'\xe9' + struct.pack('<i', disp + 1) + b'\x90')]


def tall_canvas_coop(image):
    """[(image offset, bytes)] that increases sRender height from 1080 to 2160 across all 4 sites."""
    sites1 = find(image, RENDER_CONSTR)
    if len(sites1) != 1:
        raise LookupError('sRender constructor height not found' if not sites1 else 'sRender constructor height found twice')

    sites2 = find(image, RENDER_INIT_RES)
    if len(sites2) != 1:
        raise LookupError('sRender initResolution height not found' if not sites2 else 'sRender initResolution height found twice')

    sites3 = find(image, RENDER_FALLBACK)
    if len(sites3) != 1:
        raise LookupError('sRender fallback height not found' if not sites3 else 'sRender fallback height found twice')

    sites4 = find(image, RENDER_MODE_TABLE)
    if len(sites4) != 1:
        raise LookupError('sRender mode table height not found' if not sites4 else 'sRender mode table height found twice')

    return [
        (sites1[0] + RENDER_CONSTR_IMM_OFFSET, HEIGHT_2160),
        (sites2[0] + RENDER_INIT_RES_IMM_OFFSET, HEIGHT_2160),
        (sites3[0] + RENDER_FALLBACK_IMM_OFFSET, HEIGHT_2160),
        (sites4[0] + RENDER_MODE_TABLE_IMM_OFFSET, HEIGHT_2160),
    ]


def write_patches(path, writes):
    blob = struct.pack('<8sQQ', b'G3PATCH2', EBOOT_BASE, len(writes))
    for offset, data in writes:
        blob += struct.pack('<QQ', offset, len(data)) + data
    Path(path).write_bytes(blob)


def build(elf_path, out_path, unlimited, tall_canvas_coop=False):
    writes = []
    notes = []
    image = eboot_image(Path(elf_path).read_bytes())
    if unlimited:
        try:
            writes += unlimited_fps(image)
            notes.append('unlimited fps')
        except LookupError as error:
            notes.append(f'unlimited fps skipped: {error}')
    if tall_canvas_coop:
        try:
            writes += globals()['tall_canvas_coop'](image)
            notes.append('tall canvas co-op')
        except LookupError as error:
            notes.append(f'tall canvas co-op skipped: {error}')
    write_patches(out_path, writes)
    return writes, notes


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('elf')
    ap.add_argument('out')
    ap.add_argument('--unlimited-fps', action='store_true')
    ap.add_argument('--tall-canvas-coop', action='store_true')
    a = ap.parse_args()
    writes, notes = build(a.elf, a.out, a.unlimited_fps, tall_canvas_coop=a.tall_canvas_coop)
    for offset, data in writes:
        print(f'  {offset + EBOOT_BASE:#x}: {data.hex()}')
    print('Patches:', ', '.join(notes) or 'none', f'({len(writes)} writes)')
