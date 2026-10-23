#!/usr/bin/env python3
"""Automated audio testing & verification tool for Basara 4 PC.
Runs basara4-core.exe headlessly with audio dumping, analyzes PCM waveforms,
converts dumps to .wav, and reports peak/RMS metrics and log events.
"""
import math
import os
import struct
import subprocess
import sys
import wave
from pathlib import Path

ROOT = Path(r"G:\basara4-windows")
BIN = ROOT / "bin"
USER = ROOT / "user"
OUT = ROOT / "out"
GAME = ROOT / "game" / "Sengoku Basara 4 Sumeragi (CUSA01159)"
EXE = BIN / "basara4-core.exe"

def analyze_pcm_f32(pcm_path, channels=2, sample_rate=48000, wav_path=None):
    if not pcm_path.exists():
        return None
    raw = pcm_path.read_bytes()
    num_floats = len(raw) // 4
    if num_floats == 0:
        return None
    
    samples = struct.unpack(f"<{num_floats}f", raw[:num_floats * 4])
    total_frames = num_floats // channels
    duration = total_frames / sample_rate
    
    peak = max(abs(s) for s in samples)
    sum_sq = sum(s * s for s in samples)
    rms = math.sqrt(sum_sq / num_floats) if num_floats > 0 else 0.0
    
    # Active duration (|s| > 0.001)
    active_count = sum(1 for s in samples if abs(s) > 0.001)
    active_duration = (active_count / channels) / sample_rate
    
    peak_db = 20 * math.log10(peak) if peak > 1e-6 else -999.0
    rms_db = 20 * math.log10(rms) if rms > 1e-6 else -999.0
    
    # Convert to 16-bit WAV if requested
    if wav_path:
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            # Clip and scale to int16
            int16_samples = []
            for s in samples:
                val = int(max(-1.0, min(1.0, s)) * 32767.0)
                int16_samples.append(val)
            wf.writeframes(struct.pack(f"<{len(int16_samples)}h", *int16_samples))
            
    return {
        "duration": duration,
        "active_duration": active_duration,
        "peak": peak,
        "peak_db": peak_db,
        "rms": rms,
        "rms_db": rms_db,
        "frames": total_frames,
    }

def run_audio_test(timeout_sec=15):
    print(f"=== Running automated audio test ({timeout_sec}s timeout) ===")
    USER.mkdir(parents=True, exist_ok=True)
    
    # Clean previous dumps
    for p in USER.glob("test_audio*.f32"):
        p.unlink(missing_ok=True)
    for p in USER.glob("test_audio*.wav"):
        p.unlink(missing_ok=True)
        
    dump_prefix = str(USER / "test_audio")
    log_path = USER / "test_audio.log"
    log_path.unlink(missing_ok=True)
    
    env = os.environ.copy()
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
    env["SPARTAN_GPU_USER_DIR"] = str(USER)
    env["SPARTAN_CONFIG"] = str(USER / "basara4.ini")
    env["SPARTAN_AUDIO_DEBUG"] = str(log_path)
    env["SPARTAN_AUDIO_DUMP"] = dump_prefix
    env["SPARTAN_AUDIO_TRACE"] = "1"
    env["SPARTAN_FILE_TRACE"] = "1"
    env["SPARTAN_NGS2_TRACE"] = "1"
    env["SPARTAN_VBLANK_HZ"] = "0"
    env["SPARTAN_FPS_LIMIT"] = "60"
    
    cmd = [
        str(EXE),
        str(OUT / "boot-linked.bin"),
        "--content-profile", str(OUT / "content.bin"),
        "--app0", str(GAME),
        "--user", str(USER),
        "--no-game-hooks",
        "--timeout", str(timeout_sec)
    ]
    
    print(f"Executing: {cmd[0]} (timeout {timeout_sec}s)...")
    try:
        proc = subprocess.run(cmd, env=env, cwd=str(USER), capture_output=True, timeout=timeout_sec + 10)
        print(f"Process exited with code {proc.returncode}")
    except subprocess.TimeoutExpired as e:
        print("Subprocess hit timeout, killing process...")
        if e.stdout:
            (USER / "test_audio.stdout.log").write_bytes(e.stdout)
            stdout_str = e.stdout.decode("utf-8", errors="replace")
            graphs = [l for l in stdout_str.splitlines() if "graph of system" in l or " lvl=" in l]
            if graphs:
                print(f"Captured {len(graphs)} graph lines:")
                for l in graphs[:40]:
                    print("  " + l)
        
    print("\n=== PCM Waveform Analysis ===")
    found_any = False
    for p in sorted(USER.glob("test_audio*.f32")):
        found_any = True
        wav_path = p.with_suffix(".wav")
        res = analyze_pcm_f32(p, channels=2, wav_path=wav_path)
        if res:
            print(f"File: {p.name}")
            print(f"  Duration:        {res['duration']:.2f} s ({res['frames']} frames)")
            print(f"  Active Sound:    {res['active_duration']:.2f} s ({res['active_duration']/res['duration']*100:.1f}%)")
            print(f"  Peak Amplitude:  {res['peak']:.4f} ({res['peak_db']:.1f} dBFS)")
            print(f"  RMS Power:       {res['rms']:.4f} ({res['rms_db']:.1f} dBFS)")
            print(f"  WAV exported:    {wav_path.name}")
            
    if not found_any:
        print("No audio dumps produced! (Check if SPARTAN_AUDIO_DUMP is working)")
        
    if log_path.exists():
        print("\n=== Audio Log Summary ===")
        summary_tool = Path(r"G:\basara4-pc\tools\audio_log_summary.py")
        if summary_tool.exists():
            subprocess.run(["python", str(summary_tool), str(log_path)])

if __name__ == "__main__":
    t = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    run_audio_test(t)
