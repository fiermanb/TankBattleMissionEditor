# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Learn how the game's own AI route maps treat each object type and write
layouts/nav_types.json (used by navmesh_edit to decide what blocks the AI).

For every original battle map, the original NavMesh is drawn on a 0.5 m grid;
for every object type, the share of its footprint that the route map treats as
drivable is collected over all maps. Run after a game update:

    python packaging/generate_nav_types.py "<game folder>"
"""

import collections
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

from navmesh_edit import CELL, NavMesh, decode_tile  # noqa: E402
from object_import import base_name  # noqa: E402
from scene_io import GameContext  # noqa: E402
from scene_model import SceneModel  # noqa: E402


def drivable(nav, origin, n):
    img = Image.new("L", (n, n), 0)
    d = ImageDraw.Draw(img)
    for t in nav.data["m_NavMeshTiles"]:
        tile = decode_tile(bytes(t["m_MeshData"]))
        for p in tile["polys"]:
            d.polygon([((tile["verts"][i][0] - origin[0]) / CELL, (tile["verts"][i][2] - origin[1]) / CELL)
                       for i in p[:p[13]]], fill=1)
    return np.asarray(img) > 0


def main(game):
    ctx = GameContext(game)
    stats = collections.defaultdict(lambda: [0, 0])
    for idx in range(len(ctx.scene_names)):
        label = ctx.scene_label(idx)
        if not label[:2].isdigit() or label.endswith("_Menu") or label.startswith("90_") or label.startswith("00"):
            continue
        m = SceneModel(ctx, idx, terrain="heights")
        if m.terrain is None:
            continue
        try:
            nav = NavMesh(m)
        except ValueError:
            continue
        n = int(round(m.terrain.size_x / CELL))
        ox, oz = m.terrain.origin[0], m.terrain.origin[2]
        orig = drivable(nav, (ox, oz), n)
        roots = {}
        for g in m.foot:
            if m.active(g):
                roots.setdefault(m.object_root(g), []).append(g)
        for r, parts in roots.items():
            pts = np.concatenate([m.foot[g] for g in parts])
            cx, cz = (pts[:, 0] - ox) / CELL, (pts[:, 1] - oz) / CELL
            x0, z0 = int(max(0, np.floor(cx.min()))), int(max(0, np.floor(cz.min())))
            x1, z1 = int(min(n, np.ceil(cx.max()) + 1)), int(min(n, np.ceil(cz.max()) + 1))
            if x0 >= x1 or z0 >= z1:
                continue
            img = Image.new("L", (x1 - x0, z1 - z0), 0)
            d = ImageDraw.Draw(img)
            for g in parts:
                d.polygon([((x - ox) / CELL - x0, (z - oz) / CELL - z0) for x, z in m.foot[g]], fill=1)
            a = np.asarray(img) > 0
            st = stats[base_name(m.name(r))]
            st[0] += int((orig[z0:z1, x0:x1] & a).sum())
            st[1] += int(a.sum())
        print(label, flush=True)
    out = {k: round(w / t, 3) for k, (w, t) in sorted(stats.items()) if t}
    path = os.path.join(SRC, "layouts", "nav_types.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(f"{len(out)} object types written to {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
