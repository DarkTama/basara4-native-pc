# Multi-Monitor & Vertical Split-Screen Co-op Architecture

This document provides a comprehensive technical reference for split-screen reverse engineering in Sengoku Basara 4 Sumeragi (CUSA01159), the Capcom MT Framework engine viewport mechanics, the active vertical split-screen binary patch, and alternative multi-monitor architectures evaluated for future development.

---

## 1. MT Framework Split-Screen Reverse Engineering

Sengoku Basara 4 Sumeragi is built upon Capcom's proprietary **MT Framework** engine (64-bit PlayStation 4 version). In cooperative multiplayer (2 Players), the engine defaults to a horizontal split-screen layout (top half for Player 1, bottom half for Player 2), rendering each player's camera into a 1920x540 viewport when running at the standard 1080p canvas resolution.

### 1.1 `sCamera::Viewport` Class

The viewport management system is governed by the `sCamera::Viewport` class:

- **Dynamic Type Information (DTI):** `0x1521a20`
- **Virtual Method Table (vtable):** `0x13e8cd0`
- **Instance Size:** `0x1a0` (416 bytes)

Each active camera owns an instance of `sCamera::Viewport`. This structure defines the normalized coordinate boundaries `(X_min, Y_min, Width, Height)` within the global framebuffer, projection matrix aspect ratio parameters, scissor rectangles, and depth clip bounds.

### 1.2 Viewport Mode Selection Jump Table

During camera initialization and layout updates, the engine selects the viewport bounding configuration via a 32-bit relative displacement jump table.

- **Virtual Address:** `0x00d90b50`
- **ELF File Offset:** `0x00d94b50` (in decrypted `eboot.elf` / `eboot.bin`)
- **Table Signature (Raw Bytes):**
  ```hex
  fa fe ff ff fd ff ff ff 00 ff ff ff 0a ff ff ff 14 ff ff ff
  32 ff ff ff 50 ff ff ff 6f ff ff ff 8e ff ff ff c6 ff ff ff
  ```

#### Jump Table Dispatch Targets

The table contains 10 entries (Mode 0 through Mode 9), with 32-bit signed relative displacements from the jump table base address (`0x00d90b50`):

| Mode | Entry Offset | Displacement Hex (LE) | Signed Displacement | Target Virtual Address | Function & Layout Description |
|:---:|:---:|:---:|:---:|:---:|:---|
| **0** | `+0x00` | `fa fe ff ff` | -262 | `0x00d90a4a` | **Fullscreen (1P)**: Used in Main Menu, Battle Preparation, Pre-rendered & Real-Time Cutscenes (`[0.0, 0.0, 1.0, 1.0]`). |
| **1** | `+0x04` | `fd ff ff ff` | -3 | `0x00d90b4d` | **Default / Invalid Return**: NOP / direct return. |
| **2** | `+0x08` | `00 ff ff ff` | -256 | `0x00d90a50` | **Horizontal Top Half (P1 Default)**: `Y ∈ [0.0, 0.5]`, `X ∈ [0.0, 1.0]`. |
| **3** | `+0x0c` | `0a ff ff ff` | -246 | `0x00d90a5a` | **Horizontal Bottom Half (P2 Default)**: `Y ∈ [0.5, 1.0]`, `X ∈ [0.0, 1.0]`. |
| **4** | `+0x10` | `14 ff ff ff` | -236 | `0x00d90a64` | **Vertical Left Half (P1 Left)**: `X ∈ [0.0, 0.5]`, `Y ∈ [0.0, 1.0]`. |
| **5** | `+0x14` | `32 ff ff ff` | -206 | `0x00d90a82` | **Vertical Right Half (P2 Right)**: `X ∈ [0.5, 1.0]`, `Y ∈ [0.0, 1.0]`. |
| **6** | `+0x18` | `50 ff ff ff` | -176 | `0x00d90aa0` | **4-Player Quadrant 0**: Top-Left (`X ∈ [0.0, 0.5]`, `Y ∈ [0.0, 0.5]`). |
| **7** | `+0x1c` | `6f ff ff ff` | -145 | `0x00d90abf` | **4-Player Quadrant 1**: Bottom-Left (`X ∈ [0.0, 0.5]`, `Y ∈ [0.5, 1.0]`). |
| **8** | `+0x20` | `8e ff ff ff` | -114 | `0x00d90ade` | **4-Player Quadrant 2**: Top-Right (`X ∈ [0.5, 1.0]`, `Y ∈ [0.0, 0.5]`). |
| **9** | `+0x24` | `c6 ff ff ff` | -58 | `0x00d90b16` | **4-Player Quadrant 3**: Bottom-Right (`X ∈ [0.5, 1.0]`, `Y ∈ [0.5, 1.0]`). |

