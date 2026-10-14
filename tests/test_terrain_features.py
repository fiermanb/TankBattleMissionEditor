# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Terrain features: profile brushes computed on a base ground, stored with
the scenario.

Run: python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from terrain_edit import HEIGHT_MAX, TerrainPaint  # noqa: E402
from terrain_features import (BASE_FILE, FEATURES_FILE, TerrainFeatures, clean_brush,  # noqa: E402
                              default_brushes, find_layer)

from gamepath import GAME  # noqa: E402

SCALE = 128.0
FLAT_VALUE = int(round(10.0 / SCALE * HEIGHT_MAX))
FLAT = FLAT_VALUE / HEIGHT_MAX * SCALE


def flat_ground(res=513, size=512.0):
    base = np.full((res, res), FLAT_VALUE, np.int32)
    alphas = [np.zeros((256, 256, 4), np.uint8), np.zeros((256, 256, 4), np.uint8)]
    alphas[0][..., 0] = 255
    f = TerrainFeatures(base, alphas, {"origin": (0.0, 0.0, 0.0), "size": (size, size), "height_scale": SCALE})
    return f, base.copy(), [a.copy() for a in alphas]


def brush(name):
    return next(b for b in default_brushes() if b["name"] == name)


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.f, self.h, self.a = flat_ground()

    def ground(self, x, z):
        return self.h[int(round(z)), int(round(x))] / HEIGHT_MAX * SCALE

    def draw(self, b, points, texture=None):
        s = self.f.make_stroke(b, points, texture)
        self.f.strokes.append(s)
        self.f.apply(self.h, self.a, [s])
        return s

    def test_trench_profile(self):
        trench = dict(brush("Trench"), crossings=0.0)
        self.draw(trench, [(50, 100), (450, 100)])
        self.assertAlmostEqual(self.ground(200, 100), FLAT - 1.6, places=2)
        rim = max(self.ground(200, z) for z in (101, 102, 103, 104))
        self.assertAlmostEqual(rim, FLAT + 0.3, delta=0.05, msg="rim beside the trench")
        self.assertAlmostEqual(self.ground(200, 110), FLAT, places=3)

    def test_overlap_does_not_add_up(self):
        trench = dict(brush("Trench"), crossings=0.0)
        self.draw(trench, [(50, 100), (450, 100)])
        self.draw(trench, [(200, 20), (200, 300)])
        self.draw(trench, [(50, 100), (450, 100)])
        self.assertAlmostEqual(self.ground(200, 100), FLAT - 1.6, places=2, msg="crossing trenches")
        self.assertAlmostEqual(self.ground(300, 100), FLAT - 1.6, places=2, msg="drawn twice")
        hill = brush("Embankment")
        self.draw(hill, [(100, 400), (300, 400)])
        self.draw(hill, [(200, 350), (200, 450)])
        self.assertAlmostEqual(self.ground(200, 400), FLAT + 2.0, places=2)

    def test_ai_crossings(self):
        s = self.draw(brush("Trench"), [(50, 100), (450, 100)])
        self.assertEqual(s["crossings"], 90.0)
        self.assertAlmostEqual(self.ground(95, 100), FLAT, places=3, msg="crossing 45 m along the trench")
        self.assertAlmostEqual(self.ground(140, 100), FLAT - 1.6, places=2)

    def test_removing_restores_the_base(self):
        base_h, base_a = self.h.copy(), [a.copy() for a in self.a]
        mud = 4
        s = self.draw(brush("Crater"), [(250, 250)], mud)
        self.assertLess(self.ground(250, 250), FLAT - 0.9)
        self.assertEqual(int(self.a[1][125, 125, 0]), 255)
        self.f.strokes.remove(s)
        self.f.apply(self.h, self.a, [s])
        self.assertTrue(np.array_equal(self.h, base_h))
        for a, b in zip(self.a, base_a):
            self.assertTrue(np.array_equal(a, b))

    def test_level_meets_the_ground_at_the_side_slope(self):
        b = dict(brush("Level ground"), level=FLAT + 2.0)
        self.draw(b, [(300, 300)])
        self.assertAlmostEqual(self.ground(300, 300), FLAT + 2.0, places=2)
        self.assertAlmostEqual(self.ground(300, 315), FLAT + 2.0 - 5 * np.tan(np.radians(20)), places=2)
        self.assertAlmostEqual(self.ground(300, 330), FLAT, places=3)

    def test_texture_weights_add_up(self):
        self.draw(brush("Paint"), [(20, 20), (480, 400)], 5)
        total = sum(a.astype(int).sum(axis=2) for a in self.a)
        self.assertTrue((total == 255).all())

    def test_brush_parameters_are_checked(self):
        b = clean_brush({"name": "x", "kind": "shape", "width": 1000, "slope": -5})
        self.assertEqual((b["width"], b["slope"]), (400.0, 1.0))
        with self.assertRaises(ValueError):
            clean_brush({"kind": "blob"})
        names = ["Terrain_Layer (Grass)", "Terrain_Layer (Road)", "Terrain_Layer (Sand Road)"]
        self.assertEqual(find_layer(names, "Road"), 1)
        self.assertEqual(find_layer(names, "sand"), 2)
        self.assertIsNone(find_layer(names, "Mud"))


