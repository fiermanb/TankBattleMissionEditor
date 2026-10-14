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
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scenario_backup import ScenarioBackups, convert_fields, purge_inactive, walk_pptrs  # noqa: E402
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


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class CustomSourceTest(unittest.TestCase):
    def test_restore_takes_an_original_briefing_on_the_same_terrain(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        field = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "20_Open_Field_Intercept_Practice")
        mgr.create(field, "Copy A", "x")
        a = next(p for p, d in mgr.missions() if d["Scene_Title"] == "Copy A")
        self.assertNotIn("Copy A", [lab for lab, _ in mgr.terrains()][0], "custom scenarios are no templates")
        mgr.create(a, "Copy B", "y")
        entry_b = next(s for s in mgr.custom if s["title"] == "Copy B")
        self.assertEqual(entry_b["source"], "Copy_A")
        backups = ScenarioBackups(ctx, mgr)
        backups.backup(entry_b)
        mgr.remove_all()
        _, battle, _ = backups.restore(entry_b["scene_id"])
        restored = next(s for s in mgr.custom if s["battle_index"] == battle)
        src = next(d for _, d in mgr.missions() if d["Scene_ID"] == restored["source"])
        self.assertTrue(mgr.is_original(src))
        self.assertEqual(mgr.terrain_of(src["Battle_Scene_Name"]), mgr.terrain_of(restored["battle_scene"]))
        mission = mgr.mission_for_scene(restored["battle_scene"])[1]
        self.assertEqual(mission["Preview_Textures"], src["Preview_Textures"])


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class RemoveOneTest(unittest.TestCase):
    def test_one_scenario_goes_the_others_stay(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        src = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "10_Urban_Area_Close_Combat_Basic")
        for title in ("First", "Second", "Third"):
            mgr.create(src, title, f"{title} briefing.")
        third = next(s for s in mgr.custom if s["title"] == "Third")
        m = SceneModel(ctx, third["battle_index"], terrain=None)
        hostile = next(e for e in m.spawns if m.sc.read(e)["Relationship"] == 1)
        go = m.event_go[hostile]
        m.begin("move")
        m.move_to(m.tr_of_go[go], 12.0, 34.0, follow_terrain=False)
        m.commit()
        m.save()
        original = next(d for _, d in mgr.missions() if mgr.is_original(d))
        mgr.set_mission_text(original["Battle_Scene_Name"], title="Renamed original")

        backups = ScenarioBackups(ctx, mgr)
        second = next(s for s in mgr.custom if s["title"] == "Second")
        done, report = backups.remove_one(second["scene_id"])
        self.assertEqual(done, ["First", "Third"])
        self.assertEqual([s["title"] for s in mgr.custom], ["First", "Third"], report)
        self.assertFalse(os.path.isdir(backups.folder(second["scene_id"])), "its backup is deleted")
        third = next(s for s in mgr.custom if s["title"] == "Third")
        m = SceneModel(ctx, third["battle_index"], terrain=None)
        moved = next(e for e in m.spawns if m.name(m.event_go[e]) == m.name(go)
                     and abs(m.world(m.tr_of_go[m.event_go[e]])[0][0] - 12.0) < 1e-3)
        self.assertAlmostEqual(m.world(m.tr_of_go[m.event_go[moved]])[0][2], 34.0, places=3)
        self.assertEqual(mgr.mission_for_scene(original["Battle_Scene_Name"])[1]["Scene_Title"],
                         "Renamed original", "changes to the original missions stay")

        first = next(s for s in mgr.custom if s["title"] == "First")
        backups.remove_one(first["scene_id"], keep_backup=True)
        self.assertEqual([s["title"] for s in mgr.custom], ["Third"])
        self.assertIn("First", [b["title"] for b in backups.missing()], "a kept backup can be restored")


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class ScenarioExchangeTest(unittest.TestCase):
    def test_export_import(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        src = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "10_Urban_Area_Close_Combat_Basic")
        _, battle_idx = mgr.create(src, "Shared Map", "For a friend.")
        m = SceneModel(ctx, battle_idx, terrain=None)
        hostile = next(e for e in m.spawns if m.sc.read(e)["Relationship"] == 1)
        scenery = next(m.object_root(g) for g in m.foot if m.active(m.object_root(g)))
        m.begin("move")
        m.move_to(m.tr_of_go[m.event_go[hostile]], 12.0, 34.0, follow_terrain=False)
        m.delete(scenery)
        m.commit()
        m.save()
        full = SceneFile(ctx, ctx.scene_path(battle_idx))

        backups = ScenarioBackups(ctx, mgr)
        path = os.path.join(tmp, "Shared_Map.tbscenario")
        backups.export(mgr.custom[0], path, "test")
        with zipfile.ZipFile(path) as z:
            shipped = z.read("battle.level")
        level = os.path.join(tmp, "shipped.level")
        with open(level, "wb") as f:
            f.write(shipped)
        sc = SceneFile(ctx, level)
        kept = set(sc.all_pids())
        self.assertNotIn(scenery, kept, "a deleted object is left out of the export")
        self.assertTrue(kept < set(full.all_pids()))
        self.assertIn(m.event_go[hostile], kept)
        for p in kept:
            walk_pptrs(sc.read(p), lambda r: self.assertTrue(
                r["m_FileID"] or not r["m_PathID"] or r["m_PathID"] in kept, f"dangling reference in {p}"))
            if sc.type_name(p) not in ("Transform", "RectTransform"):
                self.assertEqual(sc.read(p), full.read(p), p)
        self.assertEqual(purge_inactive(sc), (0, 0), "nothing more to remove")
        self.assertEqual(os.path.exists(os.path.join(backups.folder(mgr.custom[0]["scene_id"]), "battle.level")),
                         True, "the local backup keeps the full scene")

        title, _, second_idx, _ = backups.import_file(path)
        self.assertEqual(title, "Shared Map (2)", "a name in use gets a number")
        with open(ctx.scene_path(second_idx), "rb") as f:
            self.assertEqual(f.read(), shipped)
        original = shipped

        mgr.remove_all()
        for b in backups.list():
            backups.delete(b["folder"])
        title, _, third_idx, _ = backups.import_file(path)
        self.assertEqual(title, "Shared Map")
        with open(ctx.scene_path(third_idx), "rb") as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(mgr.mission_for_scene(ctx.scene_label(third_idx))[1]["Scene_Briefing"], "For a friend.")

        bad = os.path.join(tmp, "bad.tbscenario")
        with open(bad, "wb") as f:
            f.write(b"PK\x05\x06" + b"\0" * 18)
        with self.assertRaises(ValueError):
            backups.import_file(bad)


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class PlayerTanksTest(unittest.TestCase):
    def test_choice_is_set_and_survives_a_restore(self):
        tmp = tempfile.mkdtemp(dir=os.path.dirname(GAME))
        self.addCleanup(remove_tree, tmp)
        game = os.path.join(tmp, "game")
        os.makedirs(game)
        mirror_game(game)
        ctx = GameContext(game)
        mgr = ScenarioManager(ctx, os.path.join(tmp, "backups"))
        src = next(p for p, d in mgr.missions() if d["Battle_Scene_Name"] == "26_Open_Field_Mass_Assault")
        mgr.create(src, "Tank Choice", "x")
        entry = mgr.custom[0]
        tanks = mgr.player_tanks(entry)
        self.assertEqual(len(tanks), 19, "every player tank has a button on the briefing screen")
        props = sorted(t["prop"] for t in tanks)
        choice = {p: (i % 3 != 0, i % 2 == 0) for i, p in enumerate(props)}
        with self.assertRaises(ValueError):
            mgr.set_player_tanks(entry, {p: (False, False) for p in props})
        self.assertEqual(mgr.set_player_tanks(entry, choice), sum(1 for a, _ in choice.values() if a))
        got = {t["prop"]: (t["available"], t["free"]) for t in mgr.player_tanks(entry)}
        self.assertEqual(got, choice)
        # the shown tanks are laid out without gaps, 12 per column, each with a size
        from scene_model import SceneModel
        menu = SceneModel(ctx, entry["menu_index"], terrain=None)
        buttons = mgr._player_buttons(menu)
        lst = menu.parent_go(next(iter(buttons)))
        shown = [menu.go_of_tr[c["m_PathID"]] for c in menu.tr[menu.tr_of_go[lst]]["m_Children"]
                 if menu.go_of_tr.get(c["m_PathID"]) in buttons and menu.go[menu.go_of_tr[c["m_PathID"]]]["m_IsActive"]]
        for i, g in enumerate(shown):
            t = menu.sc.read(menu.tr_of_go[g])
            col, row = divmod(i, 12)
            self.assertEqual(t["m_AnchoredPosition"]["y"], -(row + 0.5) * t["m_SizeDelta"]["y"])
            self.assertGreater(t["m_SizeDelta"]["x"], 0)
            self.assertEqual(t["m_AnchoredPosition"]["x"] > 0, col > 0)

        backups = ScenarioBackups(ctx, mgr)
        entry = mgr.custom[0]
        backups.backup(entry)
        mgr.remove_all()
        backups.restore(entry["scene_id"])
        restored = mgr.custom[0]
        got = {t["prop"]: (t["available"], t["free"]) for t in mgr.player_tanks(restored)}
        self.assertEqual(got, choice, "a restored scenario gets its tank choice back")


if __name__ == "__main__":
    unittest.main()
