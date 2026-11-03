# Dual-Monitor Co-op Architecture: 1920x2160 Tall-Canvas Presentation

This document provides a comprehensive technical reference for the dual-monitor cooperative multiplayer implementation in Sengoku Basara 4 Sumeragi (CUSA01159) on PC.

---

## 1. Overview & Architectural Principle

In Capcom's **MT Framework** (64-bit PS4 engine), 2-player co-op natively uses a top/bottom horizontal split-screen:
- **Player 1 (Mode 2):** Top half of canvas
- **Player 2 (Mode 3):** Bottom half of canvas

In standard 1080p operation, this produces two ultra-wide viewports of `1920x540` (32:9 aspect ratio).

Rather than forcing MT Framework into vertical split mode (which previously failed due to 8:9 viewport squishing and projection matrix mismatches), the **1920x2160 Tall-Canvas** architecture preserves the engine's native horizontal split logic while doubling the vertical render canvas:

```
    Engine Render Target: 1920x2160 Canvas
    +------------------------------------------+
    |                                          |
    |      Player 1 Viewport: Mode 2           |  --> Blit to Left Monitor (0, 0, 1920, 1080)
    |          1920 x 1080 (16:9)              |
    |                                          |
    +------------------------------------------+
    |                                          |
    |      Player 2 Viewport: Mode 3           |  --> Blit to Right Monitor (1920, 0, 1920, 1080)
    |          1920 x 1080 (16:9)              |
    |                                          |
    +------------------------------------------+
                         |
                         v
    Swapchain: 3840x1080 Borderless Window across 2 Displays
    +-------------------------+-------------------------+
    |  Monitor 1 (Player 1)   |  Monitor 2 (Player 2)   |
    |    1920 x 1080 (16:9)   |    1920 x 1080 (16:9)   |
    +-------------------------+-------------------------+
```

### Why Tall Canvas Works
1. **Mathematical Viewport Slicing:**
   - Mode 2 calculates: `top = 0`, `bottom = height / 2` $\rightarrow [0, 0, 1920, 1080]$
   - Mode 3 calculates: `top = height / 2`, `bottom = height` $\rightarrow [0, 1080, 1920, 2160]$
   Both viewports are mathematically exact 16:9 rectangles ($1920 \times 1080$).
2. **Camera Projection Coherence:**
   The engine derives camera aspect ratios dynamically via `viewport_width / viewport_height`:
   $$\text{Aspect} = \frac{1920}{1080} = 1.77778 \quad (16:9)$$
   No 3D aspect ratio patch, field-of-view alteration, or un-stretch hack is required.
3. **1:1 Pixel Presentation:**
   The Vulkan presenter copies both 1920x1080 halves directly into the 3840x1080 swapchain with zero scaling, zero filtering blur, and negligible GPU transfer overhead (<0.05 ms).

---

## 2. Eboot Binary Patches (`launcher/basara4_patches.py`)

The game's internal canvas dimensions are hardcoded within MT Framework's `sRender` class inside `eboot.elf`. The tall-canvas patch modifies the height constant from `1080` (`0x438`) to `2160` (`0x870`) across four unique code sites:

| Site | Location | Original Instruction | Patched Instruction | Purpose |
|:---:|:---:|:---|:---|:---|
| **1** | VA `0xdbd28e` (file `0xdc128e`) | `c7 83 bc 00 00 00 38 04 00 00` (`mov [rbx+0xbc], 0x438`) | `... 70 08 00 00` (`0x870`) | `sRender` constructor default height |
| **2** | VA `0dbc326` (file `0xdc0326`) | `41 c7 85 bc 00 00 00 38 04 00 00` (`mov [r13+0xbc], 0x438`) | `... 70 08 00 00` (`0x870`) | `sRender::initResolution` active height |
| **3** | VA `0xdbc360` (file `0xdc0360`) | `48 b8 80 07 00 00 38 04 00 00` (`movabs rax, 0x43800000780`) | `... 70 08 00 00` (`0x870`) | `sRender::initResolution` fallback mode |
| **4** | VA `0xdbcbe9` (file `0xdc0be9`) | `48 b8 80 07 00 00 38 04 00 00` (`movabs rax, 0x43800000780`) | `... 70 08 00 00` (`0x870`) | `sRender::initDisplayModeTable` resolution mode |

All intermediate render targets (HDR color buffer, G-buffers, depth/stencil buffer) are allocated dynamically from `sRender`'s dimensions and automatically scale to 1920x2160 without intermediate clamps.