class StoreTest(unittest.TestCase):
    def test_save_load_and_stale_ground(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: [os.remove(os.path.join(tmp, n)) for n in os.listdir(tmp)] and os.rmdir(tmp))
        f, h, a = flat_ground()
        s = f.make_stroke(brush("Trench"), [(50, 100), (450, 100)], 4)
        f.strokes.append(s)
        f.apply(h, a, [s])
        f.save(tmp, h, a)
        self.assertEqual(sorted(os.listdir(tmp)), sorted([BASE_FILE, FEATURES_FILE]))
        g, stale = TerrainFeatures.load(tmp, h, a)
        self.assertIsNone(stale)
        self.assertEqual(g.strokes, f.strokes)
        self.assertEqual(g.next_id, f.next_id)
        self.assertTrue(np.array_equal(g.heights_block(0, h.shape[0], 0, h.shape[1]), h))
        h[0, 0] += 1
        g, stale = TerrainFeatures.load(tmp, h, a)
        self.assertIsNone(g)
        self.assertIn("changed", stale)


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class ScenarioFeaturesTest(unittest.TestCase):
    def test_features_travel_with_the_scenario(self):
        from scenario_backup import ScenarioBackups
        from scenarios import ScenarioManager
        from scene_io import GameContext
        from scene_model import SceneModel
        from test_scenarios import mirror_game, remove_tree
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        src = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "20_Open_Field_Intercept_Practice")
        _, battle_idx = mgr.create(src, "Dug in", "x")
        m = SceneModel(ctx, battle_idx)
        tp = m.terrain_paint = TerrainPaint(m)
        t = m.terrain
        f = TerrainFeatures(tp.heights.copy(), [x.copy() for x in tp.alphas],
                            {"origin": [float(v) for v in t.origin], "size": [t.size_x, t.size_z],
                             "height_scale": tp.height_scale})
        m.begin("trench")
        made = tp.ensure_copy()
        s = f.make_stroke(brush("Trench"), [(-100.0, 0.0), (100.0, 0.0)], find_layer(t.layer_names, "Mud"))
        f.strokes.append(s)
        f.apply(tp.heights, tp.alphas, [s])
        tp.dirty = tp.heights_dirty = True
        m.commit()
        m.save()
        entry = mgr.custom[0]
        mgr.manifest["scenarios"][0]["terrain_source"] = {"file": made[0], "pid": made[1], "desc": None}
        backups = ScenarioBackups(ctx, mgr)
        mgr.manifest["scenarios"][0]["terrain_source"]["desc"] = backups.index.describe(*made)
        folder = backups.folder(entry["scene_id"])
        f.save(folder, tp.heights, tp.alphas)
        exported = os.path.join(tmp, "dug_in.tbscenario")
        backups.export(entry, exported, "test")
        mgr.remove_all()
        for b in backups.list():
            backups.delete(b["folder"])
        _, _, idx, report = backups.import_file(exported)
        self.assertFalse(any("could not" in line or "no longer" in line for line in report), report)
        tp2 = TerrainPaint(SceneModel(ctx, idx))
        new_entry = next(c for c in mgr.custom if c["battle_index"] == idx)
        g, stale = TerrainFeatures.load(backups.folder(new_entry["scene_id"]), tp2.heights, tp2.alphas)
        self.assertIsNone(stale)
        self.assertEqual([x["points"] for x in g.strokes], [s["points"]])


if __name__ == "__main__":
    unittest.main()
