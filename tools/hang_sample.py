#!/usr/bin/env python3
"""Basara 4 PC - by Razz.   Copyright (C) 2026 Razz.   GPL-2.0-or-later (NOTICE.md).

Sample where a running (or hung) game is spending its time.  The PS4 code runs natively, so a thread's instruction pointer IS the guest
address; this reads it from outside without touching the game's code.

  python tools/hang_sample.py [pid] [--seconds 3]        (default: the running basara4-core)

1. finds the busiest threads over a short window (per-thread CPU time),
2. samples those threads' RIP many times and prints which guest addresses they sit on (0x8xxxxxxxx = game image),
3. walks the rbp chain of a few samples to show who called what (return addresses),
so a black screen with a core at 100% turns into "thread X loops in 0x800... called from 0x800...".
Each suspend lasts microseconds; the game is not modified.
"""
import ctypes
import ctypes.wintypes as wt
import struct
import subprocess
import sys
import time
from collections import Counter

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
THREAD_ALL = 0x0002 | 0x0008 | 0x0040 | 0x0400  # suspend/resume, get context, query information, query limited
PROCESS_VM_READ = 0x0010 | 0x0400
TH32CS_SNAPTHREAD = 0x4
CONTEXT_AMD64 = 0x100000
CTX_FLAGS = CONTEXT_AMD64 | 0x1 | 0x2

k32.OpenThread.restype = wt.HANDLE
k32.OpenProcess.restype = wt.HANDLE
k32.SuspendThread.restype = wt.DWORD
k32.ResumeThread.restype = wt.DWORD


class THREADENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ThreadID", wt.DWORD), ("th32OwnerProcessID", wt.DWORD),
                ("tpBasePri", wt.LONG), ("tpDeltaPri", wt.LONG), ("dwFlags", wt.DWORD)]


def find_pid():
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "(Get-Process basara4-core -ErrorAction SilentlyContinue | Select-Object -First 1).Id"],
                         capture_output=True, text=True).stdout.strip()
    if not out:
        sys.exit("basara4-core is not running")
    return int(out)


def thread_ids(pid):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    te = THREADENTRY32()
    te.dwSize = ctypes.sizeof(te)
    ids = []
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            ids.append(te.th32ThreadID)
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return ids


def cpu_time(handle):
    c, e, kt, ut = (wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME())
    k32.GetThreadTimes(handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut))
    f = lambda x: (x.dwHighDateTime << 32 | x.dwLowDateTime) / 1e7
    return f(kt) + f(ut)


def snapshot_context(handle, buf):
    """Suspend, read RIP/RSP/RBP, resume.  Returns (rip, rsp, rbp) or None."""
    if k32.SuspendThread(handle) == 0xFFFFFFFF:
        return None
    try:
        ctypes.memset(buf, 0, 1232 + 16)
        addr = (ctypes.addressof(buf) + 15) & ~15
        struct.pack_into("<I", (ctypes.c_char * 4).from_address(addr + 0x30), 0, CTX_FLAGS)
        if not k32.GetThreadContext(handle, ctypes.c_void_p(addr)):
            return None
        raw = ctypes.string_at(addr, 1232)
        return struct.unpack_from("<Q", raw, 0xF8)[0], struct.unpack_from("<Q", raw, 0x98)[0], struct.unpack_from("<Q", raw, 0xA0)[0]
    finally:
        k32.ResumeThread(handle)


def read_u64(proc, address):
    data = ctypes.create_string_buffer(8)
    n = ctypes.c_size_t()
    if not k32.ReadProcessMemory(proc, ctypes.c_void_p(address), data, 8, ctypes.byref(n)) or n.value != 8:
        return None
    return struct.unpack("<Q", data.raw)[0]


psapi = ctypes.WinDLL("psapi", use_last_error=True)
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ctypes.POINTER(wt.HMODULE), wt.DWORD, ctypes.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleInformation.argtypes = [wt.HANDLE, wt.HMODULE, ctypes.c_void_p, wt.DWORD]
psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, wt.HMODULE, wt.LPWSTR, wt.DWORD]


class MODULEINFO(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("size", wt.DWORD), ("entry", ctypes.c_void_p)]


