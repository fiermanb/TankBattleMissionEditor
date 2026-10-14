# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Lighting presets of custom scenarios (day, dusk, night) on an isolated copy
of the game: values, the scenario's own sky, undo, backup and restore.

Run: python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lighting import PRESETS, Lighting, source_scene  # noqa: E402
from scenario_backup import ScenarioBackups  # noqa: E402
from scenarios import ScenarioManager  # noqa: E402
from scene_io import GameContext  # noqa: E402
from scene_model import SceneModel  # noqa: E402

from gamepath import GAME  # noqa: E402
from test_scenarios import mirror_game, remove_tree  # noqa: E402


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class LightingTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        mirror_game(game)
        self.ctx = GameContext(game)
        self.mgr = ScenarioManager(self.ctx, os.path.join(tmp, "backups"))

    def scenario(self, source, title):
        src = next(p for p, d in self.mgr.missions() if d["Battle_Scene_Name"] == source)
        _, idx = self.mgr.create(src, title, "x")
        entry = next(s for s in self.mgr.custom if s["battle_index"] == idx)
        return entry

    def lighting(self, entry):
        m = SceneModel(self.ctx, entry["battle_index"], terrain=None)
        return m, Lighting(self.ctx, m, source_scene(self.ctx, self.mgr, entry))

    def test_presets_undo_backup_restore(self):
        entry = self.scenario("20_Open_Field_Intercept_Practice", "Dark Field")
        m, lt = self.lighting(entry)
        self.assertEqual(lt.preset(), "day")
        day = lt.lighting()

        m.begin("night")
        undo = lt.apply(dict(PRESETS["night"]), preset=True)
        m.commit(undo_fn=undo)
        self.assertEqual(lt.preset(), "night")
        self.assertIsNotNone(lt.own_sky(), "the sky is the scenario's own copy")
        self.assertAlmostEqual(lt.lighting()["sky_brightness"], 0.08, places=4)
        self.assertEqual(lt.lighting()["visibility"], 600.0)
        m.undo()
        self.assertEqual(lt.preset(), "day", "undo brings the day back")
        self.assertIsNone(lt.own_sky())

        m.begin("night")
        m.commit(undo_fn=lt.apply(dict(PRESETS["night"]), preset=True))
        m.begin("brighter moon")
        lt.apply({"sun_intensity": 0.25})
        m.commit()
        self.assertEqual(lt.preset(), "custom")
        self.assertAlmostEqual(lt.lighting()["sun_intensity"], 0.25, places=4)
        m.save()

        backups = ScenarioBackups(self.ctx, self.mgr)
        backups.backup(entry)
        self.mgr.remove_all()
        _, idx, report = backups.restore(entry["scene_id"], force=True)
        self.assertFalse(any("could not" in r or "no longer" in r for r in report), report)
        restored = next(s for s in self.mgr.custom if s["battle_index"] == idx)
        m2, lt2 = self.lighting(restored)
        own = os.path.basename(m2.own_assets().path)
        self.assertIsNotNone(lt2.own_sky(), "the restored scenario has its own sky again")
        old = f"sharedassets{entry['battle_index']}.assets"
        if old != own:
            self.assertNotIn(old, m2.sc.externals, "no reference to the old asset file")
        self.assertAlmostEqual(lt2.lighting()["sky_brightness"], 0.08, places=4)
        self.assertAlmostEqual(lt2.lighting()["sun_intensity"], 0.25, places=4)

        m2.begin("day")
        lt2.apply_day()
        m2.commit()
        self.assertEqual(lt2.preset(), "day")
        self.assertEqual(lt2.lighting()["visibility"], day["visibility"])

    def test_night_keeps_the_fog_visibility(self):
        entry = self.scenario("23_Open_Field_Intercept_Fog", "Foggy Night")
        m, lt = self.lighting(entry)
        before = lt.lighting()["visibility"]
        self.assertLess(before, PRESETS["night"]["visibility"])
        m.begin("night")
        m.commit(undo_fn=lt.apply(dict(PRESETS["night"]), preset=True))
        self.assertEqual(lt.lighting()["visibility"], before, "a preset never raises the visibility")
        rs = next(p for p in m.sc.all_pids() if m.sc.type_name(p) == "RenderSettings")
        self.assertTrue(m.sc.read(rs)["m_Fog"])
        self.assertEqual(lt.preset(), "night")


if __name__ == "__main__":
    unittest.main()
