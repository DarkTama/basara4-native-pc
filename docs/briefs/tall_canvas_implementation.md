# Implementation brief: Dual-monitor co-op via 1920x2160 tall canvas (experimental)

Repo: C:/Users/DarkTama/Documents/Projects/basara4-native-pc, branch feat/dual-monitor-coop
(checked out in this folder; do not create worktrees). Read docs/research/tall_canvas_findings.md
first; it is the technical basis. Game install for local testing: D:/Games/basara4-windows
(decrypted eboot at D:/Games/basara4-windows/out/eboot.elf). No local MSYS2: C/C++ is validated
only via CI. Python tests run locally with pytest.

## Background
The previous approach (vertical split: jump-table redirect + ASPECT_SITE + HUD_P2_SITE) failed
in game: picture stretched 2x, P2 HUD stayed in P1's area. Findings show the HUD_P2_SITE address
was an effect controller, not HUD. New approach: keep the game's native top/bottom split but make
the render canvas 1920x2160 so each player gets a native 1920x1080 16:9 viewport, then the
presenter puts the top half on the left monitor and the bottom half on the right monitor of a
3840x1080 borderless window.

## Tasks

### 1. Eboot patches (launcher/basara4_patches.py)
- Remove the vertical-split patch set (SPLIT_TABLE redirect, ASPECT_SITE, HUD_P2_SITE and
  vertical_splitscreen()). Replace with a `tall_canvas_coop(image)` function returning writes for:
  a) sRender height 1080 -> 2160 at the 4 sites in the findings doc (VA 0xdbd28e, 0xdbc326,
     0xdbc360, 0xdbcbe9; file offset = VA + 0x4000 in eboot.elf, but use the image offsets that
     find() returns, same as existing code). Change only the 0x438 immediate to 0x870, width
     0x780 unchanged. Use signatures long enough to be unique; each must match exactly once
     (raise LookupError otherwise, same pattern as unlimited_fps).
     Verified disassembly:
       0xdbd28e: c7 83 bc 00 00 00 38 04 00 00   mov dword [rbx+0xbc], 0x438
       0xdbc326: 41 c7 85 bc 00 00 00 38 04 00 00 mov dword [r13+0xbc], 0x438
       0xdbc360: 48 b8 80 07 00 00 38 04 00 00 ; 49 89 85 b8 00 00 00
       0xdbcbe9: 48 b8 80 07 00 00 38 04 00 00 ; 4a 89 84 2b 80 ab 07 01
  b) P2 HUD offset. Verified code at VA 0x49ded8:
       0x49ded8 80 b8 b1 00 00 00 00  cmp byte [rax+0xb1], 0      ; 2P flag
       0x49dedf 74 06                 je 0x49dee7
       0x49dee1 48 83 c3 58           add rbx, 0x58               ; 2P table entry
       0x49dee5 eb 04                 jmp 0x49deeb
       0x49dee7 48 83 c3 28           add rbx, 0x28               ; 1P entry
       0x49deeb c5 fa 10 05 e5 9c c2 00 vmovss xmm0, [rip+..]     ; 1.5f
       0x49def3 c5 fa 59 0b           vmulss xmm1, xmm0, [rbx]    ; -> [r12+0x610]
       0x49def7 c5 fa 59 53 04        vmulss xmm2, xmm0, [rbx+4]  ; -> [r12+0x614]
     (+0x8/+0xc -> 0x618/0x61c). Determine by disassembly which stored float(s) carry the P2
     Y offset (likely [rbx+4] / 0x614 and/or [rbx+0xc]) and implement the smallest correct patch
     that adds +1080.0 to P2's Y only in 2P mode (e.g. a code cave or reusing an existing
     1080.0f/720.0f rodata constant; verify any constant you reference by reading its bytes).
     Also check where 0x610..0x61c are consumed if needed to be sure. If you cannot make a
     confident HUD patch, ship tasks a) and c)-f) anyway and leave the HUD patch out, reporting
     exactly why. Do not guess register meanings without evidence.
- build(): replace vertical_split param with tall_canvas_coop (CLI flag --tall-canvas-coop).
- Update tests/test_patches.py accordingly (synthetic fixtures, missing/duplicate site errors).
- Run the builder against the real eboot and confirm all sites match once:
  python launcher/basara4_patches.py D:/Games/basara4-windows/out/eboot.elf %TEMP%/p.bin --tall-canvas-coop

### 2. Launcher (launcher/basara4_launcher.py)
- Rename config key vertical_splitscreen -> dual_monitor_coop (migrate old key on load: if the
  old key is true, set the new one). Label EN "Dual-monitor co-op (experimental)", ID
  "Co-op dual monitor (eksperimental)".
- When enabled at launch: build patches with tall_canvas_coop; set env
  SPARTAN_INTERNAL_HEIGHT=2160 (width stays 1920); ensure SPARTAN_DMEM_MB is high enough for a
  2x canvas (do not apply the low-memory 2048/2560 caps; use a value large enough, e.g. 6144 or
  per src/runtime_memory.c comments, and explain the choice); force window size 3840x1080
  borderless if not already.
- Disclaimer shown in the launcher hint/tooltip AND a one-time confirmation or visible warning
  text when the toggle is turned on (EN + ID): experimental; renders at 1920x2160 (2x pixels),
  so needs noticeably more GPU power and VRAM (6 GB+ recommended) and more system RAM; requires
  two side-by-side 1920x1080 monitors as one 3840x1080 borderless window; minimap only shows on
  one screen (game limitation); menus/cutscenes are mirrored (F8 toggles mirror/split);
  may have visual glitches.

### 3. Presenter (gpu/shadps4/video_core/renderer_vulkan/vk_presenter.cpp)
- Current dual-monitor branch (extent.width >= 3840 && extent.height <= 1440) splits the frame
  left/right. Change: when frame->height >= 2 * extent.height (tall canvas) -> battle split mode
  blits top half (0..h/2) to left half of swapchain and bottom half to right half; mirror mode
  (F8, dual_monitor_mirror) blits the top half to both monitors. Keep existing behavior for
  non-tall frames (mirror full frame both sides). See MakeImageBlitRegion already present.
- Decide automatically between mirror and split if feasible: in menus/cutscenes the game uses
  viewport mode 0 fullscreen, which on a 2160 canvas fills the whole 1920x2160 frame (squashed
  when split). If there is a cheap reliable signal, use it; otherwise keep F8 manual toggle and
  default to split, and document it. Do not spend long on auto-detect.
- Also check FSR/RCAS/upscale paths and the overlay (gpu/shim/spartanport_overlay.cpp uses
  extent height) do not break with a 1920x2160 frame and 3840x1080 swapchain.

### 4. Docs
- Rewrite docs/dual_monitor_coop.md for the tall-canvas approach (keep the jump table reference
  as background; mark the vertical split approach as abandoned with the reason).
- README.md: add a short "Dual-monitor co-op (experimental)" section with the same disclaimer
  (resource cost, requirements, limitations, F8).

### 5. Validation and delivery
- pytest tests/test_patches.py and any launcher tests touched; full suite must not gain new
  failures vs. baseline (46 pre-existing errors are known).
- Copy changed launcher/*.py to D:/Games/basara4-windows/launcher/.
- Commit in logical commits, push to origin feat/dual-monitor-coop, then trigger CI:
  gh workflow run windows-zip.yml --ref feat/dual-monitor-coop ; wait for the run and report
  its result (and fix compile errors if it fails, then re-run).
- Final report: files changed, commits, CI run id/result, which patches were included, what
  the HUD patch does and its evidence (or why skipped), DMEM value chosen, open risks.
