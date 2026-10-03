# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Object library and cross-scene import tests against the installed game.

Run: python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from object_import import ObjectLibrary, import_object  # noqa: E402
from scene_io import GameContext, SceneFile  # noqa: E402
from scene_model import SceneModel  # noqa: E402

from gamepath import GAME  # noqa: E402
TARGET = 40


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class ObjectImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = GameContext(GAME)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.lib = ObjectLibrary(cls.ctx, cls.tmp.name)
        cls.lib.build()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_library(self):
        self.assertGreater(len(self.lib.entries), 50)
        cats = {e["category"] for e in self.lib.entries}
        self.assertIn("Buildings", cats)
        self.assertIn("Trees and hedges", cats)
        again = ObjectLibrary(self.ctx, self.tmp.name)
        self.assertTrue(again.load(), "cached library must load while the game files are unchanged")

    def test_asset_file_objects(self):
        assets = [e for e in self.lib.entries if e.get("file")]
        self.assertTrue(assets, "stand-alone scenery objects from the asset files are listed")
        m = SceneModel(self.ctx, TARGET, terrain=None)
        m.begin("import")
        root, cleared = import_object(m, self.lib, assets[0], 0.0, -300.0)
        m.commit()
        self.assertEqual(cleared, 0)
        self.assertTrue(any(m.go_of_tr[p] in m.foot for p in m.subtree(m.tr_of_go[root])))
        self.assertIn(assets[0]["file"], m.sc.externals)

    def test_import_save_reload_undo(self):
        m = SceneModel(self.ctx, TARGET)
        n0 = len(m.sc.all_pids())
        picks = [next(e for e in self.lib.entries if e["category"] == c)
                 for c in ("Buildings", "Trees and hedges", "Vehicles")]
        roots = []
        for i, entry in enumerate(picks):
            m.begin("import")
            root, cleared = import_object(m, self.lib, entry, 40.0 * i, -300.0)
            m.commit()
            self.assertEqual(cleared, 0, entry["name"])
            roots.append(root)
            self.assertTrue(any(m.go_of_tr[p] in m.foot for p in m.subtree(m.tr_of_go[root])))
        m.begin("dup")
        dup = m.duplicate(roots[0])
        m.commit()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, f"level{TARGET}")
            m.sc.save(out)
            sc2 = SceneFile(self.ctx, out)
            for pid in m.sc.new_objects:
                sc2.objects[pid].read_typetree(nodes=m.sc.nodes_for(pid), wrap=False, check_read=True)
            for root in roots + [dup]:
                self.assertTrue(sc2.read(root)["m_IsActive"])
        for _ in range(4):
            m.undo()
        self.assertEqual(len(m.sc.all_pids()), n0)


if __name__ == "__main__":
    unittest.main()
