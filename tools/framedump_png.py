#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Turns the raw 32-bit frames the game dumps (user/framedump/<name>_<index>_<w>x<h>.raw) into PNGs next to them.
The presented image is the swapchain format (usually B8G8R8A8); the guest display buffer is B8G8R8A8 or R8G8B8A8
depending on the game's setting, so both channel orders are written when unsure ("_bgr" / "_rgb").

  python tools/framedump_png.py [framedump_dir]
"""
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"G:\basara4-windows\user\framedump")
pattern = re.compile(r"(.+)_(\d+)_(\d+)x(\d+)\.raw$")
done = 0
for raw in sorted(folder.glob("*.raw")):
    m = pattern.match(raw.name)
    if not m:
        continue
    name, index, width, height = m.group(1), m.group(2), int(m.group(3)), int(m.group(4))
    data = np.fromfile(raw, dtype=np.uint8)
    if data.size < width * height * 4:
        print(f"{raw.name}: short file ({data.size} bytes)")
        continue
    data = data[: width * height * 4].reshape(height, width, 4)
    bgr = raw.with_name(raw.stem + "_bgr.png")
    if not bgr.exists():
        Image.fromarray(data[:, :, [2, 1, 0]]).save(bgr)  # B8G8R8A8 -> RGB
        done += 1
    if name != "output":
        rgb = raw.with_name(raw.stem + "_rgb.png")
        if not rgb.exists():
            Image.fromarray(data[:, :, [0, 1, 2]]).save(rgb)  # R8G8B8A8 -> RGB
            done += 1
print(f"{done} PNG files written in {folder}")
