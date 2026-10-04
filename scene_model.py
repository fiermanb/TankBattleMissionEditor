# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""High-level, editable model of one Tank Battle Classic mission scene.

Builds on scene_io.SceneFile: resolves the transform hierarchy, terrain,
tank spawn events, waypoint packs and scenery footprints, and implements the
edit operations (move, rotate, field edits, duplicate, delete) with undo.

Edits never mutate dicts returned by SceneFile.read(); they replace them via
_set(), which records the previous state for undo.

Event enum labels are inferred from how the shipped missions use the values;
the build carries no enum names for these integer fields.
"""

import copy
import math
import os
import re

import numpy as np
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

from scene_io import SceneFile

EVENT_SCRIPT = "Event_Controller_CS"
TANK_PROP_SCRIPT = "Tank_Prop_SO"

EVENT_TYPES = {
    0: "Spawn tank",
    1: "Show message",
    2: "Change AI settings",
    3: "Remove tanks",
    4: "Artillery fire",
    6: "Destroy target",
    7: "Change lighting",
    10: "Mission complete",
    11: "Mission failed",
    12: "Activate object",
    13: "Enable events",
}
TRIGGER_TYPES = {0: "Timer", 1: "Tanks destroyed", 2: "Trigger collider"}
RELATIONSHIPS = {0: "Friendly", 1: "Hostile"}

REF_LISTS = {
    "Trigger_Tanks": "Trigger_Num",
    "Target_Tanks": "Target_Num",
    "Useless_Events": "Useless_Event_Num",
    "Disabled_Events": "Disabled_Event_Num",
    "Trigger_Collider_Scripts": "Trigger_Collider_Num",
}
REF_SINGLES = (
    "Respawn_Target", "Follow_Target", "Commander", "New_Respawn_Target",
    "New_Follow_Target", "Artillery_Target", "Activation_Object", "Trigger_Script",
)
PACK_FIELDS = ("WayPoint_Pack", "New_WayPoint_Pack", "Respawn_Point_Pack", "New_Respawn_Point_Pack")

DUPLICATE_OFFSET = 8.0
HOLE_MARGIN = 2.0
SYSTEM_ROOT_NAMES = ("game_controller",)
SUFFIX_RE = re.compile(r"( \(\d+\))+$")


def label(table, value):
    return f"{table.get(value, 'Unknown')} ({value})"


# ---- quaternion helpers (Unity: left-handed, +X east, +Y up, +Z north) ----

def q_tuple(q):
    return (q["x"], q["y"], q["z"], q["w"])


def q_dict(q):
    return {"x": float(q[0]), "y": float(q[1]), "z": float(q[2]), "w": float(q[3])}


def q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def q_inv(q):
    x, y, z, w = q
    n = x * x + y * y + z * z + w * w or 1.0
    return (-x / n, -y / n, -z / n, w / n)


def q_rotate(q, v):
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2 * (y * vz - z * vy)
    ty = 2 * (z * vx - x * vz)
    tz = 2 * (x * vy - y * vx)
    return np.array((vx + w * tx + (y * tz - z * ty),
                     vy + w * ty + (z * tx - x * tz),
                     vz + w * tz + (x * ty - y * tx)))


def q_yaw(deg):
    r = math.radians(deg) / 2
    return (0.0, math.sin(r), 0.0, math.cos(r))


def heading_of(q):
    f = q_rotate(q, (0.0, 0.0, 1.0))
    return math.degrees(math.atan2(f[0], f[2])) % 360


def v_arr(v):
    return np.array((v["x"], v["y"], v["z"]), dtype=float)


def v_dict(a):
    return {"x": float(a[0]), "y": float(a[1]), "z": float(a[2])}


# ---- shared per-game catalogues -----------------------------------------

def tank_catalogue(ctx):
    """[(external file, path id, asset name, tank name)] of all Tank_Prop_SO assets
    in any of the game's asset files."""
    if getattr(ctx, "_tank_catalogue", None) is not None:
        return ctx._tank_catalogue
    result = []
    for ext in ctx.files_using_script(TANK_PROP_SCRIPT) or ["sharedassets3.assets"]:
        sf = ctx.external(ext)
        if sf is None:
            continue
        exts = [e.path for e in sf.externals]
        for pid, o in sf.objects.items():
            if o.type.name != "MonoBehaviour":
                continue
            head = o.parse_monobehaviour_head()
            p = head.m_Script
            if p.m_FileID == 0 or exts[p.m_FileID - 1] != "globalgamemanagers.assets":
                continue
            info = ctx.scripts.get(p.m_PathID)
            if not info or info[2] != TANK_PROP_SCRIPT:
                continue
            d = o.read_typetree(nodes=ctx.script_nodes(p.m_PathID), wrap=False, check_read=False)
            result.append((ext, pid, d["m_Name"], d.get("Tank_Name", d["m_Name"])))
    result.sort(key=lambda r: (not r[2].startswith("AI_"), r[3].lower()))
    ctx._tank_catalogue = result
    return result