*Discovery Note:* Capcom MT Framework natively includes full code branches for 2-player vertical split (Modes 4 and 5) and 4-player quadrant layouts (Modes 6 through 9), inherited from earlier MT Framework titles (such as *Resident Evil 5 / 6* and *Lost Planet 2*). However, Sengoku Basara 4 Sumeragi hardcodes the co-op camera allocator to assign Mode 2 to Player 1 and Mode 3 to Player 2.

### 1.3 In-Game HUD and UI Layout Offsets

In horizontal co-op mode, the 2D user interface (health bars, Basara gauges, combo counters, mini-map, weapon art indicators) is offset vertically so Player 2's HUD renders in the lower half of the 1080p screen.

- **HUD Shift Code Location:** Virtual address `0x0021fa8f`
  ```asm
  0x0021fa8f: addss xmm2, xmm2, [rip + 0xe96e45]  ; RIP + 0xe96e45 = 0x0010b68dc
  ```
- **Constant Value at `0x0010b68dc`:** `540.0f` (`0x44070000` in IEEE 754 float).
- **Behavior:** When rendering Player 2 UI elements, the rasterizer loads this `540.0f` constant to shift screen-space Y coordinates down by half of 1080 pixels (`1080 / 2 = 540`).

---

## 2. Technical Comparison of Architectural Approaches

Three distinct architectural approaches were researched to achieve dual-monitor co-op play.

| Feature / Criteria | Approach A: Native Vertical Split Patch (Active) | Approach B: Vulkan Presenter Reprojection | Approach C: Multi-Instance Sandboxed Co-op |
|:---|:---|:---|:---|
| **Primary Mechanism** | Redirect Mode 2 -> 4 & Mode 3 -> 5 in jump table via binary patch. | Intercept Vulkan swapchain presentation, blit 1920x1080 into two windows/monitors. | Launch two separate game instances with network memory/input synchronization. |
| **Implementation Complexity** | **Low / Minimal** (clean 8-byte patch in `patches.bin`). | **Medium-High** (custom Vulkan layer or SDL multi-window swapchain management). | **Very High** (network IPC, save state sync, RNG lockstep, process isolation). |
| **Performance Overhead** | **0% (Zero)**: Single render pass, single engine instance. | **< 1%**: GPU blit / compute copy overhead. | **~100%**: Dual GPU load, dual memory footprint, CPU contention. |
| **Stability & Desync Risk** | **Rock-solid**: 100% engine-native deterministic simulation. | **High**: Frame timing synchronization between multiple swapchains/monitors. | **Extremely High**: Game state divergence, physics desyncs, audio duplication. |
| **Hardware Requirements** | Standard single-GPU setup; supports ultra-wide or spanned display. | Multi-monitor setup with Vulkan multi-window capability. | Powerful CPU/GPU capable of running two concurrent instances. |
| **Input Isolation** | Native engine support: Player 1 (P1 pad/KBM), Player 2 (P2 pad). | Same as Approach A. | Requires direct input hooks / raw input device masks per process. |
| **Current Status** | **ACTIVE & SHIPPED** in launcher settings. | Documented architecture for future presenter development. | Deprecated / Theoretical alternative. |

---

## 3. Menu Mirroring & Dual Monitor Span Architecture

### 3.1 Problem Statement
When running in co-op vertical split mode across dual monitors (e.g., an AMD Eyefinity, NVIDIA Surround, or borderless window spanning `3840x1080` at 32:9), the global swapchain extends across both physical displays. 

While battle scenes cleanly render two vertical viewports (Player 1 on the left and Player 2 on the right), non-battle scenes in Sengoku Basara 4 Sumeragi are natively designed for a single 16:9 canvas (`1920x1080`):
- **Main Menu & Title Screen**
- **Character Selection & Costume / Weapon Select**
- **Battle Preparation & Basara Mart (Shop / Upgrades)**
- **Pre-rendered (FMV) & Real-time In-Engine Cutscenes**
- **Battle Results & Victory Screens**

