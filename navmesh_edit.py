# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Rebuilding the AI route map (NavMesh) of a custom scenario.

The AI plans its way between waypoints over the scene's NavMesh: a map of
drivable ground that Unity bakes when a level is built. It is stored in a
NavMeshData asset (in the level's sharedassets file) as tiles of 128 x 128 m in
Unity's version of the Detour tile format (version 16):

  header (72 bytes): magic "DNAV", version, tile x/y, layer, counts of
      polygons, vertices, detail meshes, detail vertices, detail triangles and
      bounding-volume nodes, tile bounds, quantisation factor (1 / cell size)
  vertices (3 floats), polygons (6 vertex indices, 6 neighbours, flags,
      vertex count, area; neighbour = polygon index + 1 inside the tile, or
      0x8000 | side for the adjacent tile: 0 east, 2 north, 4 west, 6 south;
      corners clockwise), detail meshes, detail vertices, detail triangles
      (3 indices + edge flags), bounding-volume tree (quantised bounds and
      polygon index or negative escape offset; 2 x polygon count nodes)

All tiles of the game decode and re-encode byte for byte with this layout.

A rebuild computes the drivable ground on a 0.5 m grid: terrain no steeper
than the agent slope, no terrain holes, no obstacles, outside "not walkable"
modifier volumes, eroded by the agent radius. Obstacles are learned from the
game's own route maps (layouts/nav_types.json: the share of each object type's
footprint that the original maps treat as drivable; below OBSTACLE_SHARE it is
an obstacle, with the outlines of its physics colliders as seen from above,
as Unity bakes them); objects with a NavMeshModifier "ignore from build"
never are.
The drivable area becomes a 2:1 balanced quadtree of squares (from POLY_SIZE
up to MAX_LEAF x POLY_SIZE metres where the ground is open and flat); squares
with smaller neighbours carry the shared edge midpoints, so neighbouring
polygons always share whole edges as Detour requires. The new NavMeshData goes into the
scenario's own sharedassetsN.assets, like a painted terrain.
"""

import copy
import json
import math
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw

from object_import import base_name, category
from scene_io import SceneFile
from scene_model import mesh_aabb, q_rotate
from terrain_edit import own_assets_name

MAGIC = 0x444E4156
VERSION = 16
CELL = 0.5
TILE_CELLS = 256
POLY_SIZE = 4.0
OBSTACLE_SHARE = 0.3
SURFACE_MIN = 0.3

HEADER = struct.Struct("<11i3f3ff")
POLY = struct.Struct("<6H6HIBBBB")
DETAIL_MESH = struct.Struct("<IIHH")
DETAIL_TRI = struct.Struct("<4H")
BVNODE = struct.Struct("<6Hi")
EDGE_BOUNDARY = 1


# ---- tile format -------------------------------------------------------------

def decode_tile(blob):
    h = HEADER.unpack_from(blob, 0)
    magic, version, tx, ty, layer, npoly, nvert, ndm, ndv, ndt, nbv = h[:11]
    t = {"magic": magic, "version": version, "x": tx, "y": ty, "layer": layer,
         "bmin": h[11:14], "bmax": h[14:17], "quant": h[17]}
    off = HEADER.size
    t["verts"] = np.frombuffer(blob, "<f4", nvert * 3, off).reshape(-1, 3).copy()
    off += nvert * 12
    t["polys"] = [POLY.unpack_from(blob, off + i * POLY.size) for i in range(npoly)]
    off += npoly * POLY.size
    t["dmeshes"] = [DETAIL_MESH.unpack_from(blob, off + i * DETAIL_MESH.size) for i in range(ndm)]
    off += ndm * DETAIL_MESH.size
    t["dverts"] = np.frombuffer(blob, "<f4", ndv * 3, off).reshape(-1, 3).copy()
    off += ndv * 12
    t["dtris"] = [DETAIL_TRI.unpack_from(blob, off + i * DETAIL_TRI.size) for i in range(ndt)]
    off += ndt * DETAIL_TRI.size
    t["bv"] = [BVNODE.unpack_from(blob, off + i * BVNODE.size) for i in range(nbv)]
    off += nbv * BVNODE.size
    t["trailing"] = blob[off:]
    return t


def encode_tile(t):
    out = bytearray(HEADER.pack(t["magic"], t["version"], t["x"], t["y"], t["layer"], len(t["polys"]),
                                len(t["verts"]), len(t["dmeshes"]), len(t["dverts"]), len(t["dtris"]),
                                len(t["bv"]), *t["bmin"], *t["bmax"], t["quant"]))
    out += np.asarray(t["verts"], dtype="<f4").tobytes()
    for p in t["polys"]:
        out += POLY.pack(*p)
    for d in t["dmeshes"]:
        out += DETAIL_MESH.pack(*d)
    out += np.asarray(t["dverts"], dtype="<f4").reshape(-1, 3).tobytes()
    for d in t["dtris"]:
        out += DETAIL_TRI.pack(*d)
    for b in t["bv"]:
        out += BVNODE.pack(*b)
    out += t.get("trailing", b"")
    return bytes(out)


def bv_tree(polys, verts, bmin, quant):
    """Detour bounding-volume tree: median split along the longest axis; leaves
    hold the polygon index, inner nodes the negative escape offset. Followed by
    one unused node (Unity stores 2 x polygon count nodes)."""
    items = []
    for i, p in enumerate(polys):
        v = verts[list(p[:p[13]])]
        lo = np.clip(np.floor((v.min(0) - bmin) * quant), 0, 65535).astype(int)
        hi = np.clip(np.ceil((v.max(0) - bmin) * quant), 0, 65535).astype(int)
        items.append((lo, hi, i))
    nodes = []

    def subdivide(a, b):
        cur = len(nodes)
        nodes.append(None)
        if b - a == 1:
            lo, hi, i = items[a]
            nodes[cur] = (*map(int, lo), *map(int, hi), i)
            return
        part = items[a:b]
        lo = np.min([it[0] for it in part], axis=0)
        hi = np.max([it[1] for it in part], axis=0)
        ext = hi - lo
        axis = 0 if ext[0] >= ext[1] and ext[0] >= ext[2] else (1 if ext[1] >= ext[2] else 2)
        part.sort(key=lambda it: it[0][axis])
        items[a:b] = part
        mid = a + (b - a) // 2
        subdivide(a, mid)
        subdivide(mid, b)
        nodes[cur] = (*map(int, lo), *map(int, hi), -(len(nodes) - cur))
    if items:
        subdivide(0, len(items))
    nodes.append((0, 0, 0, 0, 0, 0, 0))
    return nodes


# ---- learned object types ---------------------------------------------------------

def nav_types_path():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "layouts", "nav_types.json")


def load_nav_types():
    try:
        with open(nav_types_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def is_obstacle(name, types):
    key = base_name(name)
    if key in types:
        return types[key] < OBSTACLE_SHARE
    cat = category(key)
    if cat == "Buildings":
        return True
    return cat == "Trees and hedges" and "(Nav)" not in key and "hedge" not in key.lower()


# ---- the scene's route map ---------------------------------------------------------

class NavMesh:
    """The NavMesh of an open scenario (SceneModel `model`)."""

    def __init__(self, model):
        self.model = model
        sc = model.sc
        self.surface = next((p for p in model.mb if model.script_class(p) == "NavMeshSurface"), None)
        if self.surface is None:
            raise ValueError("this mission has no NavMesh")
        ref = sc.read(self.surface)["m_NavMeshData"]
        if not ref["m_FileID"]:
            raise ValueError("the NavMesh data is not in a separate asset file")
        self.file = sc.externals[ref["m_FileID"] - 1]
        self.pid = ref["m_PathID"]
        self.own_file = own_assets_name(model.index)
        self.holder = (model.own_assets() if self.file == self.own_file
                       else SceneFile(model.ctx, os.path.join(model.ctx.data_dir, self.file)))
        self.data = self.holder.read(self.pid)
        self.settings = self.data["m_NavMeshBuildSettings"]

    @property
    def owned(self):
        return self.file == self.own_file

    def tiles(self):
        return [decode_tile(bytes(t["m_MeshData"])) for t in self.data["m_NavMeshTiles"]]

    # ---- drivable ground ----

    def ignored(self):
        """GameObjects excluded from the bake (NavMeshModifier, ignore from
        build), with everything below them."""
        m = self.model
        out = set()
        for p in m.mb:
            if m.script_class(p) == "NavMeshModifier" and m.sc.read(p).get("m_IgnoreFromBuild"):
                g = m.sc.read(p)["m_GameObject"]["m_PathID"]
                if g in m.tr_of_go:
                    out |= {m.go_of_tr[t] for t in m.subtree(m.tr_of_go[g])}
        return out

    def volumes(self):
        """[(area, corners (4 x (x, z)), y0, y1)] of the NavMeshModifierVolumes."""
        m = self.model
        out = []
        for p in m.mb:
            if m.script_class(p) != "NavMeshModifierVolume":
                continue
            d = m.sc.read(p)
            g = d["m_GameObject"]["m_PathID"]
            if g not in m.tr_of_go or not m.active(g):
                continue
            pos, rot, scl = m.world(m.tr_of_go[g])
            size = np.array([d["m_Size"][k] for k in "xyz"]) * np.abs(scl)
            centre = pos + q_rotate(rot, np.array([d["m_Center"][k] for k in "xyz"]) * scl)
            corners = [centre + q_rotate(rot, np.array((sx * size[0] / 2, 0.0, sz * size[2] / 2)))
                       for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            out.append((d["m_Area"], [(c[0], c[2]) for c in corners],
                        centre[1] - size[1] / 2, centre[1] + size[1] / 2))
        return out

    def colliders(self):
        """[(GameObject, outline seen from above (x, z) points, top height)] of
        the active, solid colliders of the scene's objects."""
        m = self.model
        sc = m.sc
        out = []
        for g, comps in m.comps.items():
            if g not in m.tr_of_go or not m.active(g):
                continue
            for c in comps:
                t = sc.type_name(c)
                if t not in ("BoxCollider", "MeshCollider", "SphereCollider", "CapsuleCollider"):
                    continue
                d = sc.read(c)
                if not d.get("m_Enabled", True) or d.get("m_IsTrigger"):
                    continue
                if t == "BoxCollider":
                    centre = np.array([d["m_Center"][k] for k in "xyz"])
                    half = np.array([d["m_Size"][k] for k in "xyz"]) / 2
                elif t == "MeshCollider":
                    box = mesh_aabb(m.ctx, sc, d["m_Mesh"])
                    if box is None:
                        continue
                    centre, half = np.asarray(box[0], dtype=float), np.asarray(box[1], dtype=float)
                elif t == "SphereCollider":
                    centre = np.array([d["m_Center"][k] for k in "xyz"])
                    half = np.full(3, d["m_Radius"])
                else:
                    centre = np.array([d["m_Center"][k] for k in "xyz"])
                    half = np.full(3, d["m_Radius"])
                    half[d.get("m_Direction", 1)] = max(d["m_Height"] / 2, d["m_Radius"])
                pos, rot, scl = m.world(m.tr_of_go[g])
                corners = np.array([pos + q_rotate(rot, scl * (centre + half * np.array((sx, sy, sz))))
                                    for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
                out.append((g, convex_hull(corners[:, [0, 2]]), float(corners[:, 1].max())))
        return out

    def ground(self, types=None):
        """(walk, area, surface) grids on the 0.5 m grid over the terrain:
        drivable cells before erosion, area codes, and the height of drivable
        object surfaces (bridges, ramps) where they lie above the terrain."""
        m = self.model
        t = m.terrain
        if t is None:
            raise ValueError("this mission has no terrain")
        types = load_nav_types() if types is None else types
        n = int(round(t.size_x / CELL))
        ox, oz = t.origin[0], t.origin[2]
        res = t.res
        centres = (np.arange(n) + 0.5) * CELL
        iu = np.clip(np.round(centres / t.size_x * (res - 1)).astype(int), 0, res - 1)
        iv = np.clip(np.round(centres / t.size_z * (res - 1)).astype(int), 0, res - 1)
        gy, gx = np.gradient(t.heights, t.size_z / (res - 1), t.size_x / (res - 1))
        slope = np.degrees(np.arctan(np.hypot(gx, gy)))
        walk = slope[iv][:, iu] <= self.settings["agentSlope"]
        terrain_h = t.heights[iv][:, iu]
        if len(t.holes):
            hole = np.zeros((n, n), dtype=bool)
            span = max(1, int(round(t.size_x / 1024 / CELL)))
            hx = np.floor((t.holes[:, 0] - ox) / CELL - span / 2).astype(int)
            hz = np.floor((t.holes[:, 1] - oz) / CELL - span / 2).astype(int)
            for dx in range(span):
                for dz in range(span):
                    ok = (hx + dx >= 0) & (hz + dz >= 0) & (hx + dx < n) & (hz + dz < n)
                    hole[hz[ok] + dz, hx[ok] + dx] = True
            walk &= ~hole

        def raster(polys, value=1):
            img = Image.new("L", (n, n), 0)
            d = ImageDraw.Draw(img)
            for pts in polys:
                d.polygon([((x - ox) / CELL, (z - oz) / CELL) for x, z in pts], fill=value)
            return np.asarray(img)

        def local(pts):
            """(row slice, col slice, mask) of one polygon, drawn in its own box."""
            px = [(x - ox) / CELL for x, _ in pts]
            pz = [(z - oz) / CELL for _, z in pts]
            c0, c1 = max(0, int(min(px))), min(n, int(max(px)) + 2)
            r0, r1 = max(0, int(min(pz))), min(n, int(max(pz)) + 2)
            if c0 >= c1 or r0 >= r1:
                return None
            img = Image.new("L", (c1 - c0, r1 - r0), 0)
            ImageDraw.Draw(img).polygon([(x - c0, z - r0) for x, z in zip(px, pz)], fill=1)
            return slice(r0, r1), slice(c0, c1), np.asarray(img) > 0

        ignored = self.ignored()
        obstacles, surfaces = [], []
        for g, outline, top in self.colliders():
            if g in ignored:
                continue
            root = m.object_root(g)
            if root in ignored:
                continue
            name = m.name(root)
            centre = np.mean(outline, axis=0)
            i = min(n - 1, max(0, int((centre[1] - oz) / CELL)))
            j = min(n - 1, max(0, int((centre[0] - ox) / CELL)))
            low = top <= terrain_h[i, j] + self.settings["agentClimb"]
            if is_obstacle(name, types) and not low:
                obstacles.append(outline)
            elif category(base_name(name)) == "Roads and rails":
                surfaces.append((outline, top))
        walk &= raster(obstacles) == 0
        surface = np.full((n, n), -np.inf)
        for corners, top in surfaces:
            box = local(corners)
            if box is None:
                continue
            rs, cs, cov = box
            raised = cov & (top > terrain_h[rs, cs] + SURFACE_MIN)
            surface[rs, cs][raised] = np.maximum(surface[rs, cs][raised], top)
            walk[rs, cs][raised] = True
        area = np.zeros((n, n), dtype=np.uint8)
        hgt = np.maximum(terrain_h, surface)
        for code, corners, y0, y1 in self.volumes():
            box = local(corners)
            if box is None:
                continue
            rs, cs, cov = box
            inside = cov & (hgt[rs, cs] >= y0) & (hgt[rs, cs] <= y1)
            if code == 1:
                walk[rs, cs][inside] = False
            else:
                area[rs, cs][inside] = code
        return walk, area, surface

    # ---- tiles ----

    def build(self, types=None, poly_size=POLY_SIZE):
        """New tiles for the current scene. Returns (list of tile dicts, the
        eroded drivable grid)."""
        m = self.model
        t = m.terrain
        walk, area, surface = self.ground(types)
        drive = walk & (distance_to_blocked(walk) * CELL >= self.settings["agentRadius"])
        n = drive.shape[0]
        k = int(round(poly_size / CELL))
        nc = n // k
        centre = drive[k // 2::k, k // 2::k][:nc, :nc]
        cell_area = area[k // 2::k, k // 2::k][:nc, :nc]
        ox, oz = t.origin[0], t.origin[2]
        per_tile = int(round(TILE_CELLS * CELL / poly_size))
        ntiles = nc // per_tile
        old = self.tiles()
        ybounds = (old[0]["bmin"][1], old[0]["bmax"][1]) if old else (float(t.heights.min()), float(t.heights.max()))

        def height(x, z):
            g = m.ground(x, z)
            g = t.origin[1] if g is None else g
            i = min(n - 1, max(0, int((z - oz) / CELL)))
            j = min(n - 1, max(0, int((x - ox) / CELL)))
            return max(g, surface[i, j])

        tiles = []
        tile_m = TILE_CELLS * CELL
        for ty in range(ntiles):
            for tx in range(ntiles):
                sub = centre[ty * per_tile:(ty + 1) * per_tile, tx * per_tile:(tx + 1) * per_tile]
                if not sub.any():
                    continue
                bmin = (ox + tx * tile_m, ybounds[0], oz + ty * tile_m)
                bmax = (ox + (tx + 1) * tile_m, ybounds[1], oz + (ty + 1) * tile_m)
                tiles.append(build_tile(sub, centre, cell_area, tx, ty, per_tile, poly_size, bmin, bmax, height,
                                        int(math.floor(bmin[0] / tile_m)), int(math.floor(bmin[2] / tile_m))))
        return tiles, drive

    def write(self, tiles):
        """Store the tiles in the scenario's own copy of the NavMeshData and
        point the NavMeshSurface at it (inside model.begin/commit; the asset file
        is saved with the scene). Returns the asset file's pid of the NavMeshData."""
        m = self.model
        assets = m.own_assets()
        data = copy.deepcopy(self.data)
        hashes = [t["m_Hash"] for t in self.data["m_NavMeshTiles"]]
        blank = hashes[0] if hashes else {f"bytes[{i}]": 0 for i in range(16)}
        data["m_NavMeshTiles"] = [{"m_MeshData": encode_tile(t), "m_Hash": copy.deepcopy(blank)} for t in tiles]
        data["m_Name"] = "NavMesh-Scenario"
        if self.owned:
            assets.write(self.pid, data)
            pid = self.pid
        else:
            src = self.holder
            tid = assets.ensure_type(src, src.type_id(self.pid))
            pid = assets.add_foreign(tid, src.type_name(self.pid), src.nodes_for(self.pid), data)
        fid = m.sc.ensure_external_path(self.own_file)
        d = m.edit(self.surface)
        d["m_NavMeshData"] = {"m_FileID": fid, "m_PathID": pid}
        m._set(self.surface, d)
        self.file, self.pid, self.data = self.own_file, pid, data
        self.holder = assets
        return pid


MAX_LEAF = 8
FLAT_TOLERANCE = 1.0


def build_tile(sub, coarse, cell_area, tx, ty, per_tile, poly_size, bmin, bmax, height, tile_x, tile_y):
    """One tile from its drivable coarse cells `sub` (per_tile x per_tile, rows =
    z): a 2:1 balanced quadtree of squares (up to MAX_LEAF cells where the
    ground is drivable, of one area and flat within FLAT_TOLERANCE metres);
    squares with smaller neighbours get the shared edge midpoints, so all
    neighbouring polygons share whole edges."""
    nc = coarse.shape[0]
    areas = cell_area[ty * per_tile:(ty + 1) * per_tile, tx * per_tile:(tx + 1) * per_tile]
    gh = np.array([[height(bmin[0] + c * poly_size, bmin[2] + r * poly_size) for c in range(per_tile + 1)]
                   for r in range(per_tile + 1)])

    def uniform(r, c, s):
        block = sub[r:r + s, c:c + s]
        if not block.all():
            return False
        a = areas[r:r + s, c:c + s]
        if (a != a[0, 0]).any():
            return False
        g = gh[r:r + s + 1, c:c + s + 1]
        t = np.linspace(0.0, 1.0, s + 1)
        ty_, tx_ = t[:, None], t[None, :]
        plane = (g[0, 0] * (1 - ty_) * (1 - tx_) + g[0, -1] * (1 - ty_) * tx_
                 + g[-1, 0] * ty_ * (1 - tx_) + g[-1, -1] * ty_ * tx_)
        return np.abs(plane - g).max() <= FLAT_TOLERANCE

    leaves = {}

    def split(r, c, s):
        if s <= MAX_LEAF and uniform(r, c, s):
            leaves[(r, c)] = s
        elif s > 1:
            h = s // 2
            for dr in (0, h):
                for dc in (0, h):
                    split(r + dr, c + dc, h)
        elif sub[r, c]:
            leaves[(r, c)] = 1
    split(0, 0, per_tile)

    owner = -np.ones((per_tile, per_tile), dtype=np.int64)
    keys = []

    def rebuild_owner():
        owner[:] = -1
        keys[:] = list(leaves)
        for i, (r, c) in enumerate(keys):
            s = leaves[(r, c)]
            owner[r:r + s, c:c + s] = i

    def side_cells(r, c, s, side):
        if side == "w":
            return [(r + k, c - 1) for k in range(s)] if c > 0 else []
        if side == "e":
            return [(r + k, c + s) for k in range(s)] if c + s < per_tile else []
        if side == "s":
            return [(r - 1, c + k) for k in range(s)] if r > 0 else []
        return [(r + s, c + k) for k in range(s)] if r + s < per_tile else []

    def neighbour_sizes(r, c, s, side):
        out = set()
        for rr, cc in side_cells(r, c, s, side):
            o = owner[rr, cc]
            if o >= 0:
                out.add(leaves[keys[o]])
        return out

    def subdivide(key):
        r, c = key
        s = leaves.pop(key)
        h = s // 2
        for dr in (0, h):
            for dc in (0, h):
                leaves[(r + dr, c + dc)] = h

    changed = True
    while changed:
        changed = False
        rebuild_owner()
        for key in list(leaves):
            r, c = key
            s = leaves[key]
            smaller = [side for side in "wnes" if any(x < s for x in neighbour_sizes(r, c, s, side))]
            too_small = any(x < s / 2 for side in "wnes" for x in neighbour_sizes(r, c, s, side))
            if too_small or len(smaller) > 2:
                subdivide(key)
                changed = True
                break

    rebuild_owner()
    verts, vid = [], {}

    def vert(r2, c2):
        """Vertex at half-cell grid point (r2, c2) = (2 * row, 2 * col)."""
        if (r2, c2) not in vid:
            x = bmin[0] + c2 / 2 * poly_size
            z = bmin[2] + r2 / 2 * poly_size
            vid[(r2, c2)] = len(verts)
            verts.append((x, height(x, z), z))
        return vid[(r2, c2)]

    polys_pts = []
    for key in keys:
        r, c = key
        s = leaves[key]
        R, C, S = 2 * r, 2 * c, 2 * s
        mids = {side: any(x < s for x in neighbour_sizes(r, c, s, side)) for side in "wnes"}
        pts = [(R, C)]
        if mids["w"]:
            pts.append((R + S // 2, C))
        pts.append((R + S, C))
        if mids["n"]:
            pts.append((R + S, C + S // 2))
        pts.append((R + S, C + S))
        if mids["e"]:
            pts.append((R + S // 2, C + S))
        pts.append((R, C + S))
        if mids["s"]:
            pts.append((R, C + S // 2))
        polys_pts.append(pts)

    segs = {}
    for pi, pts in enumerate(polys_pts):
        for j in range(len(pts)):
            a, b = pts[j], pts[(j + 1) % len(pts)]
            segs.setdefault((min(a, b), max(a, b)), []).append(pi)

    last = 2 * per_tile
    polys, dmeshes, dverts, dtris = [], [], [], []
    for pi, pts in enumerate(polys_pts):
        idx = [vert(*p) for p in pts]
        neis = []
        for j in range(len(pts)):
            a, b = pts[j], pts[(j + 1) % len(pts)]
            others = [q for q in segs[(min(a, b), max(a, b))] if q != pi]
            if others:
                neis.append(others[0] + 1)
                continue
            side = None
            if a[1] == b[1] == 0:
                side, cells = 4, [(ty * per_tile + rr // 2, tx * per_tile - 1) for rr in range(min(a[0], b[0]), max(a[0], b[0]), 2)]
            elif a[1] == b[1] == last:
                side, cells = 0, [(ty * per_tile + rr // 2, (tx + 1) * per_tile) for rr in range(min(a[0], b[0]), max(a[0], b[0]), 2)]
            elif a[0] == b[0] == 0:
                side, cells = 6, [(ty * per_tile - 1, tx * per_tile + cc // 2) for cc in range(min(a[1], b[1]), max(a[1], b[1]), 2)]
            elif a[0] == b[0] == last:
                side, cells = 2, [((ty + 1) * per_tile, tx * per_tile + cc // 2) for cc in range(min(a[1], b[1]), max(a[1], b[1]), 2)]
            ok = side is not None and any(0 <= gr < nc and 0 <= gc < nc and coarse[gr, gc] for gr, gc in cells)
            neis.append(0x8000 | side if ok else 0)
        nv = len(idx)
        r, c = keys[pi]
        area = int(areas[r, c])
        polys.append(tuple(idx) + (0,) * (6 - nv) + tuple(neis) + (0,) * (6 - nv) + (1 << area, nv, area, 0, 0))
        # detail mesh: the centre as one extra vertex, a fan of triangles to the polygon edges
        cx = bmin[0] + (c + leaves[keys[pi]] / 2) * poly_size
        cz = bmin[2] + (r + leaves[keys[pi]] / 2) * poly_size
        dmeshes.append((len(dverts), len(dtris), 1, nv))
        dverts.append((cx, height(cx, cz), cz))
        for j in range(nv):
            dtris.append((nv, j, (j + 1) % nv, EDGE_BOUNDARY << 2))
    verts = np.array(verts, dtype=np.float32)
    quant = 1.0 / CELL
    return {"magic": MAGIC, "version": VERSION, "x": tile_x, "y": tile_y, "layer": 0,
            "bmin": bmin, "bmax": bmax, "quant": quant, "verts": verts, "polys": polys,
            "dmeshes": dmeshes, "dverts": np.array(dverts, dtype=np.float32).reshape(-1, 3), "dtris": dtris,
            "bv": bv_tree(polys, verts, np.array(bmin), quant), "trailing": b""}


def convex_hull(points):
    """Convex hull of 2-D points (monotone chain), as a list of (x, z)."""
    pts = sorted(set(map(tuple, np.round(points, 4))))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def distance_to_blocked(walk):
    """Approximate distance (in cells) from each cell to the nearest blocked
    cell or the grid border (3-4 chamfer, two passes)."""
    big = 10 ** 6
    d = np.where(walk, big, 0).astype(np.int64)
    d[0, :] = d[-1, :] = d[:, 0] = d[:, -1] = 0
    h = d.shape[0]
    idx = np.arange(d.shape[1]) * 3

    def along(row):
        a = np.minimum.accumulate(row - idx) + idx
        b = (np.minimum.accumulate((row + idx)[::-1]) - idx[::-1])[::-1]
        return np.minimum(np.minimum(row, a), b)
    for y in range(1, h):
        prev = d[y - 1]
        cand = np.minimum(np.minimum(prev + 3, np.r_[big, prev[:-1]] + 4), np.r_[prev[1:], big] + 4)
        d[y] = along(np.minimum(d[y], cand))
    for y in range(h - 2, -1, -1):
        nxt = d[y + 1]
        cand = np.minimum(np.minimum(nxt + 3, np.r_[big, nxt[:-1]] + 4), np.r_[nxt[1:], big] + 4)
        d[y] = along(np.minimum(d[y], cand))
    return d / 3.0


__all__ = ["NavMesh", "decode_tile", "encode_tile", "bv_tree", "load_nav_types", "is_obstacle",
           "distance_to_blocked"]
