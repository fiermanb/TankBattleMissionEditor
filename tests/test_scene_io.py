# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Round-trip tests for scene_io against the installed game.

Run: python -m unittest discover tests
Set TBC_GAME to the game folder if it is not at the default location.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import UnityPy  # noqa: E402

from scene_io import GameContext, SceneFile  # noqa: E402

from gamepath import GAME  # noqa: E402
SCENES = (12, 40, 4)


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class SceneIoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = GameContext(GAME)

    def test_unchanged_build_is_identical(self):
        for i in SCENES:
            sc = SceneFile(self.ctx, self.ctx.scene_path(i))
            self.assertEqual(sc.build(), sc.raw, f"level{i}")

    def test_builtin_and_script_reserialize_exactly(self):
        sc = SceneFile(self.ctx, self.ctx.scene_path(12))
        n = 0
        for pid in sc.all_pids():
            t = sc.type_name(pid)
            if t in ("Transform", "GameObject") or (t == "MonoBehaviour" and sc.script_class(pid)):
                sc.read(pid)
                self.assertEqual(sc.serialize(pid), sc.objects[pid].get_raw_data(), f"{t} {pid}")
                n += 1
        self.assertGreater(n, 1000)

    def test_changed_script_layout_is_refused(self):
        from scene_io import LayoutMismatch, script_key
        ctx = GameContext(GAME)
        key = next(k for k in ctx.layouts if k.endswith("Event_Controller_CS"))
        ctx.layouts[key] = [row for row in ctx.layouts[key] if row[2] != "Trigger_Time"]
        ctx.layouts_exact, ctx.generator, ctx._node_cache = False, None, {}
        self.assertTrue(ctx.verify_layouts)
        sc = SceneFile(ctx, ctx.scene_path(12))
        ev = next(p for p in sc.all_pids() if sc.type_name(p) == "MonoBehaviour"
                  and sc.script_class(p) == "Event_Controller_CS")
        with self.assertRaises(LayoutMismatch):
            sc.read(ev)
        self.assertTrue(script_key("a", "", "b"))

    def test_modify_and_clone_reload(self):
        sc = SceneFile(self.ctx, self.ctx.scene_path(12))
        tr = next(p for p in sc.all_pids() if sc.type_name(p) == "Transform")
        d = sc.read(tr)
        d["m_LocalPosition"]["x"] = 1234.5
        sc.write(tr, d)
        clone = sc.add_object(tr, copy.deepcopy(d))
        out = sc.build()
        env = UnityPy.load(out)
        sf = next(iter(env.files.values()))
        self.assertEqual(len(sf.objects), len(sc.objects) + 1)
        self.assertEqual(sf.objects[tr].read_typetree()["m_LocalPosition"]["x"], 1234.5)
        self.assertEqual(sf.objects[clone].read_typetree()["m_LocalPosition"]["x"], 1234.5)
        for pid, o in sc.objects.items():
            if pid != tr:
                self.assertEqual(sf.objects[pid].get_raw_data(), o.get_raw_data())
        self.assertEqual(sf.header.data_offset % 16, 0)


if __name__ == "__main__":
    unittest.main()
