"""Every thread of the running game: where it waits, with host function names (llvm-symbolizer on the unstripped build).
  python tools/hang_all.py [pid]
Best effort: return addresses are found by scanning each thread's stack (host code has no frame pointers), so a frame or two can be stale."""
import ctypes
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hang_sample as hs  # noqa: E402

SYM = r"C:\msys64\clang64\bin\llvm-symbolizer.exe"
EXE = r"G:\basara4-pc\out\gpu\basara4-core.exe"


def thread_name(tid):
    h = hs.k32.OpenThread(0x0800 | 0x0040, False, tid)  # query limited information
    if not h:
        return '?'
    p = ctypes.c_wchar_p()
    try:
        if hs.k32.GetThreadDescription(h, ctypes.byref(p)) >= 0 and p.value:
            return p.value
    except Exception:
        pass
    return ''


def symbolize(rvas):
    if not rvas:
        return {}
    out = subprocess.run([SYM, f"--obj={EXE}", "--relative-address", "--functions=short", "--demangle", "--inlining=false"] + [hex(r) for r in rvas],
                         capture_output=True, text=True).stdout.split("\n\n")
    names = {}
    for rva, block in zip(rvas, out):
        line = block.strip().split("\n")[0] if block.strip() else "?"
        names[rva] = line[:110]
    return names


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else hs.find_pid()
    proc = hs.k32.OpenProcess(hs.PROCESS_VM_READ, False, pid)
    hs.MODULES.extend(hs.load_modules(proc))
    buf = ctypes.create_string_buffer(1232 + 16)
    rows = []
    for tid in hs.thread_ids(pid):
        h = hs.k32.OpenThread(hs.THREAD_ALL, False, tid)
        if not h:
            continue
        ctx = hs.snapshot_context(h, buf)
        if not ctx:
            continue
        rip, rsp, _ = ctx
        frames = []
        for _, _, rva in hs.stack_scan(proc, rsp, 2048):
            if rva not in frames:
                frames.append(rva)
        rows.append((tid, rip, frames[:10], hs.cpu_time(h)))
    names = symbolize(sorted({r for _, _, f, _ in rows for r in f}))
    rows.sort(key=lambda r: -r[3])
    for tid, rip, frames, cpu in rows:
        if not frames and not hs.is_guest(rip):
            continue
        print(f"thread {tid} [{thread_name(tid)}]  cpu={cpu:.0f}s  at {hs.where(rip)}")
        for r in frames[:8]:
            print(f"      {names.get(r, '?')}")


if __name__ == "__main__":
    main()
