#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

One-run debug recorder.  Started by "Debug run.bat" next to the game.  While the game runs it

  * writes user/timeline.log: time of day + the interesting lines of last_run.log and audio_debug.log
  * records the game's own frame analyzer (every render pass, target, shader and texture of a frame) into user/captures
    and the raw presented / guest display buffers into user/framedump, automatically:
      - while an event cutscene (evp*/evm*) plays: one analyzer frame per second, one raw frame every two seconds
      - on F9 (global key, works while the game has focus): 5 analyzer frames + 3 raw frames, labelled "manual"
  * records the WHOLE primary screen (1280x720, NVENC) in one-minute pieces user/recordings/rec_HHMMSS.mkv, the last 30
    kept, deleted when the next run starts.  Anything visible on that screen is in it; set DEBUG_NO_VIDEO=1 to turn it off.
  * labels every burst in the timeline, so "the double picture in evm012_02 at 23:27" maps to files
  * when the game exits: converts the raw frames to PNG and writes user/report.txt (audio findings, GPU warnings, cutscenes,
    crash dumps, file list) - nothing has to be asked for afterwards

  python tools/debug_watch.py [user_dir]          stops by itself shortly after basara4-core exits
"""
import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

user = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"G:\basara4-windows\user")
timeline = user / "timeline.log"
capture_trigger = user / "capture.trigger"
dump_trigger = user / "framedump.trigger"
dump_dir = user / "framedump"
VK_F9 = 0x78

GPU_KEEP = re.compile(r"DEVICE LOST|checkpoint|stage |  #\d|image view range|depth/stencil|resolved to the depth|FindImage:|  cached|"
                      r"cannot serve|GetBarriers|Assertion|Unreachable|STOP:|Fault|AddSource|Image overlap|Unresolvable|Error|Critical|"
                      r"Frame capture", re.I)
GPU_DROP = re.compile(r"open\(.*not found|GetAudioData|Pipeline binary cache|HLE thread|Extension VK_|is not fully supported|semaphore id=")
AUDIO_KEEP = re.compile(r"ngs2\.parse_file path=.*(event|bgm)|ngs2\.end .*stream_complete|ngs2\.start .*channels=6|ngs2\.event1|"
                        r"ngs2\.at9_decode_failed|AvPlayer|Fault|Assertion")
PARSE = re.compile(r"parse_file path=\S*/event/(ev[pm]\d+_\d+)\.sspr")
START = re.compile(r"ngs2\.start rack=(\S+) voice=(\d+) major=1 .*channels=6 .*streaming=1")
STOP = re.compile(r"ngs2\.(?:end|event1) rack=(\S+) voice=(\d+)")


def running():
    root = str(user.parent).replace("'", "''")  # only this install's game, not another copy running
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"(Get-Process basara4-core -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{root}*' }} | Measure-Object).Count"],
                       capture_output=True, text=True).stdout.strip()
    return r not in ("", "0")


def key_down(vk):
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def dir_size(path):
    return sum(f.stat().st_size for f in path.glob("*") if f.is_file()) if path.exists() else 0


FFMPEG = shutil.which("ffmpeg") or next(iter(Path(os.environ.get("LOCALAPPDATA", "")).glob(
    "Microsoft/WinGet/Packages/Gyan.FFmpeg*/ffmpeg-*/bin/ffmpeg.exe")), None)
recordings = user / "recordings"


def start_recording(note):
    """Desktop duplication (works in borderless and exclusive fullscreen) straight into NVENC; one file per minute."""
    if os.environ.get("DEBUG_NO_VIDEO"):
        note("[debug] screen recording is off (DEBUG_NO_VIDEO)")
        return None
    if not FFMPEG:
        note("[debug] no ffmpeg found: no screen recording")
        return None
    recordings.mkdir(exist_ok=True)
    for old in recordings.glob("rec_*.mkv"):  # the pictures of a previous run do not stay on disk
        try:
            old.unlink()
        except OSError:
            pass
    for encoder, extra in (("h264_nvenc", ["-preset", "p1", "-tune", "ll", "-rc", "vbr", "-cq", "30", "-b:v", "0"]),
                           ("libx264", ["-preset", "ultrafast", "-crf", "30"])):
        cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
               "ddagrab=framerate=30:draw_mouse=0:output_idx=0,hwdownload,format=bgra,scale=1280:720,format=yuv420p"
               if encoder == "libx264" else
               "ddagrab=framerate=30:draw_mouse=0:output_idx=0,hwdownload,format=bgra,scale=1280:720,format=nv12",
               "-c:v", encoder, *extra, "-g", "60", "-f", "segment", "-segment_time", "60", "-reset_timestamps", "1",
               "-strftime", "1", str(recordings / "rec_%H%M%S.mkv")]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000)
        time.sleep(2.0)
        if proc.poll() is None:
            note(f"[debug] screen recording started ({encoder}) -> {recordings}")
            return proc
        note(f"[debug] screen recording with {encoder} failed: {proc.stderr.read().decode(errors='replace')[:200]}")
    return None


def trim_recordings(keep=30):
    pieces = sorted(recordings.glob("rec_*.mkv"), key=lambda f: f.stat().st_mtime)
    for old in pieces[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass


def stop_recording(proc):
    if proc and proc.poll() is None:
        try:
            proc.stdin.write(b"q")
            proc.stdin.flush()
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


def write_report(note):
    """Everything worth reading after a run, in one file."""
    tools = Path(__file__).parent
    out = [f"Report written {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
    try:
        r = subprocess.run([sys.executable if sys.executable.lower().endswith("python.exe") else "python",
                            str(tools / "framedump_png.py"), str(dump_dir)], capture_output=True, text=True, timeout=300)
        out += ["== frame dumps", r.stdout.strip() or r.stderr.strip(), ""]
    except Exception as e:
        out += [f"== frame dumps: {e}", ""]
    try:
        r = subprocess.run(["python", str(tools / "audio_session_report.py"), str(user)], capture_output=True, text=True, timeout=600)
        out += ["== audio report (tools/audio_session_report.py)", r.stdout.strip() or r.stderr.strip(), ""]
    except Exception as e:
        out += [f"== audio report: {e}", ""]
    if timeline.exists():
        lines = timeline.read_text(encoding="utf-8", errors="replace").splitlines()
        out += ["== cutscenes and debug captures"] + [l for l in lines if "[debug]" in l and "raw frame requested" not in l][-60:] + [""]
        out += ["== GPU / crash lines"] + [l for l in lines if "[gpu]" in l and re.search(
            r"DEVICE LOST|cannot serve|resolved to the depth|image view range|depth/stencil|FindImage|Fault|Assertion|Unreachable|Crash dump|STOP:",
            l)][-60:] + [""]
    crash = user / "crash"
    dumps = sorted(crash.glob("*.dmp")) if crash.exists() else []
    out += ["== crash dumps"] + ([f"{d.name} ({d.stat().st_size >> 20} MB)" for d in dumps] or ["none"]) + [""]
    for label, folder in (("analyzer frames", user / "captures"), ("raw frames", dump_dir), ("screen recording pieces", recordings)):
        n = len(list(folder.glob("*"))) if folder.exists() else 0
        out.append(f"{label}: {n} files in {folder}")
    (user / "report.txt").write_text("\n".join(out), encoding="utf-8")
    note("[debug] report written: user/report.txt")


class Burst:
    """A series of analyzer / raw-frame triggers, spaced so the game consumes each before the next is created."""

    def __init__(self, label, analyzer_every, raw_every, until, max_raw):
        self.label, self.analyzer_every, self.raw_every = label, analyzer_every, raw_every
        self.until, self.max_raw = until, max_raw
        self.next_analyzer = time.time() + 0.5
        self.next_raw = time.time() + 1.0
        self.analyzer_count = self.raw_count = 0

    def done(self, now):
        return now > self.until


def main():
    user.mkdir(parents=True, exist_ok=True)
    (user / "captures").mkdir(exist_ok=True)
    dump_dir.mkdir(exist_ok=True)
    files = {"gpu": [user / "last_run.log", 0, GPU_KEEP, GPU_DROP], "aud": [user / "audio_debug.log", 0, AUDIO_KEEP, None]}
    for entry in files.values():
        if entry[0].exists():
            entry[1] = entry[0].stat().st_size
    bursts = []
    current_event = None
    active = {}  # (rack, voice) -> Burst for the cutscene voice
    f9_was_down = False
    seen_running = False
    idle_after_exit = 0
    started = time.time()
    recorder = None
    last_trim = 0
    with open(timeline, "a", encoding="utf-8", buffering=1) as log:
        def note(text):
            log.write(f"{datetime.now():%H:%M:%S} {text}\n")

        log.write(f"==== {datetime.now():%Y-%m-%d %H:%M:%S} debug_watch on {user} (F9 = capture now) ====\n")
        while True:
            now = time.time()
            for key, entry in files.items():
                path, pos, keep, drop = entry
                if not path.exists():
                    continue
                size = path.stat().st_size
                if size < pos:
                    entry[1] = pos = 0
                    note(f"[{key}] -- new log file --")
                if size > pos:
                    with open(path, "rb") as f:
                        f.seek(pos)
                        chunk = f.read(size - pos)
                    entry[1] = size
                    for line in chunk.decode("utf-8", errors="replace").splitlines():
                        if keep.search(line) and not (drop and drop.search(line)):
                            note(f"[{key}] {line[:400]}")
                        if key == "aud":
                            m = PARSE.search(line)
                            if m:
                                current_event = m.group(1)
                            m = START.search(line)
                            if m and current_event:
                                b = Burst(current_event, 1.0, 2.0, now + 40, 8)
                                bursts.append(b)
                                active[(m.group(1), m.group(2))] = b
                                note(f"[debug] cutscene {current_event} started: capturing")
                            m = STOP.search(line)
                            if m and (m.group(1), m.group(2)) in active:
                                b = active.pop((m.group(1), m.group(2)))
                                b.until = now + 0.5
                                note(f"[debug] cutscene {b.label} ended: {b.analyzer_count} analyzer frames, {b.raw_count} raw frames")

            down = key_down(VK_F9)
            if down and not f9_was_down:
                b = Burst("manual", 0.6, 1.5, now + 4, 3)
                bursts.append(b)
                note("[debug] F9: manual capture")
            f9_was_down = down

            for b in list(bursts):
                if b.done(now):
                    bursts.remove(b)
                    continue
                if now >= b.next_analyzer and not capture_trigger.exists() and b.analyzer_count < 60:
                    capture_trigger.write_text("1")
                    b.analyzer_count += 1
                    b.next_analyzer = now + b.analyzer_every
                if (now >= b.next_raw and not dump_trigger.exists() and b.raw_count < b.max_raw
                        and dir_size(dump_dir) < 3 << 30):
                    dump_trigger.write_text("1")
                    b.raw_count += 1
                    b.next_raw = now + b.raw_every
                    note(f"[debug] raw frame requested ({b.label} #{b.raw_count})")

            time.sleep(0.1)
            if int(now * 10) % 5 == 0:
                if running():
                    if not seen_running:
                        recorder = start_recording(note)
                    seen_running = True
                    idle_after_exit = 0
                    if now - last_trim > 120:
                        trim_recordings()
                        last_trim = now
                else:
                    idle_after_exit += 1
                    if (seen_running and idle_after_exit > 40) or (not seen_running and time.time() - started > 3600):
                        stop_recording(recorder)
                        note("==== game closed ====")
                        if seen_running:
                            write_report(note)
                        note("==== debug_watch stopped ====")
                        return


if __name__ == "__main__":
    main()
