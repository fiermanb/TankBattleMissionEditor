# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Object library and import of scenery objects from other mission scenes.

The library lists every distinct scenery object (house, tree, fence, vehicle,
prop...) found in the original missions, as the whole object a click selects
(SceneModel.object_root). It is built once by scanning the missions and cached
in a JSON file; it is rebuilt when the game's scene files change.

Stand-alone objects stored in the game's asset files (for example tree
variants that no mission places) are included when they consist of scenery
components only.

Importing copies the object's GameObjects and components into the open scene:
  - references inside the copied object are renumbered,
  - references to other game files (meshes, materials, sounds, scripts) are
    mapped to the target scene's external file list, adding entries as needed,
  - component types and script references missing from the target scene are
    added to its type table,
  - baked lightmap references are cleared (they belong to the source scene),
  - references to objects outside the copied object are cleared and counted
    (for objects from an asset file, references to the file's meshes and
    materials become references to that file).
"""

import copy
import json
import os

import numpy as np

from scene_model import SUFFIX_RE, SceneModel, q_inv, q_mul, q_rotate, q_dict, v_dict

LIBRARY_VERSION = 3
EVENTS_PER_TYPE = 4
# components allowed in stand-alone objects from the asset files (scenery only:
# no AI, weapons, effects or timers)
SAFE_SCRIPTS = {"Break_Object_CS", "Break_Object_Tree_CS", "Break_Object_Parts_CS", "NavMeshModifier",
                "Audio_Random_Pitch_CS", "Utility_Random_Y_Rotattion_CS", "Utility_Random_Scale_CS",
                "Utility_Snap_To_Terrain_CS"}
SAFE_TYPES = {"GameObject", "Transform", "MeshFilter", "MeshRenderer", "MeshCollider", "BoxCollider",
              "SphereCollider", "CapsuleCollider", "Rigidbody", "LODGroup", "AudioSource", "Animator"}
EXCLUDED_TOP_LEVEL = ("game_controller", "navmesh", "guide", "camera", "canvas", "eventsystem")
MAX_SIZE = 250.0
MAX_PARTS = 400

CATEGORIES = [
    ("Buildings", ("bld", "build", "house", "station", "church", "bunker", "hangar", "tower", "shop",
                   "barracks", "platform", "preset")),
    ("Trees and hedges", ("tree", "bush", "forest", "hedge", "grass", "plant")),
    ("Fences and walls", ("fence", "wall", "barrier", "trap", "gate")),
    ("Vehicles", ("veh", "truck", "car", "train", "wagon", "plane")),
    ("Roads and rails", ("rail", "road", "bridge", "footpath", "path")),
]


def category(name):
    n = name.lower()
    for cat, keys in CATEGORIES:
        if any(k in n for k in keys):
            return cat
    return "Props"


def base_name(name):
    return SUFFIX_RE.sub("", name)


class ObjectLibrary:
    def __init__(self, ctx, cache_dir):
        self.ctx = ctx
        self.path = os.path.join(cache_dir, "object_library.json")
        self.entries = None
        self.events = None
        self._sources = {}

    def source_scenes(self):
        """Original mission battle scenes (custom scenarios are excluded)."""
        out = []
        for i in range(len(self.ctx.scene_names)):
            lab = self.ctx.scene_label(i)
            low = lab.lower()
            if (low.endswith("_menu") or "loading" in low or "title" in low or "mission_select" in low
                    or low.startswith("99_") or low.startswith("90_")):
                continue
            out.append(i)
        return out

    def signature(self):
        sig = []
        for i in self.source_scenes():
            st = os.stat(self.ctx.scene_path(i))
            sig.append([i, st.st_size, int(st.st_mtime)])
        for f in self.ctx.asset_files():
            st = os.stat(os.path.join(self.ctx.data_dir, f))
            sig.append([f, st.st_size, int(st.st_mtime)])
        return sig

    def source_label(self, entry):
        if entry.get("file"):
            return "game assets"
        return self.ctx.scene_label(entry["scene"])

    def load(self):
        """Load the cached library if it matches the game files; True on success."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if data.get("version") != LIBRARY_VERSION or data.get("signature") != self.signature():
            return False
        self.entries = data["entries"]
        self.events = data.get("events", [])
        return True

    def build(self, progress=None):
        entries = {}
        events = []
        per_type = {}
        scenes = self.source_scenes()
        for n, idx in enumerate(scenes):
            if progress:
                progress(n, len(scenes), self.ctx.scene_label(idx))
            m = SceneModel(self.ctx, idx, terrain=None)
            event_gos = set(m.event_go.values())
            for ev in m.events:
                if ev in m.spawns or not m.active(m.event_go[ev]):
                    continue
                d = m.sc.read(ev)
                t = d["Event_Type"]
                if per_type.get(t, 0) >= EVENTS_PER_TYPE:
                    continue
                per_type[t] = per_type.get(t, 0) + 1
                go = m.event_go[ev]
                events.append({"type": t, "name": base_name(m.name(go)), "scene": idx, "go": go,
                               "message": d.get("Event_Message", "")[:60]})
            roots = {}
            for g in m.foot:
                if not m.active(g):
                    continue
                r = m.object_root(g)
                roots.setdefault(r, []).append(g)
            for r, parts in roots.items():
                top = r
                while m.parent_go(top) is not None:
                    top = m.parent_go(top)
                if any(k in m.name(top).lower() for k in EXCLUDED_TOP_LEVEL):
                    continue
                if r in event_gos or m.tr_of_go[r] in m.waypoints:
                    continue
                pts = np.concatenate([m.foot[g] for g in parts])
                w, d = (pts.max(0) - pts.min(0)).tolist()
                if w > MAX_SIZE or d > MAX_SIZE:
                    continue
                nparts = len(m.subtree(m.tr_of_go[r]))
                if nparts > MAX_PARTS:
                    continue
                key = base_name(m.name(r)).lower()
                if key in entries:
                    continue
                entries[key] = {"name": base_name(m.name(r)), "scene": idx, "go": r,
                                "category": category(m.name(r)), "w": round(w, 1), "d": round(d, 1),
                                "parts": nparts}
        self._add_asset_objects(entries, progress)
        self.entries = sorted(entries.values(), key=lambda e: (e["category"], e["name"].lower()))
        self.events = sorted(events, key=lambda e: (e["type"], e["scene"]))
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": LIBRARY_VERSION, "signature": self.signature(), "entries": self.entries,
                       "events": self.events}, f)
        return self.entries

    def _add_asset_objects(self, entries, progress=None):
        """Stand-alone objects from the asset files that consist of scenery
        components only and are not already listed from a mission."""
        files = self.ctx.asset_files()
        for n, f in enumerate(files):
            if progress:
                progress(n, len(files), f)
            try:
                m = SceneModel(self.ctx, None, terrain=None, path=os.path.join(self.ctx.data_dir, f))
            except Exception:
                continue
            for g in m.go:
                if m.parent_go(g) is not None or g not in m.tr_of_go:
                    continue
                sub = m.subtree(m.tr_of_go[g])
                parts = [m.go_of_tr[p] for p in sub]
                if not any(p in m.foot for p in parts) or len(sub) > MAX_PARTS:
                    continue
                safe = True
                for p in parts:
                    for c in m.comps.get(p, []):
                        t = m.sc.type_name(c)
                        if t == "MonoBehaviour":
                            safe &= m.script_class(c) in SAFE_SCRIPTS
                        else:
                            safe &= t in SAFE_TYPES
                if not safe:
                    continue
                key = base_name(m.name(g)).lower()
                if key in entries:
                    continue
                pts = np.concatenate([m.foot[p] for p in parts if p in m.foot])
                w, d = (pts.max(0) - pts.min(0)).tolist()
                entries[key] = {"name": base_name(m.name(g)), "scene": None, "file": f, "go": g,
                                "category": category(m.name(g)), "w": round(w, 1), "d": round(d, 1),
                                "parts": len(sub)}

    def source(self, index_or_entry):
        """Source model (heights only) of a mission index or a library entry,
        cached; at most two kept."""
        e = index_or_entry
        key = e.get("file") or e["scene"] if isinstance(e, dict) else e
        if key not in self._sources:
            if len(self._sources) >= 2:
                self._sources.pop(next(iter(self._sources)))
            if isinstance(key, str):
                self._sources[key] = SceneModel(self.ctx, None, terrain=None,
                                                path=os.path.join(self.ctx.data_dir, key))
            else:
                self._sources[key] = SceneModel(self.ctx, key, terrain="heights")
        return self._sources[key]


