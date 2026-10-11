# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Tools of the main window: one active tool at a time, visible in the cursor
and the mode bar, and easy to leave (tabs, Objects pane, Esc, right-click).

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
    def __init__(self, x, y, num=1):
        self.x, self.y, self.num, self.state, self.time = x, y, num, 0, 0
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
        app = self.app
        self.root.update()
        return app.tool, str(app.canvas.cget("cursor")), app.place is not None, bool(app.mode_bar.place_info())

    def test_one_tool_at_a_time(self):
        app = self.app
        app.set_tool("select")
        self.assertEqual(self.state(), ("select", "arrow", False, False))

        app.set_tool("raise")
        tool, cursor, placing, bar = self.state()
        self.assertEqual((tool, cursor, placing, bar), ("raise", "sb_up_arrow", False, True))
        self.assertIn("Raise", app.mode_bar.cget("text"))

        # leaving the Terrain tab ends a terrain tool
        app.show_page("left", "terrain")
        app.show_page("left", "objects")
        self.assertEqual(self.state(), ("select", "arrow", False, False))

        # Esc ends a terrain tool; a second Esc clears the selection
        app.set_tool("smooth")
        app.on_key(Key("Escape"))
        self.assertEqual(self.state()[0], "select")

        # placing ends a terrain tool, has its own cursor and bar, and Esc ends it
        app.set_tool("lower")
        app.start_place("Click on the map.", lambda x, z: None, label="Test placing")
        self.assertEqual(self.state(), ("select", "plus", True, True))
        self.assertIn("Placing: Test placing", app.mode_bar.cget("text"))
        app.on_key(Key("Escape"))
        self.assertEqual(self.state(), ("select", "arrow", False, False))

        # choosing a tool ends placing
        app.start_place("Click on the map.", lambda x, z: None)
        app.set_tool("pan")
        self.assertEqual(self.state(), ("pan", "hand2", False, True))
        app.on_key(Key("Escape"))

        # placing an item from the Objects pane ends a terrain tool
        spawn = next(i for i, k in app.obj_templates.items() if k[0] == "spawn")
        app.set_tool("texture")
        app.place_item(spawn, app.view.cx, app.view.cz)
        self.assertEqual(self.state(), ("select", "arrow", False, False))
        app.undo()

        # a right-click on the map ends a terrain tool instead of opening the menu
        app.set_tool("flatten")
        app.context_menu(Click(50, 50, 3))
        self.assertEqual(self.state(), ("select", "arrow", False, False))


if __name__ == "__main__":
    unittest.main()