def load_modules(proc):
    arr = (wt.HMODULE * 2048)()
    need = wt.DWORD()
    mods = []
    if psapi.EnumProcessModulesEx(proc, arr, ctypes.sizeof(arr), ctypes.byref(need), 3):
        for i in range(min(2048, need.value // ctypes.sizeof(wt.HMODULE))):
            info = MODULEINFO()
            name = ctypes.create_unicode_buffer(260)
            if psapi.GetModuleInformation(proc, arr[i], ctypes.byref(info), ctypes.sizeof(info)):
                psapi.GetModuleBaseNameW(proc, arr[i], name, 260)
                mods.append((info.base or 0, info.size, name.value))
    return mods


MODULES = []


def where(a):
    if is_guest(a):
        return f"guest 0x{a:x}"
    for base, size, name in MODULES:
        if base <= a < base + size:
            return f"{name}+0x{a - base:x}"
    return f"0x{a:x}"

def is_guest(a):
    return 0x800000000 <= a < 0x80f000000


def chain(proc, rip, rsp, rbp, depth=8):
    frames = [rip]
    # a leaf or a function without a frame pointer: also show the first plausible return address near rsp
    for k in range(0, 32):
        v = read_u64(proc, rsp + 8 * k)
        if v and is_guest(v) and v != rip:
            frames.append(("stack+%d" % (8 * k), v))
            break
    bp = rbp
    for _ in range(depth):
        if not bp or bp & 7 or bp < 0x10000:
            break
        ret = read_u64(proc, bp + 8)
        nxt = read_u64(proc, bp)
        if ret is None or nxt is None or not is_guest(ret):
            break
        frames.append(("rbp", ret))
        if nxt <= bp:
            break
        bp = nxt
    return frames


def stack_scan(proc, rsp, words=1024):
    """Return addresses found on the stack that point into the game exe or the driver (host call chain, best effort)."""
    data = ctypes.create_string_buffer(words * 8)
    n = ctypes.c_size_t()
    if not k32.ReadProcessMemory(proc, ctypes.c_void_p(rsp), data, words * 8, ctypes.byref(n)):
        return []
    out = []
    for i in range(n.value // 8):
        v = struct.unpack_from("<Q", data.raw, i * 8)[0]
        for base, size, name in MODULES:
            if base <= v < base + size and name.lower() in ("basara4-core.exe",):
                out.append((i * 8, name, v - base))
                break
    return out


def fmt(f):
    return where(f) if isinstance(f, int) else f"{f[0]}:{where(f[1])}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    seconds = 3.0
    if "--seconds" in sys.argv:
        seconds = float(sys.argv[sys.argv.index("--seconds") + 1])
        args = [a for a in args if a != str(sys.argv[sys.argv.index("--seconds") + 1])]
    pid = int(args[0]) if args else find_pid()
    proc = k32.OpenProcess(PROCESS_VM_READ, False, pid)
    if not proc:
        sys.exit("cannot open the process (run from the same user / elevated shell)")
    MODULES.extend(load_modules(proc))
    handles = {}
    for tid in thread_ids(pid):
        h = k32.OpenThread(THREAD_ALL, False, tid)
        if h:
            handles[tid] = h
    print(f"pid {pid}: {len(handles)} threads; measuring CPU for {seconds:.0f} s")
    before = {t: cpu_time(h) for t, h in handles.items()}
    time.sleep(seconds)
    busy = sorted(((cpu_time(h) - before[t]) / seconds, t) for t, h in handles.items())[::-1]
    hot = [(c, t) for c, t in busy if c >= 0.15][:6]
    print("busiest threads (fraction of one core): " + ", ".join(f"{t}={c:.2f}" for c, t in busy[:8]))
    if not hot:
        print("no thread above 15% - the game is not spinning right now")
        return
    buf = ctypes.create_string_buffer(1232 + 16)
    for c, t in hot:
        h = handles[t]
        hist = Counter()
        chains = Counter()
        for i in range(400):
            ctx = snapshot_context(h, buf)
            if not ctx:
                continue
            rip, rsp, rbp = ctx
            hist[rip] += 1
            if i % 4 == 0:
                chains[tuple(fmt(f) for f in chain(proc, rip, rsp, rbp))] += 1
            time.sleep(0.002)
        print(f"\n=== thread {t}  ({c:.2f} cores)  top instruction pointers of {sum(hist.values())} samples")
        for rip, n in hist.most_common(8):
            kind = "game" if is_guest(rip) else "host"
            print(f"   {n:4}  {where(rip)}")
        if not any(is_guest(r) for r in hist):
            ctx = snapshot_context(h, buf)
            if ctx:
                seen = []
                for off, name, rva in stack_scan(proc, ctx[1]):
                    if rva not in seen:
                        seen.append(rva)
                print("   game-exe return addresses on the stack (rva, innermost first): " + " ".join(f"0x{r:x}" for r in seen[:16]))
        print("   call chains (innermost first):")
        for ch, n in chains.most_common(4):
            print(f"   {n:3}x  " + " <- ".join(ch))


if __name__ == "__main__":
    main()
