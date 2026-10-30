"""Basara 4 PC launcher - by Razz.

Runs Sengoku Basara 4 Sumeragi (PS4, CUSA01159) on the native runtime in bin\\ (basara4-core.exe), a fork of
SpartanPort. Free software under the GNU GPL v2 or later; the credits are in NOTICE.md.

Everything lives next to this program:
  bin\\   the runtime (basara4-core.exe) and its DLLs
  game\\  the game dump (the folder with eboot.bin); another folder can be chosen
  out\\   the prepared game image, made automatically from eboot.bin
  user\\  saves, shader cache, settings, the log
"""
import hashlib
import importlib.util
import json
import os
import re
import runpy
import ssl
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
ROOT = (Path(sys.executable) if FROZEN else Path(__file__)).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
try:
    os.chdir(str(ROOT))
except OSError:
    pass
BIN, OUT, USER, SCRIPTS = ROOT / "bin", ROOT / "out", ROOT / "user", ROOT / "scripts"
DATA = ROOT / "data"
TRANSLATION_DB = DATA / "en_translation_db.json"
PATCH_DIR = ROOT / "mods" / "english_patch"
OVERLAY_DIR = USER / "app0_overlay"
CONFIG = USER / "launcher.json"
INI = USER / "basara4.ini"
OLD_INI = USER / "spartanport.ini"
WINDOW_ICON = USER / "window_icon.bmp"
CORE = BIN / "basara4-core.exe"
VERSION = "1.0.4"
BRAND = "Basara 4 PC"
AUTHOR = "Razz"
FORK = "DarkTama"
GITHUB_REPO = f"{FORK}/basara4-native-pc"


FATAL_PATTERNS = [
    r"(STOP: .*)",
    r"(Fault: .*)",
    r"(ASSERT_MSG.*)",
    r"(vk::Result=.*)",
    r"(VK_ERROR_\w+)",
    r"(GPU \[Render\.Vulkan\] <Error> .*)",
    r"(GPU \[Render\.Vulkan\] <Warning> .*)",
    r"(GPU \[Lib\.\w+\] <Error> .*)",
    r"(Runtime: open\(.*?\) -> not found)",
    r"(guest raised debug exception .*)",
    r"(cannot create \d+ MiB of guest memory .*)",
    r"(error 1455.*)",
    r"(ERROR_COMMITMENT_LIMIT.*)",
    r"(no rewrite table matches the image.*)",
    r"(missing MOVBE, BMI1, LZCNT.*)",
]


def extract_crash_summary(log_path, max_context_lines=10):
    """Parses log from bottom-up and extracts fatal signatures and tail context."""
    if not log_path.is_file():
        return {"detected_errors": ["File log tidak ditemukan (game belum sempat menulis log)."], "tail_context": ""}
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        return {"detected_errors": [f"Gagal membaca log: {e}"], "tail_context": ""}
    if not lines:
        return {"detected_errors": ["Log kosong (game berhenti sebelum inisialisasi)."], "tail_context": ""}

    tail_lines = lines[-max_context_lines:]
    detected = []
    for line in reversed(lines):
        for pat in FATAL_PATTERNS:
            m = re.search(pat, line)
            if m:
                matched_str = m.group(1).strip()
                if matched_str not in detected:
                    detected.append(matched_str)
        if len(detected) >= 4:
            break
    return {
        "detected_errors": detected if detected else ["Tidak ada fatal signature terdaftar (game keluar mendadak)."],
        "tail_context": "\n".join(tail_lines),
        "total_lines": len(lines)
    }


def check_latest_release(repo=GITHUB_REPO):
    """Checks the latest GitHub release. Returns dict of asset info or None."""
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "Basara4-PC-Launcher"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        if parse_version(tag) > parse_version(VERSION):
            for a in data.get("assets", []):
                name = a.get("name", "")
                if name.endswith(".zip") and ("basara4" in name.lower() or "windows" in name.lower()):
                    return {
                        "tag": tag,
                        "url": a.get("browser_download_url"),
                        "name": name,
                        "size": a.get("size", 0),
                        "body": data.get("body", ""),
                    }
            for a in data.get("assets", []):
                if a.get("name", "").endswith(".zip"):
                    return {
                        "tag": tag,
                        "url": a.get("browser_download_url"),
                        "name": name,
                        "size": a.get("size", 0),
                        "body": data.get("body", ""),
                    }
    except Exception:
        pass
    return None
LOG = USER / "last_run.log"
PREPARE_LOG = USER / "prepare.log"
MAPPING = USER / "gamepad_mapping.txt"
AUDIO_LOG = USER / "audio_debug.log"
AUDIO_WAV = USER / "audio_wav"  # SPARTAN_AUDIO_WAV_DIR: float WAV capture of the mix and of long voices
STAMP = OUT / "prepared.json"
WAV_WARNING = ("WAV capture saves the final mix and every long voice as uncompressed audio in user\\audio_wav: about 70 MB per "
               "minute, up to 3 GB for one session.\n\nUse it only to find an audio problem, and delete the files afterwards.\n\n"
               "It applies to the next game start only and switches itself off after that. It also turns on the audio debug "
               "log. Turn it on?")
PIPELINE = [("prepare.py", []), ("link_libc.py", []), ("link_modules.py", []),
            ("content_profile.py", ["--sku", "full"]),
            ("isa_rewrite.py", [])]
NO_WINDOW = 0x08000000


def wav_captures():
    """The audio captures an earlier session left in user\\audio_wav: (files, total bytes)."""
    files = sorted(AUDIO_WAV.glob("*.wav")) if AUDIO_WAV.is_dir() else []
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return files, total


def format_size(n):
    return f"{n / 2**30:.1f} GB" if n >= 2**30 else f"{n / 2**20:.0f} MB"


def check_commit_limit_mb():
    """Returns available virtual memory (page file commit limit) in MB, or None if unavailable."""
    try:
        import ctypes
        from ctypes import wintypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullAvailPageFile // (1024 * 1024)
    except Exception:
        pass
    return None


def run_script_mode():
    """`launcher.exe --run-script NAME args...`: runs one of the preparation scripts in this process."""
    script = SCRIPTS / sys.argv[2]
    sys.path.insert(0, str(SCRIPTS))
    sys.argv = [str(script)] + sys.argv[3:]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exit_:
        code = exit_.code
        sys.exit(code if isinstance(code, int) else (0 if code is None else 1))


if len(sys.argv) > 2 and sys.argv[1] == "--run-script":
    run_script_mode()
    sys.exit(0)

import tkinter as tk  # noqa: E402  (after the script mode, which needs no window)
from tkinter import filedialog, font as tkfont, messagebox, ttk  # noqa: E402

try:
    from PIL import Image, ImageTk
except ImportError:  # still works, just without artwork
    Image = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import basara4_patches  # noqa: E402
import basara4_translation  # noqa: E402
import basara4_overlay  # noqa: E402
import basara4_repatch  # noqa: E402

DEFAULTS = {"game": "", "display_mode": "borderless", "vsync": True, "fps_limit": 60, "rcas": False,
            "anisotropy": 0, "resolution": "native", "upscale": "off", "audio_log": False, "audio_wav": False,
            "gpu_index": -1, "keymap": {}, "mouse_camera": True, "mouse_sens": 100, "english_patch": False,
            "ps3_patch_source": ""}

DEFAULT_PC_KEYMAP = {
    "square": "Mouse Left",       # Serangan Biasa (Normal Attack)
    "triangle": "Mouse Right",    # Special Skill 1
    "cross": "Space",             # Lompat (Jump)
    "l1": "Left Shift",           # Tangkis / Guard / Evade
    "r1": "E",                    # Special Skill 2
    "r2": "Q",                    # Special Skill 3
    "circle": "F",                # Basara Attack (Ultimate)
    "l2": "R",                    # Sengoku Drive / Poise Boost
    "touchpad": "Tab",            # Taunt / Provoke
    "options": "Return",          # Pause / In-Game Menu
    "up": "Up",                   # D-Pad Up
    "down": "Down",               # D-Pad Down
    "left": "Left",               # D-Pad Left
    "right": "Right",             # D-Pad Right
    "l3": "Z",                    # L3
    "r3": "C",                    # R3
}

ACTION_DESCRIPTIONS = [
    ("square", "Normal Attack (Serangan Biasa)", "Kotak / Square"),
    ("triangle", "Special Skill 1 (Skill Utama)", "Segitiga / Triangle"),
    ("cross", "Jump (Lompat)", "Silang / Cross"),
    ("circle", "Basara Attack (Ultimate)", "Lingkaran / Circle"),
    ("l1", "Guard / Tangkis / Dash", "L1"),
    ("r1", "Special Skill 2 (Skill Tambahan)", "R1"),
    ("r2", "Special Skill 3 (Skill Karakter)", "R2"),
    ("l2", "Sengoku Drive / Boost", "L2"),
    ("touchpad", "Taunt / Provoke", "Touchpad / Tab"),
    ("options", "Pause / In-Game Menu", "Options / Enter"),
    ("up", "D-Pad Up (Navigasi / Perintah)", "D-Pad Atas"),
    ("down", "D-Pad Down (Navigasi / Perintah)", "D-Pad Bawah"),
    ("left", "D-Pad Left (Navigasi / Perintah)", "D-Pad Kiri"),
    ("right", "D-Pad Right (Navigasi / Perintah)", "D-Pad Kanan"),
    ("l3", "L3 Action", "L3"),
    ("r3", "R3 Action / Lock-on", "R3"),
]