---

## 3. Background: The Abandoned Vertical-Split Approach

Prior to the tall-canvas implementation, a vertical split approach was attempted and subsequently **abandoned**. The technical reasons are documented below for historical context:

1. **Jump Table Redirection (Mode 2 $\rightarrow$ 4, Mode 3 $\rightarrow$ 5):**
   - The engine's viewport jump table at VA `0xd90b50` contains entries for vertical left-half (Mode 4) and vertical right-half (Mode 5).
   - On a standard 1080p canvas (`1920x1080`), Modes 4 and 5 produce two `960x1080` viewports (an 8:9 aspect ratio).
   - Displaying 8:9 viewports across two 16:9 monitors stretched each player's picture horizontally by $2\times$.
2. **Camera Projection Matrix Incoherence:**
   - Attempting to double the scalar camera aspect ratio at VA `0x1b23a8` (`ASPECT_SITE`) failed because the internal projection matrix setup function at VA `0xd9035c` still computed projection scale deltas using `scale_x = width / (right - left) = width / 960` and `scale_y = height / 1080`.
   - This caused internal distortion and clipping that could not be corrected with a simple aspect scalar patch.
3. **HUD Patch Target Mismatch:**
   - The previously targeted `HUD_P2_SITE` at VA `0x21fa8f` was discovered via deep static disassembly to be an effect controller (`uEc000_002`) for particle coordinates, **not** player GUI elements.
   - Real HUD layout data is authored in an external stage resource file (`ui\offset\pause_ofs` via `rPauseOffsetTbl`), leaving Player 2's health bars and Basara gauges stranded in Player 1's display area.

The tall canvas resolves all three issues at the root by matching the engine's native 16:9 horizontal split math.

---

## 4. Vulkan Presenter Implementation (`vk_presenter.cpp`)

Presentation across dual monitors is handled in `Presenter::Present`:

- When the swapchain spans a dual-monitor width (`extent.width >= 3840 && extent.height <= 1440`):
  - **Tall Canvas Detection:** Checked via `frame->height >= 2 * extent.height` (e.g. 2160 vs 1080).
  - **Battle Split Mode (Default):**
    - Blit 1: Top half `(0, 0, 1920, 1080)` $\rightarrow$ Left monitor `(0, 0, 1920, 1080)`.
    - Blit 2: Bottom half `(0, 1080, 1920, 1080)` $\rightarrow$ Right monitor `(1920, 0, 1920, 1080)`.
  - **Mirror Mode (F8 Toggle):**
    - Blit 1: Top half `(0, 0, 1920, 1080)` $\rightarrow$ Left monitor `(0, 0, 1920, 1080)`.
    - Blit 2: Top half `(0, 0, 1920, 1080)` $\rightarrow$ Right monitor `(1920, 0, 1920, 1080)`.
  - **Non-tall Frames:** Mirrors full 16:9 frame across both monitors.

### Hotkey Controls
- **F8 Key:** Toggles between **Battle Split** and **Menu Mirroring**. In menus, cutscenes, and stage preparation, pressing F8 duplicates the menu interface cleanly to both monitors.

---

## 5. System Requirements & Operational Constraints

1. **GPU Power & VRAM (6 GB+ Recommended):**
   - A 1920x2160 canvas renders approximately **4.15 million pixels per frame** (identical pixel count to half of 4K, or $2\times$ standard 1080p).
   - Higher fill rate and VRAM capacity are required to maintain a consistent 60 FPS.
2. **Direct Memory Pool (`SPARTAN_DMEM_MB=6144`):**
   - The launcher allocates a 6 GiB direct memory pool for the runtime and bypasses the low-memory caps (2048/2560 MiB) to ensure the engine's expanded render targets allocate reliably without host memory exhaustion.
3. **Display Configuration:**
   - Requires two side-by-side 1920x1080 monitors arranged horizontally in Windows Display Settings.
   - The launcher automatically forces window mode to **3840x1080 Borderless**.