CONTAINER_NAMES = ("Level_Objects", "Level_Object")


def find_container(model):
    """Active root GameObject that groups level objects in the target scene, or
    None (then imported objects go to the scene root). An inactive container
    (for example in a scenario started on an empty terrain) would hide them."""
    for g, d in model.go.items():
        if model.parent_go(g) is None and d["m_Name"] in CONTAINER_NAMES and d["m_IsActive"]:
            return g
    return None


def import_object(model, library, entry, wx, wz, follow_terrain=True):
    """Copy a library object into the open scene at (wx, wz). Must be called
    inside model.begin()/commit(). Returns (new root GameObject, cleared refs)."""
    src = library.source(entry)
    go = entry["go"]
    if go not in src.go or base_name(src.name(go)) != entry["name"]:
        raise ValueError("the object library is out of date; rebuild it")
    tgt = model.sc
    root_tr = src.tr_of_go[go]
    pids = []
    for trp in src.subtree(root_tr):
        g = src.go_of_tr[trp]
        pids.append(g)
        pids.extend(src.comps.get(g, []))
    from_asset_file = bool(entry.get("file"))
    first = tgt.next_pid()
    mapping = {p: first + k for k, p in enumerate(pids)}
    cleared = [0]

    def remap(obj):
        if isinstance(obj, dict):
            if len(obj) == 2 and "m_FileID" in obj and "m_PathID" in obj:
                fid, pid = obj["m_FileID"], obj["m_PathID"]
                if pid == 0:
                    return {"m_FileID": 0, "m_PathID": 0}
                if fid == 0:
                    if pid in mapping:
                        return {"m_FileID": 0, "m_PathID": mapping[pid]}
                    if from_asset_file:
                        return {"m_FileID": tgt.ensure_external_path(entry["file"]), "m_PathID": pid}
                    cleared[0] += 1
                    return {"m_FileID": 0, "m_PathID": 0}
                return {"m_FileID": tgt.ensure_external(src.sc.sf.externals[fid - 1]), "m_PathID": pid}
            return {k: remap(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [remap(v) for v in obj]
        return copy.deepcopy(obj)

    prepared = []
    for p in pids:
        t = src.sc.type_name(p)
        nodes = src.sc.nodes_for(p)
        data = src.sc.read(p)
        if p == root_tr:
            data = dict(data)
            data["m_Father"] = {"m_FileID": 0, "m_PathID": 0}
        d = remap(data)
        if t in ("MeshRenderer", "SkinnedMeshRenderer"):
            for k in ("m_LightmapIndex", "m_LightmapIndexDynamic"):
                if k in d:
                    d[k] = 65535
            for k in ("m_LightmapTilingOffset", "m_LightmapTilingOffsetDynamic"):
                if k in d:
                    d[k] = {"x": 1.0, "y": 1.0, "z": 0.0, "w": 0.0}
        prepared.append((p, t, nodes, d, tgt.ensure_type(src.sc, src.sc.type_id(p))))
    for p, t, nodes, d, tid in prepared:
        new = model._new_foreign(tid, t, nodes, d)
        if new != mapping[p]:
            raise RuntimeError("object numbering mismatch during import")

    # place the copied root: keep the source world rotation and scale
    wp_src, wr, ws = src.world(root_tr)
    offset = 0.0
    if src.terrain is not None:
        g0 = src.ground(wp_src[0], wp_src[2])
        if g0 is not None:
            offset = wp_src[1] - g0
    g1 = model.ground(wx, wz)
    wy = (g1 + offset) if (follow_terrain and g1 is not None) else wp_src[1]
    target_pos = np.array((wx, wy, wz))

    new_root_tr = mapping[root_tr]
    container = find_container(model)
    rt = copy.deepcopy(tgt.read(new_root_tr))
    if container is not None:
        ctr = model.tr_of_go[container]
        pp, pr, ps = model.world(ctr)
        ps = np.where(ps == 0, 1, ps)
        rt["m_Father"] = {"m_FileID": 0, "m_PathID": ctr}
        rt["m_LocalPosition"] = v_dict(q_rotate(q_inv(pr), target_pos - pp) / ps)
        rt["m_LocalRotation"] = q_dict(q_mul(q_inv(pr), wr))
        rt["m_LocalScale"] = v_dict(ws / ps)
        ct = model.edit(ctr)
        ct["m_Children"] = list(ct["m_Children"]) + [{"m_FileID": 0, "m_PathID": new_root_tr}]
        model._set(ctr, ct)
    else:
        rt["m_Father"] = {"m_FileID": 0, "m_PathID": 0}
        rt["m_LocalPosition"] = v_dict(target_pos)
        rt["m_LocalRotation"] = q_dict(wr)
        rt["m_LocalScale"] = v_dict(ws)
    model._set(new_root_tr, rt)
    model.reindex()
    model.rename(mapping[go], model._unique_name(entry["name"]))
    return mapping[go], cleared[0]


def reconnect_event_refs(model, new_go, library, entry):
    """After importing an event from another mission: references to objects of
    that mission were cleared; fill each cleared single reference (message text
    box, sound, artillery, ...) with the value another event of this mission uses
    for the same field. Tank lists stay empty. Returns (reconnected, cleared)."""
    src = library.source(entry)
    src_ev = src.event_of_go(entry["go"])
    new_ev = model.event_of_go(new_go)
    if src_ev is None or new_ev is None:
        return 0, 0
    sd, nd = src.sc.read(src_ev), model.edit(new_ev)
    done = left = 0
    for k, v in sd.items():
        if not (isinstance(v, dict) and set(v) == {"m_FileID", "m_PathID"}):
            continue
        if v["m_FileID"] != 0 or not v["m_PathID"] or nd[k]["m_PathID"]:
            continue
        donor = next((model.sc.read(e)[k] for e in model.events
                      if e != new_ev and model.sc.read(e).get(k, {}).get("m_PathID")), None)
        if donor:
            nd[k] = dict(donor)
            done += 1
        else:
            left += 1
    model._set(new_ev, nd)
    return done, left
