# Investigation brief: 1920x2160 tall-canvas co-op for dual side-by-side monitors

Investigation only. Do NOT change source code, launcher, patches, or commit anything except the
report file named at the end.

## Goal
Determine whether Sengoku Basara 4 Sumeragi (PS4 CUSA01159, MT Framework) running in this native
port can render its native horizontal (top/bottom) 2-player split-screen into a 1920x2160 canvas,
so each player gets a full 1920x1080 16:9 viewport. The presenter would later blit the top half to
the left monitor and the bottom half to the right monitor of a 3840x1080 borderless window.

## Context
- Repo: C:/Users/DarkTama/Documents/Projects/basara4-native-pc, branch feat/dual-monitor-coop.
- Decrypted executable: D:/Games/basara4-windows/out/eboot.elf (file offsets differ from virtual
  addresses; see launcher/basara4_patches.py eboot_image() for mapping). Python 3 + capstone 5.0.7
  are installed for disassembly.
- docs/dual_monitor_coop.md documents the viewport mode jump table (sCamera::Viewport, modes 0-9).
- The previous approach (vertical split patch in launcher/basara4_patches.py vertical_splitscreen)
  failed in-game: viewport redirect worked, but the ASPECT_SITE patch (file 0x1b63a8) did not fix
  the 8:9 projection and the HUD_P2_SITE patch (file 0x223a7f) did not move Player 2's HUD; the HUD
  still sits at Y+540. So those sites are wrong or not the sole source.
- Current render size: gpu/shim/core/emulator_settings.h GetInternalScreenHeight() reads env
  SPARTAN_INTERNAL_HEIGHT (default 1080); check the width equivalent and how it flows. src/probe.c
  line ~1151 passes 1920,1080 into SpartanGpuConfig. sceVideoOut is HLE'd in src/ / gpu/shim.
- No local MSYS2: C/C++ can only be built in CI. Do not attempt local C builds.

## Questions to answer (with evidence: addresses, disassembly snippets, code paths)
1. Where does the game get its backbuffer / main render target size? (sceVideoOutOpen /
   sceVideoOutRegisterBuffers attributes in our HLE, hardcoded 1920/1080 constants in the eboot,
   or MT Framework sRender init params.) Can our HLE report/allocate 1920x2160 buffers?
2. Does the engine derive viewport pixel rects from normalized viewport (modes 2/3) times the
   render target size? If the target were 1920x2160, would mode 2 = 1920x1080 top and mode 3 =
   1920x1080 bottom automatically?
3. Camera projection aspect: is it computed from viewport pixel width/height (then 16:9 would be
   automatic) or a constant? Identify the actual aspect computation used for split cameras.
4. Player 2 HUD offset: find the real code that places P2 HUD in the bottom half. Is the 540.0f
   offset a constant (rodata 0x10b68dc area per docs) or derived from screen height? List every
   xref to the 540.0f constant and any 1080.0f / 1920.0f / 0.5f constants used by HUD/UI layout.
   State whether the UI layout uses a fixed 1920x1080 virtual coordinate space.
5. Minimap / shared HUD elements: where are they positioned in co-op?
6. Any other hard 1080-height assumptions likely to break (scissor, post-process buffers, UI
   projection matrix).

## Output
Write findings to docs/research/tall_canvas_findings.md:
- Verdict: feasible / feasible with N patches / not feasible, with confidence.
- For each question: answer, evidence (file offset + VA, disassembly excerpt), confidence.
- If feasible: concrete minimal plan (which HLE/C changes, which eboot patches with signatures,
  presenter blit change) for a later implementation task.
- Clearly separate verified facts from assumptions.
Commit only that file with message "docs: tall canvas co-op feasibility findings" and push to
origin feat/dual-monitor-coop.
