# Changelog

**English** | [Bahasa Indonesia](CHANGELOG.id.md)

All notable changes to Basara 4 PC. Dates are the commit dates.

## 1.0.1 - 2026-10-25

The first update after the initial release. It fixes the audio engine (music that stopped, doubled or clipped sound, dialogue
and cutscene voices), the GPU crashes and wrong pictures in event cutscenes, and a crash in the stage select. It also adds
tools that find these problems from a play session without having to describe them.

### How these fixes were checked

Every fix below was found from logs of real play sessions (the audio debug log, the game log, wall-clock timelines, captured
WAV files of every voice and of the final mix, frame captures, and Windows' own GPU driver events), not from guesses.
Where a fix could be measured it was measured: for example the music loop was compared sample by sample against the first pass.

The last test was a complete play session (17 minutes, several music tracks, event cutscenes) after the last code change. It ended with no crash, no GPU device loss, no crash dump and
no silent music voice. Tested on **one machine only: Windows, NVIDIA RTX 4070**. AMD and Intel graphics cards have not been tried.

### Music and sound (the software Ngs2 engine, `src/runtime_offline.c`)

- **Battle music loops like the original.** Before, a track played once and then fell silent. Tracks carry a loop point
  (`smpl` chunk of the sound file). The engine now reports the intro and the loop as two blocks, the loop block repeating
  for ever, and the ATRAC9 stream honours the block boundaries. Measured on a track whose next block arrived in time: the second and third pass are identical to the first pass
  sample by sample, and there is no click at the seam (only the first 40 ms or so after a seam differ slightly, where the decoder restarts). The other tracks loop with the right content. (`a923b5d`)
- **Music no longer dies when a track starts in an unusual order.** Three cases that left a music voice silent for tens of seconds
  or for ever were fixed: (`c7ba3a0`, `3e63e72`)
  - the game sends *Play* before the first chunk of the new track;
  - the voice still belongs to the previous track (the old half superframe made every superframe of the new track decode shifted);
  - a *Play* on a voice that has nothing queued (it was ignored as a repeat).
  A decoder that stops finding valid ATRAC9 now resynchronises at the start of the next block by itself.
- **Battle music started at the wrong place or with a wrong size** (`calc_block` returned the end offset in the wrong half of
  its result, then double-counted the frame offset): fixed and aligned to the ATRAC9 superframe. (`ab1f30e`, `9bf1bf2`)
- **Cutscene and dialogue voices.** Buffer starvation in cutscene audio, voices left paused when the stream switches, and pacing
  across several output ports were fixed. (`66131a4`)
- **Voices stopped by an event cutscene are stopped.** The game's "event 1" used to pause a voice, which then stayed "playing"
  for ever; this was seen together with a black screen when an event cutscene started. (`697bb08`)
- **A stream that has decoded its whole waveform and has no chunk left is finished**, instead of staying a running voice. (`27d03e2`, `a923b5d`)
- **Replacing the data of a voice no longer starts it**, and a *Play* on a stream that is already running is not a restart: aimed at dialogue lines that started again
  and at music that started by itself. (`d234359`)
- **Doubled sound** from the wet submix (port `0x2001`) is muted; streams that continue over several chunks are supported with a larger
  block queue. (`1d496a7`)
- **Stereo pairs.** Voices the game starts twice on the same data are panned left and right (systemic twin-voice panning), and a guard against
  streaming buffer starvation was added. (`f194524`)
- **Harsh digital clipping** in loud scenes is replaced by a soft-knee `tanh` limiter. (`55ceee4`)
- A broader "guard" that dropped some dialogue (`9fd4c98`) was removed again (`2acbd18`).

### Graphics (Vulkan core, `gpu/`)

- **Black screens and a hang at event cutscenes.** On NVIDIA the driver lost the GPU device when a cutscene quad sampled a depth image
  (a 1920x1080 `D32SfloatS8Uint` image) that the game described as a colour texture with three mip levels. Windows logged it as
  `nvlddmkm` event 153 at every black screen. Several fixes together removed it:
  - an image view may not reach past its image (levels and layers are clamped); (`6ef526b`)
  - the subresource table is sized to the image and barrier ranges are clamped (fixes a Vulkan barrier assertion); (`2acbd18`)
  - a view of a depth/stencil image uses the image's own format and a single aspect; (`0868a14`)
  - a texture binding whose image cannot serve the game's texture description (fewer mip levels or layers than the view, or a colour format on a
    depth image) is looked up again with the exact format. (`1fb11ac`)
