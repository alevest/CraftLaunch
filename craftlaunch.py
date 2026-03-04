#!/usr/bin/env python3
"""
CraftLaunch v3 — Standalone Minecraft Launcher
Uses minecraft-launcher-lib for reliable download & launch.

Requirements:
    pip install minecraft-launcher-lib

Run:
    python craftlaunch.py
"""

# ── auto-install dependencies ──────────────────────────────────────────────
import sys, subprocess

def _ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
    except ImportError:
        print(f"[CraftLaunch] Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

_ensure("minecraft-launcher-lib", "minecraft_launcher_lib")

# ── stdlib ─────────────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os, json, shutil, threading, platform, uuid, zipfile, re, traceback
from pathlib import Path
from datetime import datetime

# ── minecraft-launcher-lib ─────────────────────────────────────────────────
import minecraft_launcher_lib as mclib

# ══════════════════════════════════════════════════════════════════════════════
#  PATHS & CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

APP      = "CraftLaunch"
VER      = "3.0"
BASE     = Path.home() / ".craftlaunch"
MC_DIR   = BASE / "minecraft"
INST_DIR = BASE / "instances"
MODS_LIB = BASE / "mods_library"
PROFILES_FILE = BASE / "profiles.json"
SETTINGS_FILE = BASE / "settings.json"

LOADERS = ["Vanilla", "Forge", "Fabric", "NeoForge", "Quilt", "OptiFine"]

# ── colour palette ─────────────────────────────────────────────────────────
C = {
    "bg":       "#07090f",
    "panel":    "#0c0f1a",
    "card":     "#101525",
    "card2":    "#161e30",
    "border":   "#1e2d48",
    "glow":     "#00d4ff",
    "glow2":    "#0055ff",
    "green":    "#00ff88",
    "gold":     "#fbbf24",
    "red":      "#ff4444",
    "text":     "#dce8f8",
    "muted":    "#5a7090",
    "dim":      "#1e3050",
    "white":    "#ffffff",
    "hover":    "#131c30",
}

# ══════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def ensure_dirs():
    for d in [BASE, MC_DIR, INST_DIR, MODS_LIB]:
        d.mkdir(parents=True, exist_ok=True)

def load_json(p, default):
    try:
        if Path(p).exists():
            return json.loads(Path(p).read_text("utf-8"))
    except Exception:
        pass
    return default

def save_json(p, d):
    Path(p).write_text(json.dumps(d, indent=2), encoding="utf-8")

def fmt_bytes(b):
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def java_major(ver_str):
    """Parse major version number from java version string like 17.0.1 or 1.8.0_202."""
    try:
        parts = ver_str.split(".")
        major = int(parts[0])
        if major == 1:          # old style: 1.8 -> 8
            major = int(parts[1])
        return major
    except Exception:
        return 0

def get_all_javas():
    """Return list of (path, version_string, major_int) for all found Java installs."""
    candidates = ["java"]
    if platform.system() == "Windows":
        for root in [
            os.environ.get("PROGRAMFILES", "C:\\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
            "C:\\Program Files\\Eclipse Adoptium",
            "C:\\Program Files\\Eclipse Foundation",
            "C:\\Program Files\\Microsoft",
            "C:\\Program Files\\Java",
        ]:
            rp = Path(root)
            if rp.exists():
                for jp in rp.rglob("java.exe"):
                    if "jre" in str(jp).lower() or "jdk" in str(jp).lower() or "temurin" in str(jp).lower():
                        candidates.append(str(jp))
    if platform.system() == "Darwin":
        jvms = Path("/Library/Java/JavaVirtualMachines")
        if jvms.exists():
            for jp in jvms.rglob("java"):
                candidates.append(str(jp))
    for extra in ["/usr/bin/java", "/usr/local/bin/java"]:
        candidates.append(extra)

    results = []
    seen = set()
    for c in candidates:
        if c in seen: continue
        seen.add(c)
        try:
            r = subprocess.run([c, "-version"],
                               capture_output=True, text=True, timeout=5)
            out = r.stderr + r.stdout
            m = re.search(r'version "([^"]+)"', out)
            if m:
                ver = m.group(1)
                results.append((c, ver, java_major(ver)))
        except Exception:
            pass
    return results

def find_java(min_version=8):
    """Find best Java >= min_version. Returns (path, version_string) or (None, None)."""
    javas = get_all_javas()
    # Filter by minimum version, prefer highest version
    suitable = [(p, v, maj) for p, v, maj in javas if maj >= min_version]
    if suitable:
        suitable.sort(key=lambda x: x[2], reverse=True)
        return suitable[0][0], suitable[0][1]
    # Fall back to any java found
    if javas:
        return javas[0][0], javas[0][1]
    return None, None

def get_required_java_version(version_id):
    """Read the javaVersion from the installed version JSON, default 8."""
    ver_json = MC_DIR / "versions" / version_id / f"{version_id}.json"
    try:
        data = json.loads(ver_json.read_text("utf-8"))
        return data.get("javaVersion", {}).get("majorVersion", 8)
    except Exception:
        return 8

def get_mc_versions():
    """Return list of version dicts from Mojang via minecraft-launcher-lib."""
    return mclib.utils.get_version_list()

def is_installed(version_id):
    """Check if a version JAR exists."""
    jar = MC_DIR / "versions" / version_id / f"{version_id}.jar"
    return jar.exists()

# ══════════════════════════════════════════════════════════════════════════════
#  INSTALL  (minecraft-launcher-lib)
# ══════════════════════════════════════════════════════════════════════════════

def install_minecraft(version_id, log_cb, progress_cb, status_cb):
    """
    Download & install Minecraft version_id into MC_DIR.
    Uses minecraft-launcher-lib's install_minecraft_version with callbacks.
    """
    MC_DIR.mkdir(parents=True, exist_ok=True)

    total = [1]
    done  = [0]

    def set_status(s):
        status_cb(s)
        log_cb(s, "info")

    def set_max(m):
        total[0] = max(m, 1)

    def set_progress(current):
        done[0] = current
        pct = (current / total[0]) * 100
        progress_cb(min(pct, 100))

    callback = {
        "setStatus":   set_status,
        "setProgress": set_progress,
        "setMax":      set_max,
    }

    log_cb(f"Installing Minecraft {version_id} into {MC_DIR}", "info")

    mclib.install.install_minecraft_version(
        version=version_id,
        minecraft_directory=str(MC_DIR),
        callback=callback,
    )

    log_cb(f"Minecraft {version_id} installed successfully!", "success")


# ══════════════════════════════════════════════════════════════════════════════
#  LOADER INSTALL  (Forge / Fabric / Quilt / NeoForge)
# ══════════════════════════════════════════════════════════════════════════════

def get_fabric_versions(mc_version):
    """Return list of Fabric loader versions for a given MC version."""
    try:
        loaders = mclib.fabric.get_all_loader_versions()
        return [l["version"] for l in loaders]
    except Exception:
        return []

def get_quilt_versions(mc_version):
    """Return list of Quilt loader versions."""
    try:
        loaders = mclib.quilt.get_all_loader_versions()
        return [l["version"] for l in loaders]
    except Exception:
        return []

def install_fabric(mc_version, loader_version, log_cb, progress_cb, status_cb):
    """Install Fabric loader for given MC version."""
    total = [1]; done = [0]
    def set_status(s): status_cb(s); log_cb(s, "info")
    def set_max(m): total[0] = max(m, 1)
    def set_progress(c): done[0] = c; progress_cb(min((c/total[0])*100, 100))
    callback = {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}

    log_cb(f"Installing Fabric {loader_version} for MC {mc_version}…", "info")
    if loader_version:
        mclib.fabric.install_fabric(
            minecraft_version=mc_version,
            minecraft_directory=str(MC_DIR),
            loader_version=loader_version,
            callback=callback,
        )
    else:
        mclib.fabric.install_fabric(
            minecraft_version=mc_version,
            minecraft_directory=str(MC_DIR),
            callback=callback,
        )
    log_cb("Fabric installed!", "success")

def install_quilt(mc_version, loader_version, log_cb, progress_cb, status_cb):
    """Install Quilt loader."""
    total = [1]; done = [0]
    def set_status(s): status_cb(s); log_cb(s, "info")
    def set_max(m): total[0] = max(m, 1)
    def set_progress(c): done[0] = c; progress_cb(min((c/total[0])*100, 100))
    callback = {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}

    log_cb(f"Installing Quilt {loader_version} for MC {mc_version}…", "info")
    if loader_version:
        mclib.quilt.install_quilt(
            minecraft_version=mc_version,
            minecraft_directory=str(MC_DIR),
            loader_version=loader_version,
            callback=callback,
        )
    else:
        mclib.quilt.install_quilt(
            minecraft_version=mc_version,
            minecraft_directory=str(MC_DIR),
            callback=callback,
        )
    log_cb("Quilt installed!", "success")

def install_forge(mc_version, forge_version, java_path, log_cb, progress_cb, status_cb):
    """Install Forge — downloads installer jar and runs it."""
    import urllib.request, tempfile
    total = [1]; done = [0]
    def set_status(s): status_cb(s); log_cb(s, "info")
    def set_max(m): total[0] = max(m, 1)
    def set_progress(c): done[0] = c; progress_cb(min((c/total[0])*100, 100))
    callback = {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}

    try:
        # Try minecraft-launcher-lib forge support first
        forge_versions = mclib.forge.list_forge_versions(mc_version)
        if not forge_versions:
            raise ValueError(f"No Forge versions found for MC {mc_version}")
        target = forge_version if forge_version in forge_versions else forge_versions[0]
        log_cb(f"Installing Forge {target} for MC {mc_version}…", "info")
        set_status(f"Downloading Forge {target}…")
        mclib.forge.install_forge_version(
            versionid=target,
            path=str(MC_DIR),
            java=java_path,
            callback=callback,
        )
        log_cb("Forge installed!", "success")
        return target
    except Exception as e:
        log_cb(f"Forge install error: {e}", "error")
        raise

def get_forge_versions(mc_version):
    """Return list of Forge version strings for a given MC version."""
    try:
        return mclib.forge.list_forge_versions(mc_version)
    except Exception:
        return []

def get_loader_version_id(mc_version, loader, loader_version=""):
    """
    Return the version ID string that was installed for a given loader.
    e.g. fabric-loader-0.15.6-1.20.4  or  1.20.4-forge-49.0.3
    """
    if loader == "Fabric":
        # Fabric version IDs are like: fabric-loader-X.Y.Z-MC
        vers_dir = MC_DIR / "versions"
        if vers_dir.exists():
            for d in vers_dir.iterdir():
                n = d.name
                if n.startswith("fabric-loader-") and n.endswith(f"-{mc_version}"):
                    return n
        return f"fabric-loader-{loader_version}-{mc_version}" if loader_version else None
    elif loader == "Quilt":
        vers_dir = MC_DIR / "versions"
        if vers_dir.exists():
            for d in vers_dir.iterdir():
                n = d.name
                if "quilt-loader" in n and mc_version in n:
                    return n
        return None
    elif loader in ("Forge", "NeoForge"):
        vers_dir = MC_DIR / "versions"
        if vers_dir.exists():
            for d in vers_dir.iterdir():
                n = d.name
                if mc_version in n and ("forge" in n.lower() or "neoforge" in n.lower()):
                    return n
        return None
    return None

def is_loader_installed(mc_version, loader):
    """Check if a loader version is installed."""
    vid = get_loader_version_id(mc_version, loader)
    if not vid:
        return False
    jar = MC_DIR / "versions" / vid / f"{vid}.jar"
    json_f = MC_DIR / "versions" / vid / f"{vid}.json"
    # Fabric/Quilt don't have a jar, just a json
    return json_f.exists()


# ══════════════════════════════════════════════════════════════════════════════
#  LAUNCH  (minecraft-launcher-lib)
# ══════════════════════════════════════════════════════════════════════════════

def build_launch_command(version_id, profile, username, uuid_str):
    """Build argv list using minecraft-launcher-lib.
    If a mod loader (Fabric/Forge/Quilt) is installed, use its version ID instead.
    """
    game_dir = Path(profile.get("game_dir") or INST_DIR / profile["name"])
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / "mods").mkdir(exist_ok=True)

    java_path = profile.get("java_path") or "auto"
    if java_path in ("", "auto"):
        found, _ = find_java()
        java_path = found or "java"

    jvm_args = profile.get("jvm_args", "-Xmx2G -Xms512M").split()

    # Use loader version ID if a loader is installed
    loader = profile.get("loader", "Vanilla")
    launch_version = version_id
    if loader not in ("Vanilla", "OptiFine", ""):
        loader_vid = get_loader_version_id(version_id, loader)
        if loader_vid:
            launch_version = loader_vid

    options = mclib.types.MinecraftOptions(
        username=username,
        uuid=uuid_str,
        token="0",
        jvmArguments=jvm_args,
        gameDirectory=str(game_dir),
        executablePath=java_path,
    )

    return mclib.command.get_minecraft_command(
        version=launch_version,
        minecraft_directory=str(MC_DIR),
        options=options,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════

class CraftLaunch:

    def __init__(self):
        ensure_dirs()
        self.profiles    = load_json(PROFILES_FILE, [self._dflt_profile()])
        self.settings    = load_json(SETTINGS_FILE, self._dflt_settings())
        self.cur         = 0
        self.mc_versions = []
        self._installing = False
        self._cancel     = False
        self._game_proc  = None

        self._build_root()
        self._build_sidebar()
        self._build_pages()
        self._nav("home")
        self._reload_profile_list()
        threading.Thread(target=self._bg_manifest, daemon=True).start()
        threading.Thread(target=self._bg_java,     daemon=True).start()

    def _dflt_profile(self):
        return {
            "name": "Survival", "version": "1.20.4",
            "loader": "Vanilla", "loader_version": "",
            "java_path": "auto", "jvm_args": "-Xmx2G -Xms512M",
            "game_dir": "", "resolution_width": "854",
            "resolution_height": "480", "mods": [],
            "icon": "⛏", "created": datetime.now().isoformat(),
        }

    def _dflt_settings(self):
        return {
            "username": "Player",
            "uuid": str(uuid.uuid4()),
            "java_path": "auto",
            "close_on_launch": False,
        }

    # ── root window ───────────────────────────────────────────────────────
    def _build_root(self):
        self.root = tk.Tk()
        self.root.title(f"{APP}  ·  v{VER}")
        self.root.geometry("1200x760")
        self.root.minsize(980, 640)
        self.root.configure(bg=C["bg"])
        self.root.resizable(True, True)

        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("Dark.TCombobox",
            fieldbackground=C["card2"], background=C["card2"],
            foreground=C["text"], selectbackground=C["card2"],
            selectforeground=C["text"], arrowcolor=C["glow"],
            bordercolor=C["border"], lightcolor=C["card2"],
            darkcolor=C["card2"])
        s.configure("Glow.Horizontal.TProgressbar",
            troughcolor=C["card2"], background=C["glow"],
            bordercolor=C["border"], lightcolor=C["glow"],
            darkcolor=C["glow2"], thickness=8)
        s.configure("TV.Treeview",
            background=C["card"], foreground=C["text"],
            fieldbackground=C["card"], rowheight=36,
            borderwidth=0, font=("Consolas", 9))
        s.configure("TV.Treeview.Heading",
            background=C["card2"], foreground=C["muted"],
            borderwidth=0, font=("Segoe UI", 9, "bold"), relief="flat")
        s.map("TV.Treeview",
              background=[("selected", C["glow2"])],
              foreground=[("selected", C["white"])])

    # ── sidebar ───────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sb = tk.Frame(self.root, bg=C["panel"], width=230)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)
        self._sb = sb

        logo = tk.Frame(sb, bg=C["panel"])
        logo.pack(fill="x", padx=18, pady=(26, 8))

        cvs = tk.Canvas(logo, width=44, height=44, bg=C["panel"],
                        highlightthickness=0)
        cvs.pack(side="left")
        cvs.create_oval(3, 3, 41, 41, fill=C["card2"], outline=C["glow"], width=2)
        cvs.create_text(22, 22, text="⛏", font=("Segoe UI", 20), fill=C["glow"])

        tf = tk.Frame(logo, bg=C["panel"])
        tf.pack(side="left", padx=10)
        tk.Label(tf, text=APP, font=("Segoe UI", 15, "bold"),
                 bg=C["panel"], fg=C["white"]).pack(anchor="w")
        tk.Label(tf, text=f"v{VER}", font=("Consolas", 8),
                 bg=C["panel"], fg=C["muted"]).pack(anchor="w")

        tk.Frame(sb, bg=C["border"], height=1).pack(fill="x", padx=14, pady=10)

        self._nav_btns = {}
        for pid, ico, lbl in [
            ("home",     "⌂", "Home"),
            ("install",  "⬇", "Install Minecraft"),
            ("profiles", "◈", "Profiles"),
            ("mods",     "❖", "Mod Manager"),
            ("settings", "⚙", "Settings"),
            ("console",  "▶", "Console"),
        ]:
            self._nav_btns[pid] = self._make_nav_btn(sb, ico, lbl, pid)

        tk.Frame(sb, bg=C["border"], height=1).pack(
            side="bottom", fill="x", padx=14, pady=6)
        self._java_lbl = tk.Label(sb, text="☕  detecting java…",
                                  font=("Consolas", 8),
                                  bg=C["panel"], fg=C["muted"])
        self._java_lbl.pack(side="bottom", padx=16, pady=(0, 12), anchor="w")
        tk.Label(sb, text=f"{platform.system()} / {platform.machine()}",
                 font=("Consolas", 8), bg=C["panel"], fg=C["dim"]
                 ).pack(side="bottom", padx=16, anchor="w")

    def _make_nav_btn(self, parent, ico, lbl, pid):
        row = tk.Frame(parent, bg=C["panel"], cursor="hand2")
        row.pack(fill="x", padx=10, pady=1)

        bar = tk.Frame(row, bg=C["panel"], width=4)
        bar.pack(side="left", fill="y")

        ic = tk.Label(row, text=ico, font=("Segoe UI", 14),
                      bg=C["panel"], fg=C["muted"], width=2)
        ic.pack(side="left", padx=(6, 4), pady=11)

        tx = tk.Label(row, text=lbl, font=("Segoe UI", 10),
                      bg=C["panel"], fg=C["muted"], anchor="w")
        tx.pack(side="left", fill="x", expand=True)

        def click(e=None): self._nav(pid)
        def enter(e=None):
            if self._cur_page != pid:
                for w in (row, ic, tx, bar): w.config(bg=C["hover"])
        def leave(e=None):
            if self._cur_page != pid:
                for w in (row, ic, tx, bar): w.config(bg=C["panel"])

        for w in (row, ic, tx):
            w.bind("<Button-1>", click)
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)

        row._bar = bar
        row._ic  = ic
        row._tx  = tx
        return row

    def _nav(self, pid):
        self._cur_page = pid
        for p, ff in self._pages.items():
            ff.pack_forget()
        self._pages[pid].pack(fill="both", expand=True)
        for p, btn in self._nav_btns.items():
            active = p == pid
            bg = C["card"] if active else C["panel"]
            btn.config(bg=bg)
            btn._bar.config(bg=C["glow"] if active else C["panel"])
            btn._ic.config(bg=bg, fg=C["glow"] if active else C["muted"])
            btn._tx.config(bg=bg, fg=C["text"] if active else C["muted"])

    # ── pages container ───────────────────────────────────────────────────
    def _build_pages(self):
        self._pages = {}
        self._cur_page = None
        self._main = tk.Frame(self.root, bg=C["bg"])
        self._main.pack(side="left", fill="both", expand=True)

        self._pages["home"]     = self._mk_home()
        self._pages["install"]  = self._mk_install()
        self._pages["profiles"] = self._mk_profiles()
        self._pages["mods"]     = self._mk_mods()
        self._pages["settings"] = self._mk_settings()
        self._pages["console"]  = self._mk_console()

    # ═══════════════════════════════════════════════════════════════════════
    #  HOME PAGE
    # ═══════════════════════════════════════════════════════════════════════
    def _mk_home(self):
        f = tk.Frame(self._main, bg=C["bg"])

        hero = tk.Canvas(f, bg=C["bg"], height=170, highlightthickness=0)
        hero.pack(fill="x")

        def draw(e=None):
            hero.delete("all")
            w = hero.winfo_width() or 1000
            for i in range(170):
                t = i / 170
                r = int(7  + t * 6)
                g = int(9  + t * 8)
                b = int(15 + t * 28)
                hero.create_line(0, i, w, i, fill=f"#{r:02x}{g:02x}{b:02x}")
            for x in range(0, w, 64):
                hero.create_line(x, 0, x, 170, fill="#111a2e")
            for y in range(0, 170, 64):
                hero.create_line(0, y, w, y, fill="#111a2e")
            hero.create_line(0, 168, w, 168, fill=C["glow"], width=2)
            hero.create_line(0, 167, w, 167, fill=C["glow2"], width=1)
            hero.create_text(40, 70, anchor="w",
                             text=APP, font=("Segoe UI", 34, "bold"),
                             fill=C["white"])
            hero.create_text(40, 108, anchor="w",
                             text="Standalone Minecraft Launcher  ·  powered by minecraft-launcher-lib",
                             font=("Segoe UI", 10), fill=C["muted"])
            hero.create_rectangle(w - 130, 62, w - 22, 90,
                                  fill=C["card2"], outline=C["glow"], width=1)
            hero.create_text(w - 76, 76, text=f"v{VER} • Ready",
                             font=("Consolas", 9), fill=C["glow"])

        hero.bind("<Configure>", draw)
        self.root.after(120, draw)

        body = tk.Frame(f, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=16)

        stats = tk.Frame(body, bg=C["bg"])
        stats.pack(fill="x", pady=(0, 18))
        self._sc_profiles  = self._stat(stats, "◈", "Profiles",  str(len(self.profiles)))
        self._sc_installed = self._stat(stats, "▣", "Installed", self._cnt_installed())
        self._sc_mods      = self._stat(stats, "❖", "Mods",      self._cnt_mods())
        self._sc_java      = self._stat(stats, "☕", "Java",      "…")
        for sc in (self._sc_profiles, self._sc_installed,
                   self._sc_mods, self._sc_java):
            sc.pack(side="left", padx=(0, 12), ipadx=18, ipady=14)

        self._sep_lbl(body, "ACTIVE PROFILE")
        self._home_card = tk.Frame(body, bg=C["card"])
        self._home_card.pack(fill="x", pady=(6, 18))
        self._refresh_home_card()

        bar = tk.Frame(body, bg=C["bg"])
        bar.pack(fill="x")

        self._launch_btn = tk.Button(
            bar, text="▶  LAUNCH",
            font=("Segoe UI", 13, "bold"),
            bg=C["glow"], fg="#000000",
            relief="flat", cursor="hand2",
            padx=40, pady=15,
            activebackground="#00aadd",
            command=self._do_launch)
        self._launch_btn.pack(side="left")

        right = tk.Frame(bar, bg=C["bg"])
        right.pack(side="left", fill="x", expand=True, padx=18)

        self._prog_var = tk.DoubleVar()
        ttk.Progressbar(right, variable=self._prog_var, maximum=100,
                        style="Glow.Horizontal.TProgressbar"
                        ).pack(fill="x", pady=(0, 5))
        self._status_lbl = tk.Label(right, text="Ready",
                                    font=("Consolas", 9),
                                    bg=C["bg"], fg=C["muted"])
        self._status_lbl.pack(anchor="w")

        self._sep_lbl(body, "QUICK ACCESS", pady=(20, 8))
        tiles = tk.Frame(body, bg=C["bg"])
        tiles.pack(fill="x")
        for i, (t, bg, cmd) in enumerate([
            ("⬇  Install Version",  C["glow2"],  lambda: self._nav("install")),
            ("📂 Game Folder",       C["card2"],  self._open_mc_dir),
            ("🧩 Add Mods",          C["card2"],  lambda: self._nav("mods")),
            ("➕ New Profile",       C["card2"],  self._new_profile_dlg),
            ("📸 Screenshots",       C["card2"],  self._open_screenshots),
            ("☕ Java Check",        C["card2"],  self._run_java_check),
        ]):
            tile = tk.Frame(tiles, bg=bg, cursor="hand2", padx=14, pady=14)
            tile.grid(row=0, column=i, padx=5, sticky="ew")
            tiles.columnconfigure(i, weight=1)
            tk.Label(tile, text=t, font=("Segoe UI", 9),
                     bg=bg, fg=C["text"]).pack()
            for w in (tile, *tile.winfo_children()):
                w.bind("<Button-1>", lambda e, c=cmd: c())

        return f

    def _stat(self, parent, ico, lbl, val):
        card = tk.Frame(parent, bg=C["card"])
        tk.Label(card, text=ico, font=("Segoe UI", 22),
                 bg=C["card"], fg=C["glow"]).pack(anchor="w", padx=18, pady=(14, 0))
        vl = tk.Label(card, text=val, font=("Segoe UI", 22, "bold"),
                      bg=C["card"], fg=C["white"])
        vl.pack(anchor="w", padx=18)
        tk.Label(card, text=lbl, font=("Consolas", 8),
                 bg=C["card"], fg=C["muted"]).pack(anchor="w", padx=18, pady=(0, 14))
        card._val = vl
        return card

    def _sep_lbl(self, parent, text, pady=(0, 0)):
        ff = tk.Frame(parent, bg=C["bg"])
        ff.pack(fill="x", pady=pady)
        tk.Label(ff, text=text, font=("Consolas", 8, "bold"),
                 bg=C["bg"], fg=C["muted"]).pack(side="left")
        tk.Frame(ff, bg=C["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=(10, 0), pady=4)

    def _refresh_home_card(self):
        for w in self._home_card.winfo_children(): w.destroy()
        if not self.profiles: return
        p    = self.profiles[self.cur]
        inst = is_installed(p["version"])

        outer = tk.Frame(self._home_card, bg=C["card"], padx=22, pady=20)
        outer.pack(fill="x")

        cvs = tk.Canvas(outer, width=56, height=56, bg=C["card2"],
                        highlightthickness=1,
                        highlightbackground=C["glow"] if inst else C["border"])
        cvs.pack(side="left")
        cvs.create_text(28, 28, text=p.get("icon", "⛏"),
                        font=("Segoe UI", 26), fill=C["glow"])

        mid = tk.Frame(outer, bg=C["card"])
        mid.pack(side="left", padx=18, fill="both", expand=True)
        tk.Label(mid, text=p["name"], font=("Segoe UI", 15, "bold"),
                 bg=C["card"], fg=C["white"]).pack(anchor="w")
        tk.Label(mid, text=f"Minecraft {p['version']}   ·   {p['loader']}",
                 font=("Segoe UI", 10), bg=C["card"], fg=C["muted"]).pack(anchor="w")
        inst_c = C["green"] if inst else C["gold"]
        inst_t = "✓ Installed — ready to launch" if inst else \
                 "⚠  Not installed — go to Install tab first"
        tk.Label(mid, text=inst_t, font=("Consolas", 9),
                 bg=C["card"], fg=inst_c).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(outer, bg=C["card"])
        right.pack(side="right", anchor="n")
        mods = len(p.get("mods", []))
        tk.Label(right, text=f"{mods} mod(s)",
                 font=("Consolas", 9), bg=C["card"], fg=C["muted"]).pack(anchor="e")
        tk.Button(right, text="Switch ▸", font=("Segoe UI", 9),
                  bg=C["card2"], fg=C["muted"], relief="flat", cursor="hand2",
                  padx=10, pady=4,
                  command=lambda: self._nav("profiles")
                  ).pack(anchor="e", pady=(6, 0))

    # ═══════════════════════════════════════════════════════════════════════
    #  INSTALL PAGE
    # ═══════════════════════════════════════════════════════════════════════
    def _mk_install(self):
        f = tk.Frame(self._main, bg=C["bg"])
        self._hdr(f, "⬇  Install Minecraft",
                  "Download any version directly — no external launcher required")

        body = tk.Frame(f, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=0)

        fbar = tk.Frame(body, bg=C["bg"])
        fbar.pack(fill="x", pady=(0, 10))
        tk.Label(fbar, text="FILTER:", font=("Consolas", 8, "bold"),
                 bg=C["bg"], fg=C["muted"]).pack(side="left", padx=(0, 10))
        self._vfilt = tk.StringVar(value="release")
        for val, lbl in [("release", "Releases"), ("snapshot", "Snapshots"),
                         ("old_beta", "Beta"), ("old_alpha", "Alpha"), ("all", "All")]:
            tk.Radiobutton(fbar, text=lbl, variable=self._vfilt, value=val,
                           bg=C["bg"], fg=C["muted"], selectcolor=C["card"],
                           activebackground=C["bg"], activeforeground=C["glow"],
                           font=("Segoe UI", 9), cursor="hand2",
                           command=self._fill_ver_tree).pack(side="left", padx=4)
        tk.Button(fbar, text="⟳ Refresh", font=("Segoe UI", 9),
                  bg=C["card2"], fg=C["muted"], relief="flat", cursor="hand2",
                  padx=10, pady=4,
                  command=lambda: threading.Thread(
                      target=self._bg_manifest, daemon=True).start()
                  ).pack(side="right")

        tf = tk.Frame(body, bg=C["bg"])
        tf.pack(fill="both", expand=True)

        cols = ("Version", "Type", "Release Date", "Status")
        self._vtree = ttk.Treeview(tf, columns=cols, show="headings",
                                   style="TV.Treeview", selectmode="browse")
        for col, w in zip(cols, (160, 120, 180, 130)):
            self._vtree.heading(col, text=col)
            self._vtree.column(col, width=w, anchor="w")
        self._vtree.tag_configure("inst", foreground=C["green"])
        self._vtree.tag_configure("snap", foreground=C["gold"])

        vsb = ttk.Scrollbar(tf, orient="vertical", command=self._vtree.yview)
        self._vtree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._vtree.pack(fill="both", expand=True)
        self._vtree.bind("<<TreeviewSelect>>", self._on_ver_sel)

        ipanel = tk.Frame(body, bg=C["card"], padx=22, pady=18)
        ipanel.pack(fill="x", pady=(10, 0))

        left_ip = tk.Frame(ipanel, bg=C["card"])
        left_ip.pack(side="left", fill="both", expand=True)

        self._inst_ver_lbl = tk.Label(left_ip,
                                      text="Select a version from the list above",
                                      font=("Segoe UI", 13, "bold"),
                                      bg=C["card"], fg=C["white"])
        self._inst_ver_lbl.pack(anchor="w")

        self._inst_status_lbl = tk.Label(left_ip, text="",
                                         font=("Consolas", 9),
                                         bg=C["card"], fg=C["muted"])
        self._inst_status_lbl.pack(anchor="w", pady=4)

        self._inst_prog_var = tk.DoubleVar()
        self._inst_prog = ttk.Progressbar(left_ip,
                                          variable=self._inst_prog_var,
                                          maximum=100,
                                          style="Glow.Horizontal.TProgressbar",
                                          length=460)
        self._inst_prog.pack(anchor="w", pady=4)

        # Loader row
        loader_row = tk.Frame(left_ip, bg=C["card"])
        loader_row.pack(anchor="w", pady=(8, 0))

        tk.Label(loader_row, text="Mod Loader:",
                 font=("Segoe UI", 9), bg=C["card"],
                 fg=C["muted"]).pack(side="left", padx=(0, 8))

        self._inst_loader_var = tk.StringVar(value="Vanilla")
        for ldr in ["Vanilla", "Fabric", "Forge", "Quilt", "NeoForge"]:
            rb = tk.Radiobutton(loader_row, text=ldr,
                                variable=self._inst_loader_var, value=ldr,
                                bg=C["card"], fg=C["muted"],
                                selectcolor=C["card2"],
                                activebackground=C["card"],
                                activeforeground=C["glow"],
                                font=("Segoe UI", 9), cursor="hand2",
                                command=self._on_loader_change)
            rb.pack(side="left", padx=4)

        loader_ver_row = tk.Frame(left_ip, bg=C["card"])
        loader_ver_row.pack(anchor="w", pady=4)
        tk.Label(loader_ver_row, text="Loader Version:",
                 font=("Segoe UI", 9), bg=C["card"],
                 fg=C["muted"]).pack(side="left", padx=(0, 8))
        self._inst_loader_ver_var = tk.StringVar(value="latest")
        self._inst_loader_ver_cb = ttk.Combobox(
            loader_ver_row, textvariable=self._inst_loader_ver_var,
            values=["latest"], state="readonly", width=28,
            style="Dark.TCombobox")
        self._inst_loader_ver_cb.pack(side="left")

        tk.Label(left_ip,
                 text="ℹ  Vanilla = no mods  ·  Fabric/Forge needed for .jar mods",
                 font=("Consolas", 8), bg=C["card"], fg=C["muted"]
                 ).pack(anchor="w", pady=(4, 0))

        right_ip = tk.Frame(ipanel, bg=C["card"])
        right_ip.pack(side="right", anchor="center", padx=(20, 0))

        self._inst_btn = tk.Button(
            right_ip, text="⬇  INSTALL",
            font=("Segoe UI", 12, "bold"),
            bg=C["glow"], fg="#000000",
            relief="flat", cursor="hand2",
            padx=30, pady=13,
            command=self._do_install)
        self._inst_btn.pack(pady=(0, 6))

        tk.Button(right_ip, text="✕ Cancel",
                  font=("Segoe UI", 9),
                  bg=C["card2"], fg=C["red"],
                  relief="flat", cursor="hand2",
                  padx=14, pady=5,
                  command=lambda: setattr(self, "_cancel", True)
                  ).pack()

        return f

    def _fill_ver_tree(self):
        if not hasattr(self, "_vtree"): return
        for r in self._vtree.get_children():
            self._vtree.delete(r)
        filt = self._vfilt.get()
        for v in self.mc_versions:
            t = v.get("type", "")
            if filt != "all" and t != filt:
                continue
            raw_date = v.get("releaseTime", "")
            date = raw_date.strftime("%Y-%m-%d") if hasattr(raw_date, "strftime") else str(raw_date)[:10]
            inst = is_installed(v["id"])
            status = "✓ Installed" if inst else ""
            tag    = "inst" if inst else ("snap" if t == "snapshot" else "")
            self._vtree.insert("", "end",
                               values=(v["id"], t, date, status),
                               tags=(tag,))

    def _on_ver_sel(self, e=None):
        sel = self._vtree.selection()
        if not sel: return
        ver  = str(self._vtree.item(sel[0])["values"][0])
        inst = is_installed(ver)
        self._inst_ver_lbl.config(text=f"Minecraft  {ver}")
        if inst:
            self._inst_status_lbl.config(
                text="✓ Already installed — click to reinstall", fg=C["green"])
            self._inst_btn.config(text="⟳  REINSTALL", bg=C["gold"])
        else:
            self._inst_status_lbl.config(text="Ready to download & install",
                                         fg=C["muted"])
            self._inst_btn.config(text="⬇  INSTALL", bg=C["glow"])

    def _on_loader_change(self):
        """Update loader version dropdown when loader radio changes."""
        loader = self._inst_loader_var.get()
        sel = self._vtree.selection()
        ver = str(self._vtree.item(sel[0])["values"][0]) if sel else ""
        if loader == "Fabric" and ver:
            threading.Thread(
                target=self._fetch_loader_versions,
                args=("Fabric", ver), daemon=True).start()
        elif loader == "Quilt" and ver:
            threading.Thread(
                target=self._fetch_loader_versions,
                args=("Quilt", ver), daemon=True).start()
        elif loader == "Forge" and ver:
            threading.Thread(
                target=self._fetch_loader_versions,
                args=("Forge", ver), daemon=True).start()
        else:
            self._inst_loader_ver_cb.config(values=["latest"], state="readonly")
            self._inst_loader_ver_var.set("latest")

    def _fetch_loader_versions(self, loader, mc_ver):
        try:
            if loader == "Fabric":
                versions = get_fabric_versions(mc_ver)
            elif loader == "Quilt":
                versions = get_quilt_versions(mc_ver)
            elif loader == "Forge":
                versions = get_forge_versions(mc_ver)
            else:
                versions = []
            versions = versions[:40] if versions else ["latest"]
            def upd():
                self._inst_loader_ver_cb.config(
                    values=versions, state="readonly")
                self._inst_loader_ver_var.set(versions[0] if versions else "latest")
            self.root.after(0, upd)
        except Exception as e:
            self._log(f"Could not fetch {loader} versions: {e}", "warn")

    def _do_install(self):
        sel = self._vtree.selection()
        if not sel:
            messagebox.showinfo("Select Version",
                                "Please click a version in the list first.")
            return
        if self._installing:
            messagebox.showinfo("Busy", "Already installing — please wait.")
            return

        ver    = str(self._vtree.item(sel[0])["values"][0])
        loader = self._inst_loader_var.get()
        loader_ver = self._inst_loader_ver_var.get()
        if loader_ver == "latest": loader_ver = ""

        self._installing = True
        self._cancel     = False
        self._inst_btn.config(state="disabled")
        self._nav("console")
        self._log(f"═══ Installing Minecraft {ver}  [{loader}] ═══", "success")

        def progress_cb(pct):
            self.root.after(0, lambda: (
                self._inst_prog_var.set(pct),
                self._prog_var.set(pct)))

        def status_cb(s):
            self.root.after(0, lambda: (
                self._inst_status_lbl.config(text=s),
                self._status_lbl.config(text=s)))

        def run():
            try:
                # Step 1: always install vanilla first
                install_minecraft(ver, self._log, progress_cb, status_cb)

                # Step 2: install loader on top
                if loader == "Fabric":
                    progress_cb(0)
                    install_fabric(ver, loader_ver, self._log, progress_cb, status_cb)
                elif loader == "Quilt":
                    progress_cb(0)
                    install_quilt(ver, loader_ver, self._log, progress_cb, status_cb)
                elif loader in ("Forge", "NeoForge"):
                    progress_cb(0)
                    jp, _ = find_java(min_version=17)
                    jp = jp or "java"
                    install_forge(ver, loader_ver, jp, self._log, progress_cb, status_cb)

                ldr_txt = f" + {loader}" if loader != "Vanilla" else ""
                self.root.after(0, lambda: (
                    self._fill_ver_tree(),
                    self._refresh_home_card(),
                    self._update_stats(),
                    messagebox.showinfo(
                        "Installation Complete! 🎉",
                        f"Minecraft {ver}{ldr_txt} installed!\n\n"
                        f"In Profiles, set Loader to '{loader}' then hit LAUNCH.\n"
                        f"Your mods will be auto-copied to the instance.")))
            except Exception as ex:
                self._log(f"Installation failed: {ex}", "error")
                self._log(traceback.format_exc(), "dim")
                msg = str(ex)
                self.root.after(0, lambda m=msg: messagebox.showerror(
                    "Install Error",
                    f"Failed to install Minecraft {ver}:\n\n{m}"))
            finally:
                self._installing = False
                self.root.after(0, lambda: self._inst_btn.config(
                    state="normal", text="⬇  INSTALL", bg=C["glow"]))

        threading.Thread(target=run, daemon=True).start()

    # ═══════════════════════════════════════════════════════════════════════
    #  PROFILES PAGE
    # ═══════════════════════════════════════════════════════════════════════
    def _mk_profiles(self):
        f = tk.Frame(self._main, bg=C["bg"])
        self._hdr(f, "◈  Profiles", "Create and configure game instances")

        body = tk.Frame(f, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=0)

        left = tk.Frame(body, bg=C["panel"], width=240)
        left.pack(side="left", fill="y", padx=(0, 18))
        left.pack_propagate(False)

        lh = tk.Frame(left, bg=C["panel"])
        lh.pack(fill="x", padx=10, pady=(12, 8))
        tk.Label(lh, text="YOUR PROFILES", font=("Consolas", 8, "bold"),
                 bg=C["panel"], fg=C["muted"]).pack(side="left")
        tk.Button(lh, text="+ New", font=("Segoe UI", 8),
                  bg=C["glow2"], fg=C["white"], relief="flat",
                  cursor="hand2", padx=8, pady=3,
                  command=self._new_profile_dlg).pack(side="right")

        self._plist = tk.Frame(left, bg=C["panel"])
        self._plist.pack(fill="both", expand=True, padx=6, pady=4)

        self._ped = tk.Frame(body, bg=C["bg"])
        self._ped.pack(side="left", fill="both", expand=True)
        self._build_ped()

        return f

    def _reload_profile_list(self):
        if not hasattr(self, "_plist"): return
        for w in self._plist.winfo_children(): w.destroy()

        for i, p in enumerate(self.profiles):
            active = i == self.cur
            bg = C["card"] if active else C["panel"]
            fc = C["glow"] if active else C["muted"]

            card = tk.Frame(self._plist, bg=bg, cursor="hand2")
            card.pack(fill="x", pady=2)

            bar = tk.Frame(card, bg=fc if active else bg, width=3)
            bar.pack(side="left", fill="y")

            cvs = tk.Canvas(card, width=38, height=38, bg=bg, highlightthickness=0)
            cvs.pack(side="left", padx=8, pady=6)
            cvs.create_text(19, 19, text=p.get("icon", "⛏"),
                            font=("Segoe UI", 18), fill=fc)

            inf = tk.Frame(card, bg=bg)
            inf.pack(side="left", fill="both", expand=True)
            tk.Label(inf, text=p["name"], font=("Segoe UI", 10, "bold"),
                     bg=bg, fg=C["white"] if active else C["text"]).pack(anchor="w")
            tk.Label(inf, text=f"{p['version']}  ·  {p['loader']}",
                     font=("Consolas", 8), bg=bg, fg=C["muted"]).pack(anchor="w")
            inst_c = C["green"] if is_installed(p["version"]) else C["gold"]
            inst_t = "● Installed" if is_installed(p["version"]) else "○ Not installed"
            tk.Label(inf, text=inst_t, font=("Consolas", 7),
                     bg=bg, fg=inst_c).pack(anchor="w")

            def sel(e=None, idx=i):
                self.cur = idx
                self._reload_profile_list()
                self._refresh_home_card()
                self._build_ped()

            card.bind("<Button-1>", sel)
            for w in card.winfo_children():
                w.bind("<Button-1>", sel)
                for ww in w.winfo_children():
                    ww.bind("<Button-1>", sel)

    def _build_ped(self):
        pe = self._ped
        for w in pe.winfo_children(): w.destroy()
        if not self.profiles: return
        p = self.profiles[self.cur]

        canvas = tk.Canvas(pe, bg=C["bg"], highlightthickness=0)
        vsb    = ttk.Scrollbar(pe, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        inner = tk.Frame(canvas, bg=C["bg"])
        cw = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(cw, width=e.width))

        self._pf = {}

        def sec(t):
            row = tk.Frame(inner, bg=C["bg"])
            row.pack(fill="x", pady=(14, 3))
            tk.Label(row, text=t, font=("Consolas", 8, "bold"),
                     bg=C["bg"], fg=C["glow"]).pack(side="left")
            tk.Frame(row, bg=C["border"], height=1).pack(
                side="left", fill="x", expand=True, padx=(10, 0), pady=4)

        def fld(lbl, key, default="", opts=None, browse=False):
            row = tk.Frame(inner, bg=C["bg"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=lbl, font=("Segoe UI", 9),
                     bg=C["bg"], fg=C["muted"], width=22, anchor="w").pack(side="left")
            v = tk.StringVar(value=str(p.get(key, default)))
            self._pf[key] = v
            if opts is not None:
                cb = ttk.Combobox(row, textvariable=v, values=opts,
                                  state="readonly", width=26,
                                  style="Dark.TCombobox")
                cb.pack(side="left")
            else:
                ent = tk.Entry(row, textvariable=v,
                               bg=C["card2"], fg=C["text"],
                               insertbackground=C["glow"],
                               relief="flat", bd=0,
                               font=("Consolas", 9), width=34)
                ent.pack(side="left", ipady=6, ipadx=8)
                if browse:
                    tk.Button(row, text="📁", bg=C["card2"], fg=C["muted"],
                              relief="flat", cursor="hand2", padx=6,
                              command=lambda vr=v: self._browse_dir(vr)
                              ).pack(side="left", padx=4)

        # Icon + name
        head = tk.Frame(inner, bg=C["card"], padx=20, pady=18)
        head.pack(fill="x", pady=(0, 8))

        self._pf_icon = tk.StringVar(value=p.get("icon", "⛏"))
        icf = tk.Frame(head, bg=C["card"])
        icf.pack(side="left", padx=(0, 18))
        ICONS = ["⛏", "🗡", "🛡", "🏹", "🪄", "⚗",
                 "🌍", "🔥", "❄", "⭐", "🧱", "🌲",
                 "💎", "🐉", "🦊"]
        for idx, ico in enumerate(ICONS):
            r, c = divmod(idx, 5)
            sel_bg = C["card2"] if ico == p.get("icon", "⛏") else C["card"]
            btn = tk.Label(icf, text=ico, font=("Segoe UI", 15),
                           bg=sel_bg, cursor="hand2", padx=3, pady=2)
            btn.grid(row=r, column=c, padx=1, pady=1)
            def pick(e=None, i=ico, b=btn):
                self._pf_icon.set(i)
                for ch in icf.winfo_children(): ch.config(bg=C["card"])
                b.config(bg=C["card2"])
            btn.bind("<Button-1>", pick)

        nf = tk.Frame(head, bg=C["card"])
        nf.pack(side="left", fill="both", expand=True)
        tk.Label(nf, text="PROFILE NAME", font=("Consolas", 8, "bold"),
                 bg=C["card"], fg=C["muted"]).pack(anchor="w")
        self._pf["name"] = tk.StringVar(value=p["name"])
        tk.Entry(nf, textvariable=self._pf["name"],
                 bg=C["card2"], fg=C["white"],
                 insertbackground=C["glow"],
                 relief="flat", bd=0,
                 font=("Segoe UI", 16, "bold")).pack(fill="x", ipady=6)

        # Version dropdown: show installed first
        inst_vers = [v["id"] for v in self.mc_versions if is_installed(v["id"])]
        all_rel   = [v["id"] for v in self.mc_versions if v.get("type") == "release"]
        ver_opts  = inst_vers or all_rel or [p["version"]]

        sec("VERSION & LOADER")
        fld("Minecraft Version", "version", p["version"], opts=ver_opts)
        fld("Mod Loader",        "loader",  p["loader"],  opts=LOADERS)
        fld("Loader Version",    "loader_version", p.get("loader_version", ""))

        sec("JAVA")
        fld("Java Path  (auto = detect)", "java_path", p.get("java_path", "auto"))
        fld("JVM Arguments", "jvm_args", p.get("jvm_args", "-Xmx2G -Xms512M"))

        sec("GAME SETTINGS")
        fld("Game Directory", "game_dir",
            p.get("game_dir", str(INST_DIR / p["name"])), browse=True)
        fld("Resolution Width",  "resolution_width",  p.get("resolution_width", "854"))
        fld("Resolution Height", "resolution_height", p.get("resolution_height", "480"))

        brow = tk.Frame(inner, bg=C["bg"])
        brow.pack(fill="x", pady=18)

        def save():
            for k, v in self._pf.items():
                self.profiles[self.cur][k] = v.get()
            self.profiles[self.cur]["icon"] = self._pf_icon.get()
            save_json(PROFILES_FILE, self.profiles)
            self._reload_profile_list()
            self._refresh_home_card()
            self._log(f"Profile '{self.profiles[self.cur]['name']}' saved.", "success")
            messagebox.showinfo("Saved", "Profile saved!")

        tk.Button(brow, text="💾 Save",
                  font=("Segoe UI", 10, "bold"),
                  bg=C["glow"], fg="#000", relief="flat",
                  cursor="hand2", padx=20, pady=10,
                  command=save).pack(side="left", padx=(0, 8))
        tk.Button(brow, text="⎘ Duplicate",
                  font=("Segoe UI", 10), bg=C["card2"], fg=C["text"],
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  command=self._dup_profile).pack(side="left", padx=(0, 8))
        tk.Button(brow, text="🗑 Delete",
                  font=("Segoe UI", 10), bg=C["card2"], fg=C["red"],
                  relief="flat", cursor="hand2", padx=14, pady=10,
                  command=self._del_profile).pack(side="left")

    # ═══════════════════════════════════════════════════════════════════════
    #  MODS PAGE
    # ═══════════════════════════════════════════════════════════════════════
    def _mk_mods(self):
        f = tk.Frame(self._main, bg=C["bg"])
        self._hdr(f, "❖  Mod Manager",
                  "Install .jar mods — auto-copied to instance on launch")

        body = tk.Frame(f, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=28, pady=0)

        abar = tk.Frame(body, bg=C["bg"])
        abar.pack(fill="x", pady=(0, 10))
        for t, bg, fg, cmd in [
            ("📁  Install .jar",  C["glow"],  "#000",    self._install_mods),
            ("📂 Mods Library",   C["card2"], C["text"], self._open_mods_lib),
            ("✅ Enable All",     C["card2"], C["green"], self._mods_enable_all),
            ("❌ Disable All",    C["card2"], C["muted"], self._mods_disable_all),
        ]:
            tk.Button(abar, text=t, font=("Segoe UI", 10),
                      bg=bg, fg=fg, relief="flat", cursor="hand2",
                      padx=14, pady=8, command=cmd
                      ).pack(side="left", padx=(0, 8))

        dh = tk.Frame(body, bg=C["card2"], height=50)
        dh.pack(fill="x", pady=(0, 10))
        dh.pack_propagate(False)
        tk.Label(dh,
                 text="⬇  Use 'Install .jar' to add mods  ·  "
                      "Mods auto-copied to instance/mods/ on launch",
                 font=("Segoe UI", 9), bg=C["card2"], fg=C["muted"]
                 ).pack(expand=True)

        info = tk.Frame(body, bg=C["bg"])
        info.pack(fill="x", pady=(0, 6))
        tk.Label(info, text="PROFILE:", font=("Consolas", 8, "bold"),
                 bg=C["bg"], fg=C["muted"]).pack(side="left")
        self._mods_plbl = tk.Label(info, text="",
                                   font=("Consolas", 9), bg=C["bg"], fg=C["glow"])
        self._mods_plbl.pack(side="left", padx=8)
        self._mods_clbl = tk.Label(info, text="",
                                   font=("Consolas", 9), bg=C["bg"], fg=C["muted"])
        self._mods_clbl.pack(side="left")

        tf = tk.Frame(body, bg=C["bg"])
        tf.pack(fill="both", expand=True)

        cols = ("st", "Name", "Compat", "Size", "Status")
        self._mtree = ttk.Treeview(tf, columns=cols, show="headings",
                                   style="TV.Treeview", selectmode="browse")
        self._mtree.heading("st",     text="")
        self._mtree.heading("Name",   text="Mod Name")
        self._mtree.heading("Compat", text="Loader")
        self._mtree.heading("Size",   text="Size")
        self._mtree.heading("Status", text="Status")
        self._mtree.column("st",     width=30,  stretch=False, anchor="center")
        self._mtree.column("Name",   width=340, anchor="w")
        self._mtree.column("Compat", width=140, anchor="w")
        self._mtree.column("Size",   width=90,  anchor="w")
        self._mtree.column("Status", width=100, anchor="w")
        self._mtree.tag_configure("on",  foreground=C["text"])
        self._mtree.tag_configure("off", foreground=C["muted"])

        vsb = ttk.Scrollbar(tf, orient="vertical", command=self._mtree.yview)
        self._mtree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._mtree.pack(fill="both", expand=True)

        bot = tk.Frame(body, bg=C["bg"])
        bot.pack(fill="x", pady=8)
        tk.Button(bot, text="🗑 Remove Selected",
                  font=("Segoe UI", 10), bg=C["card2"], fg=C["red"],
                  relief="flat", cursor="hand2", padx=14, pady=8,
                  command=self._remove_mod).pack(side="left")

        self._refresh_mods()
        return f

    def _refresh_mods(self):
        if not hasattr(self, "_mtree"): return
        for r in self._mtree.get_children(): self._mtree.delete(r)
        if not self.profiles: return
        p = self.profiles[self.cur]
        mods = p.get("mods", [])
        if hasattr(self, "_mods_plbl"):
            self._mods_plbl.config(text=p["name"])
        en = sum(1 for m in mods if m.get("enabled", True))
        if hasattr(self, "_mods_clbl"):
            self._mods_clbl.config(text=f"{len(mods)} total  ·  {en} enabled")
        for mod in mods:
            path = Path(mod.get("path", ""))
            size = fmt_bytes(path.stat().st_size) if path.exists() else "Missing"
            enabled = mod.get("enabled", True)
            icon    = "🟢" if enabled else "🔴"
            status  = "Enabled" if enabled else "Disabled"
            tag     = "on" if enabled else "off"
            self._mtree.insert("", "end",
                               values=(icon, mod.get("name", ""),
                                       mod.get("loader_compat", "?"),
                                       size, status),
                               tags=(tag,))

    def _detect_mod_loader(self, p):
        try:
            with zipfile.ZipFile(p, "r") as z:
                names = z.namelist()
                if "fabric.mod.json" in names:
                    return "Fabric"
                if any("mods.toml" in n for n in names):
                    return "Forge/NeoForge"
                if "quilt.mod.json" in names:
                    return "Quilt"
        except Exception:
            pass
        return "Universal"

    def _install_mods(self):
        files = filedialog.askopenfilenames(
            title="Select Mod Files",
            filetypes=[("Minecraft Mods", "*.jar *.zip"),
                       ("All Files", "*.*")])
        if not files: return
        if not self.profiles:
            messagebox.showwarning("No Profile", "Create a profile first.")
            return
        p = self.profiles[self.cur]
        if "mods" not in p: p["mods"] = []
        n = 0
        for ff in files:
            src  = Path(ff)
            dest = MODS_LIB / src.name
            try:
                shutil.copy2(src, dest)
                if not any(m.get("name") == src.stem for m in p["mods"]):
                    p["mods"].append({
                        "name":          src.stem,
                        "path":          str(dest),
                        "enabled":       True,
                        "loader_compat": self._detect_mod_loader(dest),
                        "installed":     datetime.now().isoformat(),
                    })
                    n += 1
                    self._log(f"Installed: {src.name}", "success")
            except Exception as ex:
                self._log(f"Failed: {src.name} — {ex}", "error")
        save_json(PROFILES_FILE, self.profiles)
        self._refresh_mods()
        self._update_stats()
        if n: messagebox.showinfo("Done", f"{n} mod(s) installed!")

    def _remove_mod(self):
        sel = self._mtree.selection()
        if not sel: return
        idx = self._mtree.index(sel[0])
        p = self.profiles[self.cur]
        if 0 <= idx < len(p.get("mods", [])):
            removed = p["mods"].pop(idx)
            save_json(PROFILES_FILE, self.profiles)
            self._refresh_mods()
            self._log(f"Removed: {removed.get('name')}", "warn")

    def _mods_enable_all(self):
        for m in self.profiles[self.cur].get("mods", []):
            m["enabled"] = True
        save_json(PROFILES_FILE, self.profiles)
        self._refresh_mods()

    def _mods_disable_all(self):
        for m in self.profiles[self.cur].get("mods", []):
            m["enabled"] = False
        save_json(PROFILES_FILE, self.profiles)
        self._refresh_mods()

    def _open_mods_lib(self):
        self._open_dir(MODS_LIB)

    # ═══════════════════════════════════════════════════════════════════════
    #  SETTINGS PAGE
    # ═══════════════════════════════════════════════════════════════════════
    def _mk_settings(self):
        f = tk.Frame(self._main, bg=C["bg"])
        self._hdr(f, "⚙  Settings", "Launcher preferences")

        canvas = tk.Canvas(f, bg=C["bg"], highlightthickness=0)
        vsb    = ttk.Scrollbar(f, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)

        inner = tk.Frame(canvas, bg=C["bg"])
        cw = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(cw, width=e.width))

        self._sf = {}

        def sec(t):
            row = tk.Frame(inner, bg=C["bg"])
            row.pack(fill="x", padx=28, pady=(16, 3))
            tk.Label(row, text=t, font=("Consolas", 8, "bold"),
                     bg=C["bg"], fg=C["glow"]).pack(side="left")
            tk.Frame(row, bg=C["border"], height=1).pack(
                side="left", fill="x", expand=True, padx=(10, 0), pady=4)

        def sfld(lbl, key, default="", is_bool=False):
            row = tk.Frame(inner, bg=C["bg"])
            row.pack(fill="x", padx=28, pady=4)
            tk.Label(row, text=lbl, font=("Segoe UI", 10),
                     bg=C["bg"], fg=C["muted"], width=30, anchor="w").pack(side="left")
            val = self.settings.get(key, default)
            if is_bool:
                v = tk.BooleanVar(value=bool(val))
                tk.Checkbutton(row, variable=v, bg=C["bg"],
                               activebackground=C["bg"],
                               selectcolor=C["card2"],
                               command=lambda k=key, vr=v:
                               self.settings.update({k: vr.get()})
                               ).pack(side="left")
                self._sf[key] = v
            else:
                v = tk.StringVar(value=str(val))
                ent = tk.Entry(row, textvariable=v,
                               bg=C["card2"], fg=C["text"],
                               insertbackground=C["glow"],
                               relief="flat", bd=0,
                               font=("Consolas", 9), width=38)
                ent.pack(side="left", ipady=6, ipadx=8)
                v.trace_add("write",
                            lambda *a, k=key, vr=v:
                            self.settings.update({k: vr.get()}))
                self._sf[key] = v

        sec("ACCOUNT")
        sfld("Username",                       "username", "Player")
        sfld("UUID  (blank = auto-generate)",  "uuid",     "")

        sec("JAVA")
        sfld("Java Executable  (auto = detect)", "java_path", "auto")

        sec("LAUNCHER")
        sfld("Close launcher when game starts", "close_on_launch", False, is_bool=True)

        sec("JAVA AUTO-DETECT")
        jrow = tk.Frame(inner, bg=C["bg"])
        jrow.pack(fill="x", padx=28, pady=8)
        self._jdet_lbl = tk.Label(jrow,
                                  text="Press Auto-Detect to locate Java",
                                  font=("Consolas", 9),
                                  bg=C["bg"], fg=C["muted"])
        self._jdet_lbl.pack(side="left")
        tk.Button(jrow, text="🔍 Auto-Detect",
                  font=("Segoe UI", 10), bg=C["card2"], fg=C["muted"],
                  relief="flat", cursor="hand2", padx=14, pady=6,
                  command=self._run_java_check).pack(side="left", padx=14)

        sec("DATA LOCATIONS")
        for lbl, path in [
            ("Minecraft versions / libraries:", MC_DIR),
            ("Game instances:", INST_DIR),
            ("Mods library:", MODS_LIB),
        ]:
            row = tk.Frame(inner, bg=C["bg"])
            row.pack(fill="x", padx=28, pady=2)
            tk.Label(row, text=lbl, font=("Segoe UI", 9),
                     bg=C["bg"], fg=C["muted"], width=30, anchor="w").pack(side="left")
            tk.Label(row, text=str(path), font=("Consolas", 8),
                     bg=C["bg"], fg=C["dim"]).pack(side="left")

        tk.Button(inner, text="💾 Save Settings",
                  font=("Segoe UI", 11, "bold"),
                  bg=C["glow"], fg="#000", relief="flat",
                  cursor="hand2", padx=24, pady=12,
                  command=self._save_settings
                  ).pack(padx=28, pady=24, anchor="w")

        return f

    def _save_settings(self):
        save_json(SETTINGS_FILE, self.settings)
        self._log("Settings saved.", "success")
        messagebox.showinfo("Saved", "Settings saved!")

    # ═══════════════════════════════════════════════════════════════════════
    #  CONSOLE PAGE
    # ═══════════════════════════════════════════════════════════════════════
    def _mk_console(self):
        f = tk.Frame(self._main, bg=C["bg"])

        hdr = tk.Frame(f, bg=C["bg"])
        hdr.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(hdr, text="▶  Console",
                 font=("Segoe UI", 18, "bold"),
                 bg=C["bg"], fg=C["white"]).pack(side="left")
        tk.Button(hdr, text="Clear",
                  font=("Segoe UI", 9), bg=C["card2"], fg=C["muted"],
                  relief="flat", cursor="hand2", padx=12, pady=4,
                  command=self._clear_console).pack(side="right")

        self._console = tk.Text(
            f, bg="#040810", fg="#b0c8e0",
            insertbackground=C["glow"],
            font=("Consolas", 9), relief="flat",
            wrap="word", state="disabled",
            selectbackground=C["card2"],
            padx=14, pady=12)
        self._console.pack(fill="both", expand=True, padx=28, pady=(0, 18))

        self._console.tag_config("info",    foreground="#00c8ff")
        self._console.tag_config("success", foreground="#00ff88")
        self._console.tag_config("warn",    foreground="#fbbf24")
        self._console.tag_config("error",   foreground="#ff4444")
        self._console.tag_config("dim",     foreground="#1e3456")
        self._console.tag_config("ts",      foreground="#0e2040")
        self._console.tag_config("game",    foreground="#5a8060")

        return f

    def _log(self, msg, level="info"):
        def _do():
            if not hasattr(self, "_console"): return
            self._console.configure(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            self._console.insert("end", f"[{ts}] ", "ts")
            self._console.insert("end", f"{msg}\n", level)
            self._console.configure(state="disabled")
            self._console.see("end")
        self.root.after(0, _do)

    def _clear_console(self):
        self._console.configure(state="normal")
        self._console.delete("1.0", "end")
        self._console.configure(state="disabled")

    # ═══════════════════════════════════════════════════════════════════════
    #  LAUNCH
    # ═══════════════════════════════════════════════════════════════════════
    def _do_launch(self):
        if not self.profiles:
            messagebox.showwarning("No Profile", "Create a profile first.")
            return

        p   = self.profiles[self.cur]
        ver = p["version"]

        if not is_installed(ver):
            if messagebox.askyesno(
                    "Not Installed",
                    f"Minecraft {ver} is not installed yet.\n\n"
                    "Go to the Install tab to download it.\n\n"
                    "Open Install tab now?"):
                self._nav("install")
            return

        # Resolve java — check required version for this MC version
        required_java = get_required_java_version(ver)
        jp = p.get("java_path") or self.settings.get("java_path") or "auto"
        if jp in ("", "auto"):
            jp, jv = find_java(min_version=required_java)
            if not jp:
                messagebox.showerror(
                    f"Java {required_java}+ Required",
                    f"Minecraft {ver} requires Java {required_java} or newer,\n"
                    f"but no suitable Java was found on your system.\n\n"
                    f"Download Java {required_java}+ from:\n"
                    f"https://adoptium.net/temurin/releases/?version={required_java}\n\n"
                    f"After installing, relaunch CraftLaunch.")
                return
            # Warn if found java is too old
            found_major = java_major(jv)
            if found_major < required_java:
                if not messagebox.askyesno(
                    "Wrong Java Version",
                    f"Minecraft {ver} requires Java {required_java}+\n"
                    f"but only Java {found_major} ({jv}) was found.\n\n"
                    f"The game will likely crash.\n\n"
                    f"Download Java {required_java} from:\n"
                    f"https://adoptium.net/temurin/releases/?version={required_java}\n\n"
                    f"Launch anyway?"):
                    return
        else:
            jv = "custom"

        username = (self.settings.get("username") or "Player").strip() or "Player"
        uid      = (self.settings.get("uuid") or "").strip() or str(uuid.uuid4())

        self._log(f"═══ Launching  {p['name']}  (MC {ver}) ═══", "success")
        self._nav("console")
        self._launch_btn.config(state="disabled", text="⏳  Launching…")
        self._set_status("Preparing…")

        def run():
            try:
                self._set_status("Deploying mods…")
                self._deploy_mods(p)

                self._set_status("Building launch command…")
                cmd = build_launch_command(ver, p, username, uid)
                self._log(f"Java:     {jp}", "info")
                self._log(f"Version:  {ver}", "info")
                self._log(f"Username: {username}", "info")
                self._log(f"Args ({len(cmd)} total) built successfully", "dim")

                self._set_status("Starting Minecraft…")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
                self._game_proc = proc

                self._log("✓ Minecraft is running!", "success")
                self._set_status("Game running ▶")
                self.root.after(0, lambda: self._prog_var.set(100))

                if self.settings.get("close_on_launch"):
                    self.root.after(3000, self.root.quit)

                for line in proc.stdout:
                    stripped = line.rstrip()
                    if not stripped: continue
                    lvl = ("error" if any(x in stripped for x in
                                          ("ERROR", "Exception", "FATAL"))
                           else "warn" if "WARN" in stripped
                           else "game")
                    self._log(stripped, lvl)

                proc.wait()
                code = proc.returncode
                self._log(f"Game exited (code {code})",
                          "success" if code == 0 else "warn")
                self._set_status("Ready")
                self.root.after(0, lambda: self._prog_var.set(0))

            except Exception as ex:
                self._log(f"Launch failed: {ex}", "error")
                self._log(traceback.format_exc(), "dim")
                self._set_status("Error")
                msg = str(ex)
                self.root.after(0, lambda m=msg: messagebox.showerror(
                    "Launch Failed", m))
            finally:
                self.root.after(0, lambda: self._launch_btn.config(
                    state="normal", text="▶  LAUNCH"))

        threading.Thread(target=run, daemon=True).start()

    def _deploy_mods(self, profile):
        loader = profile.get("loader", "Vanilla")
        if loader in ("Vanilla", "OptiFine", ""):
            if profile.get("mods"):
                self._log(
                    "⚠  Mods are installed but loader is Vanilla — "
                    "mods will be ignored. Set loader to Fabric or Forge in Profiles.",
                    "warn")
            return

        game_dir  = Path(profile.get("game_dir") or INST_DIR / profile["name"])
        mods_dest = game_dir / "mods"
        mods_dest.mkdir(parents=True, exist_ok=True)

        # Build set of enabled mod filenames
        enabled = set()
        for mod in profile.get("mods", []):
            if mod.get("enabled", True):
                src = Path(mod.get("path", ""))
                if src.exists():
                    enabled.add(src.name)

        # Remove mods that are no longer enabled
        removed = 0
        for existing in mods_dest.iterdir():
            if existing.suffix in (".jar", ".zip") and existing.name not in enabled:
                existing.unlink()
                removed += 1

        # Copy new mods
        n = 0
        for mod in profile.get("mods", []):
            if not mod.get("enabled", True): continue
            src = Path(mod.get("path", ""))
            if src.exists():
                dest = mods_dest / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                    n += 1

        total = len(list(mods_dest.iterdir()))
        self._log(
            f"Mods folder: {total} active  (+{n} copied, -{removed} removed) → {mods_dest}",
            "success" if total > 0 else "warn")

    def _set_status(self, msg):
        self.root.after(0, lambda: self._status_lbl.config(text=msg))

    # ═══════════════════════════════════════════════════════════════════════
    #  BACKGROUND TASKS
    # ═══════════════════════════════════════════════════════════════════════
    def _bg_manifest(self):
        try:
            self._log("Fetching Mojang manifest…", "info")
            self.mc_versions = get_mc_versions()
            self._log(f"Manifest loaded — {len(self.mc_versions)} versions available",
                      "success")
            self.root.after(0, self._fill_ver_tree)
            self.root.after(0, self._update_stats)
        except Exception as e:
            self._log(f"Manifest fetch failed: {e}", "warn")

    def _bg_java(self):
        path, ver = find_java()
        def _upd():
            if path:
                self._java_lbl.config(text=f"☕  Java {ver}", fg=C["green"])
                if hasattr(self, "_sc_java"):
                    self._sc_java._val.config(text=ver or "Found")
            else:
                self._java_lbl.config(text="☕  Java not found", fg=C["red"])
                if hasattr(self, "_sc_java"):
                    self._sc_java._val.config(text="Not Found", fg=C["red"])
        self.root.after(0, _upd)

    def _run_java_check(self):
        def _check():
            path, ver = find_java()
            def _upd():
                if path:
                    self._java_lbl.config(text=f"☕  Java {ver}", fg=C["green"])
                    if hasattr(self, "_jdet_lbl"):
                        self._jdet_lbl.config(
                            text=f"Found: {path}   ({ver})", fg=C["green"])
                    if hasattr(self, "_sc_java"):
                        self._sc_java._val.config(text=ver or "OK")
                    self._log(f"Java: {path}  version {ver}", "success")
                    messagebox.showinfo("Java Found",
                                        f"Java {ver} detected!\n\nPath: {path}")
                else:
                    if hasattr(self, "_jdet_lbl"):
                        self._jdet_lbl.config(
                            text="Java not found — install from adoptium.net",
                            fg=C["red"])
                    self._log("Java not found!", "error")
                    messagebox.showerror("Java Not Found",
                                         "Java not detected.\n\n"
                                         "Download from: https://adoptium.net")
            self.root.after(0, _upd)
        threading.Thread(target=_check, daemon=True).start()

    # ═══════════════════════════════════════════════════════════════════════
    #  PROFILE HELPERS
    # ═══════════════════════════════════════════════════════════════════════
    def _new_profile_dlg(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("New Profile")
        dlg.geometry("400x240")
        dlg.configure(bg=C["bg"])
        dlg.grab_set()

        tk.Label(dlg, text="New Profile",
                 font=("Segoe UI", 15, "bold"),
                 bg=C["bg"], fg=C["white"]).pack(padx=24, pady=(22, 10), anchor="w")

        name_v = tk.StringVar(value="My World")
        ver_v  = tk.StringVar(value="1.20.4")

        for lbl, v in [("Profile Name", name_v), ("MC Version", ver_v)]:
            row = tk.Frame(dlg, bg=C["bg"])
            row.pack(fill="x", padx=24, pady=5)
            tk.Label(row, text=lbl, font=("Segoe UI", 9),
                     bg=C["bg"], fg=C["muted"], width=14, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=v,
                     bg=C["card2"], fg=C["text"],
                     insertbackground=C["glow"],
                     relief="flat", bd=0,
                     font=("Segoe UI", 11), width=24
                     ).pack(side="left", ipady=6, ipadx=8)

        def create():
            np = self._dflt_profile()
            np["name"]    = name_v.get().strip() or "Profile"
            np["version"] = ver_v.get().strip() or "1.20.4"
            self.profiles.append(np)
            save_json(PROFILES_FILE, self.profiles)
            self.cur = len(self.profiles) - 1
            self._reload_profile_list()
            self._refresh_home_card()
            self._build_ped()
            self._update_stats()
            dlg.destroy()

        tk.Button(dlg, text="✓  Create Profile",
                  font=("Segoe UI", 11, "bold"),
                  bg=C["glow"], fg="#000", relief="flat",
                  cursor="hand2", padx=24, pady=10,
                  command=create).pack(padx=24, pady=18, anchor="w")

    def _dup_profile(self):
        import copy
        p = copy.deepcopy(self.profiles[self.cur])
        p["name"] += " (Copy)"
        self.profiles.append(p)
        save_json(PROFILES_FILE, self.profiles)
        self._reload_profile_list()

    def _del_profile(self):
        if len(self.profiles) <= 1:
            messagebox.showwarning("Cannot Delete",
                                   "Must have at least one profile.")
            return
        if messagebox.askyesno("Delete",
                               f"Delete '{self.profiles[self.cur]['name']}'?"):
            self.profiles.pop(self.cur)
            self.cur = max(0, self.cur - 1)
            save_json(PROFILES_FILE, self.profiles)
            self._reload_profile_list()
            self._refresh_home_card()
            self._build_ped()
            self._update_stats()

    # ═══════════════════════════════════════════════════════════════════════
    #  MISC HELPERS
    # ═══════════════════════════════════════════════════════════════════════
    def _hdr(self, parent, title, subtitle=""):
        h = tk.Frame(parent, bg=C["panel"], pady=22)
        h.pack(fill="x")
        tk.Frame(h, bg=C["glow"], width=5).pack(side="left", fill="y")
        tf = tk.Frame(h, bg=C["panel"])
        tf.pack(side="left", padx=22)
        tk.Label(tf, text=title, font=("Segoe UI", 18, "bold"),
                 bg=C["panel"], fg=C["white"]).pack(anchor="w")
        if subtitle:
            tk.Label(tf, text=subtitle, font=("Segoe UI", 9),
                     bg=C["panel"], fg=C["muted"]).pack(anchor="w")

    def _update_stats(self):
        if hasattr(self, "_sc_profiles"):
            self._sc_profiles._val.config(text=str(len(self.profiles)))
        if hasattr(self, "_sc_installed"):
            self._sc_installed._val.config(text=self._cnt_installed())
        if hasattr(self, "_sc_mods"):
            self._sc_mods._val.config(text=self._cnt_mods())

    def _cnt_installed(self):
        try:
            return str(len([
                d for d in MC_DIR.joinpath("versions").iterdir()
                if d.is_dir() and (d / (d.name + ".jar")).exists()
            ]))
        except Exception:
            return "0"

    def _cnt_mods(self):
        return str(sum(len(p.get("mods", [])) for p in self.profiles))

    def _browse_dir(self, var):
        d = filedialog.askdirectory()
        if d: var.set(d)

    def _open_mc_dir(self):
        MC_DIR.mkdir(parents=True, exist_ok=True)
        self._open_dir(MC_DIR)

    def _open_screenshots(self):
        sd = MC_DIR / "screenshots"
        sd.mkdir(parents=True, exist_ok=True)
        self._open_dir(sd)

    def _open_dir(self, path):
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # ── run ───────────────────────────────────────────────────────────────
    def run(self):
        self._log(f"{APP} v{VER} started", "success")
        self._log(f"OS: {platform.system()} {platform.machine()}", "info")
        self._log(f"MC data: {MC_DIR}", "info")
        self._log("Library: minecraft-launcher-lib ✓", "info")
        self._log("Tip: Go to ⬇ Install tab to download a Minecraft version first.", "info")
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    CraftLaunch().run()