4. **Known Engine Limitations:**
   - See [Section 6: Status and Known Issues](#6-status-and-known-issues-v120-experimental) below for detailed technical root causes regarding UI squashing, minimap positioning, missing markers, and banner seams.

---

## 6. Status and Known Issues (v1.2.0 Experimental)

> **Development Status**: Feature development for dual-monitor co-op is currently paused at this implementation state and ships as **EXPERIMENTAL** in v1.2.0. Known issues are tracked in GitHub issue [#1](https://github.com/DarkTama/basara4-native-pc/issues/1).

### Verified Working
- In-battle 2-player split rendering using native MT Framework horizontal split logic doubled to a 1920x2160 tall canvas.
- Unstretched 16:9 1080p perspective and projection for each player without 3D aspect ratio distortion.
- Independent Player 1 and Player 2 HUD elements (health bars, Basara meters) mapped to respective monitors.
- **F8 Hotkey**: Live toggle between dual-monitor battle split and mirrored canvas blit.

### Known Issues & Technical Root Causes

1. **Vertically Squashed Menus, Preparation, Results & Cutscenes:**
   - **Symptom:** UI menus, battle preparation screens, end-of-stage results, and in-engine 3D cutscenes appear vertically squashed (characters appear wide / "fat"), even when pressing F8 for mirror mode.
   - **Root Cause:** All non-split engine phases (menus, cutscenes, stage prep) are hardcoded in MT Framework to render into the full viewport of the primary `sRender` render target assuming a standard 16:9 canvas. Because the patched canvas is 1920x2160 (8:9 aspect ratio), the full canvas presentation squashes the vertical dimension by half.

2. **Fullscreen Banners and Popups Sliced at Seam:**
   - **Symptom:** When mirror mode is disabled, full-screen notifications, festival banners, and stage tutorial dialogues are cut directly in half at the seam between the two monitors (top half on the left monitor, bottom half on the right monitor).
   - **Root Cause:** These 2D banners are drawn across the entire vertical height of the tall canvas rather than restricted to a single player's viewport.

3. **Distorted Fullscreen 2D Effects (Roulette / Spinning Wheel):**
   - **Symptom:** Mini-game roulette wheels and certain 2D post-processing overlays exhibit stretching and distortion.
   - **Root Cause:** These effects render directly to the tall canvas backbuffer using hardcoded 1080p UV coordinates and projection parameters without awareness of the 2160-height render target.

4. **Minimap Cut at Center Seam:**
   - **Symptom:** The in-game minimap is sliced across the horizontal boundary separating Player 1 and Player 2 viewports.
   - **Root Cause:** The minimap is positioned based on engine split-screen coordinates that sit across the vertical halfway line on the 2160 canvas.

5. **Missing Minimap Markers (Player, Ally, and Enemy Pointers):**
   - **Symptom:** Minimap navigation markers (character arrows, ally markers, enemy indicators) do not appear on the minimap display.
   - **Root Cause:** In the MT Framework GUI coordinate scaling routine at virtual address **VA `0x1d9b90`**, the engine calculates interface scale factors dynamically:
     $$\text{scale\_x} = \frac{\text{width}}{1280.0}, \quad \text{scale\_y} = \frac{\text{height}}{720.0}$$
     Under native 1080p, $\text{scale\_y} = \frac{1080}{720} = 1.5$.
     With the 1920x2160 tall canvas patch, `height` is 2160, yielding:
     $$\text{scale\_y} = \frac{2160}{720} = 3.0$$
     This doubles the vertical coordinate calculations for `uGUINaviIcon` and `uGUINaviMap` marker rendering, placing the marker coordinates far outside the visible viewport boundaries. (Currently unpatched).

6. **High Hardware Demands & In-Battle FPS Drops:**
   - **Symptom:** Framerate can drop significantly during intense battles with many enemy units and particle effects (e.g. dropping to ~53–90 FPS on an AMD Radeon RX 6700 XT compared to 165 FPS in single-player mode).
   - **Root Cause:** The tall canvas renders ~4.15 million pixels per frame ($2\times$ the rasterization, fill rate, and shader workload of standard 1080p) and requires an expanded 6 GiB direct memory pool (`SPARTAN_DMEM_MB=6144`).

7. **Rigid Dual-Monitor Display Setup Requirement:**
   - Requires two physical 1920x1080 displays aligned horizontally side-by-side in Windows Display Settings to form a continuous 3840x1080 desktop. Vertical setups, mixed resolutions (e.g., 1440p + 1080p), and single ultrawide monitors without borderless positioning are currently unsupported.

### Feedback and Issue Reporting
To report additional issues, visual defects, or performance profiles:
- GitHub Issue tracker: [#1](https://github.com/DarkTama/basara4-native-pc/issues/1)
- Launcher: Use the built-in **"Report a problem"** feature.
- Please include your GPU model, monitor resolution and arrangement, screenshots of the glitch, and your log file: `user/last_run.log`.
