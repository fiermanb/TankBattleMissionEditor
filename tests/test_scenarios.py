# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Scenario creation/removal test on an isolated copy of the game.

Assertions are relative to the copied game state, which may already contain
custom scenarios created by the user.

Large files are hard-linked into a temporary folder; the three shared files
that scenarios modify are real copies. All writes use atomic replacement, so
the installed game is never modified.

Run: python -m unittest discover tests
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scene_io import GameContext, SceneFile  # noqa: E402
from scene_model import SceneModel  # noqa: E402
from scenarios import SHARED_FILES, ScenarioManager  # noqa: E402

from gamepath import GAME  # noqa: E402


def mirror_game(dst):
    data_name = next(d for d in os.listdir(GAME) if d.endswith("_Data"))
    os.link(os.path.join(GAME, "GameAssembly.dll"), os.path.join(dst, "GameAssembly.dll"))
    src_data = os.path.join(GAME, data_name)
    for root, dirs, files in os.walk(src_data):
        rel = os.path.relpath(root, src_data)
        out = os.path.join(dst, data_name, rel)
        os.makedirs(out, exist_ok=True)
        for f in files:
            if rel == "." and f in SHARED_FILES:
                shutil.copy2(os.path.join(root, f), os.path.join(out, f))
            else:
                os.link(os.path.join(root, f), os.path.join(out, f))
    return os.path.join(dst, data_name)


def remove_tree(path, attempts=10):
    """Delete a folder, retrying while another process (for example a virus
    scanner) briefly holds a freshly written file open."""
    for _ in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.5)
    shutil.rmtree(path, ignore_errors=True)


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class ScenarioTest(unittest.TestCase):
    def test_create_and_remove(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        if True:
            game = os.path.join(tmp, "game")
            os.makedirs(game)
            data = mirror_game(game)
            originals = {}
            for n in SHARED_FILES:
                with open(os.path.join(data, n), "rb") as f:
                    originals[n] = f.read()
            ctx = GameContext(game)
            base = len(ctx.scene_names)
            sel0 = SceneModel(ctx, 2)
            pages0 = len(sel0.sc.read(next(p for p in sel0.mb if sel0.script_class(p) == "Mission_Select_Manager_CS"))["missionLists"])
            mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
            src_pid = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "10_Urban_Area_Close_Combat_Basic")

            menu_idx, battle_idx = mgr.create(src_pid, "My First Battle", "Destroy everything.")
            self.assertEqual((menu_idx, battle_idx), (base, base + 1))
            self.assertEqual(len(ctx.scene_names), base + 2)
            self.assertTrue(ctx.scene_names[battle_idx].endswith("/90_My_First_Battle.unity"))
            for i in (menu_idx, battle_idx):
                self.assertTrue(os.path.exists(os.path.join(data, f"level{i}")))
                self.assertTrue(os.path.exists(os.path.join(data, f"sharedassets{i}.assets")))
            found = mgr.mission_for_scene("90_My_First_Battle")
            self.assertIsNotNone(found)
            self.assertEqual(found[1]["Menu_Scene_Name"], "90_My_First_Battle_Menu")
            self.assertEqual(found[1]["Scene_Title"], "My First Battle")

            menu = SceneFile(ctx, ctx.scene_path(menu_idx))
            mm = next(p for p in menu.all_pids() if menu.type_name(p) == "MonoBehaviour"
                      and menu.script_class(p) == "Menu_Scene_Manager_CS")
            self.assertEqual(menu.read(mm)["sceneProp"]["m_PathID"], found[0])

            sel = SceneModel(ctx, 2)
            mgr_mb = next(p for p in sel.mb if sel.script_class(p) == "Mission_Select_Manager_CS")
            pages = sel.sc.read(mgr_mb)["missionLists"]
            self.assertEqual(len(pages), pages0 + 1)
            rows = [p for p in sel.mb if sel.script_class(p) == "Mission_Select_Button_CS"
                    and sel.active(sel.sc.read(p, head_only=True)["m_GameObject"]["m_PathID"])]
            props = [sel.sc.read(p)["sceneProp"]["m_PathID"] for p in rows]
            self.assertIn(found[0], props)

            battle = SceneModel(ctx, battle_idx)
            self.assertTrue(battle.spawns)

            mgr.create(src_pid, "My First Battle", "Second one.")
            self.assertEqual(len(ctx.scene_names), base + 4)
            sel = SceneModel(ctx, 2)
            pages2 = sel.sc.read(next(p for p in sel.mb if sel.script_class(p) == "Mission_Select_Manager_CS"))["missionLists"]
            self.assertEqual(len(pages2), pages0 + 1, "second scenario goes on the same custom page")

            mgr.set_mission_text("90_My_First_Battle", title="Renamed", briefing="New text")
            self.assertEqual(mgr.mission_for_scene("90_My_First_Battle")[1]["Scene_Title"], "Renamed")

            mgr.remove_all()
            self.assertEqual(len(ctx.scene_names), base)
            for n in SHARED_FILES:
                with open(os.path.join(data, n), "rb") as f:
                    self.assertEqual(f.read(), originals[n], n)
            self.assertFalse(os.path.exists(os.path.join(data, f"level{base}")))
            self.assertFalse(os.path.exists(os.path.join(data, f"sharedassets{base + 3}.assets")))


if __name__ == "__main__":
    unittest.main()
