# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Creation and removal of custom scenarios (new missions).

A mission in Tank Battle Classic consists of
  - a Scene_Prop_SO asset in sharedassets2.assets (ID, scene names, title, briefing),
  - a menu (briefing / tank selection) scene and a battle scene, each stored as
    levelN + sharedassetsN.assets and listed by path in BuildSettings
    (globalgamemanagers); the game opens scenes by name,
  - a row in the mission select scene (level2) that points at the asset.

A new scenario copies the menu and battle scene of an existing mission to new
scene indices, clones the mission asset, registers both scenes in BuildSettings
and adds a row on a "Custom Missions" page of the mission select screen.

The three shared files (globalgamemanagers, sharedassets2.assets, level2) are
backed up once before the first change; "remove all" restores them and deletes
the added scene files.

A game update (or Steam's "Verify integrity of game files") replaces the game
files: custom scenarios disappear from the scene list and the backups belong to
the old game version. check_game_update() detects this from a fingerprint of
the game stored with the backups, sets the old backups aside instead of ever
restoring them, and reports the custom scenarios that are no longer in the game.
"""

import datetime
import json
import os
import re
import shutil


def replace_file(path, data):
    """Write bytes via a temporary file and an atomic rename."""
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)

from scene_io import SceneFile
from scene_model import SceneModel

MISSION_SCRIPT = "Scene_Prop_SO"
MENU_MANAGER_SCRIPT = "Menu_Scene_Manager_CS"
SELECT_MANAGER_SCRIPT = "Mission_Select_Manager_CS"
SELECT_BUTTON_SCRIPT = "Mission_Select_Button_CS"
MISSION_ASSETS = "sharedassets2.assets"
SELECT_SCENE = 2
SCENE_DIR = "Assets/Physics Tank Maker/Demo_Scenes/"
SHARED_FILES = ("globalgamemanagers", MISSION_ASSETS, f"level{SELECT_SCENE}")
CUSTOM_PAGE_TITLE = "Custom Missions"
ROWS_PER_PAGE = 8


def slugify(title):
    s = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    return s[:40] or "Scenario"


class ScenarioManager:
    def __init__(self, ctx, backup_dir):
        self.ctx = ctx
        self.backup_dir = backup_dir
        self.shared_dir = os.path.join(backup_dir, "shared")
        self.manifest_path = os.path.join(backup_dir, "scenarios.json")
        self.manifest = self._load_manifest()
        self._terrains = None

    # ---- manifest ----------------------------------------------------

    def _load_manifest(self):
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"base_scene_count": None, "scenarios": [], "pages": []}

    def _save_manifest(self):
        os.makedirs(self.backup_dir, exist_ok=True)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2)

    @property
    def custom(self):
        return list(self.manifest["scenarios"])

    # ---- mission assets ----------------------------------------------

    def _mission_file(self):
        return SceneFile(self.ctx, os.path.join(self.ctx.data_dir, MISSION_ASSETS))

    def missions(self, mf=None):
        """[(asset pid, fields dict)] for all Scene_Prop_SO assets."""
        mf = mf or self._mission_file()
        out = []
        for pid in mf.all_pids():
            if mf.type_name(pid) == "MonoBehaviour" and mf.script_class(pid) == MISSION_SCRIPT:
                out.append((pid, mf.read(pid)))
        return out

    def mission_for_scene(self, scene_name):
        for pid, d in self.missions():
            if scene_name in (d["Battle_Scene_Name"], d["Menu_Scene_Name"]):
                return pid, d
        return None

    def scene_index(self, name):
        for i, path in enumerate(self.ctx.scene_names):
            if os.path.splitext(os.path.basename(path))[0] == name:
                return i
        return None

    def terrains(self):
        """The distinct terrains of the original missions, for starting a scenario
        on an empty map: [(label, template mission pid)]. The template is the
        mission with the fewest scripts on that terrain; tutorials and custom
        scenarios are not used as templates."""
        if self._terrains is not None:
            return self._terrains
        ctx = self.ctx
        custom = {c["scene_id"] for c in self.manifest["scenarios"]}
        groups = {}
        for pid, d in self.missions():
            idx = self.scene_index(d["Battle_Scene_Name"])
            if idx is None:
                continue
            sc = SceneFile(ctx, ctx.scene_path(idx))
            tpid = next((p for p in sc.all_pids() if sc.type_name(p) == "Terrain"), None)
            if tpid is None:
                continue
            ref = sc.read(tpid)["m_TerrainData"]
            if not ref["m_FileID"]:
                continue
            key = (sc.externals[ref["m_FileID"] - 1], ref["m_PathID"])
            g = groups.setdefault(key, {"missions": 0, "best": None})
            g["missions"] += 1
            name = (d["Scene_Title"] + " " + d["Battle_Scene_Name"]).lower()
            if d["Scene_ID"] in custom or "tutorial" in name or "turotial" in name:
                continue
            scripts = sum(1 for p in sc.all_pids() if sc.type_name(p) == "MonoBehaviour")
            if g["best"] is None or scripts < g["best"][0]:
                g["best"] = (scripts, pid, d["Scene_Title"])
        out = []
        for (file, tpid), g in groups.items():
            if g["best"] is None:
                continue
            try:
                tname = ctx.external(file).objects[tpid].peek_name()
            except Exception:
                tname = "Terrain"
            tname = re.sub(r"^Terrain[_ ]*", "", tname).replace("_", " ").strip("() ") or "Terrain"
            out.append((f"{tname}   (as in '{g['best'][2]}', {g['missions']} missions)", g["best"][1]))
        self._terrains = sorted(out)
        return self._terrains

    # ---- backups -----------------------------------------------------

    def _ensure_shared_backup(self):
        os.makedirs(self.shared_dir, exist_ok=True)
        for name in SHARED_FILES:
            dst = os.path.join(self.shared_dir, name + ".orig")
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(self.ctx.data_dir, name), dst)
        if self.manifest["base_scene_count"] is None:
            self.manifest["base_scene_count"] = len(self.ctx.scene_names)
            self._save_manifest()

    # ---- create ------------------------------------------------------

    def unique_names(self, title):
        base = slugify(title)
        existing = {os.path.splitext(os.path.basename(p))[0].lower() for p in self.ctx.scene_names}
        ids = {d["Scene_ID"].lower() for _, d in self.missions()}
        n = 0
        while True:
            slug = base if n == 0 else f"{base}_{n}"
            battle = f"90_{slug}"
            if battle.lower() not in existing and slug.lower() not in ids:
                return slug, battle, battle + "_Menu"
            n += 1

    def create(self, source_pid, title, briefing):
        """Create a new scenario from the mission asset source_pid. Returns
        (menu scene index, battle scene index)."""
        ctx = self.ctx
        mf = self._mission_file()
        src = mf.read(source_pid)
        menu_src = self.scene_index(src["Menu_Scene_Name"])
        battle_src = self.scene_index(src["Battle_Scene_Name"])
        if menu_src is None or battle_src is None:
            raise ValueError("source mission scenes are not in the build list")
        self._ensure_shared_backup()

        slug, battle_name, menu_name = self.unique_names(title)
        menu_idx = len(ctx.scene_names)
        battle_idx = menu_idx + 1
        data = ctx.data_dir

        new_mission = dict(src)
        new_mission.update({
            "m_Name": battle_name, "Scene_ID": slug,
            "Menu_Scene_Name": menu_name, "Battle_Scene_Name": battle_name,
            "Scene_Title": title, "Scene_JPN_Title": title,
            "Scene_Briefing": briefing, "Scene_JPN_Briefing": briefing,
        })
        mission_pid = mf.add_object(source_pid, new_mission)

        # menu scene copy: point its manager at the new mission asset
        menu = SceneFile(ctx, os.path.join(data, f"level{menu_src}"))
        if MISSION_ASSETS not in menu.externals:
            raise ValueError("menu scene does not reference the mission assets")
        fid = menu.externals.index(MISSION_ASSETS) + 1
        repointed = 0
        for pid in menu.all_pids():
            if menu.type_name(pid) == "MonoBehaviour" and menu.script_class(pid) == MENU_MANAGER_SCRIPT:
                d = dict(menu.read(pid))
                d["sceneProp"] = {"m_FileID": fid, "m_PathID": mission_pid}
                menu.write(pid, d)
                repointed += 1
        if not repointed:
            raise ValueError("menu scene has no Menu_Scene_Manager_CS")

        select = SceneModel(ctx, SELECT_SCENE)
        self._add_select_row(select, mission_pid, slug)

        build = SceneFile(ctx, os.path.join(data, "globalgamemanagers"))
        bpid = next(p for p in build.all_pids() if build.type_name(p) == "BuildSettings")
        bs = dict(build.read(bpid))
        bs["scenes"] = list(bs["scenes"]) + [SCENE_DIR + menu_name + ".unity", SCENE_DIR + battle_name + ".unity"]
        build.write(bpid, bs)

        snapshot = {}
        for name in SHARED_FILES:
            with open(os.path.join(data, name), "rb") as f:
                snapshot[name] = f.read()
        written = []
        try:
            written.append(os.path.join(data, f"level{menu_idx}"))
            menu.save(written[-1])
            for idx, srcidx in ((menu_idx, menu_src), (battle_idx, battle_src)):
                shutil.copy2(os.path.join(data, f"sharedassets{srcidx}.assets"),
                             os.path.join(data, f"sharedassets{idx}.assets"))
                written.append(os.path.join(data, f"sharedassets{idx}.assets"))
            shutil.copy2(os.path.join(data, f"level{battle_src}"), os.path.join(data, f"level{battle_idx}"))
            written.append(os.path.join(data, f"level{battle_idx}"))
            mf.save()
            select.save()
            build.save()
        except Exception:
            for p in written:
                try:
                    os.remove(p)
                except OSError:
                    pass
            for name, raw in snapshot.items():
                replace_file(os.path.join(data, name), raw)
            raise

        self.manifest["scenarios"].append({
            "title": title, "scene_id": slug, "menu_scene": menu_name, "battle_scene": battle_name,
            "menu_index": menu_idx, "battle_index": battle_idx, "mission_asset": mission_pid,
            "source": src["Scene_ID"],
        })
        self._save_manifest()
        self._refresh_context()
        return menu_idx, battle_idx

    def _add_select_row(self, m, mission_pid, slug):
        """Add a mission row to the custom page of the mission select scene,
        creating the page when needed."""
        mgr = next(p for p in m.mb if m.script_class(p) == SELECT_MANAGER_SCRIPT)
        pages = [r["m_PathID"] for r in m.sc.read(mgr)["missionLists"]]
        fid = m.sc.externals.index(MISSION_ASSETS) + 1

        page = None
        for g in self.manifest.get("pages", []):
            if g in m.go and self._page_list(m, g) is not None:
                if len(self._rows(m, g)) < ROWS_PER_PAGE:
                    page = g
        m.begin("Add mission row")
        if page is None:
            page = self._new_page(m, pages[-1], mgr)
            new_page = True
        else:
            new_page = False
        rows = self._rows(m, page)
        template = rows[0]
        row = m.duplicate(template, offset=None)
        if new_page:
            for r in rows:
                self._remove_from_list(m, r)
        for c in m.comps[row]:
            if m.sc.type_name(c) == "MonoBehaviour" and m.script_class(c) == SELECT_BUTTON_SCRIPT:
                d = m.edit(c)
                d["sceneProp"] = {"m_FileID": fid, "m_PathID": mission_pid}
                m._set(c, d)
            elif m.sc.type_name(c) == "MonoBehaviour" and m.script_class(c) == "Menu_Selector_Button_CS":
                d = m.edit(c)
                d["Initial_Selection_Flag"] = False
                m._set(c, d)
        m.rename(row, f"Text ({slug})")
        m.commit()
        m.reindex()

    def _page_list(self, m, page_go):
        for c in m.tr[m.tr_of_go[page_go]]["m_Children"]:
            g = m.go_of_tr[c["m_PathID"]]
            if m.name(g) == "List":
                return g
        return None

    def _rows(self, m, page_go):
        lst = self._page_list(m, page_go)
        return [m.go_of_tr[c["m_PathID"]] for c in m.tr[m.tr_of_go[lst]]["m_Children"]]

    def _remove_from_list(self, m, row):
        lst_tr = m.tr[m.tr_of_go[row]]["m_Father"]["m_PathID"]
        t = m.edit(lst_tr)
        t["m_Children"] = [c for c in t["m_Children"] if c["m_PathID"] != m.tr_of_go[row]]
        m._set(lst_tr, t)
        rt = m.edit(m.tr_of_go[row])
        rt["m_Father"] = {"m_FileID": 0, "m_PathID": 0}
        m._set(m.tr_of_go[row], rt)
        d = m.edit(row)
        d["m_IsActive"] = False
        m._set(row, d)

    def _new_page(self, m, template_page, mgr):
        page = m.duplicate(template_page, offset=None)
        for c in m.tr[m.tr_of_go[page]]["m_Children"]:
            g = m.go_of_tr[c["m_PathID"]]
            if m.name(g) == "Text (Title)":
                for comp in m.comps[g]:
                    if m.sc.type_name(comp) != "MonoBehaviour":
                        continue
                    d = m.edit(comp)
                    if "m_Text" in d:
                        d["m_Text"] = CUSTOM_PAGE_TITLE
                    if "jpnText" in d:
                        d["jpnText"] = CUSTOM_PAGE_TITLE
                    m._set(comp, d)
        d = m.edit(mgr)
        d["missionLists"] = list(d["missionLists"]) + [{"m_FileID": 0, "m_PathID": page}]
        m._set(mgr, d)
        m.rename(page, "Mission_List (Custom)")
        self.manifest.setdefault("pages", []).append(page)
        return page

    # ---- mission text --------------------------------------------------

    def set_mission_text(self, scene_name, title=None, briefing=None):
        found = self.mission_for_scene(scene_name)
        if found is None:
            raise ValueError(f"no mission asset for {scene_name}")
        self._ensure_shared_backup()
        mf = self._mission_file()
        pid = found[0]
        d = dict(mf.read(pid))
        custom = any(s["scene_id"] == d["Scene_ID"] for s in self.manifest["scenarios"])
        if title is not None:
            d["Scene_Title"] = title
            if custom:
                d["Scene_JPN_Title"] = title
        if briefing is not None:
            d["Scene_Briefing"] = briefing
            if custom:
                d["Scene_JPN_Briefing"] = briefing
        mf.write(pid, d)
        mf.save()
        for s in self.manifest["scenarios"]:
            if s["scene_id"] == d["Scene_ID"] and title is not None:
                s["title"] = title
        self._save_manifest()
        self._refresh_context()

    def set_mission_fields(self, scene_name, **fields):
        """Set plain fields (for example Allies_Count) of a mission asset."""
        found = self.mission_for_scene(scene_name)
        if found is None:
            raise ValueError(f"no mission asset for {scene_name}")
        self._ensure_shared_backup()
        mf = self._mission_file()
        d = dict(mf.read(found[0]))
        d.update(fields)
        mf.write(found[0], d)
        mf.save()
        self._refresh_context()

    # ---- removal -------------------------------------------------------

    def _restore_shared_files(self):
        for name in SHARED_FILES:
            src = os.path.join(self.shared_dir, name + ".orig")
            if os.path.exists(src):
                with open(src, "rb") as f:
                    replace_file(os.path.join(self.ctx.data_dir, name), f.read())

    # ---- game updates ----------------------------------------------------

    def _fingerprint(self):
        return {"metadata_sha256": self.ctx.metadata_sha256}

    def check_game_update(self):
        """Detect replaced game files since the backups were made. Returns None,
        or {"archive": folder or None, "orphaned": [titles], "leftovers": [files]}."""
        path = os.path.join(self.backup_dir, "game.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            stored = None
        names = {os.path.splitext(os.path.basename(p))[0] for p in self.ctx.scene_names}
        orphaned = [s for s in self.manifest.get("scenarios", []) if s["battle_scene"] not in names]
        if stored is None:
            stale = bool(orphaned)
        else:
            stale = stored.get("metadata_sha256") != self.ctx.metadata_sha256 or bool(orphaned)
        report = None
        if stale:
            archive = None
            old = [f for f in os.listdir(self.backup_dir) if re.fullmatch(r"level\d+\.orig", f)]
            if os.path.isdir(self.shared_dir) and os.listdir(self.shared_dir):
                old.append("shared")
            if old:
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                archive = os.path.join(self.backup_dir, f"before-update-{stamp}")
                os.makedirs(archive)
                for f in old:
                    shutil.move(os.path.join(self.backup_dir, f), os.path.join(archive, f))
            leftovers = []
            for s in orphaned:
                for idx in (s["menu_index"], s["battle_index"]):
                    for f in (f"level{idx}", f"sharedassets{idx}.assets"):
                        if idx >= len(self.ctx.scene_names) and os.path.exists(os.path.join(self.ctx.data_dir, f)):
                            leftovers.append(f)
            kept = [s for s in self.manifest.get("scenarios", []) if s not in orphaned]
            self.manifest["orphaned"] = self.manifest.get("orphaned", []) + orphaned
            self.manifest["scenarios"] = kept
            if not kept:
                self.manifest["base_scene_count"] = None
                self.manifest["pages"] = []
            self._save_manifest()
            report = {"archive": archive, "orphaned": [s["title"] for s in orphaned], "leftovers": leftovers}
        if stale or stored is None:
            os.makedirs(self.backup_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._fingerprint(), f, indent=2)
        return report

    def delete_leftovers(self, files):
        """Delete scene files of scenarios that are no longer in the game's scene list."""
        done = []
        for f in files:
            m = re.fullmatch(r"(?:level(\d+)|sharedassets(\d+)\.assets)", f)
            if not m or int(m.group(1) or m.group(2)) < len(self.ctx.scene_names):
                continue
            p = os.path.join(self.ctx.data_dir, f)
            if os.path.exists(p):
                os.remove(p)
                done.append(f)
        self.manifest["orphaned"] = []
        self._save_manifest()
        return done

    def has_changes(self):
        return any(os.path.exists(os.path.join(self.shared_dir, n + ".orig")) for n in SHARED_FILES)

    def remove_all(self):
        """Restore the shared files and delete every added scene file."""
        if self.manifest.get("scenarios") and not all(
                os.path.exists(os.path.join(self.shared_dir, n + ".orig")) for n in SHARED_FILES):
            raise ValueError("the backups of the shared game files are missing (the game was updated after "
                             "these scenarios were made); use Steam's 'Verify integrity of game files'")
        base = self.manifest.get("base_scene_count")
        self._restore_shared_files()
        if base is not None:
            data = self.ctx.data_dir
            for f in os.listdir(data):
                m = re.fullmatch(r"(?:level(\d+)|sharedassets(\d+)\.assets)", f)
                if m and int(m.group(1) or m.group(2)) >= base:
                    os.remove(os.path.join(data, f))
            for f in os.listdir(self.backup_dir):
                m = re.fullmatch(r"level(\d+)\.orig", f)
                if m and int(m.group(1)) >= base:
                    os.remove(os.path.join(self.backup_dir, f))
        for name in SHARED_FILES:
            p = os.path.join(self.shared_dir, name + ".orig")
            if os.path.exists(p):
                os.remove(p)
        self.manifest = {"base_scene_count": None, "scenarios": [], "pages": []}
        self._save_manifest()
        self._refresh_context()

    def _refresh_context(self):
        self.ctx.reload_build_settings()
        self.ctx._external_cache.pop(MISSION_ASSETS, None)
        self._terrains = None
