# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Editor settings, game installation detection and per-installation backups.

The editor works on an installed copy of Tank Battle Classic (Steam). The game
folder is chosen by the user (File > Select game folder) or detected in the
Steam libraries, and remembered in settings.json.

Settings and backups (the data folder) live next to the program: the packaged
executable keeps settings.json and backups/ in its own folder, so the editor is
portable and its backups are easy to find. When that folder cannot be written
(for example under Program Files) they live in %LOCALAPPDATA%/
TankBattleMissionEditor instead. The source version uses the folder named in
data_location.txt next to the source files (the folder of the executable in
use, so both see the same scenarios), else %LOCALAPPDATA%. TBME_DATA_DIR
overrides all. Versions up to 1.14 kept the data in %LOCALAPPDATA%;
move_appdata_store() moves it into the new data folder once. Older source
versions kept it next to the source files; merge_source_store() moves that
store into the data folder once.

Backups, the scenario list and the object library are kept per installation in
backups/<folder name>-<hash>/, so several installations never mix.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, "frozen", False)
PROGRAM_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE
APPDATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TankBattleMissionEditor")
LOCATION_FILE = os.path.join(HERE, "data_location.txt")
SOURCE_STORE = os.path.join(HERE, "backups")
if FROZEN:
    _OLD_DATA_DIR = os.path.join(os.path.dirname(APPDATA_DIR), "TankBattleMapEditor")
    if os.path.isdir(_OLD_DATA_DIR) and not os.path.exists(APPDATA_DIR):
        try:
            os.rename(_OLD_DATA_DIR, APPDATA_DIR)
        except OSError:
            pass


def writable(folder):
    """True when files can be created in `folder` (made if missing)."""
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, f".write-test-{os.getpid()}")
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def choose_data_dir():
    """(data folder, why) - see the module description."""
    env = os.environ.get("TBME_DATA_DIR")
    if env:
        return env, "environment"
    if FROZEN:
        if writable(PROGRAM_DIR):
            return PROGRAM_DIR, "program"
        return APPDATA_DIR, "read-only program folder"
    try:
        with open(LOCATION_FILE, "r", encoding="utf-8") as f:
            named = f.read().strip()
    except OSError:
        named = ""
    if named:
        return named, "data_location.txt"
    return APPDATA_DIR, "default"


DATA_DIR, DATA_REASON = choose_data_dir()
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
BACKUP_ROOT = os.path.join(DATA_DIR, "backups")
GAME_NAME = "Tank Battle Classic"
LEGACY_FILES = ("scenarios.json", "object_library.json")


def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def validate_game(path):
    """(True, data folder) for a Tank Battle Classic installation, else (False, reason)."""
    if not path or not os.path.isdir(path):
        return False, "The folder does not exist."
    if not os.path.isfile(os.path.join(path, "GameAssembly.dll")):
        return False, "GameAssembly.dll is missing; choose the folder that contains the game's .exe."
    data = [d for d in os.listdir(path) if d.endswith("_Data") and os.path.isdir(os.path.join(path, d))]
    if not data:
        return False, "The game's _Data folder is missing."
    dd = os.path.join(path, data[0])
    for rel in ("globalgamemanagers", "globalgamemanagers.assets", "sharedassets3.assets",
                os.path.join("il2cpp_data", "Metadata", "global-metadata.dat")):
        if not os.path.exists(os.path.join(dd, rel)):
            return False, f"{rel} is missing; the installation is incomplete."
    try:
        with open(os.path.join(dd, "app.info"), "r", encoding="utf-8", errors="replace") as f:
            info = f.read()
    except OSError:
        info = ""
    if GAME_NAME.lower() not in info.lower():
        return False, f"This is not a {GAME_NAME} installation (app.info does not name the game)."
    return True, dd