Without dedicated handling, presenting a single 16:9 canvas across a 32:9 swapchain stretches the entire interface horizontally across the monitor bezels. Crucial centered UI elements—such as dialog boxes, character portraits, stat tables, and confirmation prompts—are sliced directly down the middle by the physical display bezels, severely degrading the experience for both players.

### 3.2 Chosen Strategy: Menu Mirroring

To ensure optimal usability without requiring invasive game UI redesigns, the architecture adopts **Menu Mirroring**. Instead of stretching or single-side letterboxing, the Vulkan presentation engine duplicates the single 1920x1080 rendered frame onto both displays.

```
                         [ Rendered 16:9 Frame (1920x1080) ]
                                    /           \
                                   /             \
                  Blit 1 (Left Half)             Blit 2 (Right Half)
                          v                               v
             [ Monitor 1: 0 .. 1920 ]          [ Monitor 2: 1920 .. 3840 ]
             (Full 16:9 Menu Clone)            (Full 16:9 Menu Clone)
```

#### State Detection
The presenter distinguishes between menu scenes and active split-screen battles through one of two mechanisms:
- **`sCamera::Viewport` Mode State Tracking**: Inspecting the engine's active camera viewport modes. When only **Mode 0** (Fullscreen `[0.0, 0.0, 1.0, 1.0]`) is active, the game is in menus, preparation, or cutscenes. When **Mode 4** (Left `[0.0, 0.5]`) and **Mode 5** (Right `[0.5, 1.0]`) are active, the game is in vertical split-screen combat.
- **Presenter Aspect Ratio Checks**: Evaluating the source frame dimensions and aspect ratio flags prior to recording swapchain blit operations.

#### Operational Behavior by State
- **During Menu / Preparation (16:9 Single Canvas)**:
  - The engine outputs a single 1920x1080 frame.
  - The Vulkan presenter records two blit operations into the 3840x1080 swapchain image:
    - **Blit 1**: `src (0..1920, 0..1080)` $\rightarrow$ `dst (0..1920, 0..1080)` (Monitor 1)
    - **Blit 2**: `src (0..1920, 0..1080)` $\rightarrow$ `dst (1920..3840, 0..1080)` (Monitor 2)
  - Both players see an undistorted, identical full 16:9 menu on their respective screens during character selection, shop purchases, and battle preparation.
- **During Battle (Vertical Split Active)**:
  - The canvas is presented directly across the 3840x1080 swapchain:
    - **Player 1 (Mode 4)** renders directly into `dst (0..1920, 0..1080)` on Monitor 1.
    - **Player 2 (Mode 5)** renders directly into `dst (1920..3840, 0..1080)` on Monitor 2.
  - Each player enjoys a dedicated full 16:9 monitor experience with zero distortion.

### 3.3 Vulkan Presenter Hook Point

The dual-blit logic is integrated directly into the emulator presentation pipeline in `gpu/shadps4/video_core/renderer_vulkan/vk_presenter.cpp`:

- **Reference Functions**:
  - `MakeImageBlit(s32 frame_width, s32 frame_height, s32 dst_width, s32 dst_height, s32 offset_x, s32 offset_y)`: Builds a `vk::ImageBlit` struct defining subresource layers, source offsets, and destination rect offsets.
  - `FitImage(s32 frame_width, s32 frame_height, s32 swapchain_width, s32 swapchain_height)`: Computes scaled aspect ratio rectangles.
- **Dual-Blit Command Recording**:
  Inside the presenter render pass, command recording issues dual `cmd.blitImage(...)` invocations when menu mirroring is active:
  ```cpp
  // Monitor 1: Blit left half of swapchain (0 .. 1920)
  const vk::ImageBlit blit_left = MakeImageBlit(
      frame->width, frame->height,
      extent.width / 2, extent.height,
      0, 0
  );
  cmdbuf.blitImage(frame->image, vk::ImageLayout::eTransferSrcOptimal,
                   swapchain_image, vk::ImageLayout::eTransferDstOptimal,
                   blit_left, vk::Filter::eLinear);

  // Monitor 2: Blit right half of swapchain (1920 .. 3840)
  const vk::ImageBlit blit_right = MakeImageBlit(
      frame->width, frame->height,
      extent.width / 2, extent.height,
      extent.width / 2, 0
  );
  cmdbuf.blitImage(frame->image, vk::ImageLayout::eTransferSrcOptimal,
                   swapchain_image, vk::ImageLayout::eTransferDstOptimal,
                   blit_right, vk::Filter::eLinear);
  ```
