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
   - **Minimap:** MT Framework creates only one shared minimap instance (`uGUINaviMap`) in split-screen mode, positioned on Monitor 1.
   - **Menus & FMVs:** Authored for single 16:9 display. Use the **F8** toggle to mirror menus cleanly to both screens during battle preparation.