- **Red backgrounds and a doubled, half-size picture in cutscenes** came from the same wrong image (depth shown in the red channel, a downsampled copy
  drawn into the top-left corner). They went away with the last fix above; the guard was seen to fire and pick the right image in a test session.
- **A GPU that stops answering no longer hangs the game with no explanation.** Waiting for the GPU reports the error instead of spinning, and
  the report names the draw that lost the device (its shaders and the images it bound). On NVIDIA this uses `VK_NV_device_diagnostic_checkpoints`
  (other GPUs skip it). (`b260192`, `a74d38f`)

### Crashes

- **Stage select** no longer crashes in the MT Framework UI text markup engine (issue #1). (`313eef4`)
- **A crash dump is written** to `user\crash\core-<date>-<time>.dmp` when the process dies from a fault or a GPU assertion, so a bug report is
  more than a few lines of text. (`c7778ca`) *A dump contains parts of the game's memory (thread stacks and what they point to): only give it to people you trust.*

### Launcher

- **WAV capture is off by default and asks first.** It writes the final mix and every long voice as uncompressed audio (about 70 MB per minute, up to
  3 GB for one session). Ticking it now shows a warning and needs a confirmation, it also turns on the audio debug log, it is never remembered
  between launches (it applies to one game start, then switches itself off), and the launcher offers to delete captures left in `user\audio_wav`
  by an earlier session.

### Tools for finding problems (source repository, `tools/`)

None of these are needed to play. They are how the fixes above were found:

- `audio_session_report.py` reads a play session and lists silent voices, voices that never finish, restarts, clipping and decoder faults
  without being told when the problem happened. (`b492459`)
- `debug_watch.py` + `framedump_png.py` write a time-stamped timeline of the game, GPU and audio logs, capture frames automatically during event cutscenes
  and on **F9**, record the screen in one-minute pieces (NVENC, a rolling 30 minutes) and write `user\report.txt` when the game closes.
  *The recording captures the whole primary screen*; set `DEBUG_NO_VIDEO=1` to switch it off. (`d4b5df5`, `c7778ca`, `6f9756c`)
- `hang_sample.py` and `hang_all.py` show where every thread of a spinning game is, with host symbols. (`6ef526b`, `b260192`)
- `save_snapshot.py` keeps named copies of the save data, so a bug that needs 20 minutes to reach can be reproduced from a save made just before it. (`42bed2e`)
- The audio debug log has more detail (voice identity, call sites, WAV capture of the mix and of every long voice). (`935798e`)

### Known limitations

- Where the next loop block arrives late, the music has a short gap at the seam (10 to 80 ms were measured, only on some tracks).
- Only NVIDIA graphics have been tested. The GPU fixes follow the Vulkan rules and should not hurt other cards, but this is not proven.
- Frame rates above 60 stay experimental.
- Advanced switches (environment variables) if a music change misbehaves on your machine: `SPARTAN_NGS2_NO_LOOP_BLOCKS=1` brings back the
  old single-pass music, `SPARTAN_NGS2_EVENT1_PAUSES=1` the old handling of event 1.

## 1.0.0 - 2026-10-23

Initial release: native PC port and launcher for Sengoku Basara 4 Sumeragi (PS4, CUSA01159).
