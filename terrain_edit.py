# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Terrain texture painting for custom scenarios.

A terrain's ground textures are blended from up to 8 layers; the blend weights
are two RGBA "SplatAlpha" textures (one channel per layer, the 8 weights of a
pixel add up to 255). The terrain data is shared by many missions, so painting
first gives the scenario its own copy: the TerrainData and its weight textures
are copied into the scenario's own sharedassetsN.assets (a copy of the source
mission's file, loaded by the game with the scenario) and the scenario's
Terrain and TerrainCollider are pointed at the copy. The layers themselves
(textures, tints) and all other terrain data stay shared.
"""

import copy
import os

import numpy as np

from scene_io import SceneFile


def own_assets_name(index):
    return f"sharedassets{index}.assets"


def mip_chain(rgba):
    """Bytes of a full mip chain (box filtered) for an (h, w, 4) uint8 image."""
    out = [rgba.astype(np.uint8)]
    cur = rgba.astype(np.uint32)
    while cur.shape[0] > 1 or cur.shape[1] > 1:
        h, w = max(1, cur.shape[0] // 2), max(1, cur.shape[1] // 2)
        fy, fx = cur.shape[0] // h, cur.shape[1] // w
        cur = (cur.reshape(h, fy, w, fx, 4).sum((1, 3)) + (fy * fx) // 2) // (fy * fx)
        out.append(cur.astype(np.uint8))
    return b"".join(m.tobytes() for m in out)


def read_alpha(data):
    """Top mip level of a SplatAlpha texture (raw rows, south first) as an
    (h, w, 4) uint8 array."""
    w, h = data["m_Width"], data["m_Height"]
    if data["m_TextureFormat"] != 4:
        raise ValueError("unsupported splat texture format")
    return np.frombuffer(bytes(data["image data"])[:w * h * 4], dtype=np.uint8).reshape(h, w, 4).copy()


def write_alpha(data, arr):
    """Texture data with its pixels replaced by arr (all mip levels rebuilt)."""
    blob = mip_chain(arr)
    if data["m_MipCount"] > 1 and len(blob) != data["m_CompleteImageSize"]:
        raise ValueError("splat texture size mismatch")
    out = dict(data)
    out["image data"] = blob if data["m_MipCount"] > 1 else arr.tobytes()
    return out


def copy_terrain(ctx, assets, src_file, td_pid, alphas=None):
    """Copy TerrainData td_pid of game file src_file and its weight textures into
    `assets` (a SceneFile), optionally with new weights. Returns
    (new TerrainData pid, [new texture pids])."""
    src = SceneFile(ctx, os.path.join(ctx.data_dir, src_file))
    td = copy.deepcopy(src.read(td_pid))
    tex_old = [p["m_PathID"] for p in td["m_SplatDatabase"]["m_AlphaTextures"]]
    if any(p["m_FileID"] != 0 for p in td["m_SplatDatabase"]["m_AlphaTextures"]):
        raise ValueError("the terrain's weight textures are stored elsewhere")
    mapping = {}
    for i, p in enumerate(tex_old):
        data = copy.deepcopy(src.read(p))
        if alphas is not None:
            data = write_alpha(data, alphas[i])
        tid = assets.ensure_type(src, src.type_id(p))
        mapping[p] = assets.add_foreign(tid, src.type_name(p), src.nodes_for(p), data)

    def remap(v):
        if isinstance(v, dict):
            if set(v) == {"m_FileID", "m_PathID"}:
                if not v["m_PathID"]:
                    return v
                if v["m_FileID"] == 0:
                    if v["m_PathID"] in mapping:
                        return {"m_FileID": 0, "m_PathID": mapping[v["m_PathID"]]}
                    return {"m_FileID": assets.ensure_external_path(src_file), "m_PathID": v["m_PathID"]}
                return {"m_FileID": assets.ensure_external_path(src.externals[v["m_FileID"] - 1]),
                        "m_PathID": v["m_PathID"]}
            return {k: remap(x) for k, x in v.items()}
        if isinstance(v, list):
            return [remap(x) for x in v]
        return v
    tid = assets.ensure_type(src, src.type_id(td_pid))
    new_td = assets.add_foreign(tid, src.type_name(td_pid), src.nodes_for(td_pid), remap(td))
    return new_td, [mapping[p] for p in tex_old]


def terrain_components(sc):
    """Pids of the Terrain and TerrainCollider components in a scene file."""
    return [p for p in sc.all_pids() if sc.type_name(p) in ("Terrain", "TerrainCollider")]


class TerrainPaint:
    """Painting session on the open scenario's terrain (SceneModel `model`)."""

    def __init__(self, model):
        self.model = model
        ctx, sc = model.ctx, model.sc
        self.own_file = own_assets_name(model.index)
        self.comps = terrain_components(sc)
        tpid = next(p for p in self.comps if sc.type_name(p) == "Terrain")
        ref = sc.read(tpid)["m_TerrainData"]
        if not ref["m_FileID"]:
            raise ValueError("the terrain data is not in a separate asset file")
        self.src_file = sc.externals[ref["m_FileID"] - 1]
        self.td_pid = ref["m_PathID"]
        self.owned = self.src_file == self.own_file
        self.assets = SceneFile(ctx, os.path.join(ctx.data_dir, self.own_file))
        holder = self.assets if self.owned else SceneFile(ctx, os.path.join(ctx.data_dir, self.src_file))
        td = holder.read(self.td_pid)
        self.tex_pids = [p["m_PathID"] for p in td["m_SplatDatabase"]["m_AlphaTextures"]]
        self.alphas = [read_alpha(holder.read(p)) for p in self.tex_pids]
        self.copy = None
        self.dirty = False
        t = model.terrain
        self.origin, self.size = (t.origin, (t.size_x, t.size_z)) if t else (None, None)

    @property
    def layer_count(self):
        n = len(self.alphas) * 4
        t = self.model.terrain
        return min(n, len(t.layer_colours)) if t and t.layer_colours else n

    def ensure_copy(self):
        """Give the scenario its own terrain copy (inside model.begin/commit).
        Returns the source (file, TerrainData pid) when a copy was made now."""
        m = self.model
        if self.owned:
            return None
        made = None
        if self.copy is None:
            new_td, new_tex = copy_terrain(m.ctx, self.assets, self.src_file, self.td_pid, self.alphas)
            self.copy = (new_td, new_tex)
            made = (self.src_file, self.td_pid)
        fid = m.sc.ensure_external_path(self.own_file)
        for p in self.comps:
            m.set_field(p, "m_TerrainData", {"m_FileID": fid, "m_PathID": self.copy[0]})
        self.owned = True
        self.src = (self.src_file, self.td_pid)
        self.td_pid, self.tex_pids = self.copy
        self.src_file = self.own_file
        return made

    def resync(self):
        """Follow the scene after an undo that pointed the terrain back."""
        sc = self.model.sc
        tpid = next(p for p in self.comps if sc.type_name(p) == "Terrain")
        ref = sc.read(tpid)["m_TerrainData"]
        file = sc.externals[ref["m_FileID"] - 1]
        if file != self.own_file and self.owned:
            self.owned = False
            self.src_file, self.td_pid = self.src

    def pixel(self, wx, wz):
        res = self.alphas[0].shape[0]
        return ((wz - self.origin[2]) / self.size[1] * res, (wx - self.origin[0]) / self.size[0] * res)

    def paint(self, wx, wz, radius, layer, strength):
        """Paint `layer` around world point (wx, wz) with a soft round brush of
        `radius` metres; strength 0..1 per dab. Returns (undo patch, rows, cols)
        or None when outside the terrain."""
        res = self.alphas[0].shape[0]
        cy, cx = self.pixel(wx, wz)
        rpx = max(1.0, radius / self.size[0] * res)
        r0, r1 = int(max(0, np.floor(cy - rpx))), int(min(res, np.ceil(cy + rpx) + 1))
        c0, c1 = int(max(0, np.floor(cx - rpx))), int(min(res, np.ceil(cx + rpx) + 1))
        if r0 >= r1 or c0 >= c1:
            return None
        patch = [a[r0:r1, c0:c1].copy() for a in self.alphas]
        yy, xx = np.mgrid[r0:r1, c0:c1]
        d = np.hypot(yy + 0.5 - cy, xx + 0.5 - cx) / rpx
        fall = np.clip(1.0 - d, 0.0, 1.0)
        fall = fall * fall * (3 - 2 * fall)
        w = np.concatenate([p.astype(float) for p in patch], axis=2)
        n = w.shape[2]
        target = np.clip(w[:, :, layer] + 255.0 * strength * fall, 0, 255)
        others = np.delete(w, layer, axis=2)
        rest = others.sum(axis=2, keepdims=True)
        scale = np.where(rest > 0, (255.0 - target[:, :, None]) / np.maximum(rest, 1e-6), 0.0)
        others = np.round(others * scale)
        target = 255.0 - others.sum(axis=2)
        w = np.insert(others, layer, target, axis=2)
        w = np.clip(w, 0, 255).astype(np.uint8)
        changed = fall > 0
        for i, a in enumerate(self.alphas):
            part = a[r0:r1, c0:c1]
            part[changed] = w[:, :, i * 4:i * 4 + 4][changed]
        self.dirty = True
        return (r0, c0, patch), (r0, r1), (c0, c1)

    def restore_patch(self, undo):
        r0, c0, patch = undo
        for a, p in zip(self.alphas, patch):
            a[r0:r0 + p.shape[0], c0:c0 + p.shape[1]] = p
        self.dirty = True
        return (r0, r0 + patch[0].shape[0]), (c0, c0 + patch[0].shape[1])

    def save(self):
        """Write the weights into the scenario's terrain copy and save the asset
        file. Nothing is written while the scenario uses the shared terrain."""
        if not self.owned or not (self.dirty or self.assets.dirty):
            return False
        for p, a in zip(self.tex_pids, self.alphas):
            self.assets.write(p, write_alpha(self.assets.read(p), a))
        self.assets.save()
        self.assets = SceneFile(self.model.ctx, self.assets.path)
        self.dirty = False
        return True


__all__ = ["TerrainPaint", "copy_terrain", "read_alpha", "write_alpha", "mip_chain", "own_assets_name",
           "terrain_components"]
