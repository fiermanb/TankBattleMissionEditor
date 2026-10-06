# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Backups of custom scenarios that survive game updates.

A game update replaces the game's files: custom scenarios disappear from the
scene list and the shared asset files they refer to may be renumbered (objects
keep their type and name, but can get another path id), and a game script can
gain or lose fields. A plain copy of the scenario's scene file is therefore not
enough to bring it back.

Each backup (backups/<installation>/scenarios/<scene id>/) holds
  - battle.level: the battle scene file as saved,
  - refs.json.gz: for every object in another game file that the scene refers
    to, its type, name and ordinal among objects of that type and name,
  - scripts.json.gz: the field layout of every game script the scene uses,
  - mission.json: the mission's settings and title/briefing,
  - meta.json: source mission, game version and dates.

Restoring creates the scenario again from its source mission in the installed
game version (fresh briefing scene, mission asset and mission select entry),
then converts the backed-up battle scene: references are looked up again by
type and name, script references by script name, and objects of scripts whose
layout changed are rewritten field by field. The briefing screen is rebuilt
from the restored scene.
"""

import datetime
import gzip
import json
import os
import shutil

from scene_io import SceneFile, layout_tree, script_key

BACKUP_DIR_NAME = "scenarios"
SKIP_MISSION_FIELDS = {"m_GameObject", "m_Enabled", "m_Script", "m_Name", "Scene_ID", "Menu_Scene_Name",
                       "Battle_Scene_Name", "Scene_Title", "Scene_JPN_Title", "Scene_Briefing",
                       "Scene_JPN_Briefing", "Allies_Count"}
GGM = "globalgamemanagers.assets"


def _is_pptr(v):
    return isinstance(v, dict) and len(v) == 2 and "m_FileID" in v and "m_PathID" in v


def walk_pptrs(value, fn):
    """Return value with fn(pptr) applied to every PPtr dict inside it."""
    if _is_pptr(value):
        return fn(value)
    if isinstance(value, dict):
        return {k: walk_pptrs(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [walk_pptrs(v, fn) for v in value]
    return value


def default_value(node):
    """Zero value for a type tree node (used for fields a script gained)."""
    t = node.m_Type
    kids = node.m_Children
    if kids and kids[0].m_Type == "Array":
        return []
    if t == "string":
        return ""
    if t.startswith("PPtr<"):
        return {"m_FileID": 0, "m_PathID": 0}
    if t in ("float", "double"):
        return 0.0
    if t == "bool":
        return False
    if not kids:
        return 0
    return {k.m_Name: default_value(k) for k in kids}


def convert_fields(old, old_node, new_node):
    """Fit object data read with old_node to the layout new_node: fields with
    the same name and type are kept, new fields get zero values."""
    if not isinstance(old, dict):
        return old if old_node.m_Type == new_node.m_Type else default_value(new_node)
    old_kids = {k.m_Name: k for k in old_node.m_Children}
    out = {}
    for k in new_node.m_Children:
        ok = old_kids.get(k.m_Name)
        if ok is None or k.m_Name not in old or ok.m_Type != k.m_Type:
            out[k.m_Name] = default_value(k)
        elif k.m_Children and k.m_Children[0].m_Type != "Array" and isinstance(old[k.m_Name], dict):
            out[k.m_Name] = convert_fields(old[k.m_Name], ok, k)
        else:
            out[k.m_Name] = old[k.m_Name]
    return out


class AssetIndex:
    """Identity of objects in the game's files by type and name, so that a
    reference made in one game version can be found again in another."""

    def __init__(self, ctx):
        self.ctx = ctx
        self._tables = {}

    def table(self, file):
        """({pid: (type, name, ordinal)}, {(type, name, ordinal): pid}, {(type, name): [pid]})."""
        if file in self._tables:
            return self._tables[file]
        fwd, rev, by_name = {}, {}, {}
        sf = self.ctx.external(file)
        if sf is not None:
            counts = {}
            for pid in sorted(sf.objects):
                o = sf.objects[pid]
                t = o.type.name
                if file == GGM and t == "MonoScript" and pid in self.ctx.scripts:
                    name = script_key(*self.ctx.scripts[pid])
                else:
                    try:
                        name = o.peek_name() or ""
                    except Exception:
                        name = ""
                n = counts.get((t, name), 0)
                counts[(t, name)] = n + 1
                fwd[pid] = (t, name, n)
                rev[(t, name, n)] = pid
                by_name.setdefault((t, name), []).append(pid)
        self._tables[file] = (fwd, rev, by_name)
        return self._tables[file]

    def describe(self, file, pid):
        d = self.table(file)[0].get(pid)
        return list(d) if d else None

    def resolve(self, file, pid, desc):
        """(file, pid) of the object that was `pid` in `file` and described as
        desc (from describe()) when backed up, or None. In order: the same object
        at the same path id; the same type, name and ordinal; the only object of
        that type and name in the file; the only one in any other game file."""
        t, name, n = desc
        fwd, rev, by_name = self.table(file)
        if fwd.get(pid) == (t, name, n):
            return file, pid
        if name and (t, name, n) in rev:
            return file, rev[(t, name, n)]
        cands = by_name.get((t, name), [])
        if len(cands) == 1:
            return file, cands[0]
        if not name:
            return None
        hits = []
        for other in [GGM] + self.ctx.asset_files():
            if other == file:
                continue
            c = self.table(other)[2].get((t, name), [])
            hits += [(other, p) for p in c]
            if len(hits) > 1:
                return None
        return hits[0] if len(hits) == 1 else None


class ScenarioBackups:
    def __init__(self, ctx, scenarios):
        self.ctx = ctx
        self.scenarios = scenarios
        self.root = os.path.join(scenarios.backup_dir, BACKUP_DIR_NAME)
        self.index = AssetIndex(ctx)

    def folder(self, scene_id):
        return os.path.join(self.root, scene_id)

    def list(self):
        """Metadata of all backups, newest first."""
        out = []
        if os.path.isdir(self.root):
            for name in os.listdir(self.root):
                try:
                    with open(os.path.join(self.root, name, "meta.json"), "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    continue
                if os.path.isfile(os.path.join(self.root, name, "battle.level")):
                    meta["folder"] = name
                    out.append(meta)
        return sorted(out, key=lambda m: m.get("saved", ""), reverse=True)

    def missing(self):
        """Backups of scenarios that are not in the installed game."""
        ids = {d["Scene_ID"] for _, d in self.scenarios.missions()}
        return [m for m in self.list() if m["scene_id"] not in ids]

    # ---- backing up --------------------------------------------------------

    def _write_json(self, path, data, packed=False):
        tmp = path + ".tmp"
        if packed:
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(data, f)
        else:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)

    def backup(self, entry):
        """Back up a custom scenario (an entry of the scenario manifest) as it is
        stored in the game folder now."""
        ctx = self.ctx
        found = self.scenarios.mission_for_scene(entry["battle_scene"])
        if found is None:
            raise ValueError(f"no mission asset for {entry['battle_scene']}")
        mission = found[1]
        path = ctx.scene_path(entry["battle_index"])
        sc = SceneFile(ctx, path)

        refs, scripts = {}, {}

        def note(p):
            if p["m_FileID"] > 0 and p["m_PathID"]:
                file = sc.externals[p["m_FileID"] - 1]
                key = str(p["m_PathID"])
                per = refs.setdefault(file, {})
                if key not in per:
                    per[key] = self.index.describe(file, p["m_PathID"])
            return p
        for pid in sc.all_pids():
            walk_pptrs(sc.read(pid), note)
        for st in sc.sf.script_types:
            fid = st.local_serialized_file_index
            if fid > 0 and sc.externals[fid - 1] == GGM:
                spid = st.local_identifier_in_file
                info = ctx.scripts.get(spid)
                if info:
                    scripts[str(spid)] = {"key": script_key(*info), "rows": ctx.script_rows(spid)}
                    note({"m_FileID": fid, "m_PathID": spid})

        folder = self.folder(entry["scene_id"])
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, "battle.level.tmp")
        shutil.copyfile(path, tmp)
        os.replace(tmp, os.path.join(folder, "battle.level"))
        self._write_json(os.path.join(folder, "refs.json.gz"), refs, packed=True)
        self._write_json(os.path.join(folder, "scripts.json.gz"), scripts, packed=True)
        self._write_json(os.path.join(folder, "mission.json"),
                         {k: v for k, v in mission.items() if not isinstance(v, (bytes, bytearray))})
        self._write_json(os.path.join(folder, "meta.json"), {
            "title": mission["Scene_Title"], "briefing": mission["Scene_Briefing"],
            "scene_id": entry["scene_id"], "source": entry.get("source"),
            "battle_scene": entry["battle_scene"], "game_metadata_sha256": ctx.metadata_sha256,
            "unity_version": ctx.unity_version,
            "saved": datetime.datetime.now().isoformat(timespec="seconds")})
        return folder

    def backup_all(self):
        done = []
        for entry in self.scenarios.custom:
            self.backup(entry)
            done.append(entry["title"])
        return done

    def delete(self, scene_id):
        shutil.rmtree(self.folder(scene_id), ignore_errors=True)

    # ---- restoring ---------------------------------------------------------

    def _load(self, folder):
        with open(os.path.join(folder, "meta.json"), "r", encoding="utf-8") as f:
            meta = json.load(f)
        with open(os.path.join(folder, "mission.json"), "r", encoding="utf-8") as f:
            mission = json.load(f)
        with gzip.open(os.path.join(folder, "refs.json.gz"), "rt", encoding="utf-8") as f:
            refs = json.load(f)
        with gzip.open(os.path.join(folder, "scripts.json.gz"), "rt", encoding="utf-8") as f:
            scripts = json.load(f)
        return meta, mission, refs, scripts

    def convert(self, folder, target, force=False):
        """Write the backed-up battle scene to `target`, converted to the installed
        game version. Returns report lines. force: convert even when the game
        version is the one the backup was made with (used by the tests)."""
        ctx = self.ctx
        meta, _, refs, scripts = self._load(folder)
        src = os.path.join(folder, "battle.level")
        shutil.copyfile(src, target)
        if meta["game_metadata_sha256"] == ctx.metadata_sha256 and not force:
            return []
        if meta.get("unity_version") and meta["unity_version"] != ctx.unity_version:
            raise ValueError(f"the game now uses Unity {ctx.unity_version} (the backup was made with "
                             f"{meta['unity_version']}); this scenario cannot be converted")
        sc = SceneFile(ctx, target)
        report, missing = [], set()

        # scripts: old MonoScript path id -> (new path id, old tree, new tree)
        smap = {}
        gone = sorted(info["key"].split(".")[-1] for info in scripts.values() if ctx.script_pid(info["key"]) is None)
        if gone:
            raise ValueError("it uses game scripts that this game version no longer has: " + ", ".join(gone))
        for old, info in scripts.items():
            new = ctx.script_pid(info["key"])
            if not info["rows"]:
                raise ValueError(f"the backup has no layout for {info['key']}")
            new_rows = ctx.script_rows(new)
            old_tree = layout_tree([list(r) for r in info["rows"]])
            new_tree = layout_tree([list(r) for r in new_rows]) if new_rows else None
            smap[int(old)] = (new, old_tree, new_tree, new_rows != info["rows"])

        resolved = {}

        def remap(p):
            if p["m_FileID"] <= 0 or not p["m_PathID"]:
                return p
            file = sc.externals[p["m_FileID"] - 1]
            key = (file, p["m_PathID"])
            if key not in resolved:
                desc = refs.get(file, {}).get(str(p["m_PathID"]))
                hit = self.index.resolve(file, p["m_PathID"], tuple(desc)) if desc else None
                if hit is None:
                    missing.add(f"{desc[0]} '{desc[1]}' in {file}" if desc else f"object {p['m_PathID']} in {file}")
                    resolved[key] = {"m_FileID": 0, "m_PathID": 0}
                else:
                    resolved[key] = {"m_FileID": sc.ensure_external_path(hit[0]), "m_PathID": hit[1]}
            return dict(resolved[key])

        for pid in sc.all_pids():
            layout = None
            if sc.type_name(pid) == "MonoBehaviour":
                head = sc.read(pid, head_only=True)["m_Script"]
                if head["m_FileID"] > 0 and sc.externals[head["m_FileID"] - 1] == GGM:
                    layout = smap[head["m_PathID"]]
                    sc.obj_nodes[pid] = layout[1]
            data = sc.read(pid)
            new = walk_pptrs(data, remap)
            if layout is not None and layout[3]:
                if layout[2] is None:
                    raise ValueError("a script used by this scenario has no known layout in this game version")
                new = convert_fields(new, layout[1], layout[2])
            if layout is not None:
                sc.obj_nodes[pid] = layout[2] if layout[3] else layout[1]
            if new != data or (layout is not None and layout[3]):
                sc.write(pid, new)

        hashes = ctx.script_metadata()[0]
        updates = []
        for t in sc.sf.types:
            if t.class_id != 114 or t.script_type_index is None or t.script_type_index < 0:
                continue
            st = sc.sf.script_types[t.script_type_index]
            fid = st.local_serialized_file_index
            if fid > 0 and sc.externals[fid - 1] == GGM and str(st.local_identifier_in_file) in scripts:
                updates.append((t, st, st.local_identifier_in_file))
        for t, st, old in updates:
            new_pid = smap[old][0]
            st.local_identifier_in_file = new_pid
            new_hash = sorted(hashes.get(scripts[str(old)]["key"], []))
            if new_hash and bytes(t.old_type_hash).hex() != new_hash[0]:
                t.old_type_hash = bytes.fromhex(new_hash[0])
            sc.meta_changed = True
        sc.save(compact=True)
        if missing:
            report.append(f"{len(missing)} referenced game object(s) no longer exist and were cleared: "
                          + ", ".join(sorted(missing)[:5]) + (" ..." if len(missing) > 5 else ""))
        return report

    def restore(self, folder_name, force=False):
        """Recreate a backed-up scenario in the installed game. Returns
        (menu index, battle index, report lines)."""
        from briefing_sync import sync_mission
        from scene_model import SceneModel
        folder = os.path.join(self.root, folder_name)
        meta, mission, _, _ = self._load(folder)
        sm = self.scenarios
        missions = sm.missions()
        if any(d["Scene_ID"] == meta["scene_id"] for _, d in missions):
            raise ValueError(f"'{meta['title']}' is already in the game")
        custom = {c["scene_id"] for c in sm.custom}
        originals = [(pid, d) for pid, d in missions if d["Scene_ID"] not in custom]
        src = next((pid for pid, d in originals if d["Scene_ID"] == meta.get("source")), None)
        report = []
        if src is None:
            src = originals[0][0]
            report.append(f"Its source mission '{meta.get('source')}' is no longer in the game; the briefing "
                          "screen was built from another mission.")
        menu_idx, battle_idx = sm.create(src, meta["title"], meta["briefing"])
        report += self.convert(folder, self.ctx.scene_path(battle_idx), force=force)
        entry = next(c for c in sm.custom if c["battle_index"] == battle_idx)
        current = sm.mission_for_scene(entry["battle_scene"])[1]
        fields = {k: v for k, v in mission.items() if k not in SKIP_MISSION_FIELDS and k in current
                  and isinstance(v, (bool, int, float, str)) and type(v) is type(current[k])}
        if fields:
            sm.set_mission_fields(entry["battle_scene"], **fields)
        sm.manifest["orphaned"] = [o for o in sm.manifest.get("orphaned", []) if o["scene_id"] != meta["scene_id"]]
        sm._save_manifest()
        battle = SceneModel(self.ctx, battle_idx, terrain=None)
        report += sync_mission(self.ctx, sm, battle, sm.backup_dir)[:1]
        if entry["scene_id"] != meta["scene_id"]:
            shutil.rmtree(folder, ignore_errors=True)
        self.backup(entry)
        return menu_idx, battle_idx, report


__all__ = ["ScenarioBackups", "AssetIndex", "convert_fields", "default_value", "walk_pptrs"]