- **Overhead**: GPU transfer blits execute in under 0.05 ms, introducing negligible latency while maintaining perfect image fidelity.

---

## 4. Detailed Approach Breakdown

### Approach A: MT Framework Native Vertical Split Patch (Active Approach)

#### Implementation
Because MT Framework already contains the complete code for vertical left-half (Mode 4) and vertical right-half (Mode 5) viewports, the vertical split patch applies three coordinated binary modifications:

1. **Viewport Mode Jump Table Redirect**:
   - Signature: `SPLIT_TABLE` (`fa fe ff ff fd ff ff ff 00 ff ff ff 0a ff ff ff 14 ff ff ff 32 ff ff ff 50 ff ff ff 6f ff ff ff 8e ff ff ff c6 ff ff ff`)
   - Offset: `table_site + 8`
   - Replacement bytes: `b'\x14\xff\xff\xff\x32\xff\xff\xff'`
   - Redirects Mode 2 (P1 Top) to Mode 4 (P1 Left, displacement `-236`) and Mode 3 (P2 Bottom) to Mode 5 (P2 Right, displacement `-206`).

2. **Camera Aspect Ratio 16:9 Un-Stretch**:
   - Signature: `ASPECT_SITE` (`c5 ea 5e c9 c5 fa 59 c1 c3`)
   - Offset: `aspect_site + 8`
   - Replacement bytes: `b'\xc5\xf8\x2e\xd0\x76\x04\xc5\xfa\x58\xc0\xc3'`
   - Logic: Replaces the `ret` and trailing padding with:
     ```asm
     vucomiss xmm2, xmm0        ; compares 1.0f (xmm2) with camera aspect ratio (xmm0)
     jbe      keep_aspect       ; if aspect >= 1.0f, leave untouched
     vaddss   xmm0, xmm0, xmm0  ; if aspect < 1.0f (0.888 in 960x1080), double to 1.777 (16:9)
     keep_aspect:
     ret
     ```
     In vertical split mode on a 1920x1080 canvas, each half has an 8:9 aspect ratio (~0.888). Doubling this to 16:9 (~1.777) ensures the 3D projection renders with correct proportions when each viewport is presented/stretched across a 16:9 monitor.

3. **Player 2 HUD Repositioning to Monitor 2**:
   - Signature: `HUD_P2_SITE` (`c4 c1 7a 10 57 54 c5 ea 58 15 ?? ?? ?? ?? c4 c1 7a 58 47 58`)
   - Offset: `hud_site + 6`
   - Original instruction (8 bytes): `vaddss xmm2, xmm2, [rip + 0xe96e45]` (adds 540.0f to Y, positioning P2 at bottom half)
   - Replacement bytes: `b'\xc5\xf2\x58\x0d\xa1\x07\x0a\x01'` (`vaddss xmm1, xmm1, [rip + 0x10a07a1]`)
   - Logic: Leaves Y unshifted at the top (top alignment intact) and adds `960.0f` to X, shifting Player 2's HUD from Monitor 1 to the top-left of Monitor 2.

The runtime loader (`patches.bin` format `G3PATCH2`) locates all three signatures dynamically across any decrypted ELF image and writes the replacements:
```python
[
    (table_site + 8, b'\x14\xff\xff\xff\x32\xff\xff\xff'),
    (aspect_site + 8, b'\xc5\xf8\x2e\xd0\x76\x04\xc5\xfa\x58\xc0\xc3'),
    (hud_site + 6, b'\xc5\xf2\x58\x0d\xa1\x07\x0a\x01'),
]
```

