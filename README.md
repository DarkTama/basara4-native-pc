# Sengoku Basara 4 Sumeragi for PC (Basara 4 PC)

**English** | [Bahasa Indonesia](README.id.md)

An open-source native PC port and launcher for **Sengoku Basara 4 Sumeragi** (PlayStation 4, CUSA01159) for Windows 10/11. The game's original x86-64 code executes directly on your PC's CPU without emulation. The PlayStation 4 system libraries are re-implemented function by function in native C/C++, and GPU commands are translated to Vulkan 1.3 in real-time.

> **No copyrighted game files are included.** You must provide your own legally dumped copy of the game from your PlayStation 4 console (tested: CUSA01159).
>
> This project is an independent open-source reverse-engineering and preservation project.
> Not affiliated with, endorsed by, or associated with Capcom or Sony Interactive Entertainment.
> "Sengoku Basara" is a trademark of Capcom Co., Ltd. "PlayStation" is a trademark of Sony Interactive Entertainment.

## Fork
This repository is a fork of Razz's Basara 4 PC ([razzconnect/basara4-native-pc](https://github.com/razzconnect/basara4-native-pc)), built on SpartanPort, bloodborne_pc and shadPS4; full credits in [NOTICE.md](NOTICE.md).

## Requirements
- **OS**: Windows 10 64-bit (1903 or newer) / Windows 11 64-bit
- **CPU**: 64-bit CPU with AVX support (Intel Sandy Bridge 2011+, AMD FX)
- **GPU**: Vulkan 1.3 compliant GPU (GTX 1060 / Radeon RX 580)
- **RAM**: 8 GB RAM
- **Storage**: ~20 GB free disk space
- **Game Copy**: Legal dump of Sengoku Basara 4 Sumeragi (`CUSA01159`)
Linux is not natively supported; running the Windows build through Proton/Wine is untested.

## Install & Play
1. Download `basara4-windows.zip` from https://github.com/DarkTama/basara4-native-pc/releases and extract it.
2. Place your decrypted PS4 game dump folder into `game\`, or click **Browse** in the launcher to select its location.
3. Run **`Sengoku Basara 4.exe`**, select settings, and click **PLAY**. First launch prepares the linked binary image into `out\` (takes roughly 30–60 seconds); subsequent boots start immediately.
4. To launch directly with saved settings without opening the launcher GUI, run **`Play Sengoku Basara 4.cmd`**.

## English Translation Patch
This repository contains **no translated text or assets**. Users must supply the fan English translation patch for PS3 release **BLJM61248** (v0.85 + Matabe fix).

1. Obtain the fan English translation patch for PS3 release **BLJM61248** (`patch 0.85/rom` and `matabe fix/pl035`).
2. Open the launcher, click **"Import PS3 English Patch…"**, and select your PS3 patch folder (or `.zip` file).
3. The repatcher verifies patch files against a known hash manifest (`data/ps3_patch_manifest.json`), extracts GMD string tables, converts UI textures to native PS4 format, and repacks archives in `mods/english_patch/`.
4. Toggle the **"English Translation Patch (PS3 v0.85 Port)"** checkbox anytime to apply via a sparse overlay (`user/app0_overlay`) without modifying original game files.

Stage battle banter and story dialogue were never translated in the original PS3 community patch and remain in Japanese.

```bash
python launcher/basara4_repatch.py "mods/ps3 english patch" --game game/CUSA01159 --out mods/english_patch
```

## Controls
- **Insert** (Keyboard) or **R3 + L2** (Gamepad): Opens the in-game overlay menu (FSR settings, anisotropic filtering, controller status).
- **F12**: Captures a screenshot to `user\`.
- Native XInput & SDL3 controllers (Xbox, DualShock 4, DualSense, Switch Pro) are recognized automatically. For generic / DirectInput gamepads, click **Map controller** in the launcher to calibrate button and stick axes using `basara4-pad-mapper.exe`.

## Building from Source
Prerequisites: MSYS2 with CLANG64 environment and Python 3.10+ on Windows.

```bash
pacman -S --needed base-devel mingw-w64-clang-x86_64-toolchain \
    mingw-w64-clang-x86_64-cmake mingw-w64-clang-x86_64-ninja \
    mingw-w64-clang-x86_64-vulkan-headers mingw-w64-clang-x86_64-vulkan-loader \
    mingw-w64-clang-x86_64-sdl3 mingw-w64-clang-x86_64-ffmpeg \
    mingw-w64-clang-x86_64-glslang mingw-w64-clang-x86_64-pkg-config
```

```powershell
python -m pip install pyinstaller pillow
```

Build steps:
```bash
git clone --recursive https://github.com/DarkTama/basara4-native-pc.git
cd basara4-native-pc
bash build.sh
```

```powershell
python packaging/basara4/make_package.py --out dist/basara4-windows
```

## Logs
- `user\last_run.log`: Core runtime diagnostic log (initialization, GPU shader compiling, crash traceback).
- `user\prepare.log`: Output from the image preparation and relocation stage.
- `user\audio_debug.log`: Detailed Ngs2 voice allocations, streaming feeds, and buffer status.

## Credits
- **Razz** - Basara 4 PC port
- **Felipe Prado** - SpartanPort
- **Supermedo & deadinside28** - bloodborne_pc
- **shadPS4 team** - GPU core
- **Mystechry / MikoMango & indrasundanese** - PS3 English patch

Full notices and licenses: [NOTICE.md](NOTICE.md).

## License
Licensed under the GNU General Public License v2.0 or later (GPL-2.0-or-later).
See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
