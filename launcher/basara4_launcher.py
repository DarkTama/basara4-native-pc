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
import runpy
import subprocess
import sys
import threading
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
BIN, OUT, USER, SCRIPTS = ROOT / "bin", ROOT / "out", ROOT / "user", ROOT / "scripts"
CONFIG = USER / "launcher.json"
INI = USER / "basara4.ini"
OLD_INI = USER / "spartanport.ini"
WINDOW_ICON = USER / "window_icon.bmp"
CORE = BIN / "basara4-core.exe"
VERSION = "1.0"
BRAND = "Basara 4 PC"
AUTHOR = "Razz"
LOG = USER / "last_run.log"
PREPARE_LOG = USER / "prepare.log"
MAPPING = USER / "gamepad_mapping.txt"
AUDIO_LOG = USER / "audio_debug.log"
STAMP = OUT / "prepared.json"
PIPELINE = [("prepare.py", []), ("link_libc.py", []), ("link_modules.py", []),
            ("content_profile.py", ["--sku", "full"])]
NO_WINDOW = 0x08000000


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
from tkinter import filedialog, messagebox, ttk  # noqa: E402

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageTk
except ImportError:  # still works, just without artwork
    Image = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import basara4_patches  # noqa: E402

DEFAULTS = {"game": "", "display_mode": "borderless", "vsync": True, "fps_limit": 60, "rcas": False,
            "anisotropy": 0, "resolution": "native", "upscale": "off", "audio_log": False}
DISPLAY_MODES = [("Windowed", "windowed"), ("Borderless fullscreen", "borderless"),
                 ("Exclusive fullscreen", "fullscreen")]
FPS_LIMITS = [("60 (game default)", 60), ("90", 90), ("120", 120), ("144", 144), ("165", 165),
              ("240", 240), ("Unlimited", 0)]
RESOLUTIONS = [("1920 x 1080  (native)", "native"), ("1280 x 720", "720p"), ("1600 x 900", "900p"),
               ("2560 x 1440", "1440p"), ("3200 x 1800", "1800p"), ("3840 x 2160  (4K)", "4K")]
