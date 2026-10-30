# Basara 4 Native PC on Windows

Basara 4 Native PC builds and runs **Sengoku Basara 4 Sumeragi** (PlayStation 4, CUSA01159)
natively on 64-bit Windows 10 (1903+) / 11. Players: see `packaging/basara4/README.txt` and
`packaging/basara4/README.id.txt` (packaged with releases).

## Build

Install [MSYS2](https://www.msys2.org) (`winget install MSYS2.MSYS2`), open the
**MSYS2 CLANG64** shell and install the toolchain and libraries:

```bash
pacman -S --needed git patch \
  mingw-w64-clang-x86_64-{toolchain,cmake,ninja,pkgconf,python} \
  mingw-w64-clang-x86_64-{vulkan-headers,vulkan-loader,vulkan-memory-allocator,glslang} \
  mingw-w64-clang-x86_64-{sdl3,fmt,boost,robin-map,xxhash,ffmpeg,zydis} \
  mingw-w64-clang-x86_64-{spirv-tools,spirv-headers,spirv-cross}
```

magic_enum, miniz and xbyak (not packaged by MSYS2) are submodules under `gpu/third_party/`.
Before packaging, also install Windows Python 3.10+ from python.org with Tk support; the
packaging script uses it to freeze the launcher into an executable.

Then, in the repository, build with MSYS2 CLANG64:

```bash
git submodule update --init --recursive
bash build.sh                               # out/spartanport-probe.exe, out/spartanport-gpu-capabilities.exe
python packaging/basara4/make_package.py    # dist/basara4-windows/ package
```

For a source launcher, install Windows Python 3.10+ from python.org with Tk support. In
PowerShell at the repository root:

```powershell
python -m pip install pillow iced-x86==1.21.0
python launcher/basara4_launcher.py
```

Choose your game dump folder (containing `eboot.bin`) in the launcher.

`run.py` is the Windows runtime runner (`run.sh` equivalent).
`packaging/basara4/make_package.py` packages the runtime (`bin/basara4-core.exe`), the frozen
launcher (`Sengoku Basara 4.exe`), dependencies, and game scripts into a standalone distribution.
`Play Sengoku Basara 4.cmd` starts the game straight away with the saved settings.

## What is different from Linux

| Linux | Windows |
|---|---|
| `mov rax, fs:[0]` rewritten to `gs:[0]`, GS base set with `arch_prctl` | GS is the TEB and cannot move. The loader rewrites the displacement to `gs:[0x1480 + 8*slot]` (the TEB's `TlsSlots[slot]`, same 9-byte instruction) and each guest thread keeps its TCB in that TLS slot. The slot is claimed first thing in `main` (Vulkan drivers take many). |
| One memfd for direct + flexible memory, `mmap(MAP_FIXED)` views | A pagefile-backed section; the PS4 range 64 GiB–1 TiB is reserved as one placeholder and views replace placeholders (`VirtualAlloc2`/`MapViewOfFile3`, as in shadPS4). Partial unmaps split views and restore page protections (GPU tracking). |
| `-no-pie`, heap below 1 TiB | `--disable-high-entropy-va`: heaps and thread stacks stay low and out of the PS4 range. |
| SIGSEGV handler, `sigsetjmp` recovery | Vectored exception handler; `spartanport_setjmp`/`spartanport_longjmp` (`src/compat_win.c`) restore all Win64 callee-saved registers without SEH unwinding (guest frames have no unwind data; clang's `__builtin_longjmp` restores a wrong frame pointer on Win64). |
| Guest thread stacks from `runtime_low_map` | Windows allocates them, fully committed (`pthread_attr_setstacksize`): guest code has no stack probes and would skip the guard page. The main thread stack is 16 MiB committed. |
| glibc `nanosleep`, monotonic condition variables | High-resolution waitable timers (`compat_sleep_ns`; winpthreads' sleep rounds to 15.6 ms); semaphore deadlines use `CLOCK_REALTIME` (winpthreads rejects monotonic condition variables). |
| `libspartanportgpu.so` | `libspartanportgpu.a`, linked into `spartanport-probe.exe` by CMake (`gpu/CMakeLists.txt`), with a manifest for UTF-8 paths and long paths. |
| `execlp bash run.sh` (in-game restart) | `run.py` puts its command line in `SPARTAN_RESTART_COMMAND`; the new launch waits for the old process (`--after PID`). The launcher keeps the game in a job object. |
| Mod overlay with symlinks | Symlinks when allowed (Developer Mode), else junctions for folders and hard links for files. On a network share (SMB), which refuses junctions too, folders are mirrored with hard links so the game still reads real files through the view. |

Diagnostics added on the way: `SPARTAN_FRAME_DUMP_TRIGGER=<file>` writes the guest display buffer
and the presented image of the next frame to `SPARTAN_DUMP_DIR` (raw 32-bit pixels), which also
works for menus and movies.