def enumerate_gpus():
    """Enumerate system Vulkan GPUs using vulkan-1.dll. Returns list of (index, display_name, is_discrete)."""
    gpus = [(-1, "Auto (Prefer discrete GPU)", True)]
    try:
        import ctypes
        vulkan = ctypes.cdll.LoadLibrary("vulkan-1.dll")
        class VkApplicationInfo(ctypes.Structure):
            _fields_ = [
                ("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p),
                ("pApplicationName", ctypes.c_char_p), ("applicationVersion", ctypes.c_uint32),
                ("pEngineName", ctypes.c_char_p), ("engineVersion", ctypes.c_uint32),
                ("apiVersion", ctypes.c_uint32),
            ]
        class VkInstanceCreateInfo(ctypes.Structure):
            _fields_ = [
                ("sType", ctypes.c_uint32), ("pNext", ctypes.c_void_p),
                ("flags", ctypes.c_uint32), ("pApplicationInfo", ctypes.POINTER(VkApplicationInfo)),
                ("enabledLayerCount", ctypes.c_uint32), ("ppEnabledLayerNames", ctypes.c_void_p),
                ("enabledExtensionCount", ctypes.c_uint32), ("ppEnabledExtensionNames", ctypes.c_void_p),
            ]
        class VkPhysicalDeviceProperties(ctypes.Structure):
            _fields_ = [
                ("apiVersion", ctypes.c_uint32), ("driverVersion", ctypes.c_uint32),
                ("vendorID", ctypes.c_uint32), ("deviceID", ctypes.c_uint32),
                ("deviceType", ctypes.c_uint32), ("deviceName", ctypes.c_char * 256),
                ("pipelineCacheUUID", ctypes.c_ubyte * 16), ("limits", ctypes.c_byte * 504),
                ("sparseProperties", ctypes.c_byte * 20),
            ]
        app_info = VkApplicationInfo(sType=1, pApplicationName=b"Basara4Launcher", apiVersion=(1 << 22) | (2 << 12))
        ci = VkInstanceCreateInfo(sType=2, pApplicationInfo=ctypes.pointer(app_info))
        vulkan.vkCreateInstance.argtypes = [ctypes.POINTER(VkInstanceCreateInfo), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        vulkan.vkCreateInstance.restype = ctypes.c_int32
        vulkan.vkDestroyInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        vulkan.vkEnumeratePhysicalDevices.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p)]
        vulkan.vkEnumeratePhysicalDevices.restype = ctypes.c_int32
        vulkan.vkGetPhysicalDeviceProperties.argtypes = [ctypes.c_void_p, ctypes.POINTER(VkPhysicalDeviceProperties)]

        inst = ctypes.c_void_p()
        if vulkan.vkCreateInstance(ctypes.byref(ci), None, ctypes.byref(inst)) == 0:
            count = ctypes.c_uint32(0)
            vulkan.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), None)
            if count.value > 0:
                devs = (ctypes.c_void_p * count.value)()
                vulkan.vkEnumeratePhysicalDevices(inst, ctypes.byref(count), devs)
                for i in range(count.value):
                    p = VkPhysicalDeviceProperties()
                    vulkan.vkGetPhysicalDeviceProperties(devs[i], ctypes.byref(p))
                    name = p.deviceName.decode("utf-8", errors="replace").strip("\x00")
                    is_disc = (p.deviceType == 2)
                    kind_tag = "dGPU" if is_disc else ("iGPU" if p.deviceType == 1 else "GPU")
                    gpus.append((i, f"GPU {i}: {name} ({kind_tag})", is_disc))
            vulkan.vkDestroyInstance(inst, None)
    except Exception:
        pass
    return gpus


SYSTEM_GPUS = enumerate_gpus()
DISPLAY_MODES = [("Windowed", "windowed"), ("Borderless", "borderless"), ("Exclusive", "fullscreen")]
FPS_LIMITS = [("60 (game default)", 60), ("90", 90), ("120", 120), ("144", 144), ("165", 165),
              ("240", 240), ("Unlimited", 0)]
RESOLUTIONS = [("1920 x 1080  (native)", "native"), ("1280 x 720", "720p"), ("1600 x 900", "900p"),
               ("2560 x 1440", "1440p"), ("3200 x 1800", "1800p"), ("3840 x 2160  (4K)", "4K")]
UPSCALERS = [("Off  (plain scaling)", "off"), ("FSR 1  (sharper)", "fsr1")]
ANISO = [("Off", 0), ("2x", 2), ("4x", 4), ("8x", 8), ("16x", 16)]
BG, PANEL, FG, MUTED, GOLD, RED = "#12100e", "#1c1916", "#eadfcb", "#8d8374", "#c9a24a", "#9c2b24"
RAZZ = "#e0526a"  # the accent of the by-Razz mark
FIELD, EDGE, LINE, RULE, HOVER, OK = "#12100e", "#3f382f", "#332d27", "#26211d", "#221e1a", "#7da36b"
FONT, FONT_SMALL = ("Segoe UI", 10), ("Segoe UI", 8)
CHEAT_HINT = "Can also be toggled in game (Insert / R3+L2)"
CHEAT_KEYS = ["cheat_gold", "cheat_coins", "cheat_growth_points", "cheat_weapon_points", "cheat_weapon_rank"]

# Every string the main window shows lives here, so a translation touches one place. Hints are one line each.
NAV = [("game", "Game"), ("display", "Display"), ("controls", "Controls"), ("mods", "Mods & Cheats"), ("tools", "Tools")]
PAGE_SUBTITLE = {"display": "Applied the next time you press PLAY."}
PAGE_HINT = {
    "game": "The game image is made on the first start (about a minute).",
    "display": "In-game menu: Insert, or R3 + L2 on the pad (it changes these too).",
    "controls": "Map controller: press each pad button when asked. Skip it if your pad already works.",
    "mods": "Import needs your own PS3 patch files. Cheats can also be toggled in game.",
    "tools": "Open log: the launcher and runtime log of the last run.",
}
GROUP = {"translation": "Translation", "cheats": "Cheats"}
ROW = {  # key: (label, hint)
    "game_folder": ("Game folder", "The folder that holds eboot.bin."),
    "game_image": ("Game image", "Made from eboot.bin on the first start; again if eboot.bin changes."),
    "display_mode": ("Display mode", "Windowed, borderless fullscreen or exclusive fullscreen."),
    "gpu": ("Graphics card", "Auto picks the discrete GPU when there is one."),
    "resolution": ("Window size", "The game draws at 1080p. Window size sets windowed mode."),
    "fps": ("Frame limit", "Above 60 turns off the game's own limiter. VSync still caps at monitor refresh."),
    "upscale": ("Upscaling", "FSR 1 sharpens if the screen is above 1080p."),
    "aniso": ("Anisotropic", "Texture filtering: sharper at a slant, costs a little speed."),
    "vsync": ("VSync", "Caps the frame rate at the monitor refresh."),
    "rcas": ("Sharpening (RCAS)", "Sharpens the final image."),
    "pad": ("Gamepad", "Custom mapping is made with the buttons below."),
    "map_pad": ("Map controller", "Press each pad button when asked. Skip it if your pad already works."),
    "map_sticks": ("Map sticks only", "Maps only the two sticks; the buttons stay as they are."),
    "reset_map": ("Reset mapping", "Deletes the saved controller mapping (a .bak copy is kept)."),
    "keymap": ("Keyboard & Mouse controls", "Choose the key and mouse bindings and the mouse camera."),
    "english": ("English Translation Patch", "PS3 v0.85 port. Needs an imported patch; applied when you press PLAY."),
    "import_patch": ("Import PS3 English Patch", "Pick your own PS3 (BLJM61248) English patch folder."),
    "rebuild_patch": ("Rebuild Translation Patch", "Builds the patch again from the imported files."),
    "cheat_gold": ("Gold won't decrease", CHEAT_HINT),
    "cheat_coins": ("Special Coin x99999", CHEAT_HINT),
    "cheat_growth_points": ("Growth points won't decrease", CHEAT_HINT),
    "cheat_weapon_points": ("Weapon points won't decrease", CHEAT_HINT),
    "cheat_weapon_rank": ("Weapon rank points won't decrease", CHEAT_HINT),
    "open_log": ("Open log", "The launcher and runtime log of the last run."),
    "open_audio_log": ("Open audio log", "Needs \"Audio debug log\" on, then a game start."),
    "open_saves": ("Open saves", "Opens user\\savedata in Explorer."),
    "audio_log": ("Audio debug log", "One timestamped line per audio event, for bug reports."),
    "audio_wav": ("WAV capture (up to 3 GB)", "Next game start only; about 70 MB per minute. Turns the audio log on."),
}


def find_game():
    """The first folder under game\\ that holds an eboot.bin."""
    base = ROOT / "game"
    if (base / "eboot.bin").is_file():
        return str(base)
    for child in sorted(base.iterdir()) if base.is_dir() else []:
        if (child / "eboot.bin").is_file():
            return str(child)
    return ""


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        pass
    if not cfg["game"] or not (Path(cfg["game"]) / "eboot.bin").is_file():
        cfg["game"] = find_game()
    return cfg


def save_config(cfg):
    USER.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def patch_needs_rebuild(game: Path, db_path: Path, patch_dir: Path) -> bool:
    manifest_file = patch_dir / "manifest.json"
    if not manifest_file.is_file():
        return True
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True

    # If patch was imported from PS3 patch, do not clobber it with DB rebuild
    if manifest.get("mode") == "ps3_import":
        return False

    # Check DB sha256
    try:
        db_bytes = db_path.read_bytes()
        db_hash = hashlib.sha256(db_bytes).hexdigest()
        if manifest.get("db_sha256") != db_hash:
            return True
    except OSError:
        return True

    # Check written_arcs source arc size/mtime
    written_arcs = manifest.get("written_arcs", [])
    if not written_arcs:
        return True

    _, rom_dir = basara4_translation.resolve_game_paths(game)
    for entry in written_arcs:
        rel = entry.get("relative_path")
        if not rel:
            return True
        src_arc = rom_dir / rel
        if not src_arc.is_file():
            return True
        st = src_arc.stat()
        if entry.get("source_size") != st.st_size:
            return True
        if abs(entry.get("source_mtime", 0) - st.st_mtime) > 1e-4:
            return True
        out_arc = patch_dir / "nativePS4" / "rom" / rel
        if not out_arc.is_file():
            return True

    return False


def read_ini(path):
    values = {}
    try:
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                key, value = line.strip().split("=", 1)
                values[key] = value
    except OSError:
        pass
    return values