def tank_display(entry):
    ext, pid, asset, name = entry
    return f"{name} (AI)" if asset.startswith("AI_") else name


def mesh_aabb(ctx, sc, pptr):
    """(center, extent) of a mesh's local AABB, cached per game; None if unavailable."""
    cache = ctx.__dict__.setdefault("_aabb_cache", {})
    fid, mpid = pptr["m_FileID"], pptr["m_PathID"]
    if mpid == 0:
        return None
    if fid == 0:
        key = (sc.path, mpid)
        sf = sc.sf
    else:
        key = (sc.externals[fid - 1], mpid)
        sf = ctx.external(key[0])
    if key in cache:
        return cache[key]
    result = None
    try:
        o = sf.objects[mpid]
        node = o._get_typetree_node()
        names = [k.m_Name for k in node.m_Children]
        idx = names.index("m_LocalAABB")
        short = TypeTreeNode(node.m_Level, node.m_Type, node.m_Name, 0, 0,
                             node.m_Children[:idx + 1], m_MetaFlag=node.m_MetaFlag)
        d = o.read_typetree(nodes=short, wrap=False, check_read=False)
        box = d["m_LocalAABB"]
        result = (v_arr(box["m_Center"]), v_arr(box["m_Extent"]))
    except Exception:
        result = None
    cache[key] = result
    return result


# ---- terrain --------------------------------------------------------------