#### User Experience
- **Single Ultrawide Monitor (e.g. 32:9 or 21:9):** When spanning the game window across a 3840x1080 or 2560x1080 display, Player 1 occupies the entire left screen and Player 2 occupies the entire right screen.
- **Dual 16:9 Monitors (AMD Eyefinity / NVIDIA Surround):** Combining two 1920x1080 displays into a virtual 3840x1080 desktop gives each player a dedicated physical 16:9 monitor without any additional software layers.

---

### Approach B: Vulkan Presenter Reprojection / Compositor Blit

#### Conceptual Architecture
Rather than relying on driver-level monitor spanning (Surround/Eyefinity), Approach B intercepts the Vulkan presentation chain (`vkQueuePresentKHR`) in `gpu/` runtime or an SDL3 presenter layer.

1. The game continues rendering in 1920x1080 (either horizontal split or vertical split).
2. The custom Vulkan presentation engine creates two separate SDL3 OS windows, one placed on Monitor 1 and the other on Monitor 2.
3. During presentation:
   - Command buffer issue: `vkCmdBlitImage` (or a compute shader sampler).
   - Source: In-engine swapchain image (1920x1080).
   - Destination Window 1: Crop Source `[0, 0, 960, 1080]` and scale/blit to Monitor 1 (`1920x1080`).
   - Destination Window 2: Crop Source `[960, 0, 1920, 1080]` and scale/blit to Monitor 2 (`1920x1080`).
4. Present both swapchains synchronously using `VK_PRESENT_MODE_FIFO_KHR` or mailbox.

#### Challenges and Trade-offs
- **Aspect Ratio & FOV Distortion:** If the source buffer is 960x1080 (8:9) and stretched to a 16:9 monitor (1920x1080), horizontal stretching occurs unless the in-engine camera aspect ratio is simultaneously patched from `8/9` to `16/9`.
- **Latency & Tearing:** Multi-window presentation across independent display controllers can suffer from presentation jitter or desynchronized VSync pulses without VK_KHR_display or unified present queues.

---

### Approach C: Multi-Instance Sandboxed Co-op (Nucleus / Couchlink Style)

#### Conceptual Architecture
Running two completely isolated process instances of `eboot.bin` via sandbox:
- **Instance 1:** Window pinned to Monitor 1, hooked to Controller 1.
- **Instance 2:** Window pinned to Monitor 2, hooked to Controller 2.
- **Inter-Process Communication (IPC):** Shared memory or local loopback TCP/UDP socket syncing player transforms, health, enemy positions, and animation states.

#### Reasons for Dismissal
- Sengoku Basara 4 Sumeragi has no native netplay or LAN architecture (it was designed strictly for local couch co-op).
- Re-engineering deterministic network lockstep simulation or cross-process memory synchronization requires reverse engineering thousands of entity state structures.
- Unnecessary complexity given that Approach A provides 100% native engine rendering on a single process.

---

## 5. Implementation Status of Enhancements

### 5.1 Aspect Ratio and Field of View (FOV) Tuning (Implemented)
In vertical split mode (Mode 4 & Mode 5), MT Framework adjusts the horizontal scissor bounds to `[0.0, 0.5]` and `[0.5, 1.0]`. When displaying on an ultra-wide desktop (e.g. 3840x1080 across two 16:9 monitors) or stretching viewports across individual displays, an uncorrected 8:9 camera aspect ratio appears squished horizontally.
- The active vertical split patch now incorporates the `ASPECT_SITE` un-stretch patch: any camera aspect ratio `< 1.0f` is automatically doubled to 16:9, eliminating distortion across dual 16:9 monitors.

### 5.2 HUD X-Offset for Player 2 (Implemented)
Player 2's HUD coordinates previously inherited the horizontal split layout (Y shifted by +540.0f).
- The active vertical split patch now incorporates the `HUD_P2_SITE` patch: replacing the Y displacement with an X displacement of `+960.0f` (`vaddss xmm1, xmm1, [rip + 0x10a07a1]`).
- This cleanly positions Player 1's HUD on Monitor 1 and Player 2's HUD on Monitor 2.

---

## 6. Verification and Integration

The patch is integrated into the native PC launcher pipeline:
- **Engine patch:** `launcher/basara4_patches.py` (`vertical_splitscreen`)
- **Launcher toggle:** `launcher/basara4_launcher.py` under the Graphics / Display settings tab
- **Configuration key:** `vertical_splitscreen: true | false` in `user/launcher.json`
