#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Wall-clock timeline of a play session, written while the game runs.  The game's own log lines carry no time of day; this
polls last_run.log and audio_debug.log twice a second, stamps every NEW line with the time it appeared and writes the
interesting ones to user/timeline.log, so "the red background appeared at 23:05" can be matched to what the game was doing.

  python tools/log_watch.py [user_dir]       (stops by itself a few seconds after basara4-core exits)

Does not touch the game process.
"""
import ctypes
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

user = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "user"
out = user / "timeline.log"

GPU_KEEP = re.compile(r"DEVICE LOST|checkpoint|stage |  #\d|image view range|depth/stencil|resolved to the depth|FindImage:|  cached|"
                      r"GetBarriers|Assertion|Unreachable|STOP:|Fault|AddSource|Image overlap|Unresolvable|Error|Critical", re.I)
GPU_DROP = re.compile(r"open\(.*not found|GetAudioData|Pipeline binary cache|HLE thread|Extension VK_|is not fully supported|semaphore id=")
AUDIO_KEEP = re.compile(r"ngs2\.parse_file path=.*(event|bgm)|ngs2\.end .*(stream_complete)|ngs2\.starved|ngs2\.start .*channels=6|"
                        r"ngs2\.at9_decode_failed|AvPlayer|Fault|Assertion")


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_k32.OpenProcess.restype = ctypes.c_void_p
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32), ("th32ProcessID", ctypes.c_uint32),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_uint32), ("cntThreads", ctypes.c_uint32),
                ("th32ParentProcessID", ctypes.c_uint32), ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_uint32),
                ("szExeFile", ctypes.c_wchar * 260)]


def running():
    """Is this install's basara4-core alive?  Plain API calls: no child process, so no console window flashes
    over the game (a PowerShell per poll did exactly that)."""
    root = str(user.parent).lower()
    snap = _k32.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return False
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    found = False
    ok = _k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
    while ok and not found:
        if entry.szExeFile.lower() == "basara4-core.exe":
            handle = _k32.OpenProcess(0x1000, False, entry.th32ProcessID)  # query limited information
            if handle:
                buf = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_uint32(1024)
                if _k32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
                    found = buf.value.lower().startswith(root)
                _k32.CloseHandle(ctypes.c_void_p(handle))
        ok = _k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
    _k32.CloseHandle(ctypes.c_void_p(snap))
    return found


def main():
    files = {"gpu": [user / "last_run.log", 0, GPU_KEEP, GPU_DROP], "aud": [user / "audio_debug.log", 0, AUDIO_KEEP, None]}
    for key, entry in files.items():
        if entry[0].exists():
            entry[1] = entry[0].stat().st_size  # only what is written from now on
    with open(out, "a", encoding="utf-8", buffering=1) as log:
        log.write(f"==== {datetime.now():%Y-%m-%d %H:%M:%S} watching {user} ====\n")
        idle_after_exit = 0
        seen_running = False
        started = time.time()
        while True:
            for key, entry in files.items():
                path, pos, keep, drop = entry
                if not path.exists():
                    continue
                size = path.stat().st_size
                if size < pos:  # the launcher started a new log
                    pos = 0
                    log.write(f"{datetime.now():%H:%M:%S} [{key}] -- new log file --\n")
                if size > pos:
                    with open(path, "rb") as f:
                        f.seek(pos)
                        chunk = f.read(size - pos)
                    entry[1] = size
                    now = datetime.now().strftime("%H:%M:%S")
                    for line in chunk.decode("utf-8", errors="replace").splitlines():
                        if keep.search(line) and not (drop and drop.search(line)):
                            log.write(f"{now} [{key}] {line[:400]}\n")
                    else:
                        pass
            time.sleep(0.5)
            if running():
                seen_running = True
                idle_after_exit = 0
            else:
                idle_after_exit += 1
                if (seen_running and idle_after_exit > 20) or (not seen_running and time.time() - started > 3600):
                    log.write(f"==== {datetime.now():%H:%M:%S} game closed, watcher stopped ====\n")
                    return


if __name__ == "__main__":
    main()