class Terrain:
    def __init__(self, ctx, sc, terrain_pid, world_pos, colour=True):
        d = sc.read(terrain_pid)
        tdp = d["m_TerrainData"]
        ext = sc.externals[tdp["m_FileID"] - 1] if tdp["m_FileID"] else None
        sf = ctx.external(ext) if ext else sc.sf
        td = sf.objects[tdp["m_PathID"]].read_typetree(check_read=False)
        hm = td["m_Heightmap"]
        self.res = hm["m_Resolution"]
        scale = v_arr(hm["m_Scale"])
        self.size_x = (self.res - 1) * scale[0]
        self.size_z = (self.res - 1) * scale[2]
        self.origin = world_pos
        h = np.asarray(hm["m_Heights"], dtype=np.float32).reshape(self.res, self.res)
        self.heights = h / 32766.0 * scale[1] + world_pos[1]
        self.holes = self._hole_points(hm.get("m_Holes"))
        self.image = self._colour(ctx, sf, td["m_SplatDatabase"]) if colour else None

    def _hole_points(self, raw):
        """World (x, z) centres of the cells cut out of the terrain."""
        if raw is None or not len(raw):
            return np.zeros((0, 2))
        mask = np.frombuffer(bytes(raw), dtype=np.uint8) if isinstance(raw, (bytes, bytearray)) else np.asarray(raw)
        n = int(round(len(mask) ** 0.5))
        if n * n != len(mask):
            return np.zeros((0, 2))
        rows, cols = np.nonzero(mask.reshape(n, n) == 0)
        return np.column_stack((self.origin[0] + (cols + 0.5) * self.size_x / n,
                                self.origin[2] + (rows + 0.5) * self.size_z / n))

    def _colour(self, ctx, sf, splat):
        def deref(file, p):
            if p["m_FileID"] == 0:
                return file, file.objects.get(p["m_PathID"])
            other = ctx.external([e.path for e in file.externals][p["m_FileID"] - 1])
            return other, other.objects.get(p["m_PathID"]) if other else None

        colours = []
        for lp in splat["m_TerrainLayers"]:
            col = np.array((110.0, 110.0, 90.0))
            try:
                lf, lo = deref(sf, lp)
                layer = lo.read_typetree(check_read=False)
                tf, to = deref(lf, layer["m_DiffuseTexture"])
                tex = to.read()
                im = tex.image.convert("RGB").resize((16, 16))
                col = np.asarray(im, dtype=float).reshape(-1, 3).mean(0)
                tint = layer.get("m_DiffuseRemapMax")
                if tint:
                    col = col * np.array((tint["x"], tint["y"], tint["z"]))
            except Exception:
                pass
            colours.append(col)

        n = 1024 if self.res > 1024 else self.res - 1
        rgb = np.zeros((n, n, 3), dtype=float)
        weight = np.zeros((n, n, 1), dtype=float)
        for ti, ap in enumerate(splat["m_AlphaTextures"]):
            try:
                af, ao = deref(sf, ap)
                im = ao.read().image.convert("RGBA").resize((n, n))
                a = np.asarray(im, dtype=float) / 255.0
            except Exception:
                continue
            for ch in range(4):
                li = ti * 4 + ch
                if li < len(colours):
                    w = a[:, :, ch:ch + 1]
                    rgb += w * colours[li]
                    weight += w
        base = np.where(weight > 0.01, rgb / np.maximum(weight, 1e-6), np.array((105.0, 110.0, 80.0)))

        step = max(1, (self.res - 1) // n)
        h = self.heights[::-1, :][: n * step: step, : n * step: step]
        h = h[:n, :n]
        if h.shape != (n, n):
            h = np.pad(h, ((0, n - h.shape[0]), (0, n - h.shape[1])), mode="edge")
        cell = self.size_x / n
        gy, gx = np.gradient(h, cell)
        shade = 0.8 + 0.3 * np.clip((gx + gy) * 2.0, -1, 1)
        img = np.clip(base * shade[:, :, None], 0, 255).astype(np.uint8)
        return img

    def height_at(self, x, z):
        u = (x - self.origin[0]) / self.size_x * (self.res - 1)
        v = (z - self.origin[2]) / self.size_z * (self.res - 1)
        if not (0 <= u <= self.res - 1 and 0 <= v <= self.res - 1):
            return None
        i0, j0 = int(v), int(u)
        i1, j1 = min(i0 + 1, self.res - 1), min(j0 + 1, self.res - 1)
        fv, fu = v - i0, u - j0
        h = self.heights
        top = h[i0, j0] * (1 - fu) + h[i0, j1] * fu
        bot = h[i1, j0] * (1 - fu) + h[i1, j1] * fu
        return float(top * (1 - fv) + bot * fv)


# ---- the scene model ------------------------------------------------------

class SceneModel:
    def __init__(self, ctx, index, terrain="full", path=None):
        """terrain: "full" (heights and colour image), "heights" or None.
        path: open another serialized file (for example an asset file with
        stand-alone objects) instead of scene `index`."""
        self.ctx = ctx
        self.index = index
        self.label = os.path.basename(path) if path else ctx.scene_label(index)
        self.sc = SceneFile(ctx, path or ctx.scene_path(index))
        self.undo_stack = []
        self._op = None
        self.edits = 0
        self.saved_marker = self._marker()
        self._class_cache = {}
        self.tanks = tank_catalogue(ctx)
        self.reindex()
        self.terrain = None
        tpid = next((p for p in self.sc.all_pids() if self.sc.type_name(p) == "Terrain"), None)
        if tpid is not None and terrain:
            go = self.sc.read(tpid)["m_GameObject"]["m_PathID"]
            pos, _, _ = self.world(self.tr_of_go[go])
            try:
                self.terrain = Terrain(ctx, self.sc, tpid, pos, colour=(terrain == "full"))
            except Exception:
                self.terrain = None

    # ---- indexing ----------------------------------------------------

    def reindex(self):
        sc = self.sc
        self.tr, self.go = {}, {}
        self.mb = []
        mesh_filters = {}
        for pid in sc.all_pids():
            t = sc.type_name(pid)
            if t in ("Transform", "RectTransform"):
                self.tr[pid] = sc.read(pid)
            elif t == "GameObject":
                self.go[pid] = sc.read(pid)
            elif t == "MonoBehaviour":
                self.mb.append(pid)
            elif t == "MeshFilter":
                mesh_filters[pid] = sc.read(pid)
        self.rect = {p for p in self.tr if sc.type_name(p) == "RectTransform"}
        self.tr_of_go = {t["m_GameObject"]["m_PathID"]: p for p, t in self.tr.items()}
        self.go_of_tr = {p: t["m_GameObject"]["m_PathID"] for p, t in self.tr.items()}
        self.comps = {g: [c["component"]["m_PathID"] for c in d["m_Component"]
                          if c["component"]["m_FileID"] == 0] for g, d in self.go.items()}

        self.events = []
        self.event_go = {}
        for pid in self.mb:
            if self.script_class(pid) == EVENT_SCRIPT:
                self.events.append(pid)
                self.event_go[pid] = sc.read(pid, head_only=True)["m_GameObject"]["m_PathID"]
        self.spawns = [e for e in self.events if sc.read(e)["Event_Type"] == 0]

        packs = set()
        for e in self.events:
            d = sc.read(e)
            for f in PACK_FIELDS:
                p = d.get(f)
                if p and p["m_FileID"] == 0:
                    trp = self.pack_tr(p["m_PathID"])
                    if trp:
                        packs.add(trp)
        for g, d in self.go.items():
            if "waypoint_pack" in d["m_Name"].lower() and g in self.tr_of_go:
                packs.add(self.tr_of_go[g])
        self.packs = sorted(packs, key=lambda p: self.name(self.go_of_tr[p]))
        self.waypoints = {}
        for p in self.packs:
            for c in self.tr[p]["m_Children"]:
                self.waypoints[c["m_PathID"]] = p
        self.event_trs = {self.tr_of_go[g] for g in self.event_go.values() if g in self.tr_of_go}

        self.mesh_of_go = {}
        system = self.system_roots()
        for pid, d in mesh_filters.items():
            g = d["m_GameObject"]["m_PathID"]
            trp = self.tr_of_go.get(g)
            if trp is None or trp in self.rect or trp in self.waypoints:
                continue
            if self.top_level(g) in system:
                continue
            self.mesh_of_go[g] = d["m_Mesh"]
        self._wcache = {}
        self.compute_footprints()

    def pack_tr(self, pid):
        """Transform of a pack reference; WayPoint_Pack points at the GameObject,
        Respawn_Point_Pack at the Transform."""
        if pid in self.tr:
            return pid
        return self.tr_of_go.get(pid, 0)

    def spawn_pack(self, ev):
        """Transform pid of a spawn's waypoint pack, or 0."""
        return self.pack_tr(self.sc.read(ev)["WayPoint_Pack"]["m_PathID"])

    def top_level(self, go):
        g = go
        while True:
            p = self.parent_go(g)
            if p is None:
                return g
            g = p

    def system_roots(self):
        """Root objects of the game's internal systems (cameras, user interface,
        the recon plane model of the map camera): not scenery, so not shown."""
        return {g for g, d in self.go.items()
                if any(k in d["m_Name"].lower() for k in SYSTEM_ROOT_NAMES) and self.parent_go(g) is None}

    def script_class(self, pid):
        if pid not in self._class_cache:
            self._class_cache[pid] = self.sc.script_class(pid)
        return self._class_cache[pid]

    def name(self, go):
        return self.go[go]["m_Name"] if go in self.go else "?"

    def active(self, go):
        """Active in the hierarchy. Waypoint packs are inactive by design in the
        shipped scenes (the game only reads their positions), so their own flag
        is ignored."""
        g = go
        while g is not None:
            trp = self.tr_of_go.get(g)
            if not self.go[g]["m_IsActive"] and trp not in self.packs:
                return False
            father = self.tr[trp]["m_Father"]["m_PathID"] if trp else 0
            g = self.go_of_tr.get(father) if father else None
        return True

    def path(self, go):
        parts = []
        g = go
        while g is not None:
            parts.append(self.name(g))
            trp = self.tr_of_go.get(g)
            father = self.tr[trp]["m_Father"]["m_PathID"] if trp else 0
            g = self.go_of_tr.get(father) if father else None
        return " / ".join(reversed(parts))

    def parent_go(self, go):
        trp = self.tr_of_go.get(go)
        father = self.tr[trp]["m_Father"]["m_PathID"] if trp else 0
        return self.go_of_tr.get(father) if father else None

    def object_root(self, go):
        """The whole object a mesh belongs to (house, car), not the folder that groups it.

        Climbs while the parent has a mesh of its own or is a prefab-style 'SM_' object;
        folders such as 'Buildings' or 'Vehicles' stop the climb.
        """
        r = go
        while True:
            p = self.parent_go(r)
            if p is None:
                return r
            pbase = SUFFIX_RE.sub("", self.name(p))
            if p in self.foot or pbase.startswith("SM_"):
                r = p
            else:
                return r

    def subtree(self, trp):
        out, stack = [], [trp]
        while stack:
            p = stack.pop()
            out.append(p)
            stack.extend(c["m_PathID"] for c in self.tr[p]["m_Children"] if c["m_PathID"] in self.tr)
        return out

    # ---- transforms --------------------------------------------------

    def world(self, trp):
        """(position ndarray, rotation tuple, lossy scale ndarray) in world space."""
        hit = self._wcache.get(trp)
        if hit is not None:
            return hit
        t = self.tr[trp]
        lp, lr, ls = v_arr(t["m_LocalPosition"]), q_tuple(t["m_LocalRotation"]), v_arr(t["m_LocalScale"])
        father = t["m_Father"]["m_PathID"]
        if father and father in self.tr:
            pp, pr, ps = self.world(father)
            result = (pp + q_rotate(pr, ps * lp), q_mul(pr, lr), ps * ls)
        else:
            result = (lp, lr, ls)
        self._wcache[trp] = result
        return result

    def invalidate(self, trp):
        for p in self.subtree(trp):
            self._wcache.pop(p, None)

    def compute_footprints(self, gos=None):
        self.foot_version = getattr(self, "foot_version", 0) + 1
        if gos is None:
            self.foot = {}
            gos = self.mesh_of_go.keys()
        for g in gos:
            if g not in self.mesh_of_go:
                continue
            box = mesh_aabb(self.ctx, self.sc, self.mesh_of_go[g])
            if box is None:
                self.foot.pop(g, None)
                continue
            c, e = box
            pos, rot, scl = self.world(self.tr_of_go[g])
            corners = []
            for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                local = c + np.array((sx * e[0], 0.0, sz * e[2]))
                w = pos + q_rotate(rot, scl * local)
                corners.append((w[0], w[2]))
            self.foot[g] = np.array(corners)

    def refresh_subtree(self, trp):
        self.invalidate(trp)
        gos = [self.go_of_tr[p] for p in self.subtree(trp)]
        self.compute_footprints(gos)

    # ---- undo --------------------------------------------------------

    def begin(self, text):
        if self._op is None:
            self._op = {"label": text, "states": {}}

    def commit(self):
        op, self._op = self._op, None
        if op and op["states"]:
            self.undo_stack.append(op)

    def _touch(self, pid):
        if self._op is None:
            raise RuntimeError("edit outside begin()/commit()")
        states = self._op["states"]
        if pid in states:
            return
        sc = self.sc
        if pid in sc.new_objects and pid not in sc._cache:
            states[pid] = ("new",)
        else:
            states[pid] = ("old", sc._cache.get(pid), pid in sc.changed)

    def _set(self, pid, data):
        self.edits += 1
        self._touch(pid)
        self.sc.write(pid, data)
        self._sync(pid, data)

    def _sync(self, pid, data):
        t = self.sc.type_name(pid)
        if t in ("Transform", "RectTransform"):
            self.tr[pid] = data
        elif t == "GameObject":
            self.go[pid] = data

    def _new(self, template_pid, data):
        self.edits += 1
        pid = self.sc.next_pid()
        if self._op is not None:
            self._op["states"][pid] = ("new",)
        self.sc.add_object(template_pid, data)
        return pid

    def _new_foreign(self, type_id, type_name, nodes, data):
        self.edits += 1
        pid = self.sc.next_pid()
        if self._op is not None:
            self._op["states"][pid] = ("new",)
        self.sc.add_foreign(type_id, type_name, nodes, data)
        return pid

    def undo(self):
        if not self.undo_stack:
            return None
        op = self.undo_stack.pop()
        self.edits += 1
        sc = self.sc
        for pid, st in op["states"].items():
            if st[0] == "new":
                sc.remove_new(pid)
                self._class_cache.pop(pid, None)
            else:
                _, old, was_changed = st
                if old is None:
                    sc._cache.pop(pid, None)
                else:
                    sc._cache[pid] = old
                if pid in sc.new_objects:
                    continue
                if not was_changed:
                    sc.changed.pop(pid, None)
        self.reindex()
        return op["label"]

    def edit(self, pid):
        return copy.deepcopy(self.sc.read(pid))

    # ---- edit operations ---------------------------------------------

    def ground(self, x, z):
        return self.terrain.height_at(x, z) if self.terrain else None

    def move_to(self, trp, wx, wz, follow_terrain=True, wy=None):
        pos, _, _ = self.world(trp)
        if wy is None:
            wy = pos[1]
            if follow_terrain:
                g0, g1 = self.ground(pos[0], pos[2]), self.ground(wx, wz)
                if g0 is not None and g1 is not None:
                    wy = g1 + (pos[1] - g0)
        self.set_world_position(trp, np.array((wx, wy, wz)))

    def set_world_position(self, trp, wpos):
        t = self.edit(trp)
        father = t["m_Father"]["m_PathID"]
        if father and father in self.tr:
            pp, pr, ps = self.world(father)
            local = q_rotate(q_inv(pr), wpos - pp)
            local = local / np.where(ps == 0, 1, ps)
        else:
            local = wpos
        t["m_LocalPosition"] = v_dict(local)
        self._set(trp, t)
        self.refresh_subtree(trp)

    def rotate(self, trp, deg):
        t = self.edit(trp)
        _, wr, _ = self.world(trp)
        new_world = q_mul(q_yaw(deg), wr)
        father = t["m_Father"]["m_PathID"]
        if father and father in self.tr:
            _, pr, _ = self.world(father)
            local = q_mul(q_inv(pr), new_world)
        else:
            local = new_world
        n = math.sqrt(sum(c * c for c in local)) or 1.0
        t["m_LocalRotation"] = q_dict([c / n for c in local])
        self._set(trp, t)
        self.refresh_subtree(trp)

    def set_heading(self, trp, deg):
        _, wr, _ = self.world(trp)
        self.rotate(trp, deg - heading_of(wr))

    def set_scale(self, trp, factor):
        t = self.edit(trp)
        t["m_LocalScale"] = v_dict(v_arr(t["m_LocalScale"]) * factor)
        self._set(trp, t)
        self.refresh_subtree(trp)

    def set_field(self, pid, key, value):
        d = self.edit(pid)
        d[key] = value
        self._set(pid, d)
        if key == "Event_Type" or key == "m_IsActive":
            self.reindex()

    def rename(self, go, text):
        d = self.edit(go)
        d["m_Name"] = text
        self._set(go, d)

    def set_tank(self, ev, entry):
        ext, pid = entry[0], entry[1]
        d = self.edit(ev)
        d["Tank_Prop"] = {"m_FileID": self.sc.ensure_external_path(ext), "m_PathID": pid}
        self._set(ev, d)

    def tank_entry(self, ev):
        p = self.sc.read(ev)["Tank_Prop"]
        if not p["m_FileID"]:
            return None
        ext = self.sc.externals[p["m_FileID"] - 1]
        return next((t for t in self.tanks if t[0] == ext and t[1] == p["m_PathID"]), None)

    def event_of_go(self, go):
        return next((e for e, g in self.event_go.items() if g == go), None)

    def _remap(self, obj, mapping):
        if isinstance(obj, dict):
            if set(obj.keys()) >= {"m_FileID", "m_PathID"} and len(obj) == 2:
                if obj["m_FileID"] == 0 and obj["m_PathID"] in mapping:
                    return {"m_FileID": 0, "m_PathID": mapping[obj["m_PathID"]]}
                return dict(obj)
            return {k: self._remap(v, mapping) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._remap(v, mapping) for v in obj]
        return copy.deepcopy(obj)

    def duplicate(self, go, offset=(DUPLICATE_OFFSET, DUPLICATE_OFFSET), mirror=True):
        """Deep-copy a GameObject hierarchy next to the original; returns the new root GO.
        mirror: also add the copy to every event list that references the original
        (right for tanks; not for copied events, routes or waypoints)."""
        root_tr = self.tr_of_go[go]
        old = []
        for trp in self.subtree(root_tr):
            g = self.go_of_tr[trp]
            old.append(g)
            old.extend(self.comps.get(g, []))
        mapping = {}
        for pid in old:
            mapping[pid] = self._new(pid, None)
        for pid in old:
            self.sc._cache[mapping[pid]] = self._remap(self.sc.read(pid), mapping)
        new_root_tr = mapping[root_tr]
        father = self.tr[root_tr]["m_Father"]["m_PathID"]
        if father and father in self.tr:
            ft = self.edit(father)
            kids = ft["m_Children"]
            at = next((i for i, c in enumerate(kids) if c["m_PathID"] == root_tr), len(kids) - 1)
            kids.insert(at + 1, {"m_FileID": 0, "m_PathID": new_root_tr})
            self._set(father, ft)
        new_go = mapping[go]
        nd = self.edit(new_go)
        nd["m_Name"] = self._unique_name(nd["m_Name"])
        self._set(new_go, nd)

        old_refs = {go, root_tr} | {c for c in self.comps.get(go, [])}
        self.reindex()
        if mirror:
            self._mirror_event_refs(old_refs, mapping)
        if offset:
            pos, _, _ = self.world(new_root_tr)
            self.move_to(new_root_tr, pos[0] + offset[0], pos[2] - offset[1])
        return new_go

    def _unique_name(self, base):
        names = {d["m_Name"] for d in self.go.values()}
        stem = base
        if stem.endswith(")") and " (" in stem:
            head, _, num = stem.rpartition(" (")
            if num[:-1].isdigit():
                stem = head
        i = 1
        while f"{stem} ({i})" in names:
            i += 1
        return f"{stem} ({i})"

    def _mirror_event_refs(self, old_refs, mapping):
        new_events = {mapping[p] for p in old_refs if p in mapping}
        for ev in self.events:
            if ev in mapping.values() or ev in new_events:
                continue
            d = self.sc.read(ev)
            changed = None
            for lst, count in REF_LISTS.items():
                items = d.get(lst)
                if not items:
                    continue
                extra = [{"m_FileID": 0, "m_PathID": mapping[r["m_PathID"]]}
                         for r in items if r["m_FileID"] == 0 and r["m_PathID"] in old_refs]
                if extra:
                    if changed is None:
                        changed = copy.deepcopy(d)
                    before = len(changed[lst])
                    changed[lst] = changed[lst] + extra
                    if changed.get(count) == before:
                        changed[count] = len(changed[lst])
            if changed is not None:
                self._set(ev, changed)

    def delete(self, go, reindex=True):
        """Deactivate a GameObject; tank spawns are also removed from event references,
        waypoints are also detached from their pack. Returns a record of the removed
        references, which restore() can put back."""
        trp = self.tr_of_go[go]
        refs = {go, trp} | set(self.comps.get(go, []))
        record = {"lists": [], "singles": [], "pack": None}
        if any(e in refs for e in self.events) or trp in self.event_trs:
            for ev in self.events:
                if ev in refs:
                    continue
                d = self.sc.read(ev)
                changed = None
                for lst, count in REF_LISTS.items():
                    items = d.get(lst) or []
                    gone = [r for r in items if r["m_FileID"] == 0 and r["m_PathID"] in refs]
                    if gone:
                        keep = [r for r in items if r not in gone]
                        if changed is None:
                            changed = copy.deepcopy(d)
                        if changed.get(count) == len(items):
                            changed[count] = len(keep)
                        changed[lst] = keep
                        record["lists"] += [(ev, lst, dict(r)) for r in gone]
                for f in REF_SINGLES:
                    r = d.get(f)
                    if r and r["m_FileID"] == 0 and r["m_PathID"] in refs:
                        if changed is None:
                            changed = copy.deepcopy(d)
                        changed[f] = {"m_FileID": 0, "m_PathID": 0}
                        record["singles"].append((ev, f, dict(r)))
                if changed is not None:
                    self._set(ev, changed)
        if trp in self.waypoints:
            pack = self.waypoints[trp]
            pt = self.edit(pack)
            kids = [c["m_PathID"] for c in pt["m_Children"]]
            record["pack"] = (pack, kids.index(trp) if trp in kids else len(kids))
            pt["m_Children"] = [c for c in pt["m_Children"] if c["m_PathID"] != trp]
            self._set(pack, pt)
            t = self.edit(trp)
            t["m_Father"] = {"m_FileID": 0, "m_PathID": 0}
            pos, rot, _ = self.world(trp)
            t["m_LocalPosition"] = v_dict(pos)
            t["m_LocalRotation"] = q_dict(rot)
            self._set(trp, t)
            self._wcache.pop(trp, None)
        d = self.edit(go)
        d["m_IsActive"] = False
        self._set(go, d)
        if reindex:
            self.reindex()
        return record

    def restore(self, go, record=None, reindex=True):
        """Reactivate a deleted GameObject; with the record returned by delete(),
        its event references and waypoint pack membership are put back."""
        if record:
            for ev, lst, ref in record["lists"]:
                d = self.edit(ev)
                items = d.get(lst) or []
                if ref not in items:
                    count = REF_LISTS[lst]
                    if d.get(count) == len(items):
                        d[count] = len(items) + 1
                    d[lst] = list(items) + [ref]
                    self._set(ev, d)
            for ev, f, ref in record["singles"]:
                d = self.edit(ev)
                if not d[f]["m_PathID"]:
                    d[f] = ref
                    self._set(ev, d)
            if record["pack"]:
                pack, idx = record["pack"]
                trp = self.tr_of_go[go]
                if pack in self.tr:
                    pos, rot, _ = self.world(trp)
                    pt = self.edit(pack)
                    kids = list(pt["m_Children"])
                    kids.insert(min(idx, len(kids)), {"m_FileID": 0, "m_PathID": trp})
                    pt["m_Children"] = kids
                    self._set(pack, pt)
                    t = self.edit(trp)
                    t["m_Father"] = {"m_FileID": 0, "m_PathID": pack}
                    self._set(trp, t)
                    self._wcache.clear()
                    self.set_world_position(trp, pos)
        d = self.edit(go)
        d["m_IsActive"] = True
        self._set(go, d)
        if reindex:
            self.reindex()

    def copy_spawn(self, go, relationship):
        """New tank spawn copied from spawn GameObject `go`, which may be deleted:
        the copy is active, has the given side and no references to deleted
        objects or empty routes. Returns the new GameObject."""
        new = self.duplicate(go, offset=None)
        ev = self.event_of_go(new)
        if not self.go[new]["m_IsActive"]:
            self.set_field(new, "m_IsActive", True)
        if self.sc.read(ev)["Relationship"] != relationship:
            self.set_field(ev, "Relationship", relationship)
        self.drop_dead_refs(ev)
        return new

    def drop_dead_refs(self, ev):
        """Clear the references of event `ev` to deleted (inactive) objects and to
        routes without waypoints, for example after copying a deleted spawn."""
        owner = {c: g for g, comps in self.comps.items() for c in comps}

        def dead(pid):
            g = pid if pid in self.go else self.go_of_tr.get(pid, owner.get(pid))
            return g is not None and not self.active(g)

        d = self.edit(ev)
        changed = False
        for lst, count in REF_LISTS.items():
            items = d.get(lst) or []
            keep = [r for r in items if r["m_FileID"] != 0 or not dead(r["m_PathID"])]
            if len(keep) != len(items):
                if d.get(count) == len(items):
                    d[count] = len(keep)
                d[lst] = keep
                changed = True
        for f in REF_SINGLES:
            r = d.get(f)
            if r and r["m_FileID"] == 0 and r["m_PathID"] and dead(r["m_PathID"]):
                d[f] = {"m_FileID": 0, "m_PathID": 0}
                changed = True
        for f in PACK_FIELDS:
            r = d.get(f)
            if r and r["m_FileID"] == 0 and r["m_PathID"]:
                trp = self.pack_tr(r["m_PathID"])
                if not trp or not self.tr[trp]["m_Children"]:
                    d[f] = {"m_FileID": 0, "m_PathID": 0}
                    changed = True
        if changed:
            self._set(ev, d)
        return changed

    def clear_to_terrain(self):
        """Reduce the scene to its terrain: deactivate all scenery, AI tank spawns,
        mission events and unused waypoints. Kept are the game systems, light,
        terrain, navigation mesh, the player's spawn, the 'mission failed'
        events that trigger on the player's destruction, and objects over holes
        in the terrain (tunnels), which would otherwise leave open pits. Call inside
        begin()/commit(). Returns (events, waypoints, objects) removed."""
        sc = self.sc
        keep = {e for e in self.spawns
                if sc.read(e)["Tank_ID"] == 1 and self.active(self.event_go[e])}
        player = {self.event_go[e] for e in keep}
        player |= {self.tr_of_go[g] for g in player if g in self.tr_of_go}
        for e in self.events:
            d = sc.read(e)
            refs = [r["m_PathID"] for r in d.get("Trigger_Tanks") or [] if r["m_FileID"] == 0]
            if d["Event_Type"] == 11 and d["Trigger_Type"] == 1 and refs and all(r in player for r in refs):
                keep.add(e)
        removed = [e for e in self.events if e not in keep and self.active(self.event_go[e])]
        for e in removed:
            self.delete(self.event_go[e], reindex=False)

        used_packs = set()
        for e in keep:
            d = sc.read(e)
            for f in PACK_FIELDS:
                p = d.get(f)
                if p and p["m_FileID"] == 0:
                    used_packs.add(self.pack_tr(p["m_PathID"]))
        points = [w for w, p in self.waypoints.items() if p not in used_packs]
        for w in points:
            self.delete(self.go_of_tr[w], reindex=False)

        holes = self.terrain.holes if self.terrain is not None else np.zeros((0, 2))
        covering = set()
        for g, corners in self.foot.items():
            if len(holes) and self.active(g):
                lo, hi = corners.min(0) - HOLE_MARGIN, corners.max(0) + HOLE_MARGIN
                if np.any(np.all((holes >= lo) & (holes <= hi), axis=1)):
                    covering.add(self.object_root(g))
        needed = set()
        for g in covering:
            while g is not None and g not in needed:
                needed.add(g)
                g = self.parent_go(g)

        objects = 0

        def prune(g):
            nonlocal objects
            if g in covering or not self.go[g]["m_IsActive"]:
                return
            if g in needed:
                for c in self.tr[self.tr_of_go[g]]["m_Children"]:
                    if self.go_of_tr.get(c["m_PathID"]) is not None:
                        prune(self.go_of_tr[c["m_PathID"]])
                return
            nd = self.edit(g)
            nd["m_IsActive"] = False
            self._set(g, nd)
            objects += 1

        keep_roots = self.system_roots() | {self.top_level(self.event_go[e]) for e in keep}
        keep_roots |= {self.go_of_tr[p] for p in self.packs}
        for g in list(self.go):
            if self.parent_go(g) is not None or g in keep_roots:
                continue
            kinds = {sc.type_name(c) if sc.type_name(c) != "MonoBehaviour" else self.script_class(c)
                     for c in self.comps.get(g, [])}
            if kinds & {"Terrain", "Light", "NavMeshSurface", "Game_Controller_CS"}:
                continue
            prune(g)
        self.reindex()
        return len(removed), len(points), objects

    def assign_pack(self, ev, pack_tr):
        d = self.edit(ev)
        d["WayPoint_Pack"] = {"m_FileID": 0, "m_PathID": self.go_of_tr[pack_tr] if pack_tr else 0}
        self._set(ev, d)
        self.reindex()

    def _marker(self):
        stack = getattr(self, "undo_stack", [])
        return (len(stack), id(stack[-1]) if stack else None)

    @property
    def dirty(self):
        """True when the undo history differs from the state at opening or at
        the last save (undoing every change returns to 'not dirty')."""
        return self._marker() != self.saved_marker

    def save(self):
        size = self.sc.save()
        self.saved_marker = self._marker()
        return size
