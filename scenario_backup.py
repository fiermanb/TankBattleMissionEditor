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
  - meta.json: source mission, game version and dates,
  - terrain.npz (only for an edited terrain): the weight maps and the
    heightmap; the terrain copy is made again from the original terrain on
    restore,
  - for a rebuilt AI route map only its source (meta.json "navmesh"): the
    route map is derived data and is rebuilt on restore.

A backup can be exported as one .tbscenario file (a zip of these files) and
imported in another installation: it contains no game assets, only the map and
the names of the assets it uses, which every copy of the game has.

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
import io
import zipfile

import numpy as np

from scene_io import SceneFile, layout_tree, script_key
from terrain_edit import copy_terrain, own_assets_name, read_alpha, read_heights, terrain_components

BACKUP_DIR_NAME = "scenarios"
EXCHANGE_SUFFIX = ".tbscenario"
EXCHANGE_FORMAT = 1
BACKUP_FILES = ("meta.json", "mission.json", "refs.json.gz", "scripts.json.gz", "battle.level")
OPTIONAL_FILES = ("terrain.npz",)
MAX_IMPORT_BYTES = 256 * 1024 * 1024
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
        own = own_assets_name(entry["battle_index"])

        refs, scripts = {}, {}

        def note(p):
            if p["m_FileID"] > 0 and p["m_PathID"]:
                file = sc.externals[p["m_FileID"] - 1]
                if file == own:
                    return p
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
        terrain = self._backup_terrain(sc, own, entry, folder)
        navmesh = None
        for p in sc.all_pids():
            if sc.type_name(p) == "MonoBehaviour" and sc.script_class(p) == "NavMeshSurface":
                ref = sc.read(p)["m_NavMeshData"]
                if ref["m_FileID"] and sc.externals[ref["m_FileID"] - 1] == own:
                    src = entry.get("navmesh_source") or {}
                    navmesh = {"source_file": src.get("file"), "source_pid": src.get("pid"),
                               "source": src.get("desc")}
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
            "unity_version": ctx.unity_version, "terrain": terrain, "navmesh": navmesh,
            "saved": datetime.datetime.now().isoformat(timespec="seconds")})
        return folder

    def _backup_terrain(self, sc, own, entry, folder):
        """Store the weights of a painted terrain (the scenario's own copy) in
        terrain.npz. Returns the terrain description for meta.json, or None."""
        path = os.path.join(folder, "terrain.npz")
        comps = terrain_components(sc)
        tpid = next((p for p in comps if sc.type_name(p) == "Terrain"), None)
        ref = sc.read(tpid)["m_TerrainData"] if tpid is not None else None
        if not ref or not ref["m_FileID"] or sc.externals[ref["m_FileID"] - 1] != own:
            if os.path.exists(path):
                os.remove(path)
            return None
        assets = SceneFile(self.ctx, os.path.join(self.ctx.data_dir, own))
        td = assets.read(ref["m_PathID"])
        alphas = [read_alpha(assets.read(p["m_PathID"])) for p in td["m_SplatDatabase"]["m_AlphaTextures"]]
        buf = io.BytesIO()
        np.savez_compressed(buf, *alphas, heights=read_heights(td).astype(np.uint16))
        with open(path + ".tmp", "wb") as f:
            f.write(buf.getvalue())
        os.replace(path + ".tmp", path)
        src = entry.get("terrain_source") or {}
        desc = src.get("desc") or ["TerrainData", td.get("m_Name", ""), 0]
        return {"own_file": own, "source_file": src.get("file"), "source_pid": src.get("pid"), "source": desc}

    def backup_all(self):
        done = []
        for entry in self.scenarios.custom:
            self.backup(entry)
            done.append(entry["title"])
        return done

    def delete(self, scene_id):
        shutil.rmtree(self.folder(scene_id), ignore_errors=True)

    # ---- exchange: one file per scenario ---------------------------------

    def export(self, entry, path, editor_version=""):
        """Write a custom scenario (manifest entry) as one .tbscenario file, from
        a fresh backup of its current state."""
        folder = self.backup(entry)
        tmp = path + ".tmp"
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("format.json", json.dumps({"format": EXCHANGE_FORMAT, "editor_version": editor_version}))
            for name in BACKUP_FILES + OPTIONAL_FILES:
                if os.path.exists(os.path.join(folder, name)):
                    z.write(os.path.join(folder, name), name)
        os.replace(tmp, path)
        return path

    def read_exchange(self, path):
        """(meta, {file name: bytes}) of a .tbscenario file, checked: only the
        known member names are read, with a size limit."""
        try:
            z = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as e:
            raise ValueError(f"not a scenario file ({e})") from None
        with z:
            names = set(z.namelist())
            missing = [n for n in ("format.json",) + BACKUP_FILES if n not in names]
            if missing:
                raise ValueError("not a scenario file (missing " + ", ".join(missing) + ")")
            members = ("format.json",) + BACKUP_FILES + tuple(n for n in OPTIONAL_FILES if n in names)
            if sum(z.getinfo(n).file_size for n in members) > MAX_IMPORT_BYTES:
                raise ValueError("the scenario file is too large")
            fmt = json.loads(z.read("format.json"))
            if fmt.get("format") != EXCHANGE_FORMAT:
                raise ValueError("this scenario file was made with a newer editor version; update the editor")
            files = {n: z.read(n) for n in members if n != "format.json"}
        meta = json.loads(files["meta.json"])
        for key in ("title", "scene_id", "game_metadata_sha256"):
            if not isinstance(meta.get(key), str):
                raise ValueError("the scenario file is damaged (meta.json)")
        return meta, files

    def import_file(self, path):
        """Install a .tbscenario file in the game. A scenario whose name is
        already used (in the game or among the backups) is installed under a
        numbered title. Returns (title, menu index, battle index, report)."""
        from scenarios import slugify
        meta, files = self.read_exchange(path)
        taken_ids = {d["Scene_ID"].lower() for _, d in self.scenarios.missions()}
        taken_ids |= {b["folder"].lower() for b in self.list()}
        title, n = meta["title"], 1
        while slugify(title).lower() in taken_ids:
            n += 1
            title = f"{meta['title']} ({n})"
        meta.update({"title": title, "scene_id": slugify(title),
                     "imported": datetime.datetime.now().isoformat(timespec="seconds")})
        folder = self.folder(meta["scene_id"])
        os.makedirs(folder)
        try:
            for name, data in files.items():
                if name == "meta.json":
                    data = json.dumps(meta, indent=2).encode("utf-8")
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(data)
            menu_idx, battle_idx, report = self.restore(meta["scene_id"])
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        return title, menu_idx, battle_idx, report

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
        own_old = (meta.get("terrain") or {}).get("own_file")

        def remap(p):
            if p["m_FileID"] <= 0 or not p["m_PathID"]:
                return p
            file = sc.externals[p["m_FileID"] - 1]
            if file == own_old:
                return p
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

    def _restore_terrain(self, folder, info, battle_idx, entry):
        """Make the scenario's terrain copy again from the original terrain of
        the installed game version with the backed-up weights, and point the
        restored scene at it."""
        ctx = self.ctx
        with np.load(os.path.join(folder, "terrain.npz")) as z:
            alphas = [z[k] for k in sorted((k for k in z.files if k.startswith("arr_")),
                                           key=lambda k: int(k.split("_")[-1]))]
            heights = z["heights"].astype(np.int32) if "heights" in z.files else None
        hit = None
        if info.get("source_file") and info.get("source_pid") is not None:
            hit = self.index.resolve(info["source_file"], info["source_pid"], tuple(info["source"]))
        if hit is None:
            for f in ctx.asset_files():
                cands = self.index.table(f)[2].get(("TerrainData", info["source"][1]), [])
                if len(cands) == 1:
                    hit = (f, cands[0])
                    break
        if hit is None:
            return ["Its painted terrain could not be restored: the original terrain is no longer in the game."]
        src = SceneFile(ctx, os.path.join(ctx.data_dir, hit[0]))
        tex = [src.read(p["m_PathID"]) for p in src.read(hit[1])["m_SplatDatabase"]["m_AlphaTextures"]]
        fitted = []
        for a, t in zip(alphas, tex):
            h, w = t["m_Height"], t["m_Width"]
            if a.shape[:2] != (h, w):
                rows = np.minimum((np.arange(h) * a.shape[0] // h), a.shape[0] - 1)
                cols = np.minimum((np.arange(w) * a.shape[1] // w), a.shape[1] - 1)
                a = a[rows][:, cols]
            fitted.append(np.ascontiguousarray(a))
        own_new = own_assets_name(battle_idx)
        assets = SceneFile(ctx, os.path.join(ctx.data_dir, own_new))
        new_td, _ = copy_terrain(ctx, assets, hit[0], hit[1], fitted, heights)
        assets.save()
        level = SceneFile(ctx, ctx.scene_path(battle_idx))
        if info["own_file"] in level.externals:
            i = level.externals.index(info["own_file"])
            level.sf.externals[i].path = own_new
            level.externals[i] = own_new
            level.meta_changed = True
            fid = i + 1
        else:
            fid = level.ensure_external_path(own_new)
        for p in terrain_components(level):
            d = dict(level.read(p))
            d["m_TerrainData"] = {"m_FileID": fid, "m_PathID": new_td}
            level.write(p, d)
        level.save()
        for s in self.scenarios.manifest["scenarios"]:
            if s["scene_id"] == entry["scene_id"]:
                s["terrain_source"] = {"file": hit[0], "pid": hit[1], "desc": self.index.describe(hit[0], hit[1])}
        self.scenarios._save_manifest()
        return []

    def _restore_navmesh(self, info, battle_idx, entry):
        """Point the restored scene back at its source route map, then rebuild
        the route map for the restored scene (it is derived data)."""
        from navmesh_edit import NavMesh
        from scene_model import SceneModel
        ctx = self.ctx
        hit = None
        if info.get("source_file") and info.get("source_pid") is not None and info.get("source"):
            hit = self.index.resolve(info["source_file"], info["source_pid"], tuple(info["source"]))
        level = SceneFile(ctx, ctx.scene_path(battle_idx))
        surface = next((p for p in level.all_pids() if level.type_name(p) == "MonoBehaviour"
                        and level.script_class(p) == "NavMeshSurface"), None)
        if surface is None:
            return []
        if hit is None:
            mission = next((d for _, d in self.scenarios.missions() if d["Scene_ID"] == entry.get("source")), None)
            src_idx = self.scenarios.scene_index(mission["Battle_Scene_Name"]) if mission else None
            if src_idx is None:
                return ["Its AI route map could not be restored: the source mission is not in the game."]
            source = SceneFile(ctx, ctx.scene_path(src_idx))
            sp = next((p for p in source.all_pids() if source.type_name(p) == "MonoBehaviour"
                       and source.script_class(p) == "NavMeshSurface"), None)
            if sp is None:
                return ["Its AI route map could not be restored."]
            r = source.read(sp)["m_NavMeshData"]
            hit = (source.externals[r["m_FileID"] - 1], r["m_PathID"])
        d = dict(level.read(surface))
        d["m_NavMeshData"] = {"m_FileID": level.ensure_external_path(hit[0]), "m_PathID": hit[1]}
        level.write(surface, d)
        level.save()
        model = SceneModel(ctx, battle_idx, terrain="heights")
        nav = NavMesh(model)
        tiles, _ = nav.build()
        model.begin("Rebuild AI route map")
        nav.write(tiles)
        model.commit()
        model.save()
        for s in self.scenarios.manifest["scenarios"]:
            if s["scene_id"] == entry["scene_id"]:
                s["navmesh_source"] = {"file": hit[0], "pid": hit[1], "desc": self.index.describe(*hit)}
        self.scenarios._save_manifest()
        return []

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
        originals = [(pid, d) for pid, d in missions if sm.is_original(d)]
        src = next((pid for pid, d in originals if d["Scene_ID"] == meta.get("source")), None)
        report = []
        fallback = src is None
        if fallback:
            src = originals[0][0]
        menu_idx, battle_idx = sm.create(src, meta["title"], meta["briefing"])
        report += self.convert(folder, self.ctx.scene_path(battle_idx), force=force)
        entry = next(c for c in sm.custom if c["battle_index"] == battle_idx)
        if meta.get("terrain"):
            report += self._restore_terrain(folder, meta["terrain"], battle_idx, entry)
        if fallback:
            # the source mission is not an original one: take the briefing scene of
            # an original mission on the same terrain
            terrain = None
            if meta.get("terrain"):
                t = meta["terrain"]
                if t.get("source_file") and t.get("source_pid") is not None:
                    terrain = self.index.resolve(t["source_file"], t["source_pid"], tuple(t["source"]))
            else:
                terrain = sm.terrain_of(entry["battle_scene"])
            better = sm.original_on_terrain(terrain) if terrain else None
            if better is not None and better != src:
                sm.rebase_menu(entry, better)
            else:
                report.append(f"Its source mission '{meta.get('source')}' is not an original mission of this "
                              "game; the briefing screen was built from another mission.")
        if meta.get("navmesh"):
            report += self._restore_navmesh(meta["navmesh"], battle_idx, entry)
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
