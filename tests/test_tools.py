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
        app.set_tool("raise")
        self.assertEqual(self.state(), ("raise", "sb_up_arrow"))
        self.assertIn("Raise terrain", app.status_lbl.cget("text"))
        # tabs do not change the tool
        app.show_page("left", "objects")
        app.show_page("left", "toc")
        self.assertEqual(self.state()[0], "raise")
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
        app.set_tool("lower")
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
