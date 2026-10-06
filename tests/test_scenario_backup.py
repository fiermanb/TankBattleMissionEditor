# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Scenario backups: back up, remove (as a game update does), restore.

Runs on an isolated copy of the game (see test_scenarios.mirror_game). The
restore is forced through the full conversion, which in the same game version
must reproduce every object exactly.

Run: python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scenario_backup import ScenarioBackups, convert_fields  # noqa: E402
from scenarios import ScenarioManager  # noqa: E402
from scene_io import GameContext, SceneFile, layout_tree  # noqa: E402
from scene_model import SceneModel  # noqa: E402

from gamepath import GAME  # noqa: E402
from test_scenarios import mirror_game, remove_tree  # noqa: E402


class ConvertFieldsTest(unittest.TestCase):
    def test_fields_follow_the_new_layout(self):
        old = layout_tree([[0, "S", "Base", 0], [1, "int", "a", 0], [1, "float", "b", 0], [1, "int", "gone", 0]])
        new = layout_tree([[0, "S", "Base", 0], [1, "int", "a", 0], [1, "string", "c", 0],
                           [1, "vector", "d", 0], [2, "Array", "Array", 0], [3, "int", "size", 0],
                           [3, "int", "data", 0], [1, "int", "b", 0]])
        out = convert_fields({"a": 5, "b": 1.5, "gone": 7}, old, new)
        self.assertEqual(out, {"a": 5, "c": "", "d": [], "b": 0})


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class ScenarioBackupTest(unittest.TestCase):
    def test_backup_remove_restore(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        src = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "20_Open_Field_Intercept_Practice")
        _, battle_idx = mgr.create(src, "Backup Test", "Hold the field.")

        m = SceneModel(ctx, battle_idx)
        m.begin("edit")
        m.clear_to_terrain()
        template = next(e for e in m.spawns if m.sc.read(e)["Tank_ID"] != 1)
        tank = m.copy_spawn(m.event_go[template], 1)
        m.move_to(m.tr_of_go[tank], 25.0, 40.0)
        m.new_route((0.0, 0.0, 10.0))
        m.commit()
        m.save()
        mission = mgr.mission_for_scene(m.label)[1]
        field = "Enable_Assisted_Kills"
        mgr.set_mission_fields(m.label, **{field: not mission[field]})

        backups = ScenarioBackups(ctx, mgr)
        entry = mgr.custom[0]
        backups.backup(entry)
        saved_file = SceneFile(ctx, ctx.scene_path(battle_idx))
        saved = {pid: saved_file.read(pid) for pid in saved_file.all_pids()}
        self.assertEqual(backups.missing(), [])

        mgr.remove_all()
        self.assertEqual([b["scene_id"] for b in backups.missing()], [entry["scene_id"]])

        _, battle2, report = backups.restore(entry["scene_id"], force=True)
        restored = SceneFile(ctx, ctx.scene_path(battle2))
        self.assertEqual(sorted(restored.all_pids()), sorted(saved))
        for pid, data in saved.items():
            self.assertEqual(restored.read(pid), data, f"object {pid}")
        self.assertFalse(any("no longer exist" in line for line in report), report)
        restored_mission = mgr.mission_for_scene(ctx.scene_label(battle2))[1]
        self.assertEqual(restored_mission[field], not mission[field])
        self.assertEqual(restored_mission["Scene_Title"], "Backup Test")
        self.assertEqual(backups.missing(), [])
        model = SceneModel(ctx, battle2, terrain=None)
        self.assertEqual(sorted(model.sc.read(e)["Tank_ID"] for e in model.spawns if model.active(model.event_go[e])),
                         [0, 1])

        # a game update that renumbers a shared asset file: every reference to it must follow
        shifted, offset = "sharedassets1.assets", 1000000
        real_table = backups.index.table

        def renumbered(file):
            fwd, rev, by_name = real_table(file)
            if file != shifted:
                return fwd, rev, by_name
            return ({p + offset: d for p, d in fwd.items()}, {d: p + offset for d, p in rev.items()},
                    {k: [p + offset for p in v] for k, v in by_name.items()})
        backups.index.table = renumbered
        mgr.remove_all()
        _, battle3, report = backups.restore(entry["scene_id"], force=True)
        moved = SceneFile(ctx, ctx.scene_path(battle3))
        fid = saved_file.externals.index(shifted) + 1
        checked = 0

        def refs(value):
            if isinstance(value, dict):
                if set(value) == {"m_FileID", "m_PathID"}:
                    yield value
                    return
                for v in value.values():
                    yield from refs(v)
            elif isinstance(value, list):
                for v in value:
                    yield from refs(v)
        for pid, data in saved.items():
            for old, new in zip(refs(data), refs(moved.read(pid))):
                if old["m_FileID"] == fid and old["m_PathID"]:
                    self.assertEqual(new["m_PathID"], old["m_PathID"] + offset)
                    checked += 1
                else:
                    self.assertEqual(new, old)
        self.assertGreater(checked, 100)


if __name__ == "__main__":
    unittest.main()
