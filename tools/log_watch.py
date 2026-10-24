#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Wall-clock timeline of a play session, written while the game runs.  The game's own log lines carry no time of day; this
polls last_run.log and audio_debug.log twice a second, stamps every NEW line with the time it appeared and writes the
interesting ones to user/timeline.log, so "the red background appeared at 23:05" can be matched to what the game was doing.

  python tools/log_watch.py [user_dir]       (stops by itself a few seconds after basara4-core exits)

Does not touch the game process.
"""
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

user = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"G:\basara4-windows\user")
out = user / "timeline.log"

GPU_KEEP = re.compile(r"DEVICE LOST|checkpoint|stage |  #\d|image view range|depth/stencil|resolved to the depth|FindImage:|  cached|"
                      r"GetBarriers|Assertion|Unreachable|STOP:|Fault|AddSource|Image overlap|Unresolvable|Error|Critical", re.I)
GPU_DROP = re.compile(r"open\(.*not found|GetAudioData|Pipeline binary cache|HLE thread|Extension VK_|is not fully supported|semaphore id=")
AUDIO_KEEP = re.compile(r"ngs2\.parse_file path=.*(event|bgm)|ngs2\.end .*(stream_complete)|ngs2\.starved|ngs2\.start .*channels=6|"
                        r"ngs2\.at9_decode_failed|AvPlayer|Fault|Assertion")


def running():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "(Get-Process basara4-core -ErrorAction SilentlyContinue | Measure-Object).Count"],
                       capture_output=True, text=True).stdout.strip()
    return r not in ("", "0")


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