def _steam_roots():
    roots = []
    try:
        import winreg
        for hive, key, value in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                 (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(os.path.normpath(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    except ImportError:
        pass
    roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    out = []
    for r in roots:
        if os.path.isdir(r) and r.lower() not in (o.lower() for o in out):
            out.append(r)
    return out


def _steam_libraries():
    libs = []
    for root in _steam_roots():
        libs.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libs.append(os.path.normpath(m.group(1).replace("\\\\", "\\")))
    out = []
    for lib in libs:
        if os.path.isdir(lib) and lib.lower() not in (o.lower() for o in out):
            out.append(lib)
    return out


def detect_game():
    """Installed game folders found in the Steam libraries."""
    found = []
    for lib in _steam_libraries():
        apps = os.path.join(lib, "steamapps")
        candidates = [os.path.join(apps, "common", GAME_NAME)]
        try:
            for f in os.listdir(apps):
                if f.startswith("appmanifest_") and f.endswith(".acf"):
                    with open(os.path.join(apps, f), "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                    if re.search(r'"name"\s+"%s"' % re.escape(GAME_NAME), text, re.I):
                        m = re.search(r'"installdir"\s+"([^"]+)"', text)
                        if m:
                            candidates.append(os.path.join(apps, "common", m.group(1)))
        except OSError:
            pass
        for c in candidates:
            if validate_game(c)[0] and c.lower() not in (x.lower() for x in found):
                found.append(c)
    return found


def game_programs(game_root):
    """File names of the game's program(s) in its folder (not crash handlers)."""
    try:
        return [f for f in os.listdir(game_root) if f.lower().endswith(".exe") and "crash" not in f.lower()]
    except OSError:
        return []


def game_running(game_root):
    """True when the game's program is running (Windows task list)."""
    import subprocess
    names = {n.lower() for n in game_programs(game_root)}
    if not names or sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    running = {line.split('","')[0].strip('"').lower() for line in out.splitlines() if line.startswith('"')}
    return bool(names & running)


def steam_app_id(game_root):
    """Steam app id of an installed game folder (from the appmanifest of its
    Steam library), or None when the folder is not in a Steam library."""
    common = os.path.dirname(os.path.abspath(game_root))
    apps = os.path.dirname(common)
    if os.path.basename(common).lower() != "common" or not os.path.isdir(apps):
        return None
    folder = os.path.basename(os.path.abspath(game_root)).lower()
    for f in os.listdir(apps):
        if f.startswith("appmanifest_") and f.endswith(".acf"):
            try:
                with open(os.path.join(apps, f), "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            m = re.search(r'"installdir"\s+"([^"]+)"', text)
            a = re.search(r'"appid"\s+"(\d+)"', text)
            if m and a and m.group(1).lower() == folder:
                return a.group(1)
    return None


def backup_dir_for(game_root):
    norm = os.path.normcase(os.path.abspath(game_root))
    tag = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    name = re.sub(r"[^A-Za-z0-9]+", "_", os.path.basename(norm.rstrip("\\/")) or "game").strip("_")
    return os.path.join(BACKUP_ROOT, f"{name}-{tag}")


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _merge_manifests(dst, src):
    """Scenario manifest holding the scenarios of both (dst wins on equal ids)."""
    out = dict(dst)
    for key in ("scenarios", "orphaned"):
        have = {s["scene_id"] for s in dst.get(key, [])}
        out[key] = list(dst.get(key, [])) + [s for s in src.get(key, []) if s["scene_id"] not in have]
    out["scenarios"].sort(key=lambda s: s.get("battle_index", 0))
    out["pages"] = list(dict.fromkeys(list(dst.get("pages", [])) + list(src.get("pages", []))))
    bases = [b for b in (dst.get("base_scene_count"), src.get("base_scene_count")) if b is not None]
    out["base_scene_count"] = min(bases) if bases else None
    return out


def _merge_installation(src, dst):
    """Merge one installation's store from the source version (src) into the
    shared store (dst)."""
    if not os.path.isdir(dst):
        shutil.copytree(src, dst)
        return
    m_src = _load_json(os.path.join(src, "scenarios.json"), {})
    m_dst = _load_json(os.path.join(dst, "scenarios.json"), {})
    # the shared game files of the copy that changed the game first are the true originals
    src_shared, dst_shared = os.path.join(src, "shared"), os.path.join(dst, "shared")
    src_base, dst_base = m_src.get("base_scene_count"), m_dst.get("base_scene_count")
    if os.path.isdir(src_shared) and os.listdir(src_shared) and (
            not os.path.isdir(dst_shared) or not os.listdir(dst_shared)
            or (src_base is not None and (dst_base is None or src_base < dst_base))):
        shutil.rmtree(dst_shared, ignore_errors=True)
        shutil.copytree(src_shared, dst_shared)
    if m_src or m_dst:
        merged = _merge_manifests(m_dst, m_src)
        with open(os.path.join(dst, "scenarios.json"), "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
    for name in os.listdir(src):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if re.fullmatch(r"level\d+\.orig", name):
            if not os.path.exists(d) or os.path.getmtime(s) < os.path.getmtime(d):
                shutil.copy2(s, d)
        elif name in ("object_library.json", "game.json"):
            if not os.path.exists(d):
                shutil.copy2(s, d)
        elif name.startswith("before-update-") and os.path.isdir(s):
            target = d if not os.path.exists(d) else d + "-source"
            if not os.path.exists(target):
                shutil.copytree(s, target)
        elif name == "scenarios" and os.path.isdir(s):
            os.makedirs(d, exist_ok=True)
            for sid in os.listdir(s):
                ss, dd = os.path.join(s, sid), os.path.join(d, sid)
                newer = _load_json(os.path.join(ss, "meta.json"), {}).get("saved", "") > \
                    _load_json(os.path.join(dd, "meta.json"), {}).get("saved", "")
                if not os.path.exists(dd) or newer:
                    shutil.rmtree(dd, ignore_errors=True)
                    shutil.copytree(ss, dd)
        elif name not in ("scenarios.json", "shared") and not os.path.exists(d):
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)


def move_appdata_store():
    """Move the data folder of versions up to 1.14 (%LOCALAPPDATA%) into the
    data folder, once: every installation's backups are merged (newest scenario
    backup and oldest original files win), then the old folder is removed.
    Returns the installation folders merged; nothing happens when the data
    folder is the old one."""
    if os.path.normcase(os.path.abspath(DATA_DIR)) == os.path.normcase(os.path.abspath(APPDATA_DIR)):
        return []
    if not os.path.isdir(APPDATA_DIR):
        return []
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    done = []
    for name in sorted(os.listdir(APPDATA_DIR)):
        src, dst = os.path.join(APPDATA_DIR, name), os.path.join(DATA_DIR, name)
        if name == "backups" and os.path.isdir(src):
            for inst in sorted(os.listdir(src)):
                s_inst = os.path.join(src, inst)
                if os.path.isdir(s_inst):
                    _merge_installation(s_inst, os.path.join(BACKUP_ROOT, inst))
                    done.append(inst)
                elif not os.path.exists(os.path.join(BACKUP_ROOT, inst)):
                    shutil.copy2(s_inst, os.path.join(BACKUP_ROOT, inst))
        elif not os.path.exists(dst):
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    shutil.rmtree(APPDATA_DIR)
    return done


def adopt_sibling_store():
    """Executable in a new folder without data of its own (for example a new
    version unpacked next to the old one): take over the settings and backups
    of the most recently used editor folder beside it (a copy; the old folder
    keeps its own). Returns that folder, or None."""
    if not FROZEN or DATA_REASON != "program" or os.path.isdir(BACKUP_ROOT):
        return None
    parent = os.path.dirname(PROGRAM_DIR)
    found = []
    try:
        names = os.listdir(parent)
    except OSError:
        return None
    for name in names:
        folder = os.path.join(parent, name)
        backups = os.path.join(folder, "backups")
        if (name.lower().startswith("tankbattle") and os.path.normcase(folder) != os.path.normcase(PROGRAM_DIR)
                and os.path.isdir(backups)):
            stamp = max([os.path.getmtime(backups)] + [os.path.getmtime(os.path.join(backups, n))
                                                       for n in os.listdir(backups)])
            found.append((stamp, folder))
    if not found:
        return None
    src = max(found)[1]
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    for inst in os.listdir(os.path.join(src, "backups")):
        s_inst = os.path.join(src, "backups", inst)
        if os.path.isdir(s_inst):
            _merge_installation(s_inst, os.path.join(BACKUP_ROOT, inst))
    if os.path.exists(os.path.join(src, "settings.json")) and not os.path.exists(SETTINGS_PATH):
        shutil.copy2(os.path.join(src, "settings.json"), SETTINGS_PATH)
    return src


def merge_source_store():
    """Merge the store of older source versions (backups/ next to the source
    files) into the shared store, once. The old store is then moved, unchanged,
    to legacy-source-backups-<date> in the shared data folder. Returns the
    installation folders merged."""
    if FROZEN or not os.path.isdir(SOURCE_STORE) or os.path.normcase(SOURCE_STORE) == os.path.normcase(BACKUP_ROOT):
        return []
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    done = []
    for name in sorted(os.listdir(SOURCE_STORE)):
        src = os.path.join(SOURCE_STORE, name)
        if os.path.isdir(src):
            _merge_installation(src, os.path.join(BACKUP_ROOT, name))
            done.append(name)
    old_settings = os.path.join(HERE, "settings.json")
    if os.path.exists(old_settings) and not os.path.exists(SETTINGS_PATH):
        shutil.copy2(old_settings, SETTINGS_PATH)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(SOURCE_STORE, os.path.join(DATA_DIR, f"legacy-source-backups-{stamp}"))
    return done


def migrate_legacy_backups(game_root, scene_names):
    """Move backups made before per-installation folders existed (directly in
    backups/) into this installation's folder, when they belong to it: the
    custom scenarios they list exist in this installation, or there are none
    and this is the editor's default sibling game folder."""
    target = backup_dir_for(game_root)
    legacy = [f for f in (os.listdir(BACKUP_ROOT) if os.path.isdir(BACKUP_ROOT) else [])
              if f in LEGACY_FILES or f == "shared" or re.fullmatch(r"level\d+\.orig", f)]
    if not legacy:
        return False
    belongs = False
    try:
        with open(os.path.join(BACKUP_ROOT, "scenarios.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        names = {os.path.splitext(os.path.basename(p))[0] for p in scene_names}
        scen = manifest.get("scenarios", [])
        belongs = bool(scen) and all(s["battle_scene"] in names for s in scen)
    except (OSError, ValueError):
        manifest = None
    if not belongs and not FROZEN and (manifest is None or not manifest.get("scenarios")):
        default = os.path.join(os.path.dirname(HERE), GAME_NAME)
        belongs = os.path.normcase(os.path.abspath(game_root)) == os.path.normcase(os.path.abspath(default))
    if not belongs:
        return False
    os.makedirs(target, exist_ok=True)
    for f in legacy:
        dst = os.path.join(target, f)
        if not os.path.exists(dst):
            shutil.move(os.path.join(BACKUP_ROOT, f), dst)
    return True
