# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Briefing synchronisation and game folder checks, on an isolated copy of the game.

Run: python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings  # noqa: E402
from briefing_sync import BriefingSync, battle_groups, sync_mission  # noqa: E402
from scenarios import ScenarioManager  # noqa: E402
from scene_io import GameContext  # noqa: E402
from scene_model import SceneModel  # noqa: E402
from test_scenarios import GAME, mirror_game, remove_tree  # noqa: E402


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class BriefingSyncTest(unittest.TestCase):
    def test_validate_game(self):
        ok, _ = settings.validate_game(GAME)
        self.assertTrue(ok)
        self.assertFalse(settings.validate_game(os.path.dirname(GAME))[0])
        a, b = settings.backup_dir_for(GAME), settings.backup_dir_for(GAME + "_other")
        self.assertNotEqual(a, b)

    def test_sync_after_edits(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        src = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "10_Urban_Area_Close_Combat_Basic")
        menu_idx, battle_idx = mgr.create(src, "Sync Test", "Text.")

        battle = SceneModel(ctx, battle_idx, terrain=None)
        enemies = [e for e in battle.spawns if battle.sc.read(e)["Relationship"] == 1]
        key = battle.sc.read(enemies[0])["Key_Name"]
        battle.begin("edit")
        battle.duplicate(battle.event_go[enemies[0]])
        battle.duplicate(battle.event_go[enemies[1]])
        battle.set_field(enemies[2], "Key_Name", "Scouts")
        battle.delete(battle.event_go[enemies[3]])
        battle.commit()
        battle.save()
        battle = SceneModel(ctx, battle_idx, terrain=None)
        groups = battle_groups(battle)

        lines = sync_mission(ctx, mgr, battle, os.path.join(tmp, "backups"))
        self.assertTrue(lines[0].startswith("Briefing updated"))
        sync = BriefingSync(ctx, menu_idx)
        rows = sync.rows()
        for k, g in groups.items():
            self.assertIn(k, rows, k)
            row, el = rows[k]
            self.assertTrue(sync.menu.active(row))
            units = sync.menu.sc.read(sync.text_under(row, "Text (Units)"))["m_Text"]
            self.assertEqual(units, f"x{len(g['units'])}", k)
            syms = sync.symbol_gos(el)
            self.assertEqual(len(syms), len(g["units"]), k)
            self.assertTrue(all(sync.menu.active(s) for s in syms))
            placed = sorted((round(sync.menu.world(sync.menu.tr_of_go[s])[0][0], 1),
                             round(sync.menu.world(sync.menu.tr_of_go[s])[0][2], 1)) for s in syms)
            wanted = sorted((round(p[0], 1), round(p[2], 1)) for p, _ in g["units"])
            self.assertEqual(placed, wanted, k)
        self.assertIn("Scouts", rows)
        self.assertIn(key, rows)
        mission = mgr.mission_for_scene(ctx.scene_label(battle_idx))[1]
        friends = sum(1 for e in battle.spawns if battle.sc.read(e)["Relationship"] == 0
                      and battle.sc.read(e)["Tank_ID"] != 1 and battle.active(battle.event_go[e]))
        self.assertEqual(mission["Allies_Count"], friends)


if __name__ == "__main__":
    unittest.main()