def write_ini(path, updates):
    """Update only the given keys; every other line the runtime wrote stays as it was."""
    path = Path(path)
    lines, seen = [], set()
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key = line.split("=", 1)[0] if "=" in line else None
            if key in updates:
                lines.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                lines.append(line)
    except OSError:
        pass
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def image_ready(game):
    """Whether out\\ holds an image made from this game's current eboot.bin."""
    try:
        stamp = json.loads(STAMP.read_text(encoding="utf-8-sig"))
        return (stamp.get("eboot_sha256") == file_sha256(Path(game) / "eboot.bin")
                and all((OUT / name).is_file() for name in ("boot-linked.bin", "content.bin", "eboot.elf")))
    except (OSError, ValueError):
        return False


def prepare_image(game, progress):
    """Runs the preparation scripts on the game's eboot.bin. Returns an error text or None."""
    OUT.mkdir(parents=True, exist_ok=True)
    USER.mkdir(parents=True, exist_ok=True)
    with open(PREPARE_LOG, "w", encoding="utf-8", errors="replace") as log:
        for index, (name, extra) in enumerate(PIPELINE, 1):
            progress(f"Preparing the game image ({index}/{len(PIPELINE)}): {name[:-3]}…")
            script = [sys.executable, "--run-script", name] if FROZEN else [sys.executable, str(SCRIPTS / name)]
            result = subprocess.run(script + [str(game), "--out", str(OUT)] + extra, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            if result.returncode:
                return f"{name} failed (exit {result.returncode}). See user\\prepare.log"
    STAMP.write_text(json.dumps({"eboot_sha256": file_sha256(Path(game) / "eboot.bin"), "game": str(game)}),
                     encoding="utf-8")
    return None


class Launcher(tk.Tk):
    def __init__(self, play_now=False):
        super().__init__()
        self.cfg = load_config()
        self.proc = None
        self.busy = False
        self.user_stopped = False
        self.pending = None
        self.play_now = play_now
        self.title(f"Sengoku Basara 4 Sumeragi  \u00b7  {BRAND} by {AUTHOR}")
        self.configure(bg=BG)
        self.geometry("1000x620")
        self.resizable(False, False)
        icon = Path(__file__).resolve().parent / "basara4.ico"
        if icon.is_file():
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass
        self._style()
        self._art()
        self._build()
        self._load_ini_into_ui()
        self._refresh_image_label()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(500, self._poll)
        self.update_info = None
        self.update_btn = None
        if play_now:
            self.iconify()
            self.after(300, self.on_play)
        else:
            self.after(700, self._check_wav_leftovers)
            self.after(1200, lambda: threading.Thread(target=self._check_update_thread, daemon=True).start())

    # ---- look ----
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL,
                    lightcolor=PANEL, darkcolor=PANEL, font=FONT)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Panel.TLabel", background=PANEL, foreground=FG)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Head.TLabel", background=PANEL, foreground=GOLD, font=("Segoe UI Semibold", 11))
        s.configure("TRadiobutton", background=PANEL, foreground=FG)
        s.configure("TCheckbutton", background=PANEL, foreground=FG, indicatorbackground=FIELD,
                    indicatorforeground=GOLD, upperbordercolor=EDGE, lowerbordercolor=EDGE)
        s.map("TRadiobutton", background=[("active", PANEL)], foreground=[("active", GOLD)])
        s.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", GOLD)],
              indicatorbackground=[("active", FIELD)])
        s.configure("TButton", background="#2a2622", foreground=FG, padding=(12, 6), borderwidth=1,
                    bordercolor="#2a2622", lightcolor="#2a2622", darkcolor="#2a2622")
        edge = [("disabled", LINE), ("active", "#3a342e")]  # a disabled button keeps a visible outline
        s.map("TButton", background=[("active", "#3a342e"), ("disabled", "#1c1916")],
              foreground=[("disabled", MUTED)], bordercolor=edge, lightcolor=edge, darkcolor=edge)
        s.configure("Play.TButton", background=RED, foreground="#fff", font=("Segoe UI Semibold", 15),
                    padding=(10, 12), bordercolor=RED, lightcolor=RED, darkcolor=RED)
        play_edge = [("disabled", "#4a2522"), ("active", "#b8362d")]
        s.map("Play.TButton", background=[("active", "#b8362d"), ("disabled", "#4a2522")],
              bordercolor=play_edge, lightcolor=play_edge, darkcolor=play_edge)
        s.configure("Seg.Toolbutton", background="#2a2622", foreground=FG, padding=(10, 6), borderwidth=0,
                    anchor="center")
        s.map("Seg.Toolbutton", background=[("selected", GOLD), ("active", "#3a342e")],
              foreground=[("selected", BG)])
        s.configure("TCombobox", arrowcolor=GOLD, bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE,
                    padding=(6, 4), selectbackground=FIELD, selectforeground=FG)
        s.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
              selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)],
              bordercolor=[("focus", GOLD)], lightcolor=[("focus", GOLD)], darkcolor=[("focus", GOLD)])
        s.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, bordercolor=EDGE,
                    lightcolor=EDGE, darkcolor=EDGE, padding=(6, 4))
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", GOLD)
        self.option_add("*TCombobox*Listbox.selectForeground", BG)

    def _art(self):
        self.cover = None
        if Image is None or not self.cfg["game"]:
            return
        try:
            self.cover = ImageTk.PhotoImage(
                Image.open(Path(self.cfg["game"]) / "sce_sys" / "icon0.png").convert("RGB").resize((220, 220)))
        except OSError:
            pass

    # ---- layout ----
    def _build(self):
        self._font = tkfont.Font(self, font=FONT)
        self.game_var = tk.StringVar(value=self.cfg["game"])
        self.mode_var = tk.StringVar(value=self.cfg["display_mode"])
        self.gpu_var, self.fps_var, self.aniso_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.res_var, self.up_var = tk.StringVar(), tk.StringVar()
        self.vsync_var = tk.BooleanVar(value=self.cfg["vsync"])
        self.rcas_var = tk.BooleanVar(value=self.cfg["rcas"])
        self.audio_var = tk.BooleanVar(value=self.cfg.get("audio_log", False))
        self.wav_var = tk.BooleanVar(value=False)
        self.english_var = tk.BooleanVar(value=self.cfg.get("english_patch", False))
        self.cheat_vars = {key: tk.BooleanVar(value=False) for key in CHEAT_KEYS}  # live in the runtime ini

        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True, padx=14, pady=14)
        left = tk.Frame(shell, bg=PANEL, width=240, padx=16, pady=16)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)
        self._build_left(left)
        right = tk.Frame(shell, bg=PANEL)
        right.pack(side="left", fill="both", expand=True)
        self._build_right(right)
        game = self.cfg["game"]
        self.show_page("display" if game and (Path(game) / "eboot.bin").is_file() else "game")

    def _build_left(self, left):
        bottom = tk.Frame(left, bg=PANEL)  # packed first so a long status can never push it out
        bottom.pack(side="bottom", fill="x")
        self.mark_frame = bottom
        self.version_label = tk.Label(bottom, text=f"v{VERSION}", bg=PANEL, fg=MUTED, font=FONT_SMALL)
        self.version_label.pack(pady=(0, 6))
        self.check_btn = ttk.Button(bottom, text="Check for updates", command=self.check_update_manual)
        self.check_btn.pack(fill="x")
        credit = tk.Frame(bottom, bg=PANEL, cursor="hand2")  # clicking it opens the credits
        credit.pack(pady=(10, 0))
        for text, color, font in ((f"{BRAND} by ", MUTED, FONT_SMALL), (AUTHOR, RAZZ, ("Segoe UI", 8, "bold italic")),
                                  (f" · fork by {FORK}", MUTED, FONT_SMALL)):
            tk.Label(credit, text=text, bg=PANEL, fg=color, font=font, bd=0, padx=0).pack(side="left")
        for widget in [credit] + credit.winfo_children():
            widget.bind("<Button-1>", lambda _e: self.show_credits())

        if self.cover:
            frame = tk.Frame(left, bg=GOLD, padx=1, pady=1)
            frame.pack()
            tk.Label(frame, image=self.cover, bd=0, bg=PANEL).pack()
        tk.Label(left, text="戦国BASARA4 皇", bg=PANEL, fg=GOLD, font=("Segoe UI Semibold", 12)).pack(pady=(12, 0))
        tk.Label(left, text="Sengoku Basara 4 Sumeragi", bg=PANEL, fg=FG, font=FONT).pack()
        tk.Label(left, text="CUSA01159", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack()
        self.status = tk.Label(left, text="Ready", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=208)
        self.status.pack(pady=(14, 8))
        self.play = ttk.Button(left, text="PLAY", style="Play.TButton", command=self.on_play)
        self.play.pack(fill="x", pady=(0, 6))
        self.stop = ttk.Button(left, text="Stop game", command=self.on_stop, state="disabled")
        self.stop.pack(fill="x")

    def _build_right(self, right):
        nav = tk.Frame(right, bg=PANEL, width=150, pady=14)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Frame(right, bg=LINE, width=1).pack(side="left", fill="y")
        self.nav = {}
        for key, text in NAV:
            item = tk.Frame(nav, bg=PANEL)
            item.pack(fill="x")
            bar = tk.Frame(item, bg=PANEL, width=3)
            bar.pack(side="left", fill="y")
            label = tk.Label(item, text=text, bg=PANEL, fg=MUTED, font=("Segoe UI", 11), anchor="w", padx=13,
                             pady=9, cursor="hand2", takefocus=1, highlightthickness=1, highlightbackground=PANEL,
                             highlightcolor=GOLD)
            label.pack(side="left", fill="x", expand=True)
            for widget in (item, bar, label):
                widget.bind("<Button-1>", lambda _e, k=key: self.show_page(k))
            label.bind("<Return>", lambda _e, k=key: self.show_page(k))
            label.bind("<space>", lambda _e, k=key: self.show_page(k))
            self.nav[key] = (item, bar, label)

        content = tk.Frame(right, bg=PANEL)
        content.pack(side="left", fill="both", expand=True)
        tip = tk.Frame(content, bg=PANEL, height=35)  # the docked hint bar
        tip.pack(side="bottom", fill="x")
        tip.pack_propagate(False)
        tk.Frame(tip, bg=LINE, height=1).pack(fill="x")
        tk.Label(tip, text="?", bg=PANEL, fg=GOLD, font=("Segoe UI Semibold", 9)).pack(side="left", padx=(20, 6))
        self.hint = tk.Label(tip, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w")
        self.hint.pack(side="left", fill="x", expand=True)
        host = tk.Frame(content, bg=PANEL)
        host.pack(side="top", fill="both", expand=True, padx=20, pady=(16, 0))
        self.pages, self._lit_bar = {}, None
        for key, name in NAV:
            page = self.pages[key] = tk.Frame(host, bg=PANEL)
            title = tk.Frame(page, bg=PANEL)
            title.pack(fill="x", pady=(0, 8))
            tk.Label(title, text=name, bg=PANEL, fg=GOLD, font=("Segoe UI Semibold", 14)).pack(side="left")
            if key in PAGE_SUBTITLE:
                tk.Label(title, text=PAGE_SUBTITLE[key], bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(
                    side="left", padx=12, pady=(4, 0))
        self._page_game(self.pages["game"])
        self._page_display(self.pages["display"])
        self._page_controls(self.pages["controls"])
        self._page_mods(self.pages["mods"])
        self._page_tools(self.pages["tools"])

    def show_page(self, key):
        for name, page in self.pages.items():
            page.pack_forget()
            item, bar, label = self.nav[name]
            on = name == key
            fill = HOVER if on else PANEL
            item.config(bg=fill)
            label.config(bg=fill, fg=GOLD if on else MUTED, highlightbackground=fill,
                         font=("Segoe UI Semibold" if on else "Segoe UI", 11))
            bar.config(bg=GOLD if on else PANEL)
        self.pages[key].pack(fill="both", expand=True)
        self._light(None)
        self.hint.config(text=PAGE_HINT[key])

    # ---- settings rows ----
    def _light(self, bar):
        """The gold bar of the row being hovered or focused (None: no row)."""
        if self._lit_bar is not None:
            self._lit_bar.config(bg=PANEL)
        self._lit_bar = bar
        if bar is not None:
            bar.config(bg=GOLD)

    def _row(self, page, key, make, width=250, sub=False, stack=False):
        """One ruled row: label left, the control `make(holder)` right. Hovering or focusing it shows its hint."""
        label, hint = ROW[key]
        row = tk.Frame(page, bg=PANEL)
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)
        row.rowconfigure(0, minsize=40)
        bar = tk.Frame(row, bg=PANEL, width=3)
        bar.grid(row=0, column=0, sticky="ns")
        tk.Label(row, text=label, bg=PANEL, fg=FG, font=FONT).grid(row=0, column=1, sticky="w",
                                                                    padx=(10 + (23 if sub else 0), 0))
        holder = tk.Frame(row, bg=PANEL)
        if stack:  # a value too long for the right column gets the full width under its label
            holder.grid(row=1, column=0, columnspan=3, sticky="ew", padx=(13, 10), pady=(0, 8))
        else:
            holder.grid(row=0, column=2, sticky="e", padx=(0, 10))
        if width and not stack:
            holder.config(width=width, height=30)
            holder.pack_propagate(False)
        make(holder)
        tk.Frame(row, bg=RULE, height=1).grid(row=2, column=0, columnspan=3, sticky="ew")

        def show(_event=None):
            self._light(bar)
            self.hint.config(text=hint)

        def wire(widget):
            widget.bind("<Enter>", show, add="+")
            widget.bind("<FocusIn>", show, add="+")  # the same hint for keyboard and pad users
            for child in widget.winfo_children():
                wire(child)
        wire(row)
        return row

    def _group(self, page, key):
        tk.Label(page, text=GROUP[key].upper(), bg=PANEL, fg=GOLD, font=("Segoe UI Semibold", 9),
                 anchor="w").pack(fill="x", padx=13, pady=(14, 2))

    def _combo_row(self, page, key, var, values):
        need = max(self._font.measure(v) for v in values) + 48  # text + arrow + padding: nothing truncated
        self._row(page, key, lambda h: ttk.Combobox(h, textvariable=var, state="readonly", values=values).pack(
            fill="both", expand=True), width=max(250, need), stack=need > 330)

    def _check_row(self, page, key, var, command=None, sub=False):
        self._row(page, key, lambda h: ttk.Checkbutton(h, text="On", variable=var, command=command).pack(side="left"),
                  sub=sub)

    def _button_row(self, page, key, text, command, attr=None):
        def make(holder):
            button = ttk.Button(holder, text=text, command=command)
            button.pack(fill="both", expand=True)
            if attr:
                setattr(self, attr, button)
        self._row(page, key, make)

    def _page_game(self, page):
        def folder(holder):
            ttk.Button(holder, text="Browse…", command=self._browse).pack(side="right", padx=(6, 0))
            entry = ttk.Entry(holder, textvariable=self.game_var)
            entry.pack(side="left", fill="both", expand=True)
            entry.bind("<Map>", lambda _e: entry.xview_moveto(1))  # the folder name matters more than the drive
        self._row(page, "game_folder", folder, width=340)

        def image(holder):
            self.image_label = tk.Label(holder, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=330,
                                        justify="right", anchor="e")
            self.image_label.pack(side="right")
        self._row(page, "game_image", image, width=None)
        self.game_var.trace_add("write", lambda *_: self._refresh_image_label())
        # Rows for "Game update folder" and "DLC folder" will go here.

    def _page_display(self, page):
        def modes(holder):
            for label, key in DISPLAY_MODES:
                ttk.Radiobutton(holder, text=label, value=key, variable=self.mode_var, style="Seg.Toolbutton").pack(
                    side="left", fill="both", expand=True, padx=1)
        self._row(page, "display_mode", modes, width=340)
        self._combo_row(page, "gpu", self.gpu_var, [name for _, name, _ in SYSTEM_GPUS])
        self._combo_row(page, "resolution", self.res_var, [n for n, _ in RESOLUTIONS])
        self._combo_row(page, "fps", self.fps_var, [n for n, _ in FPS_LIMITS])
        self._combo_row(page, "upscale", self.up_var, [n for n, _ in UPSCALERS])
        self._combo_row(page, "aniso", self.aniso_var, [n for n, _ in ANISO])
        self._check_row(page, "vsync", self.vsync_var)
        self._check_row(page, "rcas", self.rcas_var)

    def _page_controls(self, page):
        def pad(holder):
            self.pad_label = tk.Label(holder, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=340,
                                      justify="right", anchor="e")
            self.pad_label.pack(side="right")
        self._row(page, "pad", pad, width=None)
        self._pad_text()
        self._button_row(page, "map_pad", "Map…", lambda: self.map_pad([]))
        self._button_row(page, "map_sticks", "Map sticks…", lambda: self.map_pad(["--sticks"]))
        self._button_row(page, "reset_map", "Reset", self.reset_map)
        self._button_row(page, "keymap", "Edit…", self.open_keymap_ui)

    def _page_mods(self, page):
        self._group(page, "translation")
        self._check_row(page, "english", self.english_var)
        self._button_row(page, "import_patch", "Import…", self.on_import_ps3_patch, "import_patch_btn")
        self._button_row(page, "rebuild_patch", "Rebuild", self.on_rebuild_patch, "rebuild_patch_btn")
        self._group(page, "cheats")
        for key in CHEAT_KEYS:
            self._check_row(page, key, self.cheat_vars[key])

    def _page_tools(self, page):
        self._button_row(page, "open_log", "Open", self.open_log)
        self._button_row(page, "open_audio_log", "Open", self.open_audio_log)
        self._button_row(page, "open_saves", "Open", self.open_saves)
        self._check_row(page, "audio_log", self.audio_var, self._audio_toggled)
        self._check_row(page, "audio_wav", self.wav_var, self._wav_toggled, sub=True)

    # ---- helpers ----
    def _browse(self):
        path = filedialog.askdirectory(initialdir=self.game_var.get() or str(ROOT), mustexist=True)
        if path:
            self.game_var.set(os.path.normpath(path))

    def _refresh_image_label(self):
        game = self.game_var.get().strip()
        if not game or not (Path(game) / "eboot.bin").is_file():
            text = "No eboot.bin in this folder."
        elif image_ready(game):
            text = "Game image ready."
        else:
            text = "The game image is made on the first start (about a minute); again if eboot.bin changes."
        self.image_label.config(text=text, fg=OK if text == "Game image ready." else MUTED)

    def _ini(self):
        if OLD_INI.exists() and not INI.exists():
            os.replace(OLD_INI, INI)  # settings made before the runtime was renamed
        return INI

    def _load_ini_into_ui(self):
        ini = read_ini(self._ini())
        mode = ini.get("display_mode", self.cfg["display_mode"])
        if mode in {k for _, k in DISPLAY_MODES}:
            self.mode_var.set(mode)
        if "vsync" in ini:
            self.vsync_var.set(ini["vsync"] == "1")
        if "rcas" in ini:
            self.rcas_var.set(ini["rcas"] == "1")
        res = ini.get("render_resolution", self.cfg["resolution"])
        self.res_var.set(next((n for n, v in RESOLUTIONS if v == res), RESOLUTIONS[0][0]))
        fsr = ini.get("fsr1")
        up = ("fsr1" if fsr == "1" else "off") if fsr is not None else self.cfg["upscale"]
        self.up_var.set(next((n for n, v in UPSCALERS if v == up), UPSCALERS[0][0]))
        fps = int(self.cfg["fps_limit"])
        self.fps_var.set(next((n for n, v in FPS_LIMITS if v == fps), FPS_LIMITS[0][0]))
        an = int(ini["anisotropy"]) if ini.get("anisotropy", "").isdigit() else self.cfg["anisotropy"]
        self.aniso_var.set(next((n for n, v in ANISO if v == an), "Off"))
        for key, var in self.cheat_vars.items():  # the in-game menu writes these too
            var.set(ini.get(key) == "1")
        saved_gpu = self.cfg.get("gpu_index", -1)
        self.gpu_var.set(next((name for idx, name, _ in SYSTEM_GPUS if idx == saved_gpu), SYSTEM_GPUS[0][1]))

    def _audio_toggled(self):
        if not self.audio_var.get():
            self.wav_var.set(False)  # the capture belongs to the log

    def _wav_toggled(self):
        if not self.wav_var.get():
            return
        if messagebox.askyesno("WAV capture", WAV_WARNING, icon="warning", default="no", parent=self):
            self.audio_var.set(True)
        else:
            self.wav_var.set(False)

    def _check_wav_leftovers(self):
        files, total = wav_captures()
        if not files or total < 50 * 2**20:
            return
        if messagebox.askyesno("Audio captures", f"user\\audio_wav holds {format_size(total)} of audio captures "
                               f"({len(files)} files) from an earlier session.\n\nDelete them to free the space?",
                               default="no", parent=self):
            for f in files:
                try:
                    f.unlink()
                except OSError:
                    pass

    def _pad_text(self):
        self.pad_label.config(text="Custom gamepad mapping in use." if MAPPING.exists()
                              else "No custom mapping: XInput, database and known pads work as they are.")

    def _collect(self):
        selected_gpu_name = self.gpu_var.get()
        selected_gpu_idx = next((idx for idx, name, _ in SYSTEM_GPUS if name == selected_gpu_name), -1)
        self.cfg.update(
            game=self.game_var.get().strip(), display_mode=self.mode_var.get(), vsync=self.vsync_var.get(),
            rcas=self.rcas_var.get(), fps_limit=dict(FPS_LIMITS)[self.fps_var.get()],
            resolution=dict(RESOLUTIONS)[self.res_var.get()], upscale=dict(UPSCALERS)[self.up_var.get()],
            anisotropy=dict(ANISO)[self.aniso_var.get()], audio_log=self.audio_var.get(),
            audio_wav=self.audio_var.get() and self.wav_var.get(),
            gpu_index=selected_gpu_idx,
            english_patch=self.english_var.get())
        save_config(self.cfg)
        write_ini(self._ini(), {
            "display_mode": self.cfg["display_mode"], "vsync": int(self.cfg["vsync"]),
            "fps_limit": self.cfg["fps_limit"], "rcas": int(self.cfg["rcas"]),
            "anisotropy": self.cfg["anisotropy"], "render_resolution": self.cfg["resolution"],
            "fsr1": int(self.cfg["upscale"] == "fsr1"),
            "gpu_id": self.cfg.get("gpu_index", -1),
            **{key: int(var.get()) for key, var in self.cheat_vars.items()}})

    def _env(self):
        env = os.environ.copy()
        env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
        env["SPARTAN_GPU_USER_DIR"] = str(USER)
        env["SPARTAN_CONFIG"] = str(self._ini())
        env["SPARTAN_WINDOW_TITLE"] = f"Sengoku Basara 4 Sumeragi - {BRAND} by {AUTHOR}"
        gpu_idx = self.cfg.get("gpu_index", -1)
        if gpu_idx != -1:
            env["SPARTAN_GPU_ID"] = str(gpu_idx)
        else:
            # Auto: If discrete GPU exists, prioritize it automatically
            disc_idx = next((idx for idx, _, is_disc in SYSTEM_GPUS if is_disc and idx >= 0), None)
            if disc_idx is not None:
                env["SPARTAN_GPU_ID"] = str(disc_idx)
            else:
                env.pop("SPARTAN_GPU_ID", None)
        avail_mb = check_commit_limit_mb()
        if avail_mb is not None and avail_mb < 3200:
            env["SPARTAN_DMEM_MB"] = "2048"
        elif avail_mb is not None and avail_mb < 4608:
            env["SPARTAN_DMEM_MB"] = "2560"
        if WINDOW_ICON.exists():
            env["SPARTAN_WINDOW_ICON"] = str(WINDOW_ICON)
        if MAPPING.exists():
            env["SDL_GAMECONTROLLERCONFIG_FILE"] = str(MAPPING)
        if self.cfg.get("audio_log"):  # one timestamped line per audio event, for bug reports
            env["SPARTAN_AUDIO_DEBUG"] = str(AUDIO_LOG)
        if self.cfg.get("audio_log") and self.cfg.get("audio_wav"):  # needs about 70 MB per minute
            env["SPARTAN_AUDIO_WAV_DIR"] = str(AUDIO_WAV)
        if self.cfg["fps_limit"] != 60:  # uncapped: 480 Hz vblank, the present thread paces to the limit
            env["SPARTAN_VBLANK_HZ"] = "0"
            env["SPARTAN_FPS_LIMIT"] = str(self.cfg["fps_limit"])

        # Keyboard & Mouse Control Mapping
        user_keymap = dict(DEFAULT_PC_KEYMAP)
        user_keymap.update(self.cfg.get("keymap", {}))
        # Format spec: button=name,button=name,...
        spec_parts = [f"{btn}={src}" for btn, src in user_keymap.items() if src]
        if spec_parts:
            env["SPARTAN_KEY_MAP"] = ",".join(spec_parts)

        if self.cfg.get("mouse_camera", True):
            env["SPARTAN_MOUSE_LOCK"] = "1"
            env["SPARTAN_MOUSE_AIM_STYLE"] = "0"  # 0: relative / free camera rotation
            sens = int(self.cfg.get("mouse_sens", 100))
            env["SPARTAN_MOUSE_AIM_SENSITIVITY"] = str(sens)

        return env

    # ---- starting the game ----
    def on_play(self):
        if self.busy or self.proc:
            return
        self.user_stopped = False
        self._collect()
        game = Path(self.cfg["game"]) if self.cfg["game"] else None
        exe = CORE
        if not game or not (game / "eboot.bin").is_file():
            self._deiconify()
            messagebox.showerror("Cannot start", "Choose the game folder (the one with eboot.bin).")
            return
        if not exe.is_file():
            self._deiconify()
            messagebox.showerror("Cannot start", f"Runtime not found: {exe}")
            return
        avail_mb = check_commit_limit_mb()
        if avail_mb is not None and avail_mb < 2560:
            if not messagebox.askyesno(
                "Virtual Memory Sangat Rendah",
                f"Sisa alokasi memori virtual Windows Anda hanya ~{avail_mb} MB.\n"
                "Game membutuhkan setidaknya ~3584 MB (3.5 GB) virtual memory.\n\n"
                "Jika game crash dengan Error 1455, pastikan Virtual Memory (Paging File) aktif di Windows.\n\n"
                "Tetap jalankan game?"):
                return
        self.busy = True
        self.play.config(state="disabled")
        self.pending = None
        threading.Thread(target=self._prepare_then_launch, args=(game, exe), daemon=True).start()
        self.after(200, self._wait_prepare)

    def on_import_ps3_patch(self):
        if self.busy or self.proc:
            return
        game = Path(self.cfg["game"]) if self.cfg.get("game") else None
        if not game or not (game / "eboot.bin").is_file():
            messagebox.showerror("Cannot import patch", "Choose the game folder (the one with eboot.bin) first.")
            return

        initial_dir = self.cfg.get("ps3_patch_source") or str(ROOT / "mods")
        src_dir = filedialog.askdirectory(title="Select the PS3 (BLJM61248) English patch folder", initialdir=initial_dir)
        if not src_dir:
            return

        self.cfg["ps3_patch_source"] = src_dir
        save_config(self.cfg)
        self._run_ps3_repatch(Path(src_dir), game)

    def _run_ps3_repatch(self, src_path: Path, game: Path):
        self.import_patch_btn.config(state="disabled")
        self.rebuild_patch_btn.config(state="disabled")
        self.status.config(text="Importing PS3 English patch (GMD + TEX)…")

        def _worker():
            err = None
            res = None
            try:
                def _prog(cur, tot, msg):
                    self.after(0, lambda c=cur, t=tot, m=msg: self.status.config(
                        text=f"Importing PS3 patch… {c}/{t} ({m[:30]})"
                    ))
                res = basara4_repatch.repatch(
                    src_path, game, PATCH_DIR, progress=_prog
                )
                if self.cfg.get("english_patch") or OVERLAY_DIR.exists():
                    basara4_overlay.build_overlay(game, PATCH_DIR, OVERLAY_DIR)
            except Exception as e:
                err = str(e)

            def _done():
                self.import_patch_btn.config(state="normal")
                self.rebuild_patch_btn.config(state="normal")
                if err:
                    self.status.config(text=f"PS3 patch import failed: {err}")
                    messagebox.showerror("PS3 Patch Import Failed", err, parent=self)
                else:
                    self.status.config(text="PS3 English patch imported successfully.")
                    s = res["stats"] if res else {}
                    v = res["verification"] if res else {}
                    summary = (
                        f"PS3 English patch imported successfully!\n\n"
                        f"Verification: {v.get('total_matched', 0)} files matched\n"
                        f"Archives patched: {s.get('arcs_written', 0)}\n"
                        f"GMD strings replaced: {s.get('gmd_strings_replaced', 0)}\n"
                        f"Textures translated: {s.get('textures_translated', 0)}\n"
                        f"Textures kept (native): {s.get('textures_kept', 0)}\n"
                        f"Elapsed: {s.get('elapsed_seconds', 0)}s\n"
                        f"Output location: {PATCH_DIR}"
                    )
                    messagebox.showinfo("PS3 Patch Import Complete", summary, parent=self)

            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def on_rebuild_patch(self):
        if self.busy or self.proc:
            return
        game = Path(self.cfg["game"]) if self.cfg.get("game") else None
        if not game or not (game / "eboot.bin").is_file():
            messagebox.showerror("Cannot build patch", "Choose the game folder (the one with eboot.bin) first.")
            return

        ps3_src = self.cfg.get("ps3_patch_source")
        if ps3_src and Path(ps3_src).exists():
            self._run_ps3_repatch(Path(ps3_src), game)
            return

        if not TRANSLATION_DB.is_file():
            messagebox.showerror(
                "Cannot build patch",
                'No English patch yet: click "Import PS3 English Patch…" and select your PS3 (BLJM61248) English patch folder.',
                parent=self,
            )
            return
        self.rebuild_patch_btn.config(state="disabled")
        self.status.config(text="Building English patch…")

        def _worker():
            err = None
            stats = None
            try:
                def _prog(cur, tot, msg):
                    self.after(0, lambda c=cur, t=tot: self.status.config(text=f"Building English patch… {c + 1}/{t}"))
                stats = basara4_translation.build_patch(
                    game, TRANSLATION_DB, PATCH_DIR, progress=_prog
                )
                if self.cfg.get("english_patch") or OVERLAY_DIR.exists():
                    basara4_overlay.build_overlay(game, PATCH_DIR, OVERLAY_DIR)
            except Exception as e:
                err = str(e)

            def _done():
                self.rebuild_patch_btn.config(state="normal")
                if err:
                    self.status.config(text=f"English patch build failed: {err}")
                    messagebox.showerror("English Patch Build Failed", err, parent=self)
                else:
                    self.status.config(text="English patch built successfully.")
                    s = stats.get("stats", {}) if stats else {}
                    summary = (
                        f"English translation patch built successfully!\n\n"
                        f"Archives patched: {s.get('arcs_written', 0)} / {s.get('arcs_found', 0)}\n"
                        f"GMD files modified: {s.get('gmds_modified', 0)}\n"
                        f"Strings replaced: {s.get('strings_replaced', 0)}\n"
                        f"Output location: {PATCH_DIR}"
                    )
                    messagebox.showinfo("English Patch Complete", summary, parent=self)

            self.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    def _progress(self, text):
        self.pending_text = text

    def _prepare_then_launch(self, game, exe):
        self.pending_text = "Starting…"
        error = None
        if not image_ready(game):
            error = prepare_image(game, self._progress)
        patch_args, notice = [], ""
        if not error and self.cfg["fps_limit"] != 60:
            try:
                writes, _ = basara4_patches.build(OUT / "eboot.elf", OUT / "patches.bin", True)
            except OSError:
                writes = []
            if writes:
                patch_args = ["--patches", str(OUT / "patches.bin")]
            else:
                self.cfg["fps_limit"] = 60
                notice = "Frame limiter not found in this eboot.bin: staying at 60.  "

        app0 = game
        if not error and self.cfg.get("english_patch"):
            manifest_file = PATCH_DIR / "manifest.json"
            is_ps3_import = False
            if manifest_file.is_file():
                try:
                    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    is_ps3_import = (manifest_data.get("mode") == "ps3_import")
                except (OSError, ValueError):
                    pass

            if is_ps3_import:
                try:
                    app0 = basara4_overlay.build_overlay(game, PATCH_DIR, OVERLAY_DIR)
                except Exception as e:
                    error = f"Failed to build English patch: {e}"
            elif TRANSLATION_DB.is_file():
                try:
                    if patch_needs_rebuild(game, TRANSLATION_DB, PATCH_DIR):
                        def _prog(cur, tot, msg):
                            self._progress(f"Building English patch… {cur + 1}/{tot}")
                        basara4_translation.build_patch(
                            game, TRANSLATION_DB, PATCH_DIR, progress=_prog
                        )
                    app0 = basara4_overlay.build_overlay(game, PATCH_DIR, OVERLAY_DIR)
                except Exception as e:
                    error = f"Failed to build English patch: {e}"
            else:
                error = 'No English patch yet: click "Import PS3 English Patch…" and select your PS3 (BLJM61248) English patch folder.'

        self.pending = (error, game, exe, patch_args, notice, app0)

    def _wait_prepare(self):
        if self.pending is None:
            self.status.config(text=getattr(self, "pending_text", "Starting…"))
            self.after(200, self._wait_prepare)
            return
        error, game, exe, patch_args, notice, app0 = self.pending
        self.busy = False
        if error:
            self.play.config(state="normal")
            self._deiconify()
            self.status.config(text=error)
            messagebox.showerror("Cannot prepare the game", error)
            return
        if self.cfg["fps_limit"] == 60:
            self.fps_var.set(FPS_LIMITS[0][0])
        USER.mkdir(parents=True, exist_ok=True)
        self._make_window_icon(game)
        if self.cfg.get("audio_log") and AUDIO_LOG.exists():  # keep the previous run's log
            try:
                os.replace(AUDIO_LOG, AUDIO_LOG.with_name("audio_debug.previous.log"))
            except OSError:
                pass
        if self.cfg.get("audio_log") and self.cfg.get("audio_wav"):  # only this run's captures stay
            AUDIO_WAV.mkdir(parents=True, exist_ok=True)
            for old in AUDIO_WAV.glob("*.wav"):
                try:
                    old.unlink()
                except OSError:
                    pass
        extra_args = []
        isa_table = OUT / "isa-rewrite.bin"
        if isa_table.is_file():
            extra_args = ["--isa-rewrite", str(isa_table)]

        log = open(LOG, "wb")
        self.proc = subprocess.Popen(
            [str(exe), str(OUT / "boot-linked.bin"), "--content-profile", str(OUT / "content.bin"),
             "--app0", str(app0), "--user", str(USER), "--no-game-hooks", "--cheats"] + extra_args + patch_args,
            env=self._env(), cwd=str(USER), stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        if self.cfg.get("audio_wav"):  # a one-session option: the next start has it off again
            self.cfg["audio_wav"] = False
            save_config(self.cfg)
            self.wav_var.set(False)
        self.stop.config(state="normal")
        self.status.config(text=notice + ("Running, writing user\\audio_debug.log…" if self.cfg.get("audio_log")
                                          else "Running…  (Insert opens the in-game menu)"))
        self._refresh_image_label()

    def _make_window_icon(self, game):
        if Image is None or WINDOW_ICON.exists():
            return
        try:
            Image.open(Path(game) / "sce_sys" / "icon0.png").convert("RGBA").resize((128, 128)).save(
                WINDOW_ICON, format="BMP")
        except OSError:
            pass

    def show_credits(self):
        notice = ROOT / "NOTICE.md"
        try:
            text = notice.read_text(encoding="utf-8")
        except OSError:
            text = "Credits are in NOTICE.md."
        win = tk.Toplevel(self)
        win.title(f"{BRAND} - credits")
        win.configure(bg=BG)
        win.geometry("640x460")
        box = tk.Text(win, bg=PANEL, fg=FG, wrap="word", relief="flat", padx=14, pady=12, font=("Segoe UI", 10))
        box.insert("1.0", text)
        box.config(state="disabled")
        box.pack(fill="both", expand=True, padx=10, pady=10)

    # ---- auto update ----
    def _check_update_thread(self):
        info = check_latest_release()
        if info:
            self.update_info = info
            self.after(0, self._show_update_available)

    def _show_update_available(self):
        if not self.update_info or getattr(self, "_update_btn_shown", False):
            return
        self._update_btn_shown = True
        tag = self.update_info["tag"]
        self.version_label.config(text=f"v{VERSION}  (Update: {tag})", fg=GOLD)
        self.update_btn = tk.Button(
            self.mark_frame,
            text=f"Update ke {tag}",
            bg=GOLD,
            fg=BG,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            activebackground="#dbb458",
            activeforeground=BG,
            cursor="hand2",
            command=self.prompt_update,
        )
        self.update_btn.pack(before=self.check_btn, fill="x", pady=(0, 6), ipady=3)

    def check_update_manual(self):
        self.status.config(text="Memeriksa pembaruan...")

        def run():
            info = check_latest_release()
            if info:
                self.update_info = info
                self.after(0, lambda: (
                    self.status.config(text="Ready"),
                    self._show_update_available(),
                    self.prompt_update()
                ))
            else:
                self.after(0, lambda: (
                    self.status.config(text="Ready"),
                    messagebox.showinfo("Update", f"Anda sudah menggunakan versi terbaru (v{VERSION}).")
                ))

        threading.Thread(target=run, daemon=True).start()

    def prompt_update(self):
        if not self.update_info:
            return
        tag = self.update_info["tag"]
        size_mb = self.update_info["size"] / (1024 * 1024)
        if messagebox.askyesno(
            "Update Tersedia",
            f"Versi baru {tag} tersedia di GitHub!\nUkuran unduhan: ~{size_mb:.1f} MB.\n\n"
            "Launcher akan mengunduh dan menerapkan update secara otomatis.\n"
            "Data game (folder game\\) dan save data Anda (folder user\\) TIDAK akan terhapus.\n\n"
            "Unduh dan pasang update sekarang?"
        ):
            self._download_and_apply_update(self.update_info)

    def _download_and_apply_update(self, info):
        win = tk.Toplevel(self)
        win.title("Basara 4 PC - Updater")
        win.configure(bg=BG)
        win.geometry("440x180")
        win.resizable(False, False)
        win.grab_set()

        tk.Label(win, text=f"Mengunduh Pembaruan {info['tag']}...", bg=BG, fg=GOLD, font=("Segoe UI", 11, "bold")).pack(pady=(18, 8))
        status_lbl = tk.Label(win, text="Menghubungkan ke GitHub...", bg=BG, fg=FG, font=("Segoe UI", 9))
        status_lbl.pack(pady=(0, 8))

        prog = ttk.Progressbar(win, orient="horizontal", length=360, mode="determinate")
        prog.pack(pady=6)

        def worker():
            updates_dir = USER / "updates"
            updates_dir.mkdir(parents=True, exist_ok=True)
            zip_path = updates_dir / "update.zip"

            try:
                req = urllib.request.Request(info["url"], headers={"User-Agent": "Basara4-PC-Launcher"})
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                    total = int(resp.headers.get("Content-Length", info["size"] or 1))
                    downloaded = 0
                    chunk_size = 64 * 1024
                    with open(zip_path, "wb") as f:
                        while True:
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            percent = int((downloaded / total) * 100) if total else 0
                            cur_mb = downloaded / (1024 * 1024)
                            tot_mb = total / (1024 * 1024)
                            win.after(0, lambda p=percent, c=cur_mb, t=tot_mb: (
                                prog.configure(value=p),
                                status_lbl.configure(text=f"Mengunduh: {c:.1f} MB / {t:.1f} MB ({p}%)")
                            ))

                win.after(0, lambda: self._apply_update_and_restart(win))
            except Exception as e:
                win.after(0, lambda err=e: (
                    win.destroy(),
                    messagebox.showerror("Gagal Mengunduh", f"Pembaruan gagal diunduh:\n{err}")
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_and_restart(self, win):
        win.destroy()
        updater_cmd = USER / "updater.cmd"
        cmd_content = (
            "@echo off\r\n"
            "setlocal enabledelayedexpansion\r\n"
            "title Basara 4 PC - Updater\r\n"
            "cd /d \"%~dp0\"\r\n"
            "echo Menunggu launcher menutup...\r\n"
            "timeout /t 2 /nobreak >nul\r\n"
            "set \"ZIP=%~dp0updates\\update.zip\"\r\n"
            "set \"EXTRACT=%~dp0updates\\extract\"\r\n"
            "set \"DEST=%~dp0..\"\r\n"
            "if not exist \"!ZIP!\" exit /b 1\r\n"
            "echo Mengekstrak pembaruan...\r\n"
            "if exist \"!EXTRACT!\" rmdir /s /q \"!EXTRACT!\"\r\n"
            "mkdir \"!EXTRACT!\"\r\n"
            "tar -xf \"!ZIP!\" -C \"!EXTRACT!\" 2>nul\r\n"
            "if errorlevel 1 (\r\n"
            "    powershell.exe -NoProfile -Command \"Expand-Archive -Path '!ZIP!' -DestinationPath '!EXTRACT!' -Force\"\r\n"
            ")\r\n"
            "set \"SRC=!EXTRACT!\"\r\n"
            "if not exist \"!EXTRACT!\\Sengoku Basara 4.exe\" (\r\n"
            "    for /d %%D in (\"!EXTRACT!\\*\") do (\r\n"
            "        if exist \"%%~fD\\Sengoku Basara 4.exe\" set \"SRC=%%~fD\"\r\n"
            "    )\r\n"
            ")\r\n"
            "echo Menerapkan file baru...\r\n"
            "if exist \"!SRC!\\_internal\" xcopy /e /y /i \"!SRC!\\_internal\" \"!DEST!\\_internal\" >nul\r\n"
            "if exist \"!SRC!\\bin\" xcopy /e /y /i \"!SRC!\\bin\" \"!DEST!\\bin\" >nul\r\n"
            "if exist \"!SRC!\\scripts\" xcopy /e /y /i \"!SRC!\\scripts\" \"!DEST!\\scripts\" >nul\r\n"
            "if exist \"!SRC!\\launcher\" xcopy /e /y /i \"!SRC!\\launcher\" \"!DEST!\\launcher\" >nul\r\n"
            "if exist \"!SRC!\\Sengoku Basara 4.exe\" copy /y \"!SRC!\\Sengoku Basara 4.exe\" \"!DEST!\\\" >nul\r\n"
            "if exist \"!SRC!\\Play Sengoku Basara 4.cmd\" copy /y \"!SRC!\\Play Sengoku Basara 4.cmd\" \"!DEST!\\\" >nul\r\n"
            "if exist \"!SRC!\\NOTICE.md\" copy /y \"!SRC!\\NOTICE.md\" \"!DEST!\\\" >nul\r\n"
            "if exist \"!SRC!\\LICENSE\" copy /y \"!SRC!\\LICENSE\" \"!DEST!\\\" >nul\r\n"
            "rmdir /s /q \"!EXTRACT!\" 2>nul\r\n"
            "del /f /q \"!ZIP!\" 2>nul\r\n"
            "echo Memulai versi baru...\r\n"
            "start \"\" \"!DEST!\\Sengoku Basara 4.exe\"\r\n"
            "del /f /q \"%~f0\" 2>nul\r\n"
            "exit /b 0\r\n"
        )
        updater_cmd.write_text(cmd_content, encoding="ascii")
        subprocess.Popen(["cmd.exe", "/c", str(updater_cmd)], creationflags=NO_WINDOW)
        self.destroy()

    def _deiconify(self):
        self.deiconify()

    def on_stop(self):
        if self.proc and self.proc.poll() is None:
            self.user_stopped = True
            self.proc.kill()

    def _show_crash_dialog(self, code):
        data = extract_crash_summary(LOG)
        win = tk.Toplevel(self)
        win.title("Basara 4 PC — Crash Diagnostic")
        win.configure(bg=BG)
        win.geometry("740x560")
        win.minsize(680, 480)
        win.grab_set()

        # Bottom Button Bar dipack DULUAN dengan side="bottom" agar TIDAK TERDORONG KELUAR LAYAR
        btn_bar = ttk.Frame(win, style="TFrame", padding=12)
        btn_bar.pack(side="bottom", fill="x")

        # Top Header Frame
        header_frame = ttk.Frame(win, style="Panel.TFrame", padding=14)
        header_frame.pack(side="top", fill="x", padx=12, pady=(12, 6))

        exit_hex = f"{code & 0xFFFFFFFF:#x}" if isinstance(code, int) else "Unknown"
        tk.Label(
            header_frame,
            text=f"⚠️ Game Berhenti Mendadak (Exit Code {exit_hex})",
            bg=PANEL,
            fg=RED,
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")

        tk.Label(
            header_frame,
            text="Terdeteksi indikasi error dari log permainan di bawah ini:",
            bg=PANEL,
            fg=FG,
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(4, 6))

        for err in data["detected_errors"]:
            tk.Label(
                header_frame,
                text=f"• {err}",
                bg=PANEL,
                fg=GOLD,
                font=("Consolas", 9, "bold"),
                wraplength=680,
                justify="left"
            ).pack(anchor="w", padx=6)

        # Center Body Frame
        body_frame = ttk.Frame(win, style="TFrame")
        body_frame.pack(side="top", fill="both", expand=True, padx=12, pady=6)

        ttk.Label(body_frame, text="Cuplikan 10 Baris Log Terakhir (Raw Context):", style="Panel.TLabel").pack(anchor="w", pady=(0, 4))
        txt = tk.Text(body_frame, bg="#0d0b0a", fg="#d6cfc7", relief="flat", wrap="none", font=("Consolas", 9), padx=8, pady=8)
        txt.insert("1.0", data["tail_context"])
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)

        def copy_details():
            try:
                import platform
                import ctypes
                from ctypes import wintypes
                k = ctypes.windll.kernel32
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64),
                    ]
                mem = MEMORYSTATUSEX()
                mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                k.GlobalMemoryStatusEx(ctypes.byref(mem))
                ram_total = f"{mem.ullTotalPhys // (1024*1024)} MB"
                ram_avail = f"{mem.ullAvailPhys // (1024*1024)} MB"
                commit_avail = f"{mem.ullAvailPageFile // (1024*1024)} MB"
            except Exception:
                ram_total = ram_avail = commit_avail = "N/A"

            gpu_list = "\n".join(f"  - {name}" for _, name, _ in SYSTEM_GPUS) if SYSTEM_GPUS else "  - None detected"

            audio_tail = ""
            if AUDIO_LOG.is_file():
                try:
                    a_lines = AUDIO_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
                    if a_lines:
                        audio_tail = "\n".join(a_lines[-6:])
                except Exception:
                    pass

            clip_text = (
                f"=== Sengoku Basara 4 PC — Crash Diagnostic Report ===\n"
                f"Version       : v{VERSION}\n"
                f"Exit Code     : {exit_hex}\n"
                f"OS            : {platform.system()} {platform.release()} (Build {platform.version()})\n"
                f"Architecture  : {platform.machine()} ({platform.processor()})\n"
                f"RAM           : Total {ram_total} | Free {ram_avail} | Available Commit {commit_avail}\n"
                f"Vulkan GPUs   :\n{gpu_list}\n"
                f"Detected Fatal Signatures:\n" + "\n".join(f"  * {e}" for e in data["detected_errors"]) + "\n\n"
                f"--- [Last 10 Log Lines (last_run.log)] ---\n"
                f"{data['tail_context']}\n"
            )
            if audio_tail:
                clip_text += f"\n--- [Recent Audio Log (audio_debug.log)] ---\n{audio_tail}\n"

            clip_text += "\n=== End of Report ==="
            self.clipboard_clear()
            self.clipboard_append(clip_text)
            messagebox.showinfo(
                "Tersalin ke Clipboard",
                "Laporan crash lengkap (spek hardware, GPU, log error, dan audio log) berhasil disalin!\n"
                "Anda tinggal Paste (Ctrl+V) langsung ke laporan GitHub untuk bantuan teknis.",
                parent=win
            )

        def open_issues():
            import webbrowser
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/issues")

        ttk.Button(btn_bar, text="📋 Copy Error ke Clipboard", command=copy_details).pack(side="left", padx=4)
        ttk.Button(btn_bar, text="📁 Buka File Log", command=self.open_log).pack(side="left", padx=4)
        ttk.Button(btn_bar, text="Report on GitHub", command=open_issues).pack(side="left", padx=4)
        ttk.Button(btn_bar, text="Tutup", command=win.destroy).pack(side="right", padx=4)

    # ---- keyboard & mouse mapping dialog ----
    def open_keymap_ui(self):
        win = tk.Toplevel(self)
        win.title("Keyboard & Mouse Controls — Custom Keymap")
        win.configure(bg=BG)
        win.geometry("740x620")
        win.minsize(680, 520)
        win.grab_set()

        cur_map = dict(DEFAULT_PC_KEYMAP)
        cur_map.update(self.cfg.get("keymap", {}))
        mouse_cam_var = tk.BooleanVar(value=self.cfg.get("mouse_camera", True))
        mouse_sens_var = tk.IntVar(value=self.cfg.get("mouse_sens", 100))

        # Top Header
        top_frame = ttk.Frame(win, style="Panel.TFrame", padding=12)
        top_frame.pack(fill="x", padx=14, pady=(12, 6))

        tk.Label(top_frame, text="⚙️ Pengaturan Kontrol Keyboard & Mouse", bg=PANEL, fg=GOLD, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(top_frame, text="Klik tombol untuk mengubah tombol pemetaan. Tekan tombol Keyboard atau Mouse untuk mengikat.", bg=PANEL, fg=FG, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

        # Mouse Settings Bar
        mouse_bar = ttk.Frame(top_frame, style="Panel.TFrame")
        mouse_bar.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(mouse_bar, text="Aktifkan Kamera Mouse (Mouse Look)", variable=mouse_cam_var).pack(side="left")

        sens_frame = ttk.Frame(mouse_bar, style="Panel.TFrame")
        sens_frame.pack(side="right")
        ttk.Label(sens_frame, text="Sensitivitas: ", style="Panel.TLabel").pack(side="left")
        sens_lbl = ttk.Label(sens_frame, text=f"{mouse_sens_var.get()}%", style="Panel.TLabel", width=5)
        sens_lbl.pack(side="right")
        def on_sens_scroll(val):
            sens_lbl.config(text=f"{int(float(val))}%")
        scale = ttk.Scale(sens_frame, from_=20, to=300, variable=mouse_sens_var, command=on_sens_scroll, length=120)
        scale.pack(side="left", padx=6)

        # Body - Scrollable Mapping Table
        body_container = ttk.Frame(win, style="TFrame")
        body_container.pack(fill="both", expand=True, padx=14, pady=6)

        canvas = tk.Canvas(body_container, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body_container, orient="vertical", command=canvas.yview)
        scroll_content = ttk.Frame(canvas, style="TFrame")

        scroll_content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_content, anchor="nw")
        def on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        btn_widgets = {}
        active_listening = [None]  # holds current button listening

        def format_binding_display(key_str):
            if not key_str:
                return "None"
            return key_str.upper() if len(key_str) <= 3 else key_str

        def finish_listen(action_name, new_key):
            if active_listening[0] == action_name:
                active_listening[0] = None
                cur_map[action_name] = new_key
                btn = btn_widgets.get(action_name)
                if btn:
                    btn.config(text=format_binding_display(new_key), bg="#2c2824", fg=GOLD)
                win.unbind("<Key>")
                win.unbind("<Button-1>")
                win.unbind("<Button-2>")
                win.unbind("<Button-3>")

        def start_listen(action_name):
            # If another was listening, reset it
            if active_listening[0]:
                old_action = active_listening[0]
                old_btn = btn_widgets.get(old_action)
                if old_btn:
                    old_btn.config(text=format_binding_display(cur_map.get(old_action, "")), bg="#2c2824", fg=GOLD)
            active_listening[0] = action_name
            btn = btn_widgets.get(action_name)
            if btn:
                btn.config(text="[ TEKAN TOMBOL... ]", bg=RED, fg="#fff")

            def on_key_event(event):
                # Translate tkinter keysym to SDL scancode name
                keysym = event.keysym
                # Special mappings
                key_map_table = {
                    "Return": "Return", "Escape": "Escape", "space": "Space",
                    "BackSpace": "Backspace", "Tab": "Tab", "Shift_L": "Left Shift",
                    "Shift_R": "Right Shift", "Control_L": "Left Ctrl", "Control_R": "Right Ctrl",
                    "Alt_L": "Left Alt", "Alt_R": "Right Alt", "Caps_Lock": "CapsLock",
                    "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
                    "comma": "Comma", "equal": "Equals", "period": "Period",
                    "slash": "Slash", "semicolon": "Semicolon", "minus": "Minus"
                }
                mapped_name = key_map_table.get(keysym, keysym.capitalize() if len(keysym) > 1 else keysym.upper())
                finish_listen(action_name, mapped_name)
                return "break"

            def on_mouse_event(btn_num):
                name_map = {1: "Mouse Left", 2: "Mouse Middle", 3: "Mouse Right"}
                finish_listen(action_name, name_map.get(btn_num, f"Mouse {btn_num}"))
                return "break"

            win.bind("<Key>", on_key_event)
            # Give focus to window to capture keys
            win.focus_set()
            # Allow clicking mouse buttons to bind
            win.bind("<Button-1>", lambda e: on_mouse_event(1))
            win.bind("<Button-2>", lambda e: on_mouse_event(2))
            win.bind("<Button-3>", lambda e: on_mouse_event(3))

        # Render Table Items (Styled like game interaction list)
        for row_idx, (action_key, action_label, ps_button_label) in enumerate(ACTION_DESCRIPTIONS):
            item_frame = tk.Frame(scroll_content, bg="#181512", padx=12, pady=7, highlightbackground="#25211d", highlightthickness=1)
            item_frame.pack(fill="x", pady=2, padx=4)

            # Left Label: Action name
            lbl_text = f"{action_label}"
            tk.Label(item_frame, text=lbl_text, bg="#181512", fg=FG, font=("Segoe UI Semibold", 9), anchor="w").pack(side="left", padx=(4, 0))

            # Sub-label for PS button
            tk.Label(item_frame, text=f"({ps_button_label})", bg="#181512", fg=MUTED, font=("Segoe UI", 8), anchor="w").pack(side="left", padx=8)

            # Right: Key Box Button
            val = cur_map.get(action_key, "")
            key_btn = tk.Button(
                item_frame,
                text=format_binding_display(val),
                bg="#2c2824",
                fg=GOLD,
                activebackground=RED,
                activeforeground="#fff",
                font=("Consolas", 10, "bold"),
                width=16,
                relief="flat",
                bd=0,
                padx=8,
                pady=4,
                cursor="hand2",
                command=lambda a=action_key: start_listen(a)
            )
            key_btn.pack(side="right")
            btn_widgets[action_key] = key_btn

        # Movement Fixed Notice
        notice_frame = tk.Frame(scroll_content, bg=PANEL, padx=12, pady=6)
        notice_frame.pack(fill="x", pady=(8, 4), padx=4)
        tk.Label(
            notice_frame,
            text="ℹ️ Pergerakan Karakter otomatis terikat pada W, A, S, D (Standar PC). Kamera mouse mengontrol analog kanan.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8, "italic")
        ).pack(anchor="w")

        # Bottom Action Bar
        bottom_bar = ttk.Frame(win, style="TFrame", padding=12)
        bottom_bar.pack(fill="x")

        def reset_defaults():
            cur_map.clear()
            cur_map.update(DEFAULT_PC_KEYMAP)
            mouse_cam_var.set(True)
            mouse_sens_var.set(100)
            sens_lbl.config(text="100%")
            for k, b in btn_widgets.items():
                b.config(text=format_binding_display(cur_map.get(k, "")), bg="#2c2824", fg=GOLD)
            messagebox.showinfo("Reset Preset", "Pengaturan kontrol berhasil dikembalikan ke Preset Default PC!", parent=win)

        def save_and_close():
            self.cfg["keymap"] = cur_map
            self.cfg["mouse_camera"] = mouse_cam_var.get()
            self.cfg["mouse_sens"] = int(mouse_sens_var.get())
            save_config(self.cfg)
            win.destroy()
            messagebox.showinfo("Tersimpan", "Pengaturan kontrol Keyboard & Mouse berhasil disimpan!", parent=self)

        ttk.Button(bottom_bar, text="🔄 Reset ke Default PC", command=reset_defaults).pack(side="left", padx=4)
        ttk.Button(bottom_bar, text="Batal", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(bottom_bar, text="💾 Simpan Pengaturan", style="Play.TButton", command=save_and_close).pack(side="right", padx=4)

    def _check_exit_error(self, code):
        if code in (0, None) or not LOG.is_file():
            return None
        self._show_crash_dialog(code)
        return f"Game crash (exit code {code & 0xFFFFFFFF:#x}). Silakan cek jendela diagnostik."

    def _poll(self):
        if self.proc and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self.play.config(state="normal")
            self.stop.config(state="disabled")
            if code not in (0, None) and not self.user_stopped:
                self._deiconify()
                self._show_crash_dialog(code)
            if self.play_now:
                if code in (0, None) or self.user_stopped:
                    self.destroy()
                return
            err_msg = f"Game exited with code {code & 0xFFFFFFFF:#x}. See the log." if (code not in (0, None) and not self.user_stopped) else None
            self.status.config(text=err_msg if err_msg else "Game closed.")
            self._load_ini_into_ui()
        self.after(500, self._poll)

    # ---- controller and files ----
    def map_pad(self, extra):
        exe = BIN / "basara4-pad-mapper.exe"
        if not exe.is_file():
            messagebox.showerror("Mapper missing", str(exe))
            return
        env = os.environ.copy()
        env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
        USER.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([str(exe), str(MAPPING)] + extra, env=env, creationflags=0x00000010)  # new console
        self.status.config(text="Follow the prompts in the mapper window, then press PLAY.")
        self.after(4000, self._pad_text)

    def reset_map(self):
        if MAPPING.exists() and messagebox.askyesno("Reset mapping", "Delete the saved controller mapping?"):
            os.replace(MAPPING, str(MAPPING) + ".bak")
            self._pad_text()

    def open_log(self):
        if LOG.exists():
            os.startfile(LOG)
        else:
            messagebox.showinfo("Log", "No log yet. Start the game first.")

    def open_audio_log(self):
        if AUDIO_LOG.exists():
            os.startfile(AUDIO_LOG)
        else:
            messagebox.showinfo("Audio log", "No audio log yet. Tick \"Audio debug log\", then start the game.")

    def open_saves(self):
        path = USER / "savedata"
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Game running", "Stop the game and close the launcher?"):
                return
            self.proc.kill()
        try:
            self._collect()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    Launcher(play_now="--play" in sys.argv).mainloop()
