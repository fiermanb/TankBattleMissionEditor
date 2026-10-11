# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""AI route map (NavMesh) decoding and rebuilding.

Run: python -m unittest discover tests
"""

import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from navmesh_edit import NavMesh, decode_tile, encode_tile  # noqa: E402
from scenario_backup import ScenarioBackups  # noqa: E402
from scenarios import ScenarioManager  # noqa: E402
from scene_io import GameContext  # noqa: E402
from scene_model import SceneModel  # noqa: E402
from terrain_edit import TerrainPaint  # noqa: E402

from gamepath import GAME  # noqa: E402
from test_scenarios import mirror_game, remove_tree  # noqa: E402

URBAN = "02_01_Free_Roam_Urban_Area"


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def covered(tiles, x, z):
    """True when a route map polygon contains world point (x, z)."""
    for t in tiles:
        for p in t["polys"]:
            pts = [(t["verts"][i][0], t["verts"][i][2]) for i in p[:p[13]]]
            inside = False
            for k in range(len(pts)):
                (x0, z0), (x1, z1) = pts[k], pts[k - 1]
                if (z0 > z) != (z1 > z) and x < (x1 - x0) * (z - z0) / (z1 - z0) + x0:
                    inside = not inside
            if inside:
                return True
    return False


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class NavMeshTest(unittest.TestCase):
    def test_tiles_round_trip(self):
        ctx = GameContext(GAME)
        idx = next(i for i in range(len(ctx.scene_names)) if ctx.scene_label(i) == URBAN)
        nav = NavMesh(SceneModel(ctx, idx, terrain=None))
        for t in nav.data["m_NavMeshTiles"]:
            blob = bytes(t["m_MeshData"])
            self.assertEqual(encode_tile(decode_tile(blob)), blob)

    def test_rebuild_after_removing_a_building(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        data = mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        src = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == URBAN)
        _, idx = mgr.create(src, "Open City", "x")
        m = SceneModel(ctx, idx)
        nav = NavMesh(m)
        shared = os.path.join(data, nav.file)
        before = digest(shared)
        house = next(g for g in {m.object_root(x) for x in m.foot}
                     if m.name(g).startswith("SM_Bld_City_Preset") and m.active(g))
        hx, _, hz = m.world(m.tr_of_go[house])[0]
        self.assertFalse(covered(nav.tiles(), hx, hz), "the building blocks the original route map")

        tp = TerrainPaint(m)
        m.terrain_paint = tp
        m.begin("paint")
        made = tp.ensure_copy()
        undo, _, _ = tp.paint(0.0, 0.0, 20.0, 1, 1.0)
        m.commit(undo_fn=lambda: tp.restore_patch(undo))
        near = [g for g in {m.object_root(x) for x in m.foot} if m.active(g)
                and ((m.world(m.tr_of_go[g])[0][0] - hx) ** 2 + (m.world(m.tr_of_go[g])[0][2] - hz) ** 2) < 35 ** 2]
        m.begin("delete")
        for g in near:
            m.delete(g, reindex=False)
        m.reindex()
        m.commit()
        tiles, _ = nav.build()
        m.begin("rebuild")
        nav.write(tiles)
        m.commit()
        m.save()
        self.assertEqual(digest(shared), before, "the original route map is not changed")

        m2 = SceneModel(ctx, idx)
        nav2 = NavMesh(m2)
        self.assertTrue(nav2.owned)
        self.assertTrue(covered(nav2.tiles(), hx, hz), "the building's spot is drivable after the rebuild")
        self.assertTrue(TerrainPaint(m2).owned, "the terrain copy survives next to the route map")
        for t in nav2.data["m_NavMeshTiles"]:
            blob = bytes(t["m_MeshData"])
            self.assertEqual(encode_tile(decode_tile(blob)), blob)

        entry = mgr.custom[0]
        for s in mgr.manifest["scenarios"]:
            s["terrain_source"] = {"file": made[0], "pid": made[1], "desc": None}
            s["navmesh_source"] = {"file": os.path.basename(shared), "pid": None, "desc": None}
        backups = ScenarioBackups(ctx, mgr)
        for s in mgr.manifest["scenarios"]:
            s["terrain_source"]["desc"] = backups.index.describe(*made)
        backups.backup(entry)
        mgr.remove_all()
        _, idx3, report = backups.restore(entry["scene_id"], force=True)
        self.assertFalse(any("could not" in line for line in report), report)
        m3 = SceneModel(ctx, idx3)
        nav3 = NavMesh(m3)
        self.assertTrue(nav3.owned, "the restored scenario has its own route map again")
        self.assertTrue(covered(nav3.tiles(), hx, hz))
        self.assertTrue(TerrainPaint(m3).owned)


if __name__ == "__main__":
    unittest.main()
