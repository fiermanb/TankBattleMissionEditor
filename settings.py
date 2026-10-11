# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Editor settings, game installation detection and per-installation backups.

The editor works on an installed copy of Tank Battle Classic (Steam). The game
folder is chosen by the user (File > Select game folder) or detected in the
Steam libraries, and remembered in settings.json. Settings and backups live in
%LOCALAPPDATA%/TankBattleMissionEditor, for the packaged executable and the
source version alike, so both see the same custom scenarios and backups. Older
source versions kept them next to the source files; merge_source_store() moves
that store into the shared one once.

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
DATA_DIR = os.environ.get("TBME_DATA_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "TankBattleMissionEditor")
SOURCE_STORE = os.path.join(HERE, "backups")
if FROZEN:
    _OLD_DATA_DIR = os.path.join(os.path.dirname(DATA_DIR), "TankBattleMapEditor")
    if os.path.isdir(_OLD_DATA_DIR) and not os.path.exists(DATA_DIR):
        try:
            os.rename(_OLD_DATA_DIR, DATA_DIR)
        except OSError:
            pass
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
