# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Map tools of the main window, after QGIS / ArcGIS: one exclusive tool that
stays active until another is chosen (tabs do not change it), an Add tool
chosen from the Objects pane, routes drawn like lines, hints in the status bar.

Run: python -m unittest discover tests
"""

import os
import sys
import tkinter as tk
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import editor  # noqa: E402

from gamepath import GAME  # noqa: E402
SCENE = 18


class Key:
    def __init__(self, keysym):
        self.keysym = keysym
        self.state = 0


class Click:
    def __init__(self, x, y, time=0, num=1):
        self.x, self.y, self.num, self.state, self.time = x, y, num, 0, time
        self.x_root, self.y_root = x, y


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class ToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        cls.app = editor.EditorApp(cls.root, GAME, SCENE, auto_library=False)
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def state(self):
        self.root.update()
        return self.app.tool, str(self.app.canvas.cget("cursor"))

    def pressed(self):
        return {name for name, btn in self.app.tool_buttons.items() if btn.active}

    def test_tools_are_exclusive_and_persistent(self):
        app = self.app
        app.set_tool("vertex")
        self.assertEqual(self.state(), ("vertex", "tcross"))
        self.assertIn("Edit terrain features", app.status_lbl.cget("text"))
        # tabs do not change the tool
        app.show_page("left", "objects")
        app.show_page("left", "toc")
        self.assertEqual(self.state()[0], "vertex")
        self.assertIn("Esc, right-click > Stop", app.status_lbl.cget("text"))
        # with nothing in progress Esc returns to Select / move
        app.on_key(Key("Escape"))
        self.assertEqual(self.state(), ("select", "arrow"))
        app.set_tool("pan")
        self.assertEqual(self.state(), ("pan", "hand2"))
        self.assertEqual(self.pressed(), {"pan"})
        # the pressed button clicked again returns to Select / move
        app.press_tool("pan", lambda: app.set_tool("pan"))
        self.assertEqual(self.state(), ("select", "arrow"))
        app.press_tool("pan", lambda: app.set_tool("pan"))
        self.assertEqual(self.state()[0], "pan")
        app.set_tool("select")

    def test_chosen_object_clicked_again_stops_the_add_tool(self):
        app = self.app
        app.use_template(("spawn", 0))
        self.root.update()
        iid = app.obj_tree.selection()[0]
        app.obj_drag = {"item": iid, "start": (0, 0), "active": False,
                        "chosen": app.tool == "add" and iid in app.obj_tree.selection()}
        app.on_obj_release(Click(0, 0))
        self.assertEqual(self.state(), ("select", "arrow"))

    def test_add_tool_places_with_each_click(self):
        app, m = self.app, self.app.model
        app.set_tool("feature")
        app.use_template(("spawn", 1))
        self.assertEqual(self.state(), ("add", "plus"))
        self.assertEqual(self.pressed(), {"add:hostile"})
        self.assertIn("click on the map", app.status_lbl.cget("text"))
        before = len(m.spawns)
        w, h = 400, 300
        app.view.w, app.view.h = w, h
        app.on_press(Click(200, 150, time=1000))
        app.on_press(Click(260, 150, time=5000))
        self.assertEqual(len(m.spawns), before + 2)
        self.assertEqual(self.state()[0], "add", "the Add tool stays active")
        app.undo()
        app.undo()
        self.assertEqual(len(m.spawns), before)
        app.set_tool("select")
        self.root.update()
        self.assertEqual(app.obj_tree.selection(), (), "choosing another tool clears the Objects pane choice")
        self.assertEqual(self.state()[0], "select")

    def test_route_is_drawn_like_a_line(self):
        app, m = self.app, self.app.model
        app.view.w, app.view.h = 400, 300
        depth = len(m.undo_stack)
        packs = len(m.packs)
        app.use_template(("route",))
        app.on_press(Click(100, 100, time=1000))
        self.assertIsNotNone(app.sketch)
        pack = app.sketch["pack"]
        app.on_press(Click(150, 100, time=3000))
        app.on_press(Click(200, 120, time=5000))
        self.assertEqual(len(m.tr[pack]["m_Children"]), 3)
        app.on_key(Key("BackSpace"))
        self.assertEqual(len(m.tr[pack]["m_Children"]), 2)
        app.context_menu(Click(220, 120, num=3))
        self.assertIsNone(app.sketch, "a right-click finishes the route")
        self.assertEqual(len(m.packs), packs + 1)
        self.assertEqual(self.state()[0], "add")
        # a second route, cancelled with Esc, leaves nothing behind
        app.on_press(Click(300, 200, time=9000))
        app.on_press(Click(320, 220, time=11000))
        app.on_key(Key("Escape"))
        self.assertIsNone(app.sketch)
        self.assertEqual(len(m.packs), packs + 1)
        while len(m.undo_stack) > depth:
            app.undo()
        self.assertEqual(len(m.packs), packs)
        app.set_tool("select")

    def test_terrain_features_are_drawn_and_edited(self):
        app, m = self.app, self.app.model
        app.can_paint = lambda: True
        self.addCleanup(lambda: delattr(app, "can_paint"))
        try:
            app.terrain_session()
        except Exception as ex:
            self.skipTest(f"terrain of scene {SCENE} cannot be edited: {ex}")
        app.view.w, app.view.h = 400, 300
        app.view.cx, app.view.cz, app.view.scale = 0.0, 0.0, 2.0
        depth = len(m.undo_stack)
        app.brush_tree.selection_set("b0")
        self.root.update()
        self.assertEqual(app.brush["name"], "Trench")
        self.assertEqual(self.state(), ("feature", "pencil"), "choosing a brush chooses the Draw terrain tool")
        x, z = app.view.s2w(150, 150)
        g0 = m.ground(x, z)
        app.on_press(Click(100, 150, time=1000))
        app.on_release(Click(100, 150, time=1050))
        self.assertEqual(app.sketch["kind"], "terrain")
        app.on_press(Click(200, 150, time=3000))
        app.on_release(Click(200, 150, time=3050))
        app.on_press(Click(250, 180, time=5000))
        app.on_key(Key("BackSpace"))
        self.assertEqual(len(app.sketch["points"]), 2)
        app.on_key(Key("Return"))
        self.assertIsNone(app.sketch)
        f = app.features()
        self.assertEqual(len(f.strokes), 1)
        self.assertAlmostEqual(m.ground(x, z) - g0, -1.6, delta=0.05, msg="the trench is cut 1.6 m deep")
        table = app.tables["features"]
        self.assertEqual(table.get_children(), (f"f{f.strokes[0]['id']}",))
        self.assertEqual(table.set(f"f{f.strokes[0]['id']}", "brush"), "Trench")
        self.assertEqual(table.set(f"f{f.strokes[0]['id']}", "height"), "-1.6")
        # editing a cell changes the feature (one undo step) and the ground
        app.feature_cell_spec(f.strokes[0]["id"], "height")[2]("-1.0")
        self.assertAlmostEqual(m.ground(x, z) - g0, -1.0, delta=0.05)
        self.assertEqual(table.set(f"f{f.strokes[0]['id']}", "height"), "-1")
        self.assertIsNone(app.feature_cell_spec(f.strokes[0]["id"], "level"), "not used by a cut")
        app.undo()
        self.assertAlmostEqual(m.ground(x, z) - g0, -1.6, delta=0.05)
        table.selection_set(f"f{f.strokes[0]['id']}")
        self.root.update()
        self.assertEqual(app.feature_sel, f.strokes[0]["id"], "a table row selects the feature")
        app.select_feature(None)
        # a second stroke over the same line does not deepen it
        app.on_press(Click(100, 150, time=7000))
        app.on_press(Click(200, 150, time=9000))
        app.on_key(Key("Return"))
        self.assertEqual(len(f.strokes), 2)
        self.assertAlmostEqual(m.ground(x, z) - g0, -1.6, delta=0.05)
        # Edit terrain features: select, move a point, delete
        app.set_tool("vertex")
        app.on_press(Click(150, 150, time=20000))
        app.on_release(Click(150, 150, time=20050))
        sid = app.feature_sel
        self.assertEqual(sid, f.strokes[-1]["id"], "the last drawn feature is on top")
        app.on_press(Click(200, 150, time=22000))
        app.on_motion(Click(200, 100, time=22100))
        app.on_release(Click(200, 100, time=22200))
        self.assertAlmostEqual(f.stroke(sid)["points"][1][1], app.view.s2w(200, 100)[1], places=3)
        app.on_key(Key("Delete"))
        self.assertIsNone(f.stroke(sid))
        self.assertAlmostEqual(m.ground(x, z) - g0, -1.6, delta=0.05, msg="the first trench remains")
        app.on_key(Key("Escape"))
        while len(m.undo_stack) > depth:
            app.undo()
        self.assertEqual(f.strokes, [])
        self.assertAlmostEqual(m.ground(x, z), g0, places=3, msg="undo restores the ground")
        app.set_tool("select")

    def prop_texts(self):
        self.root.update()
        return [w.cget("text") for w in self.app.props.winfo_children() if isinstance(w, tk.Label)]

    def test_event_panel_follows_type_and_trigger(self):
        app, m = self.app, self.app.model
        ev = next(e for e in m.events if e not in m.spawns and m.active(m.event_go[e]))
        depth = len(m.undo_stack)
        app.select(m.event_go[ev])
        app.op("type", m.set_field, ev, "Event_Type", 1)
        app.op("timer", m.set_field, ev, "Trigger_Type", 0)
        app.refresh_props()
        texts = self.prop_texts()
        for heading in ("Action", "Trigger", "Message", "Relations"):
            self.assertIn(heading, texts)
        self.assertIn("Time (s)", texts)
        self.assertNotIn("Required", texts)
        app.set_message(ev, "Short text")
        self.assertEqual(m.sc.read(ev)["Event_Message_JPN"], "Short text", "the Japanese text follows")
        app.op("destroyed", m.set_field, ev, "Trigger_Type", 1)
        app.set_tanks_needed(ev, "count", 2)
        d = m.sc.read(ev)
        self.assertEqual((d["Operator_Type"], d["All_Trigger_Flag"], d["Necessary_Num"]), (0, False, 2))
        app.refresh_props()
        texts = self.prop_texts()
        self.assertNotIn("Time (s)", texts, "the trigger time works for timers only")
        self.assertIn("Required", texts)
        self.assertIn("Number required", texts)
        app.op("enable", m.set_field, ev, "Event_Type", 13)
        app.refresh_props()
        texts = self.prop_texts()
        self.assertNotIn("Message", texts, "only message events show a message")
        self.assertIn("Events to enable", texts)
        while len(m.undo_stack) > depth:
            app.undo()
        app.select(None)

    def test_colour_fields(self):
        hue, level = editor.split_colour((0.036, 0.042, 0.065))
        self.assertAlmostEqual(level, 0.065)
        self.assertAlmostEqual(max(hue), 1.0)
        self.assertGreater(hue[2], hue[0], "a dim blue shows as a clear blue")
        back = editor.compose_colour(hue, level)
        for a, b in zip(back, (0.036, 0.042, 0.065)):
            self.assertAlmostEqual(a, b)
        self.assertEqual(editor.split_colour((0, 0, 0)), ((1.0, 1.0, 1.0), 0.0))
        self.assertEqual(editor.colour_hex((1.0, 0.5, 0.0)), "#FF8000")
        app = self.app
        app.select(None)
        app.refresh_props()
        self.root.update()
        rows = [w for w in app.props.winfo_children() if isinstance(w, tk.Frame)
                and any(isinstance(c, tk.Button) for c in w.winfo_children())]
        self.assertTrue(rows, "the fog colour is a colour button")

    def test_pick_tanks_on_the_map(self):
        app, m = self.app, self.app.model
        app.view.w, app.view.h = 400, 300
        ev = next(e for e in m.events if e not in m.spawns and m.active(m.event_go[e]))
        tank = next(e for e in m.spawns if m.active(m.event_go[e]))
        depth = len(m.undo_stack)
        app.select(m.event_go[ev])
        app.op("trigger", m.set_field, ev, "Trigger_Type", 1)
        app.op("no tanks", m.set_field, ev, "Trigger_Tanks", [])
        app.refresh_props()
        texts = self.prop_texts()
        self.assertIn("Cancels events", texts, "cancelling is part of the action")
        self.assertNotIn("Events to cancel", texts)
        app.start_pick(ev, "tanks", "Trigger_Tanks", "Trigger_Num", "Trigger tanks")
        self.assertEqual(self.state(), ("pick", "target"))
        trp = m.tr_of_go[m.event_go[tank]]
        q = m.world(trp)[0]
        app.view.cx, app.view.cz = q[0], q[2]
        sx, sy = app.view.w2s(q[0], q[2])
        app.on_press(Click(sx, sy, time=1000))
        self.assertEqual([r["m_PathID"] for r in m.sc.read(ev)["Trigger_Tanks"]], [trp])
        self.assertEqual(app.selected, m.event_go[ev], "the event stays selected")
        self.assertEqual(app.event_link_targets()[0], {trp}, "the tank is highlighted")
        app.op("area trigger, any hostile tank", lambda: [m.set_field(ev, "Trigger_Type", 2),
                                                          m.set_field(ev, "Trigger_Setting_Type", 1)])
        self.assertEqual(app.event_link_targets()[0], set(), "an ignored tank list is not highlighted")
        app.undo()
        app.on_press(Click(sx, sy, time=3000))
        self.assertEqual(m.sc.read(ev)["Trigger_Tanks"], [], "a second click removes it")
        app.on_key(Key("Escape"))
        self.assertEqual(self.state()[0], "select")
        while len(m.undo_stack) > depth:
            app.undo()
        app.select(None)
        self.assertTrue(editor.point_in_polygon((1, 1), [(0, 0), (2, 0), (2, 2), (0, 2)]))
        self.assertFalse(editor.point_in_polygon((3, 1), [(0, 0), (2, 0), (2, 2), (0, 2)]))

    def test_clear_selection(self):
        app, m = self.app, self.app.model
        go = m.event_go[m.spawns[0]]
        app.select(go)
        self.assertEqual(app.sel, [go])
        app.feature_sel = 99
        app.clear_selection()
        self.assertEqual(app.sel, [])
        self.assertIsNone(app.feature_sel, "a selected terrain feature is cleared too")
        self.assertIn("clearsel", app._images, "the toolbar button has its icon")

    def test_catalog_menu(self):
        app = self.app
        actions = {label: enabled for label, _, enabled in app.catalog_actions(f"s{SCENE}")}
        self.assertEqual(actions, {"Open": False}, "the open mission; an original cannot be removed")
        self.assertEqual(app.catalog_actions("orig"), [])
        custom = app.scenarios.custom
        if custom:
            labels = [label for label, _, _ in app.catalog_actions(f"s{custom[0]['battle_index']}")]
            self.assertEqual(labels, ["Open", "Remove scenario..."])
        dlg = editor.RemoveScenarioDialog(self.root, "Some scenario", 2)
        self.assertFalse(dlg.collect(), "the backup is deleted unless chosen")
        dlg.keep.set(True)
        self.assertTrue(dlg.collect())
        dlg.destroy()

    def test_trigger_area_size_fields(self):
        app = self.app
        m = app.model
        lib_area = None
        if app.library.entries is None:
            app.library.load()
        if app.library.entries:
            lib_area = next((e for e in app.library.entries if e["category"] == "Trigger areas"
                             and e.get("scene") is not None), None)
        if lib_area is None:
            self.skipTest("object library not built")
        root = app.op("Add object", editor.import_object, m, app.library, lib_area, app.view.cx, app.view.cz, True)[0]
        self.assertIn(root, m.areas)
        app.selected = root
        app.refresh_props()
        key = "radius" if "radius" in m.area_size(root) else "width"
        app.op("Resize trigger area", m.set_area_size, root, **{key: 33.0})
        self.assertAlmostEqual(m.area_size(root)[key], 33.0, places=3)
        app.undo()
        app.undo()

    def test_player_tank_dialog(self):
        tanks = [{"prop": "A_TankProp", "name": "A", "available": True, "free": False},
                 {"prop": "B_TankProp", "name": "B", "available": False, "free": True}]
        dlg = editor.PlayerTankDialog(self.root, tanks)
        self.assertEqual(dlg.collect(), {"A_TankProp": (True, False), "B_TankProp": (False, True)})
        dlg.set_all(2, True)
        self.assertEqual(dlg.collect()["A_TankProp"], (True, True))
        for _, a, _ in dlg.rows:
            a.set(False)
        with self.assertRaises(ValueError):
            dlg.collect()
        dlg.destroy()

    def test_messages_use_the_message_bar(self):
        app = self.app
        app.status("Test warning", "warn", banner=True)
        self.root.update()
        self.assertTrue(app.msg_bar.place_info())
        self.assertEqual(app.msg_lbl.cget("text"), "Test warning")
        app.hide_message()
        self.assertFalse(app.msg_bar.place_info())


if __name__ == "__main__":
    unittest.main()