UPSCALERS = [("Off  (plain scaling)", "off"), ("FSR 1  (sharper)", "fsr1")]
ANISO = [("Off", 0), ("2x", 2), ("4x", 4), ("8x", 8), ("16x", 16)]
BG, PANEL, FG, MUTED, GOLD, RED = "#12100e", "#1c1916", "#eadfcb", "#8d8374", "#c9a24a", "#9c2b24"
RAZZ = "#e0526a"  # the accent of the by-Razz mark


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
        self.pending = None
        self.play_now = play_now
        self.title(f"Sengoku Basara 4 Sumeragi  \u00b7  {BRAND} by {AUTHOR}")
        self.configure(bg=BG)
        self.geometry("980x560")
        self.minsize(900, 520)
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
        if play_now:
            self.iconify()
            self.after(300, self.on_play)

    # ---- look ----
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL,
                    lightcolor=PANEL, darkcolor=PANEL, font=("Segoe UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Panel.TLabel", background=PANEL, foreground=FG)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Head.TLabel", background=PANEL, foreground=GOLD, font=("Segoe UI Semibold", 11))
        s.configure("TRadiobutton", background=PANEL, foreground=FG)
        s.configure("TCheckbutton", background=PANEL, foreground=FG)
        s.map("TRadiobutton", background=[("active", PANEL)], foreground=[("active", GOLD)])
        s.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", GOLD)])
        s.configure("TButton", background="#2a2622", foreground=FG, padding=(12, 6), borderwidth=0)
        s.map("TButton", background=[("active", "#3a342e"), ("disabled", "#1c1916")],
              foreground=[("disabled", MUTED)])
        s.configure("Play.TButton", background=RED, foreground="#fff", font=("Segoe UI Semibold", 15),
                    padding=(10, 12))
        s.map("Play.TButton", background=[("active", "#b8362d"), ("disabled", "#4a2522")])
        s.configure("TCombobox", arrowcolor=GOLD, selectbackground=PANEL, selectforeground=FG)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", FG)

    def _art(self):
        self.cover = self.bg_img = None
        if Image is None or not self.cfg["game"]:
            return
        sys_dir = Path(self.cfg["game"]) / "sce_sys"
        try:
            self.cover = ImageTk.PhotoImage(Image.open(sys_dir / "icon0.png").convert("RGB").resize((220, 220)))
        except OSError:
            pass
        try:
            bg = Image.open(sys_dir / "pic1.png").convert("RGB").resize((980, 560))
            bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(6))).enhance(0.25)
            self.bg_img = ImageTk.PhotoImage(bg)
        except OSError:
            pass

    # ---- layout ----
    def _build(self):
        if self.bg_img:
            tk.Label(self, image=self.bg_img, bd=0).place(x=0, y=0, relwidth=1, relheight=1)
        root = ttk.Frame(self, style="TFrame")
        root.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.94, relheight=0.92)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root, style="Panel.TFrame", padding=16)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        if self.cover:
            tk.Label(left, image=self.cover, bd=0, bg=PANEL).pack()
        ttk.Label(left, text="戦国BASARA4 皇", style="Head.TLabel").pack(pady=(12, 0))
        ttk.Label(left, text="Sengoku Basara 4 Sumeragi", style="Panel.TLabel").pack()
        ttk.Label(left, text="CUSA01159", style="Muted.TLabel").pack()
        self.status = ttk.Label(left, text="Ready", style="Muted.TLabel", wraplength=220)
        self.status.pack(pady=(14, 4))
        self.play = ttk.Button(left, text="PLAY", style="Play.TButton", command=self.on_play)
        self.play.pack(fill="x", pady=(2, 6))
        self.stop = ttk.Button(left, text="Stop game", command=self.on_stop, state="disabled")
        self.stop.pack(fill="x")
        mark = ttk.Frame(left, style="Panel.TFrame")
        mark.pack(side="bottom", pady=(18, 0))
        tk.Label(mark, text=BRAND.upper(), bg=PANEL, fg=GOLD, font=("Georgia", 11, "bold")).pack()
        tk.Label(mark, text=f"by {AUTHOR}", bg=PANEL, fg=RAZZ, font=("Georgia", 12, "italic")).pack()
        tk.Label(mark, text=f"v{VERSION}", bg=PANEL, fg=MUTED, font=("Segoe UI", 8)).pack()
        ttk.Button(left, text="Credits", command=self.show_credits).pack(side="bottom", fill="x", pady=(14, 0))

        right = ttk.Frame(root, style="Panel.TFrame", padding=18)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(right, text="Game", style="Head.TLabel").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        self.game_var = tk.StringVar(value=self.cfg["game"])
        self.game_var.trace_add("write", lambda *_: self._refresh_image_label())
        ttk.Label(right, text="Game folder", style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=4)
        ttk.Entry(right, textvariable=self.game_var).grid(row=r, column=1, sticky="ew", padx=8)
        ttk.Button(right, text="Browse…", command=self._browse).grid(row=r, column=2)
        r += 1
        self.image_label = ttk.Label(right, text="", style="Muted.TLabel")
        self.image_label.grid(row=r, column=1, sticky="w", padx=8)
        r += 1

        ttk.Separator(right).grid(row=r, column=0, columnspan=3, sticky="ew", pady=12)
        r += 1
        ttk.Label(right, text="Display", style="Head.TLabel").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        self.mode_var = tk.StringVar(value=self.cfg["display_mode"])
        modes = ttk.Frame(right, style="Panel.TFrame")
        modes.grid(row=r, column=0, columnspan=3, sticky="w", pady=4)
        for label, key in DISPLAY_MODES:
            ttk.Radiobutton(modes, text=label, value=key, variable=self.mode_var).pack(side="left", padx=(0, 18))
        r += 1

        opts = ttk.Frame(right, style="Panel.TFrame")
        opts.grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        ttk.Label(opts, text="Window size", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.res_var = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.res_var, state="readonly", width=20,
                     values=[n for n, _ in RESOLUTIONS]).grid(row=3, column=1, padx=(8, 24), pady=(10, 0))
        ttk.Label(opts, text="Upscaling", style="Panel.TLabel").grid(row=3, column=2, sticky="w", pady=(10, 0))
        self.up_var = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.up_var, state="readonly", width=18,
                     values=[n for n, _ in UPSCALERS]).grid(row=3, column=3, padx=8, pady=(10, 0))
        ttk.Label(opts, text="The game draws at 1080p. Window size sets the window in windowed mode; fullscreen "
                  "uses your screen. FSR 1 sharpens the picture when the window or screen is bigger than 1080p.",
                  style="Muted.TLabel", wraplength=560).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Label(opts, text="Frame limit", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.fps_var = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.fps_var, state="readonly", width=15,
                     values=[n for n, _ in FPS_LIMITS]).grid(row=0, column=1, padx=(8, 24))
        ttk.Label(opts, text="Anisotropic", style="Panel.TLabel").grid(row=0, column=2, sticky="w")
        self.aniso_var = tk.StringVar()
        ttk.Combobox(opts, textvariable=self.aniso_var, state="readonly", width=8,
                     values=[n for n, _ in ANISO]).grid(row=0, column=3, padx=8)
        self.vsync_var = tk.BooleanVar(value=self.cfg["vsync"])
        self.rcas_var = tk.BooleanVar(value=self.cfg["rcas"])
        self.audio_var = tk.BooleanVar(value=self.cfg.get("audio_log", False))
        ttk.Checkbutton(opts, text="VSync", variable=self.vsync_var).grid(row=1, column=0, sticky="w", pady=8)
        ttk.Checkbutton(opts, text="Sharpening (RCAS)", variable=self.rcas_var).grid(
            row=1, column=1, columnspan=2, sticky="w", pady=8)
        ttk.Checkbutton(opts, text="Audio debug log", variable=self.audio_var).grid(
            row=1, column=3, sticky="w", pady=8)
        ttk.Label(opts, text="Above 60 turns off the game's own frame limiter (experimental). "
                  "VSync caps the rate at your monitor's refresh.",
                  style="Muted.TLabel", wraplength=560).grid(row=2, column=0, columnspan=4, sticky="w")
        r += 1
        ttk.Label(right, text="In-game menu: Insert, or R3 + L2 on the pad (it changes these too).",
                  style="Muted.TLabel").grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1

        ttk.Separator(right).grid(row=r, column=0, columnspan=3, sticky="ew", pady=12)
        r += 1
        ttk.Label(right, text="Controller & files", style="Head.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w")
        r += 1
        tools = ttk.Frame(right, style="Panel.TFrame")
        tools.grid(row=r, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Button(tools, text="Map controller", command=lambda: self.map_pad([])).pack(side="left", padx=(0, 8))
        ttk.Button(tools, text="Map sticks only", command=lambda: self.map_pad(["--sticks"])).pack(
            side="left", padx=(0, 8))
        ttk.Button(tools, text="Reset mapping", command=self.reset_map).pack(side="left", padx=(0, 8))
        ttk.Button(tools, text="Open log", command=self.open_log).pack(side="left", padx=(0, 8))
        ttk.Button(tools, text="Open saves", command=self.open_saves).pack(side="left", padx=(0, 8))
        ttk.Button(tools, text="Open audio log", command=self.open_audio_log).pack(side="left")
        r += 1
        self.pad_label = ttk.Label(right, text="", style="Muted.TLabel")
        self.pad_label.grid(row=r, column=0, columnspan=3, sticky="w")
        self._pad_text()

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
        self.image_label.config(text=text)

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

    def _pad_text(self):
        self.pad_label.config(text="Custom gamepad mapping in use." if MAPPING.exists()
                              else "No custom mapping: XInput and known pads work as they are.")

    def _collect(self):
        self.cfg.update(
            game=self.game_var.get().strip(), display_mode=self.mode_var.get(), vsync=self.vsync_var.get(),
            rcas=self.rcas_var.get(), fps_limit=dict(FPS_LIMITS)[self.fps_var.get()],
            resolution=dict(RESOLUTIONS)[self.res_var.get()], upscale=dict(UPSCALERS)[self.up_var.get()],
            anisotropy=dict(ANISO)[self.aniso_var.get()], audio_log=self.audio_var.get())
        save_config(self.cfg)
        write_ini(self._ini(), {
            "display_mode": self.cfg["display_mode"], "vsync": int(self.cfg["vsync"]),
            "fps_limit": self.cfg["fps_limit"], "rcas": int(self.cfg["rcas"]),
            "anisotropy": self.cfg["anisotropy"], "render_resolution": self.cfg["resolution"],
            "fsr1": int(self.cfg["upscale"] == "fsr1")})

    def _env(self):
        env = os.environ.copy()
        env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
        env["SPARTAN_GPU_USER_DIR"] = str(USER)
        env["SPARTAN_CONFIG"] = str(self._ini())
        env["SPARTAN_WINDOW_TITLE"] = f"Sengoku Basara 4 Sumeragi - {BRAND} by {AUTHOR}"
        if WINDOW_ICON.exists():
            env["SPARTAN_WINDOW_ICON"] = str(WINDOW_ICON)
        if MAPPING.exists():
            env["SDL_GAMECONTROLLERCONFIG_FILE"] = str(MAPPING)
        if self.cfg.get("audio_log"):  # one timestamped line per audio event, for bug reports
            env["SPARTAN_AUDIO_DEBUG"] = str(AUDIO_LOG)
        if self.cfg["fps_limit"] != 60:  # uncapped: 480 Hz vblank, the present thread paces to the limit
            env["SPARTAN_VBLANK_HZ"] = "0"
            env["SPARTAN_FPS_LIMIT"] = str(self.cfg["fps_limit"])
        return env

    # ---- starting the game ----
    def on_play(self):
        if self.busy or self.proc:
            return
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
        self.busy = True
        self.play.config(state="disabled")
        self.pending = None
        threading.Thread(target=self._prepare_then_launch, args=(game, exe), daemon=True).start()
        self.after(200, self._wait_prepare)

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
        self.pending = (error, game, exe, patch_args, notice)

    def _wait_prepare(self):
        if self.pending is None:
            self.status.config(text=getattr(self, "pending_text", "Starting…"))
            self.after(200, self._wait_prepare)
            return
        error, game, exe, patch_args, notice = self.pending
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
        log = open(LOG, "wb")
        self.proc = subprocess.Popen(
            [str(exe), str(OUT / "boot-linked.bin"), "--content-profile", str(OUT / "content.bin"),
             "--app0", str(game), "--user", str(USER), "--no-game-hooks"] + patch_args,
            env=self._env(), cwd=str(USER), stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
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

    def _deiconify(self):
        self.deiconify()

    def on_stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def _poll(self):
        if self.proc and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self.play.config(state="normal")
            self.stop.config(state="disabled")
            if self.play_now:
                self.destroy()
                return
            self.status.config(text="Game closed." if code in (0, None) else
                               f"Game exited with code {code & 0xFFFFFFFF:#x}. See the log.")
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
    Launcher(play_now="--play" in sys.argv).mainloop()
