# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Terrain texture painting on an isolated copy of the game.

Run: python -m unittest discover tests
"""

import hashlib
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scenario_backup import ScenarioBackups  # noqa: E402
from scenarios import ScenarioManager  # noqa: E402
from scene_io import GameContext, SceneFile  # noqa: E402
from scene_model import SceneModel  # noqa: E402
from terrain_edit import TerrainPaint, mip_chain, read_alpha  # noqa: E402

from gamepath import GAME  # noqa: E402
from test_scenarios import mirror_game, remove_tree  # noqa: E402


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class MipTest(unittest.TestCase):
    def test_mip_chain_size(self):
        img = np.zeros((1024, 1024, 4), dtype=np.uint8)
        self.assertEqual(len(mip_chain(img)), 5592404)


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class TerrainPaintTest(unittest.TestCase):
    def test_paint_save_reload_undo(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        data = mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        src = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "20_Open_Field_Intercept_Practice")
        _, battle_idx = mgr.create(src, "Painted", "x")

        m = SceneModel(ctx, battle_idx)
        tp = TerrainPaint(m)
        m.terrain_paint = tp
        shared = os.path.join(data, tp.src_file)
        before = digest(shared)
        layer = 2
        m.begin("paint")
        made = tp.ensure_copy()
        undo, rows, cols = tp.paint(0.0, 0.0, 20.0, layer, 1.0)
        m.commit(undo_fn=lambda: tp.restore_patch(undo))
        self.assertIsNotNone(made)
        centre = tuple(int(v) for v in tp.pixel(0.0, 0.0))
        weights = np.concatenate([a[centre] for a in tp.alphas])
        self.assertGreaterEqual(int(weights[layer]), 250)
        self.assertEqual(int(weights.sum()), 255)
        m.terrain.recolour(tp.alphas, rows, cols)
        m.save()

        self.assertEqual(digest(shared), before, "the shared terrain is not changed")
        m2 = SceneModel(ctx, battle_idx)
        tp2 = TerrainPaint(m2)
        self.assertTrue(tp2.owned)
        for a, b in zip(tp.alphas, tp2.alphas):
            self.assertTrue(np.array_equal(a, b))
        assets = SceneFile(ctx, os.path.join(data, tp2.own_file))
        tex = assets.read(tp2.tex_pids[0])
        self.assertEqual(len(tex["image data"]), tex["m_CompleteImageSize"])
        self.assertTrue(np.array_equal(read_alpha(tex), tp2.alphas[0]))

        m2.terrain_paint = tp2
        m2.begin("paint again")
        undo2, _, _ = tp2.paint(0.0, 0.0, 10.0, (layer + 1) % tp2.layer_count, 0.5)
        m2.commit(undo_fn=lambda: tp2.restore_patch(undo2))
        changed = [a.copy() for a in tp2.alphas]
        m2.undo()
        for a, b in zip(tp.alphas, tp2.alphas):
            self.assertTrue(np.array_equal(a, b), "undo restores the weights")
        self.assertFalse(all(np.array_equal(a, b) for a, b in zip(changed, tp2.alphas)))

        # painted terrain survives backup / removal / restore and export / import
        mgr.manifest["scenarios"][0]["terrain_source"] = {
            "file": tp.src[0], "pid": tp.src[1], "desc": None}
        backups = ScenarioBackups(ctx, mgr)
        mgr.manifest["scenarios"][0]["terrain_source"]["desc"] = backups.index.describe(tp.src[0], tp.src[1])
        entry = mgr.custom[0]
        backups.backup(entry)
        self.assertIn("terrain.npz", os.listdir(backups.folder(entry["scene_id"])))
        exported = os.path.join(tmp, "painted.tbscenario")
        backups.export(entry, exported, "test")
        painted = [a.copy() for a in tp.alphas]
        mgr.remove_all()
        self.assertEqual(digest(shared), before)
        for force in (True, False):
            if not force:
                mgr.remove_all()
                for b in backups.list():
                    backups.delete(b["folder"])
                _, _, idx, report = backups.import_file(exported)
            else:
                _, idx, report = backups.restore(entry["scene_id"], force=True)
            self.assertFalse(any("could not" in line or "no longer" in line for line in report), report)
            m3 = SceneModel(ctx, idx)
            tp3 = TerrainPaint(m3)
            self.assertTrue(tp3.owned, "the restored scenario uses its own terrain copy")
            self.assertEqual(tp3.own_file, f"sharedassets{idx}.assets")
            for a, b in zip(painted, tp3.alphas):
                self.assertTrue(np.array_equal(a, b))
        self.assertEqual(digest(shared), before)


if __name__ == "__main__":
    unittest.main()
