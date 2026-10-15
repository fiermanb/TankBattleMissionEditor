# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Tank Battle Classic mission editor (Tkinter, Windows desktop look).

Usage:
    python editor.py [--game "<Tank Battle Classic folder>"] [--scene 18]

Layout: menu bar; toolbar blocks with grippers (Standard,
Tools, Rotate, Edit, Insert); tabbed dock areas in resizable panes - Table Of
Contents and Objects on the left, Properties and Catalog on the right, the
Spawns / Events tables under the map; status bar with cursor coordinates.
See README.md for controls and limitations.
"""

import argparse
import collections
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

import icons
import settings
from version import APP_NAME, COPYRIGHT, __version__
from briefing_sync import sync_mission
from scenario_backup import EXCHANGE_SUFFIX, ScenarioBackups
from navmesh_edit import NavMesh, decode_tile
from lighting import PRESET_NAMES, PRESETS, Lighting, source_scene
import event_types as et
from terrain_edit import TerrainPaint
from terrain_features import (AI_MAX_SLOPE, FEATURES_FILE, KIND_NAMES, KINDS, TerrainFeatures, clean_brush,
                              default_brushes, find_layer, layer_label, length, line_distance)
from terrain_features import bbox as feature_bbox
from object_import import ObjectLibrary, base_name, category, import_object, reconnect_event_refs
from scenarios import ScenarioManager
from scene_io import GameContext
from scene_model import (
    EVENT_TYPES, OPERATORS, RELATIONSHIPS, TRIGGER_SETTINGS, TRIGGER_TYPES, SceneModel, heading_of, label, tank_display,
)


# palette: Windows 10 native controls, flat borders
FACE = "#F0F0F0"
TOOLBAR = "#F5F5F5"
WINDOW = "#FFFFFF"
BORDER = "#A0A0A0"
FIELD_BORDER = "#7A7A7A"
SEL_BG = "#0078D7"
SEL_FG = "#FFFFFF"
HOVER_BG = "#E5F3FF"
HOVER_BORDER = "#CCE8FF"
CHECKED_BG = "#CCE8FF"
CHECKED_BORDER = "#3399FF"
CAPTION_BG = "#C9D6E4"
CAPTION_ACTIVE_BG = "#A7C0DC"
TAB_TEXT = "#6D6D6D"
INFO_BG = "#FFFFFF"
INFO_BORDER = "#767676"
WORKSPACE = "#FFFFFF"
OK_FG = "#006400"
WARN_FG = "#A00000"
UI_FONT = ("Segoe UI", 9)
UI_BOLD = ("Segoe UI", 9, "bold")
MAP_FONT = ("Segoe UI", 8)

COL_PLAYER = (255, 215, 60)
COL_FRIEND = (80, 160, 255)
COL_HOSTILE = (240, 70, 60)
COL_HIDDEN = (150, 150, 150)
COL_GOLD = (255, 210, 80)
PACK_COLOURS = [(255, 160, 40), (60, 220, 220), (220, 90, 220), (150, 230, 90),
                (250, 250, 120), (120, 150, 255), (255, 120, 150), (200, 200, 200)]
CATEGORY_COLOURS = {
    "Buildings": (170, 152, 132),
    "Trees and hedges": (58, 112, 48),
    "Fences and walls": (150, 140, 118),
    "Vehicles": (150, 105, 170),
    "Roads and rails": (110, 110, 122),
    "Props": (128, 128, 118),
    "Trigger areas": (235, 125, 30),
}
AREA_COLOUR = CATEGORY_COLOURS["Trigger areas"]
LARGE_AREA = 20000.0
ALT_MASK = 0x20000
SHIFT_MASK = 0x1
CTRL_MASK = 0x4

# table of contents: key, label, legend kind, colour, parent key
TOC_LAYERS = [
    ("Spawns", "Tank spawns", None, None, None),
    ("Player", "Player", "triangle", COL_PLAYER, "Spawns"),
    ("Friendly", "Friendly", "triangle", COL_FRIEND, "Spawns"),
    ("Hostile", "Hostile", "triangle", COL_HOSTILE, "Spawns"),
    ("Waypoints", "Waypoints", "diamond", PACK_COLOURS[0], None),
    ("RouteLinks", "Tank route links", "routelink", PACK_COLOURS[0], "Waypoints"),
    ("Events", "Events", "event", None, None),
    ("Scenery", "Scenery", None, None, None),
] + [(cat, cat, "square", col, "Scenery") for cat, col in CATEGORY_COLOURS.items()] + [
    ("Terrain", "Terrain", "terrain", None, None),
    ("Features", "Terrain features", "route", (139, 69, 19), "Terrain"),
    ("Elevation", "Elevation tint", "elevation", None, "Terrain"),
    ("NavMesh", "AI route map", "navmesh", None, "Terrain"),
    ("Contours", "Contour lines", "contour", None, "Terrain"),
    ("Deleted", "Deleted objects", "square", COL_HIDDEN, None),
]
TOC_DEFAULT_OFF = {"Deleted", "Elevation", "Contours", "NavMesh"}
TOC_OVERLAYS = {"Elevation", "Contours", "NavMesh"}
OBJ_HINT = ("Click an item to choose the Add tool for it, then click on the map to place one with each "
            "click. Dragging an item onto the map places one directly.")
DOUBLE_CLICK_MS = 400
EVENT_MARK = 18

TOOLS = ("select", "pan", "zoomin", "zoomout", "rotate")
TOOL_CURSORS = {"select": "arrow", "pan": "hand2", "zoomin": "crosshair", "zoomout": "crosshair",
                "rotate": "exchange", "feature": "pencil", "vertex": "tcross", "add": "plus"}
FEATURE_COLOURS = {"cut": "#8B4513", "raise": "#D2691E", "level": "#B8A000", "smooth": "#9050C0",
                   "paint": "#2E9E3E"}
MESSAGE_COLOURS = {None: ("#DCEAF8", "#7FA7D6"), "ok": ("#DFF0D8", "#79B26B"),
                   "warn": ("#FCF3CF", "#D2B03C")}
MESSAGE_MS = {None: 5000, "ok": 5000, "warn": 10000}
ADD_BUTTONS = {("spawn", 1): "add:hostile", ("spawn", 0): "add:friendly", ("route",): "add:route",
               ("waypoint",): "add:waypoint"}
TOOL_NAMES = {"select": "Select / move", "pan": "Pan", "zoomin": "Zoom in", "zoomout": "Zoom out",
              "rotate": "Rotate", "feature": "Draw terrain", "vertex": "Edit terrain features", "add": "Add"}
TERRAIN_TOOLS = ("feature", "vertex")
FREEHAND_PX = 6
BRUSH_PARAMS = (("width", "Width", "m", "Width at ground level"),
                ("height", "Height", "m", "Change of the ground: negative cuts, positive raises"),
                ("slope", "Side slope", "deg", "Steepness of the sides (90 = vertical)"),
                ("rim", "Rim", "m", "Height of the spoil along the edges of a cut"),
                ("crossings", "AI crossings every", "m", "Undug crossings left for the AI along a cut; 0 = none"),
                ("level", "Level to", "m", "Height in metres; empty: the ground at the first point"))
KIND_PARAMS = {"shape": ("width", "height", "slope", "rim", "crossings", "texture"),
               "level": ("width", "slope", "level", "texture"),
               "smooth": ("width", "slope", "texture"),
               "paint": ("width", "texture")}

SPAWN_FIELDS = [
    ("Side", "Relationship", RELATIONSHIPS),
    ("Controlled by", "Tank_ID", {0: "AI", 1: "Player"}),
    ("Mission target", "Is_Mission_Target", "bool"),
    ("Spawn trigger", "Trigger_Type", TRIGGER_TYPES),
    ("Trigger time (s)", "Trigger_Time", "float"),
    ("Fixed on ground", "Fixed_On_Ground", "bool"),
    ("Respawn times", "Respawn_Times", "int"),
    ("Respawn interval", "Auto_Respawn_Interval", "float"),
    ("Remove after death", "Remove_After_Death", "bool"),
    ("Patrol type", "Patrol_Type", "int"),
    ("No attack", "No_Attack", "bool"),
    ("Breakthrough", "Breakthrough", "bool"),
    ("Follow closest enemy", "Follow_Closest_Enemy", "bool"),
    ("Follow player's enemy", "Follow_Player_Closest_Enemy", "bool"),
    ("Follow distance", "Follow_Distance", "float"),
    ("Through hedges", "Through_Hedges", "bool"),
    ("Attack distance", "Attack_Distance", "float"),
    ("Approach distance", "Approach_Distance", "float"),
    ("Open fire distance", "OpenFire_Distance", "float"),
    ("Lost count", "Lost_Count", "float"),
    ("Dead angle", "Dead_Angle", "float"),
    ("Extra visibility", "Extended_Visibility", "float"),
    ("Max lead multiplier", "Max_Lead_Multiplier", "float"),
    ("Patrol speed rate", "Patrol_Speed_Rate", "float"),
    ("Combat speed rate", "Combat_Speed_Rate", "float"),
    ("Attack mult. index", "Attack_Multiplier_Index", "int"),
    ("Defence mult. index", "Defence_Multiplier_Index", "int"),
    ("Drive speed index", "Drive_Speed_Multiplier_Index", "int"),
    ("Turret speed index", "Turret_Speed_Multiplier_Index", "int"),
    ("Reload speed index", "Reload_Speed_Multiplier_Index", "int"),
    ("Bullet speed index", "Bullet_Speed_Multiplier_Index", "int"),
]
AREA_FIELDS = [("Tanks that count", "Trigger_Setting_Type", TRIGGER_SETTINGS)]
FOG_KEYS = ("m_Fog", "m_FogDensity", "m_FogColor", "Current_Visibility", "Dust_Particle_Multiplier")
LIGHTING_FIELDS = [("Sun / moon colour", "sun_colour", "colour"), ("Sun / moon intensity", "sun_intensity", "float"),
                   ("Ambient light", "ambient", "colour"), ("Sky brightness", "sky_brightness", "float"),
                   ("Sky colour", "sky_colour", "colour")]
MISSION_FIELDS = [
    ("RenderSettings", None, "Fog", "m_Fog", "bool"),
    ("RenderSettings", None, "Fog density", "m_FogDensity", "float"),
    ("RenderSettings", None, "Fog colour", "m_FogColor", "colour"),
    ("MonoBehaviour", "Lighting_Control_CS", "Visibility (m)", "Current_Visibility", "float"),
    ("MonoBehaviour", "Lighting_Control_CS", "Dust particles", "Dust_Particle_Multiplier", "float"),
    ("MonoBehaviour", "Game_Controller_CS", "Recon plane available", "Available_Recon_Plane", "bool"),
    ("MonoBehaviour", "Score_Manager_CS", "Assist score", "Enable_Assist_Score", "bool"),
]


def summary(names, empty="none", limit=6):
    """A list of names for the Properties panel; a long one is shortened."""
    if not names:
        return empty
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit - 1]) + f" and {len(names) - limit + 1} more"


def _number(text):
    """A number typed in a field; 'inf' is allowed (for example an approach
    distance that holds the tank in place)."""
    return float(str(text).strip().replace(",", "."))


def _rgb_of(colour):
    return (colour["r"], colour["g"], colour["b"])


def _positive(text):
    v = float(str(text).replace(",", "."))
    if v < 0:
        raise ValueError(text)
    return v


def hexc(rgb):
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb[:3])


def is_mission_scene(name):
    low = name.lower()
    return not (low.endswith("_menu") or "loading" in low or "title" in low
                or "mission_select" in low or low.startswith("99_"))


def points_in_polys(polys, x, z):
    inside = np.zeros(len(polys), dtype=bool)
    for i in range(4):
        a, b = polys[:, i], polys[:, (i + 1) % 4]
        cond = (a[:, 1] > z) != (b[:, 1] > z)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = a[:, 0] + (z - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
        inside ^= cond & (x < xint)
    return inside


def poly_areas(polys):
    x, z = polys[:, :, 0], polys[:, :, 1]
    return 0.5 * np.abs(np.sum(x * np.roll(z, -1, 1) - np.roll(x, -1, 1) * z, axis=1))


class View:
    def __init__(self):
        self.cx, self.cz, self.scale = 0.0, 0.0, 0.8
        self.w, self.h = 800, 600

    def w2s(self, x, z):
        return (self.w / 2 + (x - self.cx) * self.scale, self.h / 2 - (z - self.cz) * self.scale)

    def s2w(self, sx, sy):
        return (self.cx + (sx - self.w / 2) / self.scale, self.cz - (sy - self.h / 2) / self.scale)

    def key(self):
        return (round(self.cx, 3), round(self.cz, 3), round(self.scale, 5), self.w, self.h)


# ---- classic widgets ---------------------------------------------------------

def apply_classic_style(root):
    """Native Windows 10 controls (ttk 'vista' theme) with matching colours."""
    root.option_add("*Font", UI_FONT)
    root.option_add("*Background", FACE)
    root.option_add("*Foreground", "#000000")
    root.option_add("*activeBackground", HOVER_BG)
    root.option_add("*selectBackground", SEL_BG)
    root.option_add("*selectForeground", SEL_FG)
    root.option_add("*highlightBackground", FACE)
    root.option_add("*highlightColor", SEL_BG)
    root.option_add("*Menu.activeBackground", HOVER_BORDER)
    root.option_add("*Menu.activeForeground", "#000000")
    root.option_add("*Menu.activeBorderWidth", 0)
    root.option_add("*Listbox.Background", WINDOW)
    root.option_add("*Listbox.relief", "flat")
    root.option_add("*Listbox.highlightThickness", 0)
    root.option_add("*Text.Background", WINDOW)
    root.option_add("*Text.relief", "flat")
    root.configure(background=FACE)
    style = ttk.Style(root)
    for theme in ("vista", "xpnative", "winnative", "clam"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break
    style.configure(".", font=UI_FONT)
    style.configure("TFrame", background=FACE)
    style.configure("TLabel", background=FACE)
    style.configure("TCheckbutton", background=FACE)
    style.configure("Treeview", background=WINDOW, fieldbackground=WINDOW, font=UI_FONT, rowheight=20,
                    borderwidth=0)
    style.configure("Treeview.Heading", font=UI_FONT)
    style.map("Treeview", background=[("selected", SEL_BG)], foreground=[("selected", SEL_FG)])
    style.configure("Toolbar.TCombobox", padding=1)


class ToolTip:
    """Classic yellow tooltip shown after the pointer rests on a widget."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip, self.after = widget, text, None, None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _e=None):
        self.after = self.widget.after(500, self.show)

    def show(self):
        if self.tip:
            return
        x = self.widget.winfo_rootx() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg=INFO_BG, fg="#575757", padx=5, pady=2, bd=0,
                 highlightthickness=1, highlightbackground=INFO_BORDER).pack()

    def hide(self, _e=None):
        if self.after:
            self.widget.after_cancel(self.after)
            self.after = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class IconButton(tk.Label):
    """Toolbar icon button, Windows 10 style: flat; light blue with a
    thin border on hover; blue fill and border when it is the active tool."""

    def __init__(self, master, image, command, tip, toggle=False):
        super().__init__(master, image=image, bd=0, padx=3, pady=3, bg=TOOLBAR,
                         highlightthickness=1, highlightbackground=TOOLBAR)
        self.command, self.toggle, self.active, self.hover, self.pressed = command, toggle, False, False, False
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        ToolTip(self, tip)

    def _set(self, hover=None):
        if hover is not None:
            self.hover = hover
        if self.pressed or self.active:
            bg, border = CHECKED_BG, CHECKED_BORDER
        elif self.hover:
            bg, border = HOVER_BG, HOVER_BORDER
        else:
            bg, border = TOOLBAR, TOOLBAR
        self.config(bg=bg, highlightbackground=border)

    def _press(self, _e):
        self.pressed = True
        self._set()

    def _release(self, e):
        self.pressed = False
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._set()
        if inside:
            self.command()

    def set_active(self, on):
        self.active = on
        self._set()


class ToolbarBlock(tk.Frame):
    """One docked toolbar: flat, with a dotted gripper on the left."""

    def __init__(self, master, title):
        super().__init__(master, bd=0, relief="flat", bg=TOOLBAR)
        self.title = title
        grip = tk.Canvas(self, width=5, height=24, highlightthickness=0, bd=0, bg=TOOLBAR)
        grip.pack(side="left", fill="y", padx=(2, 3), pady=2)
        grip.bind("<Configure>", lambda e: self._draw_grip(grip))
        ToolTip(grip, title)

    @staticmethod
    def _draw_grip(c):
        c.delete("all")
        h = c.winfo_height()
        for y in range(4, h - 3, 4):
            c.create_rectangle(1, y, 2, y + 1, fill="#A0A0A0", outline="#A0A0A0")

    def separator(self):
        tk.Frame(self, width=1, bg="#C8C8C8").pack(side="left", fill="y", padx=4, pady=4)

    def label(self, text):
        tk.Label(self, text=text, bg=TOOLBAR).pack(side="left", padx=(4, 2))


class ToolbarArea(tk.Frame):
    """Docking area that lays toolbar blocks out in rows and wraps them."""

    def __init__(self, master):
        super().__init__(master, bd=0, height=34, bg=TOOLBAR)
        self.blocks = []
        self.bind("<Configure>", lambda e: self.after_idle(self.relayout))

    def add(self, block):
        self.blocks.append(block)
        self.after_idle(self.relayout)

    def relayout(self):
        width = max(200, self.winfo_width())
        x, y, rowh = 2, 2, 0
        for b in self.blocks:
            b.update_idletasks()
            w, h = b.winfo_reqwidth(), b.winfo_reqheight()
            if x + w > width and x > 2:
                x, y, rowh = 2, y + rowh + 2, 0
            b.place(x=x, y=y)
            x += w + 6
            rowh = max(rowh, h)
        want = y + rowh + 3
        if int(self.cget("height")) != want:
            self.config(height=want)


def bordered(parent, **pack):
    """Frame with a thin grey border (Windows 10 control frame)."""
    f = tk.Frame(parent, bd=0, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER,
                 bg=WINDOW)
    if pack:
        f.pack(**pack)
    return f


class DockArea(tk.Frame):
    """Dockable window area: light blue-grey caption with the
    active page's title and a close button (darker while the dock is the one
    being worked in), pages stacked in the body, tabs with icons along the
    bottom when there is more than one page."""

    def __init__(self, master, on_close):
        super().__init__(master, bd=0, highlightthickness=1, highlightbackground=BORDER, bg=FACE)
        self.pages = {}
        self.order = []
        self.active = None
        self.on_select = None
        self.cap = tk.Frame(self, bg=CAPTION_BG)
        self.cap.pack(side="top", fill="x")
        self.title_lbl = tk.Label(self.cap, text="", anchor="w", padx=4, pady=2, bg=CAPTION_BG)
        self.title_lbl.pack(side="left", fill="x", expand=True)
        self.close = tk.Canvas(self.cap, width=16, height=16, highlightthickness=0, bd=0, bg=CAPTION_BG)
        self.close.pack(side="right", padx=4)
        self.close.create_line(4, 4, 12, 12, width=2)
        self.close.create_line(12, 4, 4, 12, width=2)
        self.close.bind("<ButtonRelease-1>", lambda e: on_close())
        ToolTip(self.close, "Close")
        self.tabs = tk.Frame(self, bg=FACE)
        self.tabs.pack(side="bottom", fill="x")
        self.body = tk.Frame(self, bd=0, bg=FACE)
        self.body.pack(side="top", fill="both", expand=True)

    def set_focus(self, on):
        bg = CAPTION_ACTIVE_BG if on else CAPTION_BG
        for w in (self.cap, self.title_lbl, self.close):
            w.config(bg=bg)

    def add_page(self, key, title, image):
        frame = tk.Frame(self.body, bg=FACE)
        tab = tk.Label(self.tabs, text=" " + title, image=image, compound="left", padx=5, pady=1, bd=0,
                       bg=FACE, fg=TAB_TEXT, highlightthickness=1, highlightbackground=FACE)
        tab.bind("<ButtonRelease-1>", lambda e: self.select(key))
        self.pages[key] = (title, frame, tab)
        self.order.append(key)
        self._layout_tabs()
        if self.active is None:
            self.select(key)
        return frame

    def _layout_tabs(self):
        for w in self.tabs.winfo_children():
            w.pack_forget()
        if len(self.order) > 1:
            for key in self.order:
                self.pages[key][2].pack(side="left", padx=(2, 0), pady=(1, 2))

    def select(self, key):
        if self.active == key:
            return
        if self.active is not None:
            _, f, t = self.pages[self.active]
            f.pack_forget()
            t.config(bg=FACE, fg=TAB_TEXT, highlightbackground=FACE)
        title, f, t = self.pages[key]
        f.pack(fill="both", expand=True)
        t.config(bg=WINDOW, fg="#000000", highlightbackground=BORDER)
        self.title_lbl.config(text=title)
        self.active = key
        if self.on_select:
            self.on_select(key)


class Dialog(tk.Toplevel):
    """Modal classic dialog with OK / Cancel."""

    def __init__(self, parent, title):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        self.result = None
        self.body = tk.Frame(self, padx=10, pady=10)
        self.body.pack(fill="both", expand=True)
        bar = tk.Frame(self, padx=10, pady=8)
        bar.pack(fill="x")
        ttk.Button(bar, text="Cancel", width=10, command=self.cancel).pack(side="right")
        ttk.Button(bar, text="OK", width=10, command=self.ok, default="active").pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)

    def run(self):
        self.update_idletasks()
        p = self.master
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_reqwidth()) // 2
        y = p.winfo_rooty() + (p.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result

    def ok(self):
        try:
            self.result = self.collect()
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e), parent=self)
            return
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

    def collect(self):
        return True


class ListDialog(Dialog):
    def __init__(self, parent, title, items, current=None, prompt=None):
        super().__init__(parent, title)
        self.items = items
        if prompt:
            tk.Label(self.body, text=prompt, anchor="w").pack(fill="x")
        frow = tk.Frame(self.body)
        frow.pack(fill="x", pady=(4, 6))
        tk.Label(frow, text="Filter:").pack(side="left")
        self.filter = tk.StringVar()
        ent = ttk.Entry(frow, textvariable=self.filter)
        ent.pack(side="left", fill="x", expand=True, padx=(6, 0))
        box = bordered(self.body, fill="both", expand=True)
        self.lb = tk.Listbox(box, width=90, height=22, bd=0, activestyle="none", exportselection=False)
        sb = ttk.Scrollbar(box, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<Double-Button-1>", lambda e: self.ok())
        self.bind("<Return>", lambda e: self.ok())
        self.filter.trace_add("write", lambda *a: self.fill())
        self.fill(current)
        ent.focus_set()

    def fill(self, current=None):
        f = self.filter.get().lower()
        self.shown = [it for it in self.items if f in it[0].lower()]
        self.lb.delete(0, "end")
        for i, it in enumerate(self.shown):
            self.lb.insert("end", it[0])
            if len(it) > 2 and it[2]:
                self.lb.itemconfig(i, foreground=it[2])
        idx = 0
        if current is not None:
            idx = next((i for i, it in enumerate(self.shown) if it[1] == current), 0)
        if self.shown:
            self.lb.selection_set(idx)
            self.lb.see(idx)

    def collect(self):
        sel = self.lb.curselection()
        if not sel:
            raise ValueError("Select an entry.")
        return self.shown[sel[0]][1]


class TextFormDialog(Dialog):
    """Title + multi-line briefing, optionally with a source choice: a copy of a
    mission, or (with terrains) an empty terrain. After run(), self.empty tells
    which of the two was chosen."""

    def __init__(self, parent, title, sources=None, title_text="", briefing="", terrains=None):
        super().__init__(parent, title)
        self.sources = sources
        self.terrains = terrains
        self.empty = False
        r = 0
        if sources:
            self.mode = tk.StringVar(value="copy")
            if terrains:
                ttk.Radiobutton(self.body, text="Copy of mission:", variable=self.mode, value="copy").grid(
                    row=r, column=0, sticky="w", pady=2)
            else:
                tk.Label(self.body, text="Copy from mission:", anchor="w").grid(row=r, column=0, sticky="w", pady=2)
            self.src = ttk.Combobox(self.body, state="readonly", width=60, values=[s[0] for s in sources])
            self.src.current(0)
            self.src.grid(row=r, column=1, sticky="ew", pady=2)
            self.src.bind("<<ComboboxSelected>>", lambda e: self.mode.set("copy"))
            r += 1
        if sources and terrains:
            ttk.Radiobutton(self.body, text="Empty terrain:", variable=self.mode, value="empty").grid(
                row=r, column=0, sticky="w", pady=2)
            self.ter = ttk.Combobox(self.body, state="readonly", width=60, values=[t[0] for t in terrains])
            self.ter.current(0)
            self.ter.grid(row=r, column=1, sticky="ew", pady=2)
            self.ter.bind("<<ComboboxSelected>>", lambda e: self.mode.set("empty"))
            r += 1
            tk.Label(self.body, anchor="w", justify="left", fg="#555555", wraplength=430,
                     text="An empty terrain keeps only the player tank and the 'player destroyed' "
                          "event; objects over tunnels stay. Add enemies and a 'Mission complete' "
                          "event yourself. AI navigation still avoids the removed buildings.").grid(
                row=r, column=1, sticky="w", pady=(0, 4))
            r += 1
        tk.Label(self.body, text="Title:", anchor="w").grid(row=r, column=0, sticky="w", pady=2)
        self.title_var = tk.StringVar(value=title_text)
        ent = ttk.Entry(self.body, textvariable=self.title_var, width=62)
        ent.grid(row=r, column=1, sticky="ew", pady=2)
        r += 1
        tk.Label(self.body, text="Briefing:", anchor="nw").grid(row=r, column=0, sticky="nw", pady=2)
        box = bordered(self.body)
        box.grid(row=r, column=1, sticky="nsew", pady=2)
        self.text = tk.Text(box, width=62, height=10, bd=0, wrap="word", padx=3, pady=2)
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", briefing)
        self.body.columnconfigure(1, weight=1)
        self.body.rowconfigure(r, weight=1)
        ent.focus_set()

    def collect(self):
        t = self.title_var.get().strip()
        if not t:
            raise ValueError("The title is empty.")
        b = self.text.get("1.0", "end").strip()
        src = self.sources[self.src.current()][1] if self.sources else None
        if self.terrains and self.mode.get() == "empty":
            self.empty = True
            src = self.terrains[self.ter.current()][1]
        return src, t, b


class TankListDialog(Dialog):
    """Choose several tanks (Shift / Ctrl + click), current ones preselected."""

    def __init__(self, parent, title, items, chosen, noun="tanks"):
        super().__init__(parent, title)
        tk.Label(self.body, text=f"Select the {noun} (Shift / Ctrl + click for several):", anchor="w").pack(fill="x")
        box = bordered(self.body, fill="both", expand=True, pady=(4, 4))
        self.items = items
        self.lb = tk.Listbox(box, width=60, height=20, bd=0, selectmode="extended", activestyle="none",
                             exportselection=False)
        sb = ttk.Scrollbar(box, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        for i, (text, value) in enumerate(items):
            self.lb.insert("end", text)
            if value in chosen:
                self.lb.selection_set(i)
        row = tk.Frame(self.body)
        row.pack(fill="x")
        ttk.Button(row, text="Select all", command=lambda: self.lb.selection_set(0, "end")).pack(side="left")
        ttk.Button(row, text="Clear", command=lambda: self.lb.selection_clear(0, "end")).pack(side="left", padx=6)

    def collect(self):
        return [self.items[i][1] for i in self.lb.curselection()]


class PlayerTankDialog(Dialog):
    """The tanks the briefing screen offers the player: shown in the list, and
    free (no unlocking needed)."""

    def __init__(self, parent, tanks):
        super().__init__(parent, "Player tanks")
        tk.Label(self.body, anchor="w", justify="left", text=(
            "Available: the tank is shown in the briefing screen's tank list.\n"
            "Free: it can be chosen without unlocking it first.")).grid(row=0, column=0, columnspan=3, sticky="w")
        for c, title in enumerate(("Tank", "Available", "Free")):
            tk.Label(self.body, text=title, font=UI_BOLD, anchor="w").grid(row=1, column=c, sticky="w", padx=(0, 12))
        self.rows = []
        for r, t in enumerate(sorted(tanks, key=lambda t: t["name"]), start=2):
            a, f = tk.BooleanVar(value=t["available"]), tk.BooleanVar(value=t["free"])
            tk.Label(self.body, text=t["name"], anchor="w").grid(row=r, column=0, sticky="w", padx=(0, 12))
            ttk.Checkbutton(self.body, variable=a).grid(row=r, column=1)
            ttk.Checkbutton(self.body, variable=f).grid(row=r, column=2)
            self.rows.append((t["prop"], a, f))
        bar = tk.Frame(self.body)
        bar.grid(row=len(tanks) + 2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        for text, col, value in (("All available", 1, True), ("All free", 2, True)):
            ttk.Button(bar, text=text, command=lambda c=col, v=value: self.set_all(c, v)).pack(side="left", padx=(0, 6))

    def set_all(self, column, value):
        for _, a, f in self.rows:
            (a if column == 1 else f).set(value)

    def collect(self):
        result = {prop: (a.get(), f.get()) for prop, a, f in self.rows}
        if not any(a for a, _ in result.values()):
            raise ValueError("At least one tank must be available.")
        return result


def outline_polygon(pts, r):
    """Flat list of screen coordinates around the polyline `pts` at distance r
    (mitred corners, the miter limited to 3 r)."""
    p = np.asarray(pts, dtype=float)
    d = np.diff(p, axis=0)
    n = np.stack([-d[:, 1], d[:, 0]], axis=1) / np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-9)[:, None]
    normals = [n[0]] + [a + b for a, b in zip(n[:-1], n[1:])] + [n[-1]]
    side = []
    for i, v in enumerate(normals):
        ref = n[min(i, len(n) - 1)]
        k = float(np.dot(v, ref))
        v = v / k if abs(k) > 1e-9 else ref
        length = float(np.hypot(*v))
        if length > 3:
            v = v * 3 / length
        side.append(v * r)
    left = [p[i] + side[i] for i in range(len(p))]
    right = [p[i] - side[i] for i in range(len(p))]
    return [float(q) for pt in left + right[::-1] for q in pt]


class RemoveScenarioDialog(Dialog):
    """Confirm removing one scenario; run() returns keep-backup (bool) or None."""

    def __init__(self, parent, title, others):
        super().__init__(parent, "Remove scenario")
        text = f"Remove '{title}' from the game?"
        if others:
            text += (f"\n\nThe game numbers its scenes, so all custom scenarios are removed and the other "
                     f"{others} restored from their backups. This can take a minute or two.")
        tk.Label(self.body, text=text, anchor="w", justify="left", wraplength=420).pack(fill="x")
        self.keep = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.body, variable=self.keep, text="Keep its backup (Scenario > Restore scenarios "
                        "from backup brings it back)").pack(anchor="w", pady=(8, 0))

    def collect(self):
        return self.keep.get()


class NameDialog(Dialog):
    """Ask for a name."""

    def __init__(self, parent, title, prompt, value=""):
        super().__init__(parent, title)
        tk.Label(self.body, text=prompt, anchor="w").pack(fill="x")
        self.var = tk.StringVar(value=value)
        ent = ttk.Entry(self.body, textvariable=self.var, width=40)
        ent.pack(fill="x", pady=(4, 0))
        ent.select_range(0, "end")
        ent.focus_set()

    def collect(self):
        name = self.var.get().strip()
        if not name:
            raise ValueError("The name is empty.")
        return name


class GameFolderDialog(Dialog):
    """Choose the installed game (Steam) folder."""

    def __init__(self, parent, current=None, reason=None):
        super().__init__(parent, "Select game folder")
        tk.Label(self.body, justify="left", anchor="w", text=(
            "Choose the folder where Tank Battle Classic is installed (the folder that contains\n"
            "'Tank Battle Classic.exe'). The editor changes the mission files of that installation.")).pack(
            fill="x")
        if reason:
            tk.Label(self.body, text=reason, fg=WARN_FG, anchor="w", justify="left").pack(fill="x", pady=(4, 0))
        tk.Label(self.body, text="Installations found in the Steam libraries:", anchor="w").pack(
            fill="x", pady=(8, 2))
        box = bordered(self.body, fill="both", expand=True)
        self.found = settings.detect_game()
        self.lb = tk.Listbox(box, height=5, width=80, bd=0, activestyle="none", exportselection=False)
        self.lb.pack(fill="both", expand=True)
        for p in self.found:
            self.lb.insert("end", p)
        if not self.found:
            self.lb.insert("end", "(none found - use Browse)")
        self.lb.bind("<<ListboxSelect>>", self._pick)
        self.lb.bind("<Double-Button-1>", lambda e: self.ok())
        row = tk.Frame(self.body)
        row.pack(fill="x", pady=(8, 0))
        tk.Label(row, text="Folder:").pack(side="left")
        self.var = tk.StringVar(value=current or (self.found[0] if self.found else ""))
        ttk.Entry(row, textvariable=self.var, width=70).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse...", command=self._browse).pack(side="left")
        self.check = tk.Label(self.body, text="", anchor="w")
        self.check.pack(fill="x", pady=(6, 0))
        self.var.trace_add("write", lambda *a: self._validate())
        self._validate()

    def _pick(self, _e=None):
        sel = self.lb.curselection()
        if sel and self.found:
            self.var.set(self.found[sel[0]])

    def _browse(self):
        p = filedialog.askdirectory(parent=self, title="Tank Battle Classic folder",
                                    initialdir=self.var.get() or None)
        if p:
            self.var.set(os.path.normpath(p))

    def _validate(self):
        ok, msg = settings.validate_game(self.var.get().strip())
        self.check.config(text="Valid Tank Battle Classic installation." if ok else msg,
                          fg=OK_FG if ok else WARN_FG)
        return ok, msg

    def collect(self):
        ok, msg = self._validate()
        if not ok:
            raise ValueError(msg)
        return os.path.normpath(self.var.get().strip())


# ---- the application -----------------------------------------------------------

class EditorApp:
    @property
    def selected(self):
        """Primary (most recently selected) object of the selection, or None."""
        sel = getattr(self, "sel", [])
        return sel[-1] if sel else None

    @selected.setter
    def selected(self, go):
        self.sel = [] if go is None else [go]

    def __init__(self, root, game_root, scene, auto_library=True):
        self.root = root
        self._terrain_rev = 0
        self._terrain_view = (None, None)
        self._navmesh_view = (None, None)
        self._contour_interval = None
        self.auto_library = auto_library
        self._lib_job = None
        self.view = View()
        self.model = None
        self.ctx = None
        self.selected = None
        self.drag = None
        self.obj_drag = None
        self.tool = "select"
        self._base_key = None
        self._photo = None
        self._terrain_img = None
        self._scenery_cache = (None, None)
        self._render_pending = False
        self._banner_after = None
        self._table_sync = False
        self.clipboard = None
        self.mouse = None
        self.sketch = None
        self.add_item = None
        self.space_down = False
        self._obj_sync = False
        self.scenes, self.custom_titles = [], {}
        self.layers = {key: key not in TOC_DEFAULT_OFF for key, *_ in TOC_LAYERS}
        apply_classic_style(root)
        self.icon_size = max(16, int(round(16 * root.winfo_fpixels("1i") / 96.0)))
        self._images = {}
        root.title("Tank Battle Classic - Mission Editor")
        self._app_icons = [ImageTk.PhotoImage(im) for im in icons.app_icon_images((16, 32, 48, 64))]
        root.iconphoto(True, *self._app_icons)
        root.geometry("1600x950")
        root.minsize(1000, 650)
        self.follow_var = tk.BooleanVar(value=True)
        self.build_menus()
        self.build_ui()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.update()
        game = self.resolve_game(game_root)
        if game is None:
            root.after(10, root.destroy)
            return
        self.load_game(game)
        if scene is not None:
            self.open_scene(scene)
        else:
            self.status("Double-click a mission in the Catalog, or use File > Open mission.")

    def backup_scenario(self, battle_index):
        """Back up the custom scenario with this battle scene (after saves and
        changes), so that a game update cannot delete it for good."""
        entry = next((s for s in self.scenarios.custom if s["battle_index"] == battle_index), None)
        if entry is None:
            return
        try:
            self.backups.backup(entry)
        except Exception as e:
            self.status(f"Backing up '{entry['title']}' failed: {e}", "warn", banner=True)

    def backup_unprotected(self):
        """Back up custom scenarios that have no backup yet (made with an older
        editor version)."""
        have = {b["scene_id"] for b in self.backups.list()}
        todo = [s for s in self.scenarios.custom if s["scene_id"] not in have]
        for s in todo:
            self.busy(f"Backing up scenario '{s['title']}'...", banner=False)
            self.backup_scenario(s["battle_index"])
        if todo:
            self.idle()
            self.status(f"Backed up {len(todo)} custom scenario(s); a game update can no longer remove them for good.",
                        "ok")

    def restore_backups(self, items=None):
        """Restore backed-up scenarios that are not in the game (after a game
        update, or after removing them). items: backup metadata to restore;
        None asks which."""
        if self.ctx and not self.game_closed("restore scenarios"):
            return
        if not self.ctx:
            return
        missing = self.backups.missing()
        if items is None:
            if not missing:
                self.status("All backed-up scenarios are in the game; there is nothing to restore.", banner=True)
                return
            choices = [(f"All {len(missing)} scenario(s)", missing, "#006000")] if len(missing) > 1 else []
            choices += [(f"{b['title']:<30} saved {b.get('saved', '?').replace('T', ' ')}", [b]) for b in missing]
            items = ListDialog(self.root, "Restore scenarios", choices, prompt=(
                "These scenarios are backed up but not in the game. A restored scenario is converted to the "
                "installed game version and appears on the 'Custom Missions' page again.")).run()
            if not items:
                return
        if not self.guard_unsaved():
            return
        done, lines = [], []
        for b in items:
            self.busy(f"Restoring scenario '{b['title']}'...")
            try:
                _, battle_idx, report = self.backups.restore(b["folder"])
                done.append(b["title"])
                lines += [f"{b['title']}: {r}" for r in report if not r.startswith("Briefing updated")]
            except PermissionError:
                lines.append(f"{b['title']}: a game file is locked. Close the game and try again.")
            except Exception as e:
                lines.append(f"{b['title']}: not restored ({e}).")
        self.idle()
        self.refresh_scenes()
        msg = f"Restored {len(done)} scenario(s): {', '.join(done)}." if done else "No scenario was restored."
        if lines:
            messagebox.showinfo("Restore scenarios", msg + "\n\n" + "\n".join(lines), parent=self.root)
        self.status(msg, "ok" if done and not lines else "warn", banner=True)

    def export_scenario(self):
        """Save a custom scenario as one .tbscenario file to share it."""
        if not self.ctx:
            return
        custom = self.scenarios.custom
        if not custom:
            self.status("There are no custom scenarios to export.", banner=True)
            return
        entry = next((s for s in custom if self.model and s["battle_index"] == self.model.index), None)
        if entry is None:
            entry = ListDialog(self.root, "Export scenario", [(s["title"], s) for s in custom],
                               prompt="Choose the scenario to export.").run()
            if not entry:
                return
        if self.model and self.model.index == entry["battle_index"] and not self.guard_unsaved():
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Export scenario", initialfile=entry["scene_id"] + EXCHANGE_SUFFIX,
            defaultextension=EXCHANGE_SUFFIX, filetypes=[("Tank Battle scenario", "*" + EXCHANGE_SUFFIX)])
        if not path:
            return
        self.busy(f"Exporting '{entry['title']}'...")
        try:
            self.backups.export(entry, path, __version__)
        except Exception as e:
            self.idle()
            self.status(f"Exporting failed: {e}", "warn", banner=True)
            return
        self.idle()
        self.status(f"Exported '{entry['title']}' to {path} ({os.path.getsize(path) // 1024} KB). It holds no game "
                    "files; the recipient needs the game and this editor (Scenario > Import scenario).",
                    "ok", banner=True)

    def import_scenario(self):
        """Install a .tbscenario file made with this editor (in any game version)."""
        if not self.ctx or not self.game_closed("import a scenario"):
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Import scenario",
            filetypes=[("Tank Battle scenario", "*" + EXCHANGE_SUFFIX), ("All files", "*.*")])
        if not path or not self.guard_unsaved():
            return
        self.busy("Importing the scenario...")
        try:
            title, _, battle_idx, report = self.backups.import_file(path)
        except PermissionError:
            self.idle()
            self.status("Importing failed: a game file is locked. Close the game and try again.", "warn",
                        banner=True)
            return
        except Exception as e:
            self.idle()
            self.status(f"Importing failed: {e}", "warn", banner=True)
            return
        self.idle()
        self.refresh_scenes()
        notes = [r for r in report if not r.startswith("Briefing updated")]
        if notes:
            messagebox.showinfo("Import scenario", f"Imported '{title}'.\n\n" + "\n".join(notes), parent=self.root)
        self.open_scene(battle_idx)
        self.status(f"Imported '{title}' (level{battle_idx}). It is on the 'Custom Missions' page of the mission "
                    "select screen.", "ok", banner=True)

    def start_game(self):
        """Start Tank Battle Classic (through Steam when it is a Steam
        installation), after offering to save the open mission."""
        if not self.ctx:
            return
        if not self.guard_unsaved():
            return
        root = self.ctx.root
        if settings.game_running(root):
            self.status("The game is already running.", "warn")
            return
        app_id = settings.steam_app_id(root)
        try:
            if app_id:
                os.startfile(f"steam://rungameid/{app_id}")
            else:
                exe = next((os.path.join(root, f) for f in os.listdir(root) if f.lower().endswith(".exe")
                            and "crash" not in f.lower()), None)
                if exe is None:
                    raise FileNotFoundError("no game program in " + root)
                subprocess.Popen([exe], cwd=root)
        except Exception as e:
            self.status(f"The game could not be started: {e}", "warn", banner=True)
            return
        self.status("Starting the game. Close it before saving in the editor again.", "ok")

    def auto_rebuild_navmesh(self):
        """Before saving a custom scenario: rebuild the AI route map when the
        terrain or the solid objects changed since it was opened (or last
        rebuilt). Returns a note for the status bar, or ''."""
        old = getattr(self, "_nav_sig", None)
        if old is None:
            return ""
        new = self.navmesh_signature()
        if new is None or new == old:
            return ""
        self.busy("Terrain or objects changed: rebuilding the AI route map...")
        try:
            self.rebuild_navmesh_now()
        except Exception as e:
            return f"The AI route map could not be rebuilt ({e}); use Scenario > Rebuild AI route map."
        return "AI route map rebuilt for the changed terrain and objects."

    def navmesh_signature(self):
        """Fingerprint of the AI route map's inputs of the open custom scenario,
        or None (not a custom scenario, or no route map)."""
        m = self.model
        if not m or m.index not in self.custom_titles:
            return None
        try:
            return NavMesh(m).inputs_signature()
        except Exception:
            return None

    def rebuild_navmesh_now(self):
        """Rebuild the AI route map of the open custom scenario from its current
        terrain and objects (as one edit). Returns (areas, tiles); raises on
        failure."""
        m = self.model
        nav = NavMesh(m)
        source = None if nav.owned else (nav.file, nav.pid)
        tiles, _ = nav.build()
        m.begin("Rebuild AI route map")
        try:
            nav.write(tiles)
        finally:
            m.commit()
        if source:
            entry = next((x for x in self.scenarios.custom if x["battle_index"] == m.index), None)
            for x in self.scenarios.manifest["scenarios"]:
                if entry and x["scene_id"] == entry["scene_id"]:
                    x["navmesh_source"] = {"file": source[0], "pid": source[1],
                                           "desc": self.backups.index.describe(*source)}
            self.scenarios._save_manifest()
        self._nav_sig = nav.inputs_signature()
        self._navmesh_view = (None, None)
        self.layers_changed()
        return sum(len(t["polys"]) for t in tiles), len(tiles)

    def rebuild_navmesh(self):
        """Scenario > Rebuild AI route map. Saving also rebuilds it by itself when
        the terrain or the solid objects changed."""
        m = self.model
        if not m:
            return
        if m.index not in self.custom_titles:
            self.status("The AI route map can be rebuilt for custom scenarios only.", "warn", banner=True)
            return
        self.busy("Rebuilding the AI route map...")
        try:
            polys, ntiles = self.rebuild_navmesh_now()
        except Exception as e:
            self.idle()
            self.status(f"Rebuilding the AI route map failed: {e}", "warn", banner=True)
            return
        self.idle()
        self.update_title()
        self.status(f"AI route map rebuilt: {polys} areas in {ntiles} tiles. Save to keep it "
                    "(Table Of Contents > Terrain > AI route map shows it). Saving also rebuilds it by itself "
                    "when terrain or objects changed.", "ok", banner=True)

    def navmesh_polygons(self):
        """[(world (x, z) points, area code)] of the open scene's route map."""
        m = self.model
        try:
            nav = NavMesh(m)
        except Exception:
            return []
        key = (id(m), nav.file, nav.pid, m.edits)
        if self._navmesh_view[0] == key:
            return self._navmesh_view[1]
        polys = []
        for t in nav.data["m_NavMeshTiles"]:
            tile = decode_tile(bytes(t["m_MeshData"]))
            for p in tile["polys"]:
                polys.append(([(tile["verts"][i][0], tile["verts"][i][2]) for i in p[:p[13]]], p[14]))
        self._navmesh_view = (key, polys)
        return polys

    def report_game_update(self, update):
        """Tell the user what a game update (or file verification) changed."""
        lines = ["The game files were replaced since the editor last changed them (game update, or "
                 "Steam's 'Verify integrity of game files')."]
        if update["archive"]:
            lines.append(f"\nThe old backups belong to the previous game version and were set aside in:\n"
                         f"{update['archive']}\nThey will not be restored. New backups are made on the next save.")
        restorable = [b for b in self.backups.missing() if b["title"] in update["orphaned"]]
        if update["orphaned"]:
            lines.append("\nThese custom scenarios are no longer in the game: " + ", ".join(update["orphaned"]) + ".")
            if restorable:
                lines.append(f"{len(restorable)} of them can be restored from their backups "
                             "(you will be asked next).")
        if update["leftovers"]:
            lines.append("\nTheir leftover files (" + ", ".join(update["leftovers"]) + ") are not used by "
                         "the game any more.\n\nDelete these leftover files now?")
            if messagebox.askyesno("Game updated", "\n".join(lines), parent=self.root):
                done = self.scenarios.delete_leftovers(update["leftovers"])
                self.status(f"Deleted {len(done)} leftover file(s) of removed scenarios.", "ok")
        else:
            messagebox.showinfo("Game updated", "\n".join(lines), parent=self.root)
        self.refresh_scenes()
        if restorable and messagebox.askyesno(
                "Restore scenarios",
                "Restore these scenarios from their backups now?\n\n" + "\n".join(b["title"] for b in restorable),
                parent=self.root):
            self.restore_backups(restorable)

    def resolve_game(self, requested):
        """Game folder from the command line, the settings, the Steam libraries or the user."""
        reason = None
        for cand in (requested, settings.load_settings().get("game")):
            if cand:
                ok, msg = settings.validate_game(cand)
                if ok:
                    return os.path.normpath(cand)
                reason = f"{cand}: {msg}"
        found = settings.detect_game()
        if len(found) == 1 and reason is None:
            self.remember_game(found[0])
            return found[0]
        res = GameFolderDialog(self.root, reason=reason).run()
        if res:
            self.remember_game(res)
        return res

    @staticmethod
    def remember_game(path):
        data = settings.load_settings()
        data["game"] = path
        settings.save_settings(data)

    def select_game_folder(self):
        cur = self.ctx.root if self.ctx else None
        res = GameFolderDialog(self.root, current=cur).run()
        if not res or (cur and os.path.normcase(res) == os.path.normcase(cur)):
            return
        if not self.guard_unsaved():
            return
        self.remember_game(res)
        self.load_game(res)

    def load_game(self, game):
        self.busy(f"Loading game data from {game} (scripts, type layouts)...")
        try:
            ctx = GameContext(game)
        except Exception as e:
            self.idle()
            messagebox.showerror("Game folder", f"The game data could not be loaded:\n{e}", parent=self.root)
            return
        self.stop_library_build()
        self.ctx = ctx
        self.backup_dir = settings.backup_dir_for(game)
        if settings.migrate_legacy_backups(game, ctx.scene_names):
            self.status("Existing backups were moved to this installation's backup folder.")
        self.scenarios = ScenarioManager(ctx, self.backup_dir)
        self.backups = ScenarioBackups(ctx, self.scenarios)
        self.library = ObjectLibrary(ctx, self.backup_dir)
        update = None
        try:
            update = self.scenarios.check_game_update()
        except OSError as e:
            self.status(f"Checking the backups failed: {e}", "warn")
        adopted = []
        try:
            adopted = self.scenarios.adopt_unknown({b["scene_id"] for b in self.backups.list()})
            if not settings.game_running(ctx.root):
                self.scenarios.consolidate_pages()
        except Exception as e:
            self.status(f"Reading the custom scenarios failed: {e}", "warn")
        self.model = None
        self.selected = None
        self._base_key = None
        self.canvas.itemconfig(self._base_item, image="")
        self.refresh_scenes()
        self.refresh_objects()
        self.refresh_props()
        self.refresh_tables()
        self.update_title()
        self.request_render()
        self.idle()
        if self.auto_library and self.library.entries is None:
            self.start_library_build(game)
        if update:
            self.report_game_update(update)
        self.backup_unprotected()
        if adopted:
            self.status(f"Recognised {len(adopted)} custom scenario(s) made by another copy of the editor: "
                        + ", ".join(adopted) + ".", "ok")
        if ctx.verify_layouts:
            self.status("Game updated: " + ", ".join(ctx.affected_script_names()) + " changed or new; objects "
                        "using these scripts cannot be edited until the editor is updated. Everything else "
                        "works as before.", "warn", banner=True)
        else:
            self.status(f"Game folder: {game}", "ok")

    def image(self, name):
        if name not in self._images:
            self._images[name] = ImageTk.PhotoImage(icons.toolbar_icon(name, self.icon_size))
        return self._images[name]

    # ---- menus -------------------------------------------------------

    def build_menus(self):
        mb = tk.Menu(self.root, tearoff=0)
        self.root.config(menu=mb)

        def menu(title, entries):
            m = tk.Menu(mb, tearoff=0)
            for e in entries:
                if e is None:
                    m.add_separator()
                elif e[0] == "check":
                    m.add_checkbutton(label=e[1], variable=e[2], command=e[3] if len(e) > 3 else None)
                else:
                    icon = e[3] if len(e) > 3 else None
                    m.add_command(label=e[0], command=e[1], accelerator=e[2] if len(e) > 2 else "",
                                  image=self.image(icon) if icon else "", compound="left")
            mb.add_cascade(label=title, menu=m, underline=0)
            return m

        menu("File", [("Open mission...", self.choose_scene, "O", "open"), ("Save", self.save, "Ctrl+S", "save"),
                      ("Restore original...", self.restore_original), None,
                      ("Select game folder...", self.select_game_folder, "", "gamefolder"),
                      ("Start game", self.start_game, "F5", "play"), None,
                      ("Exit", self.quit)])
        menu("Edit", [("Undo", self.undo, "Ctrl+Z", "undo"), None,
                      ("Cut", self.cut_selection, "Ctrl+X", "cut"),
                      ("Copy", self.copy_selection, "Ctrl+C", "copy"),
                      ("Paste", self.paste, "Ctrl+V", "paste"), None,
                      ("Select all", self.select_all, "Ctrl+A"),
                      ("Duplicate", self.duplicate_selected, "Ctrl+D", "dup"),
                      ("Delete / restore", self.delete_selected, "Del", "delete"), None,
                      ("Rotate left", lambda: self.rotate_selected(-1), "Q", "rotl"),
                      ("Rotate right", lambda: self.rotate_selected(1), "E", "rotr"), None,
                      ("check", "Terrain follow", self.follow_var)])
        menu("View", [("Zoom in", lambda: self.zoom_by(1.25), "+", "zoomin"),
                      ("Zoom out", lambda: self.zoom_by(0.8), "-", "zoomout"),
                      ("Full extent", self.fit_view, "Home", "fullext"),
                      ("Zoom to selection", self.frame_selection, "F", "zoomsel")])
        menu("Selection", [("Events list...", self.pick_event, "L", "events"),
                           ("Next spawn", lambda: self.next_spawn(1), "Tab", "nextspawn"),
                           ("Select parent", self.select_parent, "P", "parent"),
                           ("Clear selection", self.clear_selection, "Esc", "clearsel")])
        menu("Insert", [("Object...", self.add_object, "", "addobj"),
                        ("Hostile tank", lambda: self.use_template(("spawn", 1)), "", "addhost"),
                        ("Friendly tank", lambda: self.use_template(("spawn", 0)), "", "addfriend"), None,
                        ("Waypoint", lambda: self.use_template(("waypoint",)), "", "addwp"),
                        ("Route", lambda: self.use_template(("route",)), "", "addroute"),
                        ("Event...", self.add_event, "", "addevent")])
        menu("Scenario", [("New scenario...", self.new_scenario, "", "newscen"),
                          ("Mission text...", self.edit_mission_text),
                          ("Update briefing screen", self.update_briefing),
                          ("Rebuild AI route map", self.rebuild_navmesh), None,
                          ("Restore scenarios from backup...", lambda: self.restore_backups()),
                          ("Export scenario...", self.export_scenario),
                          ("Import scenario...", self.import_scenario), None,
                          ("Remove scenario...", lambda: self.remove_scenario()),
                          ("Remove all custom scenarios...", self.remove_custom)])
        menu("Windows", [("Table Of Contents", lambda: self.show_page("left", "toc"), "", "toc"),
                         ("Objects", lambda: self.show_page("left", "objects"), "", "objects"),
                         ("Properties", lambda: self.show_page("right", "props"), "", "props"),
                         ("Catalog", lambda: self.show_page("right", "catalog"), "", "catalog"),
                         ("Spawns table", lambda: self.show_page("bottom", "spawns"), "", "table"),
                         ("Events table", lambda: self.show_page("bottom", "events"), "", "events"),
                         ("Waypoints table", lambda: self.show_page("bottom", "waypoints"), "", "addwp"),
                         ("Scenery table", lambda: self.show_page("bottom", "scenery"), "", "objects"),
                         ("Terrain features table", lambda: self.show_page("bottom", "features"), "", "tvertex")])
        menu("Help", [("Controls", self.show_controls), ("Open backup folder", self.open_backup_folder),
                      ("About", self.show_about)])

    def open_backup_folder(self):
        """Help > Open backup folder: the scenario backups of this installation
        (in the data folder next to the program) in Explorer."""
        folder = os.path.join(self.backup_dir, "scenarios") if getattr(self, "backup_dir", None) else None
        if not folder or not os.path.isdir(folder):
            folder = settings.BACKUP_ROOT if os.path.isdir(settings.BACKUP_ROOT) else settings.DATA_DIR
        try:
            os.startfile(folder)
        except (OSError, AttributeError) as e:
            self.status(f"The folder could not be opened: {e}. It is {folder}.", "warn", banner=True)

    def show_controls(self):
        messagebox.showinfo("Controls", (
            "Tools: one map tool is active at a time. Its toolbar button stays pressed and the status bar "
            "shows its instructions. It stays active until you choose another tool. Esc, right-click > Stop, "
            "or a second click on the pressed button returns to Select / move.\n"
            "Toolbar tools: Select / move, Pan, Zoom in (click or drag a box), Zoom out, Rotate (drag around "
            "the selection; Shift snaps to the step), the Insert tools and the Terrain tools.\n"
            "In every tool: the wheel zooms; middle or right drag, or Space held, pans.\n\n"
            "Select / move: click selects, Shift+click adds, Ctrl+click toggles, Alt+click selects a single "
            "part. Drag on empty map for a selection box; drag a selection to move it.\n\n"
            "Objects pane: click an item (tank, route, waypoint, event, trigger area or object) to choose the "
            "Add tool for it; each click on the map then places one. Dragging an item onto the map places one "
            "directly. Right-click > Add here places at the pointer.\n"
            "Routes: click the waypoints; right-click, double-click or Enter finishes the route, Backspace "
            "removes the last waypoint, Esc cancels the route. W adds a waypoint at the pointer to the "
            "selected route. Right-click a tank > Route chooses its route or starts a new one from the tank.\n"
            "Events: Insert > Event... creates a new event or copies one. An event starts on a timer, when "
            "tanks are destroyed, or when tanks enter a trigger area; it can enable or cancel other events. "
            "Trigger areas: set their size and the tanks that count in Properties.\n"
            "Terrain (custom scenarios): choose a brush on the Terrain tab (trench, tank pit, road and "
            "others, or your own), then draw with Draw terrain: click points or drag; right-click, "
            "double-click or Enter finishes, one point places a single stamp. A brush gives the same result "
            "however often it is drawn over a place. Edit terrain features selects a drawn feature to move it, "
            "move its points or delete it; Properties change its parameters. Saving rebuilds the AI route map "
            "when terrain or objects changed, so AI tanks drive around them (also Scenario > Rebuild AI route "
            "map).\n\n"
            "Table Of Contents: a check box shows or hides a layer; double-click opens its table; right-click "
            "selects, zooms or shows only that layer.\n"
            "Tables: double-click a cell (or F2 for the name) to edit it; Enter applies, Esc cancels. Insert "
            "adds a row.\n\n"
            "Keys: Ctrl+X / C / V cut, copy, paste at the pointer; Del delete / restore; Ctrl+D duplicate; "
            "Q / E rotate by the step (Shift: 1 deg); Ctrl+Z undo; Ctrl+S save; Ctrl+A select all; P select "
            "parent; F zoom to selection; Home full extent; Tab next spawn; L events list; T terrain follow; "
            "F5 start the game. Esc cancels a route or selection box in progress, else returns to Select / "
            "move, else clears the selection."),
            parent=self.root)

    def show_about(self):
        game = self.ctx.root if self.ctx else "(none)"
        messagebox.showinfo("About", (
            f"{APP_NAME} {__version__}\n{COPYRIGHT}\n\n"
            "Edits the mission scenes of an installed copy of Tank Battle Classic.\n"
            "Free software under the GNU General Public License version 3 (see LICENSE.txt);\n"
            "it comes with ABSOLUTELY NO WARRANTY.\n"
            "Not affiliated with or endorsed by the game's developer or publisher.\n\n"
            f"Game folder: {game}\n"
            f"Script layouts: {self.ctx.layout_status() if self.ctx else '-'}\n"
            f"Settings and backups: {settings.DATA_DIR}"), parent=self.root)

    # ---- layout ------------------------------------------------------

    def build_ui(self):
        root = self.root
        self.toolbar_area = ToolbarArea(root)
        self.toolbar_area.pack(side="top", fill="x")
        self.build_toolbars()
        tk.Frame(root, height=1, bg="#D9D9D9").pack(side="top", fill="x")

        sb = tk.Frame(root, bg=FACE)
        sb.pack(side="bottom", fill="x")
        tk.Frame(root, height=1, bg="#D9D9D9").pack(side="bottom", fill="x")

        def cell(width=None, anchor="w", expand=False):
            lbl = tk.Label(sb, text="", anchor=anchor, padx=6, pady=2, bg=FACE, width=width or 0)
            lbl.pack(side="left", fill="x", expand=expand)
            return lbl
        self.status_lbl = cell(expand=True)
        for attr, width, anchor in (("tool_lbl", 18, "w"), ("coord_lbl", 36, "e"), ("zoom_lbl", 14, "w")):
            tk.Frame(sb, width=1, bg="#C8C8C8").pack(side="left", fill="y", pady=3)
            setattr(self, attr, cell(width, anchor))

        self.hpane = tk.PanedWindow(root, orient="horizontal", sashwidth=4, sashrelief="flat",
                                    bd=0, bg=FACE, opaqueresize=True)
        self.hpane.pack(side="top", fill="both", expand=True, padx=2, pady=(2, 2))
        self.docks = {}
        self.docks["left"] = DockArea(self.hpane, lambda: self.hide_dock("left"))
        self.build_toc(self.docks["left"].add_page("toc", "Table Of Contents", self.image("toc")))
        self.build_objects(self.docks["left"].add_page("objects", "Objects", self.image("objects")))
        self.build_terrain_page(self.docks["left"].add_page("terrain", "Terrain",
                                                                   self.legend(("swatch", "terrain", (0, 0, 0)))))

        self.vpane = tk.PanedWindow(self.hpane, orient="vertical", sashwidth=4, sashrelief="flat",
                                    bd=0, bg=FACE, opaqueresize=True)
        self.mapframe = tk.Frame(self.vpane, bd=0, highlightthickness=1, highlightbackground=BORDER)
        self.canvas = tk.Canvas(self.mapframe, background=WORKSPACE, highlightthickness=0, bd=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        self._base_item = self.canvas.create_image(0, 0, anchor="nw")
        self.msg_bar = tk.Frame(self.mapframe, highlightthickness=1)
        self.msg_lbl = tk.Label(self.msg_bar, anchor="w", justify="left", padx=8, pady=4)
        self.msg_lbl.pack(side="left", fill="x", expand=True)
        self.msg_close = tk.Label(self.msg_bar, text="x", padx=8, cursor="hand2")
        self.msg_close.pack(side="right")
        self.msg_close.bind("<Button-1>", lambda e: self.hide_message())
        self.docks["bottom"] = DockArea(self.vpane, lambda: self.hide_dock("bottom"))
        self.build_table(self.docks["bottom"].add_page("spawns", "Table - Spawns", self.image("table")), "spawns")
        self.build_table(self.docks["bottom"].add_page("events", "Table - Events", self.image("events")), "events")
        self.build_table(self.docks["bottom"].add_page("waypoints", "Table - Waypoints", self.image("addwp")),
                         "waypoints")
        self.build_table(self.docks["bottom"].add_page("scenery", "Table - Scenery", self.image("objects")),
                         "scenery")
        self.build_table(self.docks["bottom"].add_page("features", "Table - Terrain features",
                                                       self.image("tvertex")), "features")
        self.docks["bottom"].on_select = lambda key: self.refresh_tables()

        self.docks["right"] = DockArea(self.hpane, lambda: self.hide_dock("right"))
        self.build_properties(self.docks["right"].add_page("props", "Properties", self.image("props")))
        self.build_catalog(self.docks["right"].add_page("catalog", "Catalog", self.image("catalog")))
        self.docks["right"].select("catalog")
        self.docks["left"].select("objects")

        self.vpane.add(self.mapframe, minsize=200, stretch="always")
        self.vpane.add(self.docks["bottom"], minsize=90, height=230, stretch="never")
        self.hpane.add(self.docks["left"], minsize=170, width=310, stretch="never")
        self.hpane.add(self.vpane, minsize=300, stretch="always")
        self.hpane.add(self.docks["right"], minsize=230, width=340, stretch="never")
        self.bind_canvas()
        self.set_tool("select")
        root.bind_all("<ButtonPress>", self.on_any_click, add="+")

    def on_any_click(self, e):
        """Highlight the caption of the dock the user is working in."""
        w = e.widget
        hit = None
        while w is not None and hit is None:
            for name, dock in self.docks.items():
                if w is dock:
                    hit = name
            w = getattr(w, "master", None)
        for name, dock in self.docks.items():
            dock.set_focus(name == hit)

    def hide_dock(self, name):
        pane = self.docks[name]
        owner = self.vpane if name == "bottom" else self.hpane
        if str(pane) in {str(p) for p in owner.panes()}:
            owner.forget(pane)

    def show_page(self, dock, page):
        pane = self.docks[dock]
        if dock == "bottom":
            if str(pane) not in {str(p) for p in self.vpane.panes()}:
                self.vpane.add(pane, after=self.mapframe, minsize=90, height=230, stretch="never")
        else:
            shown = {str(p) for p in self.hpane.panes()}
            if str(pane) not in shown:
                if dock == "left":
                    self.hpane.add(pane, before=self.vpane, minsize=170, width=310, stretch="never")
                else:
                    self.hpane.add(pane, after=self.vpane, minsize=230, width=340, stretch="never")
        pane.select(page)



    def build_toolbars(self):
        area = self.toolbar_area
        self.tool_buttons = {}

        def block(title, entries):
            b = ToolbarBlock(area, title)
            for e in entries:
                if e is None:
                    b.separator()
                    continue
                icon, tip, cmd = e[:3]
                toggle = len(e) > 3 and e[3]
                if toggle:
                    cmd = (lambda c=cmd, t=toggle: self.press_tool(t, c))
                btn = IconButton(b, self.image(icon), cmd, tip, toggle=toggle)
                btn.pack(side="left", padx=0, pady=1)
                if toggle:
                    self.tool_buttons[toggle] = btn
            area.add(b)
            return b

        block("Standard", [("open", "Open mission (O)", self.choose_scene), ("save", "Save (Ctrl+S)", self.save),
                           None, ("cut", "Cut (Ctrl+X)", self.cut_selection),
                           ("copy", "Copy (Ctrl+C)", self.copy_selection),
                           ("paste", "Paste (Ctrl+V)", self.paste),
                           ("delete", "Delete (Del)", self.delete_selected),
                           None, ("undo", "Undo (Ctrl+Z)", self.undo)])
        block("Tools", [("select", "Select / move", lambda: self.set_tool("select"), "select"),
                        ("clearsel", "Clear selection (Esc)", self.clear_selection),
                        ("pan", "Pan", lambda: self.set_tool("pan"), "pan"),
                        ("zoomin", "Zoom in (click or drag a box)", lambda: self.set_tool("zoomin"), "zoomin"),
                        ("zoomout", "Zoom out (click)", lambda: self.set_tool("zoomout"), "zoomout"),
                        None, ("fullext", "Full extent (Home)", self.fit_view),
                        ("zoomsel", "Zoom to selection (F)", self.frame_selection)])
        rot = block("Rotate", [("rotate", "Rotate tool: drag around the selection (Shift snaps to the step)",
                                lambda: self.set_tool("rotate"), "rotate"),
                               ("rotl", "Rotate left by the step (Q)", lambda: self.rotate_selected(-1)),
                               ("rotr", "Rotate right by the step (E)", lambda: self.rotate_selected(1))])
        rot.label("Step:")
        self.rot_step = ttk.Combobox(rot, state="readonly", width=4, values=["1", "5", "15", "45", "90"],
                                     style="Toolbar.TCombobox")
        self.rot_step.set("15")
        self.rot_step.pack(side="left", padx=(0, 4))
        ToolTip(self.rot_step, "Rotation step in degrees")
        edit = block("Edit", [("dup", "Duplicate (Ctrl+D)", self.duplicate_selected),
                              ("parent", "Select parent (P)", self.select_parent), None,
                              ("events", "Events list (L)", self.pick_event),
                              ("nextspawn", "Next spawn (Tab)", lambda: self.next_spawn(1))])
        follow = IconButton(edit, self.image("follow"), self.toggle_follow,
                            "Terrain follow: moved objects keep their height above the ground (T)")
        edit.separator()
        follow.pack(side="left", pady=1)
        self.follow_btn = follow
        self.follow_var.trace_add("write", lambda *a: self.follow_btn.set_active(self.follow_var.get()))
        self.follow_btn.set_active(True)
        block("Insert", [("addobj", "Add object: the object chosen in the Objects pane, or choose one",
                          self.tool_add_object, "add:object"),
                         ("addhost", "Add hostile tank: click on the map", lambda: self.use_template(("spawn", 1)),
                          "add:hostile"),
                         ("addfriend", "Add friendly tank: click on the map", lambda: self.use_template(("spawn", 0)),
                          "add:friendly"), None,
                         ("addwp", "Add waypoint: click on the map (W adds one at the pointer)",
                          lambda: self.use_template(("waypoint",)), "add:waypoint"),
                         ("addroute", "New route: click its waypoints on the map", lambda: self.use_template(("route",)),
                          "add:route"),
                         ("addevent", "Add event...", self.add_event), None,
                         ("newscen", "New scenario...", self.new_scenario)])
        block("Terrain", [("tdraw", "Draw terrain: draw with the brush chosen on the Terrain tab",
                           lambda: self.set_terrain_tool("feature"), "feature"),
                          ("tvertex", "Edit terrain features: select a feature, move its points or the whole "
                           "feature", lambda: self.set_terrain_tool("vertex"), "vertex")])
        block("Game", [("play", "Start the game (F5)", self.start_game)])

    def build_toc(self, body):
        holder = bordered(body)
        holder.pack(fill="both", expand=True, padx=2, pady=2)
        self.toc = ttk.Treeview(holder, show="tree", selectmode="browse")
        sb = ttk.Scrollbar(holder, command=self.toc.yview)
        self.toc.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.toc.pack(side="left", fill="both", expand=True)
        self.toc.bind("<Button-1>", self.on_toc_click)
        self.toc.bind("<Double-Button-1>", self.on_toc_double)
        self.toc.bind("<Button-3>", self.on_toc_menu)
        self.toc.bind("<space>", lambda e: (self.toggle_layer(self.toc.focus()), "break")[1])
        self.toc_images = {}
        self._toc_count_key = None
        self.build_toc_items()

    def toc_image(self, key, checked):
        k = ("cb", checked)
        if k not in self.toc_images:
            self.toc_images[k] = ImageTk.PhotoImage(icons.checkbox(checked, self.icon_size - 3))
        return self.toc_images[k]

    def toc_symbol(self, key):
        spec = next(l for l in TOC_LAYERS if l[0] == key)
        k = ("sym", key)
        if k not in self.toc_images:
            img = (icons.event_icon(1, self.icon_size) if spec[2] == "event"
                   else icons.swatch(spec[2], spec[3] or (0, 0, 0), self.icon_size))
            self.toc_images[k] = ImageTk.PhotoImage(img)
        return self.toc_images[k]

    def build_toc_items(self, title="Layers"):
        t = self.toc
        t.delete(*t.get_children())
        self.toc_images.setdefault("layers", ImageTk.PhotoImage(icons.toolbar_icon("layers", self.icon_size)))
        t.insert("", "end", iid="root", text=" " + title, image=self.toc_images["layers"], open=True)
        for key, text, kind, colour, parent in TOC_LAYERS:
            t.insert(parent or "root", "end", iid=key, text=" " + text, image=self.toc_image(key, self.layers[key]),
                     open=True)
            if kind:
                t.insert(key, "end", iid=key + ":sym", text="", image=self.toc_symbol(key))
        self._toc_count_key = None
        self.refresh_toc_counts()

    def layer_members(self, key):
        """GameObjects drawn in Table Of Contents layer `key` (deleted ones only
        for the Deleted objects layer)."""
        m = self.model
        if not m:
            return []
        if key in ("Spawns", "Player", "Friendly", "Hostile"):
            return [m.event_go[e] for e in m.spawns if m.active(m.event_go[e])
                    and (key == "Spawns" or self.spawn_side(e) == key)]
        if key == "Waypoints":
            return [m.go_of_tr[w] for w in m.waypoints if m.active(m.go_of_tr[w])]
        if key == "Events":
            return [m.event_go[e] for e in m.events if e not in m.spawns and m.active(m.event_go[e])]
        if key == "Scenery" or key in CATEGORY_COLOURS:
            roots = {m.object_root(g) for g in m.foot}
            return [g for g in roots if m.active(g) and (key == "Scenery" or category(m.name(g)) == key)]
        if key == "Deleted":
            roots = {m.object_root(g) for g in m.foot} | set(m.event_go.values())
            return [g for g in roots if g in m.go and not m.active(g)]
        return []

    def refresh_toc_counts(self, force=False):
        """Show the number of objects behind each layer, after edits only."""
        m = self.model
        key = (id(m), m.edits if m else None)
        if not hasattr(self, "toc") or (key == self._toc_count_key and not force):
            return
        self._toc_count_key = key
        counts = collections.Counter()
        if m:
            for e in m.spawns:
                if m.active(m.event_go[e]):
                    counts[self.spawn_side(e)] += 1
                    counts["Spawns"] += 1
                else:
                    counts["Deleted"] += 1
            counts["Waypoints"] = sum(1 for w in m.waypoints if m.active(m.go_of_tr[w]))
            counts["Events"] = sum(1 for e in m.events if e not in m.spawns and m.active(m.event_go[e]))
            for g in {m.object_root(g) for g in m.foot}:
                if m.active(g):
                    counts[category(m.name(g))] += 1
                    counts["Scenery"] += 1
                else:
                    counts["Deleted"] += 1
            counts["Deleted"] += sum(1 for e in m.events if e not in m.spawns and not m.active(m.event_go[e]))
        for k, text, kind, colour, parent in TOC_LAYERS:
            n = counts[k] if m and k not in ("Terrain", "Elevation", "Contours", "RouteLinks", "NavMesh") else None
            if k == "Contours" and self._contour_interval:
                text += f" ({self._contour_interval:g} m)"
            self.toc.item(k, text=f" {text}" + (f" ({n})" if n is not None else ""))

    LAYER_TABLES = {"Spawns": "spawns", "Player": "spawns", "Friendly": "spawns", "Hostile": "spawns",
                    "Waypoints": "waypoints", "Events": "events", "Scenery": "scenery", "Deleted": "spawns",
                    "Features": "features"}

    def open_layer_table(self, key):
        page = self.LAYER_TABLES.get(key) or ("scenery" if key in CATEGORY_COLOURS else None)
        if page is None:
            return
        if key == "Deleted" and not self.layer("Deleted"):
            self.toggle_layer("Deleted")
        if page == "scenery" and key in CATEGORY_COLOURS and not self.layer(key):
            self.toggle_layer(key)
        self.show_page("bottom", page)

    def select_layer(self, key):
        gos = self.layer_members(key)
        if not gos:
            self.status(f"The layer '{key}' has no objects.")
            return
        if not self.layer(key):
            self.toggle_layer(key)
        self.select_many(gos)
        self.status(f"Selected {len(gos)} object(s) of '{key}'.", "ok")

    def zoom_to_layer(self, key):
        if key == "Terrain" or key == "root":
            self.fit_view()
            return
        self.frame_gos(self.layer_members(key))

    def solo_layer(self, key):
        """Show only this layer (and the layers it contains or belongs to)."""
        keep = {key} | set(self.toc.get_children(key))
        parent = self.toc.parent(key)
        if parent and parent != "root":
            keep.add(parent)
        for k, *_ in TOC_LAYERS:
            want = k in keep or k == "Terrain"
            if self.layers[k] != want:
                self.layers[k] = want
                self.toc.item(k, image=self.toc_image(k, want))
        self.layers_changed()

    def show_all_layers(self):
        for k, *_ in TOC_LAYERS:
            want = k not in TOC_DEFAULT_OFF
            self.layers[k] = want
            self.toc.item(k, image=self.toc_image(k, want))
        self.layers_changed()

    def on_toc_double(self, e):
        item = self.toc.identify_row(e.y)
        if item and not item.endswith(":sym") and item != "root" and self.toc.identify_element(e.x, e.y) != "image":
            self.open_layer_table(item)
            return "break"
        return None

    def on_toc_menu(self, e):
        item = self.toc.identify_row(e.y)
        if item.endswith(":sym"):
            item = self.toc.parent(item)
        if not item:
            return
        self.toc.selection_set(item)
        self.toc.focus(item)
        menu = tk.Menu(self.root, tearoff=0)
        vis = tk.BooleanVar(value=self.layer(item))
        if item == "root":
            menu.add_command(label="Show all layers", command=self.show_all_layers)
            menu.add_command(label="Full extent", command=self.fit_view, compound="left", image=self.image("fullext"))
        else:
            has_table = item in self.LAYER_TABLES or item in CATEGORY_COLOURS
            menu.add_checkbutton(label="Visible", variable=vis, command=lambda: self.toggle_layer(item))
            menu.add_separator()
            menu.add_command(label="Open table", command=lambda: self.open_layer_table(item), compound="left",
                             image=self.image("table"), state="normal" if has_table else "disabled")
            menu.add_command(label="Select all objects", command=lambda: self.select_layer(item),
                             state="normal" if item != "Terrain" else "disabled")
            menu.add_command(label="Zoom to layer", command=lambda: self.zoom_to_layer(item), compound="left",
                             image=self.image("zoomsel"))
            menu.add_separator()
            menu.add_command(label="Show only this layer", command=lambda: self.solo_layer(item))
            menu.add_command(label="Show all layers", command=self.show_all_layers)
        self._toc_menu = (menu, vis)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def on_toc_click(self, e):
        item = self.toc.identify_row(e.y)
        if not item or item == "root" or item.endswith(":sym"):
            return None
        if self.toc.identify_element(e.x, e.y) != "image":
            return None
        self.toggle_layer(item)
        return "break"

    def toggle_layer(self, item):
        if not item or item not in self.layers:
            return
        new = not self.layers[item]
        self.layers[item] = new
        self.toc.item(item, image=self.toc_image(item, new))
        for c in self.toc.get_children(item):
            if c in self.layers and c not in TOC_OVERLAYS:
                self.layers[c] = new
                self.toc.item(c, image=self.toc_image(c, new))
        parent = self.toc.parent(item)
        if parent and parent != "root" and new and not self.layers[parent]:
            self.layers[parent] = True
            self.toc.item(parent, image=self.toc_image(parent, True))
        self.layers_changed()

    def build_objects(self, body):
        top = tk.Frame(body)
        top.pack(side="top", fill="x", padx=2, pady=(2, 0))
        self.obj_top = top
        tk.Label(top, text="Filter:").pack(side="left")
        self.obj_filter = tk.StringVar()
        ttk.Entry(top, textvariable=self.obj_filter).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.obj_filter.trace_add("write", lambda *a: self.refresh_objects())
        bottom = tk.Frame(body)
        bottom.pack(side="bottom", fill="x", padx=2, pady=2)
        self.obj_build_btn = ttk.Button(bottom, text="Build object library", command=self.build_library)
        self.obj_place_btn = ttk.Button(bottom, text="Place at map centre", command=self.place_selected_object)
        self.obj_place_btn.pack(side="left")
        self.obj_hint = tk.Label(body, text=OBJ_HINT, anchor="w", justify="left", fg="#404040", wraplength=250)
        self.obj_hint.pack(side="bottom", fill="x", padx=4)
        holder = bordered(body)
        holder.pack(fill="both", expand=True, padx=2, pady=2)
        self.obj_tree = ttk.Treeview(holder, columns=("size", "source"), show="tree headings", selectmode="browse")
        self.obj_tree.heading("#0", text="Object", anchor="w")
        self.obj_tree.heading("size", text="Size (m)", anchor="w")
        self.obj_tree.heading("source", text="From mission", anchor="w")
        self.obj_tree.column("#0", width=170, stretch=True)
        self.obj_tree.column("size", width=55, stretch=False)
        self.obj_tree.column("source", width=70, stretch=True)
        sb = ttk.Scrollbar(holder, command=self.obj_tree.yview)
        self.obj_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.obj_tree.pack(side="left", fill="both", expand=True)
        self.obj_tree.bind("<<TreeviewSelect>>", lambda e: self.on_obj_select(), add="+")
        self.obj_tree.bind("<ButtonPress-1>", self.on_obj_press, add="+")
        self.obj_tree.bind("<B1-Motion>", self.on_obj_motion)
        self.obj_tree.bind("<ButtonRelease-1>", self.on_obj_release, add="+")
        self.obj_entries = {}
        self.obj_templates = {}

    def build_terrain_page(self, body):
        """Terrain tab: the brushes, the parameters of the chosen brush and the
        terrain features of the open scenario."""
        tk.Label(body, text="Brushes", font=UI_BOLD, anchor="w").pack(side="top", fill="x", padx=4, pady=(4, 0))
        holder = bordered(body)
        holder.pack(side="top", fill="x", padx=2, pady=2)
        self.brush_tree = ttk.Treeview(holder, show="tree", selectmode="browse", height=8)
        sb = ttk.Scrollbar(holder, command=self.brush_tree.yview)
        self.brush_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.brush_tree.pack(side="left", fill="both", expand=True)
        self.brush_tree.bind("<<TreeviewSelect>>", lambda e: self.on_brush_select())
        form = tk.Frame(body)
        form.pack(side="top", fill="x", padx=4, pady=2)
        self.brush_widgets, self.brush_vars = {}, {}
        tk.Label(form, text="Kind:", anchor="w").grid(row=0, column=0, sticky="w")
        cb = ttk.Combobox(form, state="readonly", width=18, values=[KIND_NAMES[k] for k in KINDS])
        cb.grid(row=0, column=1, columnspan=2, sticky="w", padx=4, pady=1)
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_brush_param())
        self.brush_widgets["kind"] = cb
        for r, (key, text, unit, tip) in enumerate(BRUSH_PARAMS, start=1):
            tk.Label(form, text=text + ":", anchor="w").grid(row=r, column=0, sticky="w")
            var = tk.StringVar()
            ent = ttk.Entry(form, textvariable=var, width=8)
            ent.grid(row=r, column=1, sticky="w", padx=4, pady=1)
            ent.bind("<Return>", lambda e: self.on_brush_param())
            ent.bind("<FocusOut>", lambda e: self.on_brush_param())
            ToolTip(ent, tip)
            tk.Label(form, text=unit, anchor="w").grid(row=r, column=2, sticky="w")
            self.brush_vars[key], self.brush_widgets[key] = var, ent
        r = len(BRUSH_PARAMS) + 1
        tk.Label(form, text="Texture:", anchor="w").grid(row=r, column=0, sticky="w")
        tex = ttk.Combobox(form, state="readonly", width=18, values=["(none)"])
        tex.grid(row=r, column=1, columnspan=2, sticky="w", padx=4, pady=1)
        tex.bind("<<ComboboxSelected>>", lambda e: self.on_brush_param())
        ToolTip(tex, "Ground texture painted under the brush")
        self.brush_widgets["texture"] = tex
        form.columnconfigure(2, weight=1)
        bar = tk.Frame(body)
        bar.pack(side="top", fill="x", padx=4, pady=(2, 4))
        ttk.Button(bar, text="Save as new brush...", command=self.save_brush_as).pack(side="left")
        self.brush_del_btn = ttk.Button(bar, text="Delete brush", command=self.delete_brush)
        self.brush_del_btn.pack(side="left", padx=(4, 0))
        self._feat_sync = self._brush_sync = False
        self.feature_sel = None
        self._vertex_click = None
        self._tex_values = [""]
        self.load_brushes()

    # ---- brushes ---------------------------------------------------------

    def load_brushes(self):
        """The built-in brushes and the user's own (settings.json "terrain_brushes")."""
        own = []
        for b in settings.load_settings().get("terrain_brushes", []):
            try:
                own.append(clean_brush(b))
            except (ValueError, TypeError, AttributeError):
                continue
        self.own_brushes = own
        self.builtin_brushes = default_brushes()
        self.brush = dict(self.builtin_brushes[0])
        self.brush_iid = "b0"
        self.fill_brush_list()

    def store_brushes(self):
        data = settings.load_settings()
        data["terrain_brushes"] = self.own_brushes
        settings.save_settings(data)

    def fill_brush_list(self):
        t = self.brush_tree
        t.delete(*t.get_children())
        for i, b in enumerate(self.builtin_brushes):
            t.insert("", "end", iid=f"b{i}", text=" " + b["name"])
        for i, b in enumerate(self.own_brushes):
            t.insert("", "end", iid=f"o{i}", text=f" {b['name']}  (own)")
        if t.exists(self.brush_iid):
            self._brush_sync = True
            try:
                t.selection_set(self.brush_iid)
                t.see(self.brush_iid)
                self.root.update_idletasks()
            finally:
                self._brush_sync = False
        self.show_brush()

    def on_brush_select(self):
        """Choosing a brush chooses the Draw terrain tool (as a template in
        ArcGIS Create Features)."""
        if self._brush_sync:
            return
        sel = self.brush_tree.selection()
        if not sel:
            return
        iid = sel[0]
        src = self.builtin_brushes if iid[0] == "b" else self.own_brushes
        self.brush = dict(src[int(iid[1:])])
        self.brush_iid = iid
        if iid[0] == "b":
            self.brush_tree.item(iid, text=" " + self.brush["name"])
        self.show_brush()
        if self.model and self.can_paint():
            self.set_tool("feature")

    def layer_names(self):
        m = self.model
        return list(m.terrain.layer_names) if m and m.terrain is not None and m.terrain.layer_names else []

    def show_brush(self):
        """Show the chosen brush's parameters on the Terrain tab."""
        b, w = self.brush, self.brush_widgets
        w["kind"].current(KINDS.index(b["kind"]))
        used = KIND_PARAMS[b["kind"]]
        for key, *_ in BRUSH_PARAMS:
            v = b.get(key)
            self.brush_vars[key].set("" if v is None else f"{v:g}")
            w[key].config(state="normal" if key in used else "disabled")
        names = self.layer_names()
        if names:
            self._tex_values = [""] + [layer_label(n) for n in names]
            idx = find_layer(names, b["texture"])
            cur = 0 if idx is None else idx + 1
        else:
            self._tex_values = [""] + ([b["texture"]] if b["texture"] else [])
            cur = len(self._tex_values) - 1
        w["texture"].config(values=["(none)"] + self._tex_values[1:])
        w["texture"].current(cur)
        w["texture"].config(state="readonly" if "texture" in used else "disabled")
        self.brush_del_btn.config(state="normal" if self.brush_iid.startswith("o") else "disabled")
        if self.tool == "feature" and self.sketch is None:
            self.status(self.tool_hint())

    def on_brush_param(self):
        """A parameter of the chosen brush changed on the Terrain tab. The
        user's own brushes keep the change; a built-in brush keeps it until
        another brush is chosen."""
        b, w = dict(self.brush), self.brush_widgets
        b["kind"] = KINDS[max(0, w["kind"].current())]
        text = ""
        try:
            for key, *_ in BRUSH_PARAMS:
                text = self.brush_vars[key].get().strip().replace(",", ".")
                if key == "level":
                    b[key] = float(text) if text else None
                elif text:
                    b[key] = float(text)
            i = max(0, w["texture"].current())
            b["texture"] = self._tex_values[i] if i < len(self._tex_values) else ""
            new = clean_brush(b)
        except ValueError:
            self.status(f"Brush: '{text}' is not a number.", "warn")
            self.show_brush()
            return
        if new == self.brush:
            return
        self.brush = new
        if self.brush_iid.startswith("o"):
            self.own_brushes[int(self.brush_iid[1:])] = dict(new)
            self.store_brushes()
        else:
            self.brush_tree.item(self.brush_iid, text=f" {new['name']}  (changed)")
        self.show_brush()

    def save_brush_as(self):
        name = NameDialog(self.root, "Save brush", "Name of the new brush:", self.brush.get("name", "")).run()
        if not name:
            return
        taken = {b["name"].lower() for b in self.own_brushes + self.builtin_brushes}
        base, n = name, 1
        while name.lower() in taken:
            n += 1
            name = f"{base} ({n})"
        self.own_brushes.append(dict(self.brush, name=name))
        self.store_brushes()
        self.brush = dict(self.own_brushes[-1])
        self.brush_iid = f"o{len(self.own_brushes) - 1}"
        self.fill_brush_list()
        self.status(f"Brush '{name}' saved.", "ok")

    def delete_brush(self):
        if not self.brush_iid.startswith("o"):
            return
        i = int(self.brush_iid[1:])
        name = self.own_brushes[i]["name"]
        if not messagebox.askyesno("Delete brush", f"Delete the brush '{name}'?", parent=self.root):
            return
        del self.own_brushes[i]
        self.store_brushes()
        self.brush, self.brush_iid = dict(self.builtin_brushes[0]), "b0"
        self.fill_brush_list()
        self.status(f"Brush '{name}' deleted.")

    @staticmethod
    def brush_colour(b):
        if b["kind"] == "shape":
            return FEATURE_COLOURS["raise" if b["height"] > 0 else "cut"]
        return FEATURE_COLOURS[b["kind"]]

    def ai_note(self, b):
        """Status text when a brush makes ground steeper than the AI can drive."""
        steep = b["kind"] in ("shape", "level", "smooth") and b["slope"] > AI_MAX_SLOPE and \
            (b["kind"] != "shape" or abs(b["height"]) > 0.3)
        if not steep:
            return ""
        note = f" Its sides ({b['slope']:g} deg) are steeper than the AI can drive ({AI_MAX_SLOPE:g} deg)"
        if b["kind"] == "shape" and b["height"] < 0 and b["crossings"] > 0:
            return note + f"; AI crossings every {b['crossings']:g} m."
        return note + "; AI tanks drive around it."

    # ---- terrain features ------------------------------------------------

    def can_paint(self):
        m = self.model
        return bool(m and m.terrain is not None and m.terrain.layer_colours and m.index in self.custom_titles)

    def refresh_terrain_page(self):
        self.show_brush()
        self.refresh_feature_list()
        if not self.can_paint() and self.tool in TERRAIN_TOOLS:
            self.set_tool("select")

    def set_terrain_tool(self, tool):
        if not self.can_paint():
            self.status("Terrain tools work on custom scenarios only (Scenario > New scenario).", "warn",
                        banner=True)
            return
        self.set_tool(tool)
        self.show_page("left", "terrain")

    def scenario_entry(self):
        """Scenario list entry of the open custom scenario, or None."""
        m = self.model
        if not m or not getattr(self, "scenarios", None):
            return None
        return next((s for s in self.scenarios.custom if s["battle_index"] == m.index), None)

    def terrain_session(self):
        """The open scenario's TerrainPaint (made on first use)."""
        m = self.model
        if getattr(m, "terrain_paint", None) is None:
            m.terrain_paint = TerrainPaint(m)
        return m.terrain_paint

    def features(self, create=False):
        """The open scenario's TerrainFeatures; create: start them on the
        present ground when there are none yet."""
        m = self.model
        f = getattr(m, "terrain_features", None) if m else None
        if f is None and create:
            tp = self.terrain_session()
            t = m.terrain
            f = TerrainFeatures(tp.heights.copy(), [a.copy() for a in tp.alphas],
                                {"origin": [float(v) for v in t.origin], "size": [t.size_x, t.size_z],
                                 "height_scale": tp.height_scale})
            m.terrain_features = f
        return f

    def load_terrain_features(self):
        """Read the stored terrain features of the opened custom scenario."""
        m = self.model
        m.terrain_features = None
        entry = self.scenario_entry()
        if entry is None or not self.can_paint() or getattr(self, "backups", None) is None:
            return
        folder = self.backups.folder(entry["scene_id"])
        if not os.path.exists(os.path.join(folder, FEATURES_FILE)):
            return
        try:
            tp = self.terrain_session()
            f, stale = TerrainFeatures.load(folder, tp.heights, tp.alphas)
        except Exception as ex:
            self.status(f"The terrain features of this scenario could not be loaded: {ex}", "warn", banner=True)
            return
        if stale:
            TerrainFeatures.discard(folder)
            self.status(f"The stored terrain features of this scenario were discarded: {stale}. The present "
                        "ground is the starting point for new features.", "warn", banner=True)
        m.terrain_features = f

    def save_terrain_features(self):
        """Store the terrain features with the saved scenario."""
        m = self.model
        f = getattr(m, "terrain_features", None)
        entry = self.scenario_entry()
        if f is None or not f.dirty or entry is None:
            return
        tp = m.terrain_paint
        f.save(self.backups.folder(entry["scene_id"]), tp.heights, tp.alphas)

    def own_terrain(self, tp):
        """Inside begin/commit: give the scenario its own terrain copy; the first
        copy is recorded in the scenario list."""
        made = tp.ensure_copy()
        entry = self.scenario_entry() if made else None
        if entry is not None:
            for s in self.scenarios.manifest["scenarios"]:
                if s["scene_id"] == entry["scene_id"]:
                    s["terrain_source"] = {"file": made[0], "pid": made[1],
                                           "desc": self.backups.index.describe(made[0], made[1])}
            self.scenarios._save_manifest()

    def edit_features(self, label, strokes, changed):
        """Replace the scenario's strokes by `strokes` and recompute the ground
        that the `changed` strokes (old and new versions) cover, as one undo
        step. Objects on the changed ground move with it."""
        m = self.model
        try:
            tp = self.terrain_session()
            f = self.features(create=True)
        except Exception as ex:
            self.status(f"This terrain cannot be edited: {ex}", "warn", banner=True)
            return False
        old = list(f.strokes)
        before = tp.heights.copy()
        m.begin(label)
        try:
            self.own_terrain(tp)
        except Exception as ex:
            m.commit()
            self.status(f"The terrain could not be copied for this scenario: {ex}", "warn", banner=True)
            return False
        f.strokes = list(strokes)
        f.dirty = True
        self.busy("Updating the ground...")
        try:
            reg = self.recompute_features(changed)
            moved = self.reseat_objects(before, reg[0]) if reg else 0
        finally:
            self.idle()

        def undo():
            f.strokes = old
            f.dirty = True
            self.recompute_features(changed)
            tp.resync()
        m.commit(undo_fn=undo)
        if moved:
            self.changed()
            self.status(f"{moved} object(s) moved with the ground.", "ok")
        else:
            self.update_title()
            self.refresh_feature_list()
            self.refresh_props()
            self.request_render()
        return True

    def recompute_features(self, changed):
        m = self.model
        tp, f = m.terrain_paint, m.terrain_features
        reg = f.apply(tp.heights, tp.alphas, changed)
        if reg:
            tp.dirty = tp.heights_dirty = True
            (hr, hc), (ar, ac) = reg
            self.show_relief(hr, hc)
            if ar[0] < ar[1] and ac[0] < ac[1]:
                self.show_painted(ar, ac)
        return reg

    def feature_name(self, s):
        return s.get("brush") or KIND_NAMES[s["kind"]]

    def feature_row(self, s):
        """Values of a terrain feature's row in Table - Terrain features."""
        used = KIND_PARAMS[s["kind"]]

        def num(key):
            v = s.get(key)
            return "" if key not in used or v is None else f"{v:g}"
        names = self.layer_names()
        tex = s.get("texture")
        texture = layer_label(names[tex]) if tex is not None and tex < len(names) else ""
        n = len(s["points"])
        return (s["id"], s.get("brush", ""), KIND_NAMES[s["kind"]], num("width"), num("height"), num("slope"),
                num("rim"), num("crossings"), num("level"), texture, n, f"{length(s['points']):.0f}" if n > 1 else "")

    def refresh_feature_list(self):
        """Fill Table - Terrain features (only when the features changed)."""
        t = getattr(self, "tables", {}).get("features")
        if t is None:
            return
        f = self.features()
        key = (id(f), tuple(repr(sorted(s.items())) for s in f.strokes)) if f is not None else None
        if key == getattr(self, "_feat_list_key", None) and key is not None:
            if self.feature_sel is not None and f.stroke(self.feature_sel) is None:
                self.feature_sel = None
            self.sync_feature_list()
            return
        self._feat_list_key = key
        t.delete(*t.get_children())
        if f is not None:
            for s in f.strokes:
                t.insert("", "end", iid=f"f{s['id']}", values=self.feature_row(s))
        if self.feature_sel is not None and (f is None or f.stroke(self.feature_sel) is None):
            self.feature_sel = None
        self.sync_feature_list()

    def sync_feature_list(self):
        t = getattr(self, "tables", {}).get("features")
        if t is None:
            return
        iid = f"f{self.feature_sel}"
        want = [iid] if self.feature_sel is not None and t.exists(iid) else []
        if list(t.selection()) == want:
            return
        self._feat_sync = True
        try:
            t.selection_set(want)
            if want:
                t.see(want[0])
            self.root.update_idletasks()
        finally:
            self._feat_sync = False

    def on_feature_list_select(self):
        if self._feat_sync:
            return
        sel = self.tables["features"].selection()
        self.select_feature(int(sel[0][1:]) if sel else None)

    def feature_cell_spec(self, sid, key):
        """Cell editing in Table - Terrain features, as cell_spec()."""
        f = self.features()
        s = f.stroke(sid) if f is not None else None
        if s is None:
            return None
        if key == "brush":
            def rename(v):
                cur = f.stroke(sid)
                self.replace_feature(cur, dict(cur, brush=v.strip()), "Rename terrain feature")
            return None, s.get("brush", ""), rename
        if key == "kind":
            return ([(KIND_NAMES[k], k) for k in KINDS], s["kind"],
                    lambda v: self.set_feature_param(sid, "kind", v))
        if key == "texture":
            names = self.layer_names()
            return ([("(none)", None)] + [(layer_label(n), i) for i, n in enumerate(names)], s.get("texture"),
                    lambda v: self.set_feature_param(sid, "texture", v))
        if key in ("width", "height", "slope", "rim", "crossings", "level") and key in KIND_PARAMS[s["kind"]]:
            v = s.get(key)
            return None, "" if v is None else f"{v:g}", lambda val, k=key: self.set_feature_param(sid, k, val)
        return None

    def select_feature(self, sid):
        """Select terrain feature `sid` (None: none); objects are deselected."""
        self.feature_sel = sid
        if sid is not None and self.sel:
            self.sel = []
            self.sync_table_selection()
        self.sync_feature_list()
        self.refresh_props()
        self.request_render()

    def clear_feature_choice(self):
        if self.feature_sel is not None:
            self.feature_sel = None
            self.sync_feature_list()

    def frame_feature(self, sid):
        f = self.features()
        s = f.stroke(sid) if f is not None and sid is not None else None
        if s is None:
            return
        pts = np.asarray(s["points"], dtype=float)
        pad = s["width"] + 20.0
        x0, z0 = pts.min(0) - pad
        x1, z1 = pts.max(0) + pad
        v = self.view
        v.cx, v.cz = (x0 + x1) / 2, (z0 + z1) / 2
        v.scale = max(0.05, min(40.0, min(v.w / max(1.0, x1 - x0), v.h / max(1.0, z1 - z0)) * 0.9))
        self.request_render()

    # ---- drawing a terrain feature --------------------------------------

    def start_terrain_sketch(self, e):
        """Draw terrain tool: the first point of a new stroke. Clicks add
        points, dragging draws freehand; it ends like a route (right-click,
        double-click or Enter)."""
        if not self.can_paint():
            self.status("Terrain tools work on custom scenarios only (Scenario > New scenario).", "warn",
                        banner=True)
            return
        self.clear_feature_choice()
        self.sketch = {"kind": "terrain", "points": [self.view.s2w(e.x, e.y)], "brush": dict(self.brush),
                       "last": (e.x, e.y, getattr(e, "time", 0))}
        self.drag = {"mode": "stroke"}
        self.canvas.config(cursor=self.cursor())
        self.status(self.sketch_hint())
        self.draw_rubber()

    def terrain_click(self, e):
        s = self.sketch
        now = getattr(e, "time", 0)
        last = s.get("last")
        if last and 0 <= now - last[2] < DOUBLE_CLICK_MS and abs(e.x - last[0]) < 6 and abs(e.y - last[1]) < 6:
            self.finish_sketch()
            return
        s["last"] = (e.x, e.y, now)
        s["points"].append(self.view.s2w(e.x, e.y))
        self.drag = {"mode": "stroke"}
        self.status(self.sketch_hint())
        self.draw_rubber()

    def stroke_motion(self, e):
        """Button held while drawing: a point every FREEHAND_PX pixels."""
        s = self.sketch
        self.mouse = (e.x, e.y)
        if s is None or s.get("kind") != "terrain":
            return
        x, z = self.view.s2w(e.x, e.y)
        lx, lz = s["points"][-1]
        if math.hypot(x - lx, z - lz) * self.view.scale >= FREEHAND_PX:
            s["points"].append((x, z))
            s["last"] = None
            self.status(self.sketch_hint())
        self.draw_rubber()

    def add_terrain_stroke(self, s):
        pts = []
        for p in s["points"]:
            if not pts or math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1]) > 0.05:
                pts.append(p)
        b = s["brush"]
        try:
            f = self.features(create=True)
        except Exception as ex:
            self.status(f"This terrain cannot be edited: {ex}", "warn", banner=True)
            return
        st = f.make_stroke(b, pts, find_layer(self.layer_names(), b["texture"]))
        name = self.feature_name(st)
        if self.edit_features(f"Draw {name}", f.strokes + [st], [st]):
            what = f"{length(pts):.0f} m" if len(pts) > 1 else "one point"
            self.status(f"Terrain feature #{st['id']} drawn: {name}, {what}." + self.ai_note(st), "ok")

    def draw_stroke_shape(self, pts, b, col, chosen, tag):
        """A stroke on the map: its width (dashed outline) and centre line, or a
        circle for one point; chosen: thicker, with its points as handles.
        Zoomed out, an unchosen stroke is one canvas item (a plain line or
        circle), so many features stay quick to draw."""
        c = self.canvas
        wpx = b["width"] * self.view.scale
        detail = chosen or wpx >= 6
        if len(pts) > 1:
            flat = [q for p in pts for q in p]
            if detail:
                c.create_polygon(*outline_polygon(pts, max(1.0, wpx / 2)), fill="", outline=col, dash=(4, 3),
                                 tags=tag)
                c.create_line(*flat, fill="#000000", width=4 if chosen else 3, tags=tag)
            c.create_line(*flat, fill=col, width=2 if chosen or not detail else 1, tags=tag)
        elif pts:
            x, y = pts[0]
            r = max(3.0, wpx / 2)
            if detail:
                c.create_oval(x - r, y - r, x + r, y + r, outline="#000000", width=4 if chosen else 3, tags=tag)
            c.create_oval(x - r, y - r, x + r, y + r, outline=col, width=2 if chosen or not detail else 1, tags=tag)
        if chosen:
            for x, y in pts:
                c.create_rectangle(x - 4, y - 4, x + 4, y + 4, fill="#FFFFFF", outline="#000000", tags=tag)

    def draw_features(self):
        f = self.features()
        if f is None or not self.layer("Terrain") or not self.layer("Features"):
            return
        v = self.view
        x0, z1 = v.s2w(0, 0)
        x1, z0 = v.s2w(v.w, v.h)
        for s in f.strokes:
            b = feature_bbox(s)
            if b[0] > x1 or b[2] < x0 or b[1] > z1 or b[3] < z0:
                continue
            pts = [v.w2s(x, z) for x, z in s["points"]]
            self.draw_stroke_shape(pts, s, self.brush_colour(s), s["id"] == self.feature_sel, "overlay")

    # ---- editing terrain features -----------------------------------------

    def feature_hit(self, sx, sy):
        """Id of the terrain feature under a screen point (the last drawn first)."""
        f = self.features()
        if f is None or not self.layer("Terrain") or not self.layer("Features"):
            return None
        p = self.view.s2w(sx, sy)
        tol = 6.0 / max(self.view.scale, 1e-6)
        for s in reversed(f.strokes):
            reach = max(s["width"] / 2, tol)
            b = feature_bbox(s, reach)
            if not (b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]):
                continue
            if line_distance(p, s["points"])[0] <= reach:
                return s["id"]
        return None

    def feature_vertex_at(self, s, sx, sy):
        best = None
        for i, (x, z) in enumerate(s["points"]):
            px, py = self.view.w2s(x, z)
            d = math.hypot(px - sx, py - sy)
            if d <= 7 and (best is None or d < best[0]):
                best = (d, i)
        return best[1] if best else None

    def vertex_press(self, e):
        """Edit terrain features tool: select a feature, drag a point or the
        whole feature; a double-click on its line adds a point."""
        f = self.features()
        now = getattr(e, "time", 0)
        last, self._vertex_click = self._vertex_click, (e.x, e.y, now)
        s = f.stroke(self.feature_sel) if f is not None and self.feature_sel is not None else None
        if s is not None:
            i = self.feature_vertex_at(s, e.x, e.y)
            if i is not None:
                self.drag = {"mode": "fvertex", "id": s["id"], "index": i, "start": (e.x, e.y),
                             "points": [list(p) for p in s["points"]], "moved": False}
                return
            double = last and 0 <= now - last[2] < DOUBLE_CLICK_MS and abs(e.x - last[0]) < 6 and \
                abs(e.y - last[1]) < 6
            if double and self.feature_hit(e.x, e.y) == s["id"]:
                self._vertex_click = None
                self.insert_feature_point(s["id"], self.view.s2w(e.x, e.y))
                return
        hit = self.feature_hit(e.x, e.y)
        if hit != self.feature_sel:
            self.select_feature(hit)
        if hit is not None:
            pts = [list(p) for p in f.stroke(hit)["points"]]
            self.drag = {"mode": "fmove", "id": hit, "start": (e.x, e.y), "origin": self.view.s2w(e.x, e.y),
                         "base": pts, "points": [list(p) for p in pts], "moved": False}

    def feature_drag_motion(self, e):
        d = self.drag
        if not d["moved"] and math.hypot(e.x - d["start"][0], e.y - d["start"][1]) < 4:
            return
        d["moved"] = True
        x, z = self.view.s2w(e.x, e.y)
        if d["mode"] == "fvertex":
            d["points"][d["index"]] = [x, z]
        else:
            dx, dz = x - d["origin"][0], z - d["origin"][1]
            d["points"] = [[px + dx, pz + dz] for px, pz in d["base"]]
        s = self.features().stroke(d["id"])
        self.canvas.delete("rubber")
        if s is not None:
            pts = [self.view.w2s(px, pz) for px, pz in d["points"]]
            self.draw_stroke_shape(pts, s, self.brush_colour(s), True, "rubber")

    def feature_drag_release(self):
        d, self.drag = self.drag, None
        self.canvas.delete("rubber")
        f = self.features()
        s = f.stroke(d["id"]) if f is not None else None
        if not d["moved"] or s is None:
            return
        label = "Move terrain feature point" if d["mode"] == "fvertex" else "Move terrain feature"
        self.replace_feature(s, dict(s, points=d["points"]), label)

    def replace_feature(self, s, new, label):
        f = self.features()
        return self.edit_features(label, [new if x["id"] == s["id"] else x for x in f.strokes], [s, new])

    def insert_feature_point(self, sid, at):
        s = self.features().stroke(sid)
        pts = [list(p) for p in s["points"]]
        if len(pts) == 1:
            pts.append(list(at))
        else:
            _, i, q = line_distance(at, pts)
            pts.insert(i + 1, list(q))
        self.replace_feature(s, dict(s, points=pts), "Add terrain feature point")

    def remove_feature_point(self, sid, index):
        s = self.features().stroke(sid)
        if s is None or len(s["points"]) < 2:
            return
        pts = [list(p) for k, p in enumerate(s["points"]) if k != index]
        self.replace_feature(s, dict(s, points=pts), "Remove terrain feature point")

    def delete_feature(self):
        f = self.features()
        s = f.stroke(self.feature_sel) if f is not None and self.feature_sel is not None else None
        if s is None:
            return
        name = self.feature_name(s)
        if self.edit_features("Delete terrain feature", [x for x in f.strokes if x["id"] != s["id"]], [s]):
            self.feature_sel = None
            self.refresh_feature_list()
            self.refresh_props()
            self.status(f"Terrain feature #{s['id']} ({name}) deleted.", "ok")

    def set_feature_param(self, sid, key, value):
        """Properties pane: change one parameter of a terrain feature."""
        f = self.features()
        s = f.stroke(sid)
        new = dict(s)
        if key == "kind":
            new["kind"] = value
            if value == "level" and new.get("level") is None:
                new["level"] = round(f.ground_height(*s["points"][0]), 2)
        elif key == "texture":
            new["texture"] = value
        else:
            text = str(value).strip().replace(",", ".")
            new[key] = float(text)
        checked = clean_brush(new)
        for k in ("kind", "width", "height", "slope", "rim", "crossings"):
            new[k] = checked[k]
        if new.get("level") is not None:
            new["level"] = checked["level"]
        if new != s:
            self.replace_feature(s, new, "Change terrain feature")

    def feature_props(self, s):
        """Properties of a terrain feature."""
        tk.Label(self.props, text="TERRAIN FEATURE", font=UI_BOLD, anchor="w", padx=2).grid(
            row=self._next(), column=0, columnspan=2, sticky="ew")
        self.prop_sep()
        sid = s["id"]
        self.prop_info("Feature", f"#{sid} {self.feature_name(s)}")
        n = len(s["points"])
        self.prop_info("Points", f"{n}, {length(s['points']):.0f} m long" if n > 1 else "1 (a single stamp)")
        self.prop_combo("Kind", [KIND_NAMES[k] for k in KINDS], KINDS.index(s["kind"]),
                        lambda i: self.set_feature_param(sid, "kind", KINDS[i]))
        for key, text, unit, _ in BRUSH_PARAMS:
            if key not in KIND_PARAMS[s["kind"]]:
                continue
            v = s.get(key)
            self.prop_entry(f"{text} ({unit})", "" if v is None else f"{v:g}",
                            lambda val, k=key: self.set_feature_param(sid, k, val))
        names = self.layer_names()
        tex = s.get("texture")
        self.prop_combo("Texture", ["(none)"] + [layer_label(x) for x in names],
                        0 if tex is None or tex >= len(names) else tex + 1,
                        lambda i: self.set_feature_param(sid, "texture", None if i == 0 else i - 1))
        note = self.ai_note(s).strip()
        if note:
            self.prop_info("AI", note)
        r = self._next()
        ttk.Button(self.props, text="Delete feature", command=self.delete_feature).grid(
            row=r, column=1, sticky="w", pady=(6, 0))

    def show_relief(self, rows, cols):
        """Redraw the map after a height change in heightmap rows and cols."""
        tp = self.model.terrain_paint
        done = self.model.terrain.update_relief(tp.heights, rows, cols, tp.alphas)
        self.paste_terrain(done)

    def show_painted(self, rows, cols):
        """Redraw the painted part of the terrain image."""
        t = self.model.terrain
        tp = self.model.terrain_paint
        done = t.recolour(tp.alphas, rows, cols)
        self.paste_terrain(done)

    def paste_terrain(self, done):
        t = self.model.terrain
        if self._terrain_img is not None and done:
            n = t.image_n
            (r0, r1), (c0, c1) = done
            if r0 < r1 and c0 < c1:
                part = np.clip(t.image[n - r1:n - r0, c0:c1].astype(np.int32) * 5 // 4, 0, 255).astype(np.uint8)
                self._terrain_img.paste(Image.fromarray(part), (c0, n - r1))
        self._terrain_rev += 1
        self._base_key = None

    def reseat_objects(self, before, box):
        """Keep tanks, waypoints and scenery on the ground after a height stroke:
        each object in the edited area moves up or down by the ground change
        under it. Inside the stroke's undo step. Returns the number moved."""
        m = self.model
        t = m.terrain
        tp = m.terrain_paint
        res = before.shape[0]
        (r0, r1), (c0, c1) = box
        x0 = t.origin[0] + c0 / (res - 1) * t.size_x
        x1 = t.origin[0] + (c1 - 1) / (res - 1) * t.size_x
        z0 = t.origin[2] + r0 / (res - 1) * t.size_z
        z1 = t.origin[2] + (r1 - 1) / (res - 1) * t.size_z

        def old_ground(x, z):
            u = (x - t.origin[0]) / t.size_x * (res - 1)
            v = (z - t.origin[2]) / t.size_z * (res - 1)
            i0, j0 = int(min(max(v, 0), res - 1)), int(min(max(u, 0), res - 1))
            i1, j1 = min(i0 + 1, res - 1), min(j0 + 1, res - 1)
            fv, fu = v - i0, u - j0
            h = (before[i0, j0] * (1 - fu) + before[i0, j1] * fu) * (1 - fv) + \
                (before[i1, j0] * (1 - fu) + before[i1, j1] * fu) * fv
            return float(h) / 32766.0 * t.height_scale + t.origin[1]
        gos = {m.event_go[e] for e in m.spawns} | {m.go_of_tr[w] for w in m.waypoints}
        gos |= {m.object_root(g) for g in m.foot}
        moved = 0
        for g in gos:
            trp = m.tr_of_go.get(g)
            if trp is None or not m.active(g):
                continue
            pos = m.world(trp)[0]
            if not (x0 <= pos[0] <= x1 and z0 <= pos[2] <= z1):
                continue
            new = m.ground(pos[0], pos[2])
            if new is None:
                continue
            delta = new - old_ground(pos[0], pos[2])
            if abs(delta) > 0.001:
                p = pos.copy()
                p[1] += delta
                m.set_world_position(trp, p)
                moved += 1
        return moved

    def create_items(self):
        """(group, label, icon, key) of the new-object templates in the Objects pane
        and the 'Add here' menu."""
        items = [("Tanks", "Hostile tank", ("swatch", "triangle", COL_HOSTILE), ("spawn", 1)),
                 ("Tanks", "Friendly tank", ("swatch", "triangle", COL_FRIEND), ("spawn", 0)),
                 ("Routes", "New route", ("swatch", "route", PACK_COLOURS[0]), ("route",)),
                 ("Routes", "Waypoint", ("swatch", "diamond", PACK_COLOURS[0]), ("waypoint",))]
        items += [("Events", name, ("event", t), ("event", t)) for t, name in sorted(EVENT_TYPES.items()) if t != 0]
        return items

    def legend(self, spec):
        """Legend symbol image: ("swatch", kind, colour) as drawn on the map, or
        ("event", type)."""
        if spec not in self._images:
            img = (icons.event_icon(spec[1], self.icon_size) if spec[0] == "event"
                   else icons.swatch(spec[1], spec[2], self.icon_size))
            self._images[spec] = ImageTk.PhotoImage(img)
        return self._images[spec]

    def create_at(self, key, x, z):
        """Create the object of a template (Objects pane or 'Add here') at (x, z)."""
        m = self.model
        if not m:
            return
        if key[0] == "spawn":
            self.add_spawn(key[1] == 1, at=(x, z))
        elif key[0] == "route":
            self.add_route((x, z))
        elif key[0] == "waypoint":
            self.add_waypoint((x, z))
        elif key[0] == "event":
            if self.library.entries is None:
                self.library.load()

            def do():
                go = self.create_event(key[1])
                m.move_to(m.tr_of_go[go], x, z, self.follow_var.get())
                return go
            self.busy("Creating the event...", banner=False)
            new = self.op("Add event", do)
            self.idle()
            if new is not None:
                self.select(new)
                self.show_page("right", "props")
                tanks = m.sc.read(m.event_of_go(new))["Trigger_Type"] == 1
                self.status(f"Added '{m.name(new)}'. Set its trigger and tanks in Properties." +
                            (" A tank trigger without tanks fires at once." if tanks else ""), "ok")

    def refresh_objects(self):
        t = self.obj_tree
        t.delete(*t.get_children())
        self.obj_entries = {}
        self.obj_templates = {}
        f = self.obj_filter.get().lower()
        groups = {}
        for group, text, icon, key in self.create_items():
            if f and f not in text.lower() and f not in group.lower():
                continue
            if group not in groups:
                groups[group] = t.insert("", "end", text=" " + group, open=True)
            iid = t.insert(groups[group], "end", text=" " + text, image=self.legend(icon), values=("", "new"))
            self.obj_templates[iid] = key
        lib = getattr(self, "library", None)
        if lib is not None and lib.entries is None:
            lib.load()
        if lib is None or lib.entries is None:
            self.obj_place_btn.pack_forget()
            if self._lib_job is not None:
                self.obj_build_btn.pack_forget()
                self.obj_hint.config(text=self.library_progress_text())
            else:
                self.obj_build_btn.pack(side="left")
                self.obj_hint.config(text="The object library is built once by scanning the original missions.")
            return
        self.obj_build_btn.pack_forget()
        self.obj_place_btn.pack(side="left")
        self.obj_hint.config(text=OBJ_HINT)
        cats = {}
        for i, e in enumerate(lib.entries):
            if f and f not in e["name"].lower() and f not in e["category"].lower():
                continue
            sym = self.legend(("swatch", "square", CATEGORY_COLOURS.get(e["category"], (128, 128, 118))))
            if e["category"] not in cats:
                cats[e["category"]] = t.insert("", "end", text=" " + e["category"], image=sym, open=bool(f))
            iid = t.insert(cats[e["category"]], "end", text=" " + e["name"], image=sym,
                           values=(f"{e['w']:.0f} x {e['d']:.0f}", self.library.source_label(e)))
            self.obj_entries[iid] = e

    def build_library(self):
        if not self.ctx:
            return
        if self.ensure_library():
            self.refresh_objects()

    def selected_object_entry(self):
        sel = self.obj_tree.selection()
        return self.obj_entries.get(sel[0]) if sel else None

    def obj_item_label(self, iid):
        return self.obj_tree.item(iid, "text").strip()

    def place_item(self, iid, x, z):
        """Place Objects pane item iid (template or library object) at (x, z)."""
        if iid in self.obj_templates:
            self.create_at(self.obj_templates[iid], x, z)
        elif iid in self.obj_entries:
            self.place_object(self.obj_entries[iid], x, z)

    def place_selected_object(self):
        """Place at map centre: one item of the Objects pane at the centre of the view."""
        sel = self.obj_tree.selection()
        if not sel or (sel[0] not in self.obj_entries and sel[0] not in self.obj_templates):
            self.status("Select an object in the Objects pane first.", banner=True)
            return
        if tuple(self.obj_templates.get(sel[0], ())) == ("route",):
            self.add_route((self.view.cx, self.view.cz))
            return
        self.place_item(sel[0], self.view.cx, self.view.cz)

    def on_obj_select(self):
        """Choosing an item in the Objects pane chooses the Add tool for it (as a
        template in ArcGIS Create Features)."""
        if self._obj_sync:
            return
        sel = self.obj_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid in self.obj_templates:
            key = tuple(self.obj_templates[iid])
            title = self.obj_item_label(iid)
            self.set_tool("add", ("template", key, title.lower() if key[0] == "spawn" else title))
        elif iid in self.obj_entries:
            e = self.obj_entries[iid]
            self.set_tool("add", ("object", e, e["name"]))

    def on_obj_press(self, e):
        item = self.obj_tree.identify_row(e.y)
        known = item in self.obj_entries or item in self.obj_templates
        chosen = known and self.tool == "add" and item in self.obj_tree.selection()
        self.obj_drag = ({"item": item, "start": (e.x_root, e.y_root), "active": False, "chosen": chosen}
                         if known else None)

    def on_obj_motion(self, e):
        d = self.obj_drag
        if not d:
            return
        if not d["active"] and math.hypot(e.x_root - d["start"][0], e.y_root - d["start"][1]) > 6:
            d["active"] = True
            self.obj_tree.config(cursor="plus")
        if d["active"]:
            over = self.root.winfo_containing(e.x_root, e.y_root) is self.canvas
            self.status("Release over the map to place the object." if over else "Drag the object onto the map.")

    def on_obj_release(self, e):
        d, self.obj_drag = self.obj_drag, None
        self.obj_tree.config(cursor="")
        if d and not d["active"] and d.get("chosen"):
            self.stop_tool()
            return
        if not d or not d["active"]:
            return
        if self.root.winfo_containing(e.x_root, e.y_root) is not self.canvas:
            self.status("Object not placed (released outside the map).")
            return
        x = e.x_root - self.canvas.winfo_rootx()
        y = e.y_root - self.canvas.winfo_rooty()
        wx, wz = self.view.s2w(x, y)
        self.place_item(d["item"], wx, wz)

    TABLE_COLUMNS = {
        "spawns": [("name", "Name", 150), ("group", "Unit group", 90), ("side", "Side", 60), ("tank", "Tank", 110),
                   ("x", "X", 70), ("z", "Z", 70), ("heading", "Heading", 60), ("trigger", "Trigger", 110),
                   ("time", "Time (s)", 60), ("respawn", "Respawns", 65), ("target", "Target", 50),
                   ("status", "Status", 60)],
        "events": [("name", "Name", 220), ("type", "Event type", 140), ("trigger", "Trigger", 120),
                   ("time", "Time (s)", 60), ("tanks", "Trigger tanks", 80), ("message", "Message", 300),
                   ("status", "Status", 60)],
        "waypoints": [("route", "Route", 160), ("no", "No.", 45), ("name", "Name", 150), ("x", "X", 70),
                      ("z", "Z", 70), ("used", "Used by", 300)],
        "scenery": [("name", "Name", 220), ("category", "Category", 120), ("x", "X", 70), ("z", "Z", 70),
                    ("heading", "Heading", 60), ("scale", "Scale", 60), ("status", "Status", 60)],
        "features": [("no", "No.", 45), ("brush", "Brush", 110), ("kind", "Kind", 110), ("width", "Width (m)", 70),
                     ("height", "Height (m)", 70), ("slope", "Side slope (deg)", 95), ("rim", "Rim (m)", 60),
                     ("crossings", "AI crossings (m)", 100), ("level", "Level to (m)", 80),
                     ("texture", "Texture", 90), ("points", "Points", 55), ("length", "Length (m)", 75)],
    }

    def build_table(self, body, kind):
        holder = bordered(body)
        holder.pack(fill="both", expand=True, padx=2, pady=2)
        cols = self.TABLE_COLUMNS[kind]
        tv = ttk.Treeview(holder, columns=[c[0] for c in cols], show="headings", selectmode="extended")
        for key, title, width in cols:
            tv.heading(key, text=title, anchor="w", command=lambda k=key, t=tv: self.sort_table(t, k))
            tv.column(key, width=width, anchor="w", stretch=False)
        ys = ttk.Scrollbar(holder, command=tv.yview)
        xs = ttk.Scrollbar(holder, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        tv.pack(side="left", fill="both", expand=True)
        if kind == "features":
            tv.configure(selectmode="browse")
            tv.bind("<<TreeviewSelect>>", lambda e: self.on_feature_list_select())
        else:
            tv.bind("<<TreeviewSelect>>", lambda e, t=tv: self.on_table_select(t))
        tv.bind("<Double-Button-1>", lambda e, t=tv, k=kind: self.on_table_double(t, k, e))
        tv.bind("<F2>", lambda e, t=tv, k=kind: self.edit_cell(t, k, t.focus(), None))
        if kind == "waypoints":
            tv.bind("<Insert>", lambda e: self.insert_waypoint_after())
        elif kind == "spawns":
            tv.bind("<Insert>", lambda e: self.insert_spawn_row())
        elif kind == "events":
            tv.bind("<Insert>", lambda e: self.insert_event_row())
        if not hasattr(self, "tables"):
            self.tables = {}
        self.tables[kind] = tv
        self.table_counts = getattr(self, "table_counts", {})

    def on_table_double(self, tv, kind, e):
        if tv.identify_region(e.x, e.y) != "cell":
            return None
        if not self.edit_cell(tv, kind, tv.identify_row(e.y), tv.identify_column(e.x)):
            if kind == "features":
                self.frame_feature(self.feature_sel)
            else:
                self.frame_selection()
        return "break"

    def cell_spec(self, kind, go, key):
        """How a table cell is edited: (choices, current, commit), with choices a
        list of (label, value) for a fixed choice, ("free", [labels]) for text
        with suggestions, None for plain text, or a callable that opens a
        dialog. Returns None when the cell cannot be edited."""
        m = self.model
        if kind in ("waypoints", "scenery"):
            return self.object_cell_spec(kind, go, key)
        ev = m.event_of_go(go)
        if ev is None:
            return None
        d = m.sc.read(ev)
        trp = m.tr_of_go[go]

        def field(k, conv=str):
            return lambda v: self.op(f"Set {k}", m.set_field, ev, k, conv(v))

        def enum(table):
            return [(str(table[k]), k) for k in sorted(table)]

        def move(axis):
            def commit(v):
                p = m.world(trp)[0]
                x, z = (float(v), p[2]) if axis == 0 else (p[0], float(v))
                self.op("Move", m.move_to, trp, x, z, self.follow_var.get())
            return commit

        if key == "name":
            return None, m.name(go), lambda v: self.op("Rename", m.rename, go, v)
        if key == "trigger":
            return enum(TRIGGER_TYPES), d["Trigger_Type"], field("Trigger_Type", int)
        if key == "time":
            if kind != "spawns" and d["Trigger_Type"] != 0:
                return None
            return None, f"{d['Trigger_Time']:g}", field("Trigger_Time", float)
        if kind == "spawns":
            p, r, _ = m.world(trp)
            if key == "group":
                groups = sorted({m.sc.read(e)["Key_Name"] for e in m.spawns} - {""})
                return ("free", groups), d["Key_Name"], field("Key_Name")
            if key == "side":
                return enum(RELATIONSHIPS), d["Relationship"], field("Relationship", int)
            if key == "tank":
                cur = m.tank_entry(ev)
                return ([(tank_display(t), i) for i, t in enumerate(m.tanks)],
                        m.tanks.index(cur) if cur in m.tanks else None,
                        lambda i: self.op("Set tank", m.set_tank, ev, m.tanks[i]))
            if key in ("x", "z"):
                axis = 0 if key == "x" else 2
                return None, f"{p[axis]:.1f}", move(axis)
            if key == "heading":
                return None, f"{heading_of(r):.0f}", lambda v: self.op("Rotate", m.set_heading, trp, float(v))
            if key == "respawn":
                return None, str(d["Respawn_Times"]), field("Respawn_Times", int)
            if key == "target":
                return [("yes", True), ("no", False)], bool(d["Is_Mission_Target"]), field("Is_Mission_Target", bool)
        else:
            if key == "type":
                return enum(EVENT_TYPES), d["Event_Type"], field("Event_Type", int)
            if key == "tanks":
                return lambda: self.choose_tanks(ev, "Trigger_Tanks", "Trigger_Num", "Trigger tanks"), None, None
            if key == "message":
                if not et.shows_message(d["Event_Type"]):
                    return None
                return None, d["Event_Message"], lambda v: self.set_message(ev, v)
        return None

    def object_cell_spec(self, kind, go, key):
        """Cell editing for the waypoint and scenery tables."""
        m = self.model
        trp = m.tr_of_go.get(go)
        if trp is None:
            return None
        p, r, _ = m.world(trp)

        def move(axis):
            def commit(v):
                q = m.world(trp)[0]
                x, z = (float(v), q[2]) if axis == 0 else (q[0], float(v))
                self.op("Move", m.move_to, trp, x, z, self.follow_var.get())
            return commit

        if key == "name":
            return None, m.name(go), lambda v: self.op("Rename", m.rename, go, v)
        if key in ("x", "z"):
            axis = 0 if key == "x" else 2
            return None, f"{p[axis]:.1f}", move(axis)
        if kind == "scenery" and key == "heading":
            return None, f"{heading_of(r):.0f}", lambda v: self.op("Rotate", m.set_heading, trp, float(v))
        if kind == "scenery" and key == "scale":
            cur = m.tr[trp]["m_LocalScale"]["x"]

            def scale(v):
                v = float(v)
                if v <= 0 or not cur:
                    raise ValueError(v)
                self.op("Scale", m.set_scale, trp, v / cur)
            return None, f"{cur:.2f}", scale
        return None

    def insert_spawn_row(self):
        """Insert in the Spawns table: a new tank of the selected tank's side
        (friendly for the player), 10 m east of it; hostile when nothing is
        selected."""
        m = self.model
        if not m:
            return
        ev = m.event_of_go(self.selected) if self.selected in m.go else None
        if ev is None or ev not in m.spawns:
            self.add_spawn(True)
            return
        d = m.sc.read(ev)
        p = m.world(m.tr_of_go[self.selected])[0]
        self.add_spawn(d["Relationship"] == 1 and d["Tank_ID"] != 1, at=(p[0] + 10, p[2]))

    def insert_event_row(self):
        """Insert in the Events table: a new event of the selected event's type,
        or the Add event dialog when nothing is selected."""
        m = self.model
        if not m:
            return
        ev = m.event_of_go(self.selected) if self.selected in m.go else None
        if ev is None or ev in m.spawns:
            self.add_event()
            return
        self.create_at(("event", m.sc.read(ev)["Event_Type"]), self.view.cx, self.view.cz)

    def insert_waypoint_after(self):
        """Insert a waypoint after the selected one: halfway to the next waypoint,
        or 10 m further along the route after the last one."""
        m = self.model
        route = self.target_route() if m else None
        if not route or route[1] is None:
            self.status("Select a waypoint in the table first.", "warn")
            return
        pack, trp = route
        kids = [c["m_PathID"] for c in m.tr[pack]["m_Children"] if c["m_PathID"] in m.tr]
        i = kids.index(trp)
        p = m.world(trp)[0]
        if i + 1 < len(kids):
            q = m.world(kids[i + 1])[0]
            at = ((p[0] + q[0]) / 2, (p[2] + q[2]) / 2)
        elif i > 0:
            q = m.world(kids[i - 1])[0]
            d = np.array((p[0] - q[0], p[2] - q[2]))
            n = float(np.hypot(*d)) or 1.0
            at = (p[0] + d[0] / n * 10, p[2] + d[1] / n * 10)
        else:
            at = (p[0] + 10, p[2])
        self.add_waypoint(at)

    def edit_cell(self, tv, kind, iid, col):
        """Edit one table cell in place (double-click, or F2 for the name).
        Enter or leaving the cell applies, Escape cancels. Returns False when
        the cell is not editable."""
        if not iid or not self.model:
            return False
        col = col or "#1"
        keys = [c[0] for c in self.TABLE_COLUMNS[kind]]
        idx = int(col[1:]) - 1
        if not 0 <= idx < len(keys):
            return False
        if kind == "features":
            spec = self.feature_cell_spec(int(iid[1:]), keys[idx])
        else:
            spec = self.cell_spec(kind, int(iid), keys[idx])
        if spec is None:
            return False
        choices, current, commit = spec
        if callable(choices):
            choices()
            return True
        tv.see(iid)
        box = tv.bbox(iid, col)
        if not box:
            return False
        x, y, w, h = box
        fixed = isinstance(choices, list)
        if fixed:
            labels = [c[0] for c in choices]
            widget = ttk.Combobox(tv, values=labels, state="readonly")
            values = [c[1] for c in choices]
            if current in values:
                widget.current(values.index(current))
        elif choices is not None:
            widget = ttk.Combobox(tv, values=choices[1])
            widget.set(current)
        else:
            widget = ttk.Entry(tv)
            widget.insert(0, current)
            widget.select_range(0, "end")
        widget.place(x=x, y=y, width=max(w, 90), height=h)
        widget.focus_set()
        done = []

        def finish(apply):
            if done or not widget.winfo_exists():
                return
            done.append(True)
            text = widget.get()
            widget.destroy()
            tv.focus_set()
            if not apply:
                return
            if fixed:
                if text not in labels:
                    return
                value = choices[labels.index(text)][1]
            else:
                value = text.strip() if choices is not None else text
            if value == current:
                return
            try:
                commit(value)
            except ValueError:
                self.status(f"Not a valid value: '{text}'.", "warn")

        def on_blur(e):
            def check():
                focus = str(self.root.tk.call("focus"))
                if not focus.startswith(str(widget)):
                    finish(not fixed)
            widget.after(150, check)

        widget.bind("<Return>", lambda e: finish(True))
        widget.bind("<KP_Enter>", lambda e: finish(True))
        widget.bind("<Escape>", lambda e: (finish(False), "break")[1])
        widget.bind("<FocusOut>", on_blur)
        if fixed:
            widget.bind("<<ComboboxSelected>>", lambda e: finish(True))
        return True

    def sort_table(self, tv, key):
        rows = [(tv.set(i, key), i) for i in tv.get_children("")]

        def k(r):
            try:
                return (0, float(r[0]))
            except ValueError:
                return (1, r[0].lower())
        reverse = getattr(tv, "_sort", None) == key
        rows.sort(key=k, reverse=reverse)
        for n, (_, i) in enumerate(rows):
            tv.move(i, "", n)
        tv._sort = None if reverse else key

    def refresh_tables(self):
        self.refresh_toc_counts()
        if not hasattr(self, "tables"):
            return
        m = self.model
        for kind, tv in self.tables.items():
            if kind not in ("scenery", "features"):
                tv.delete(*tv.get_children())
        self._feat_list_key = None
        self.refresh_feature_list()
        if not m:
            self.tables["scenery"].delete(*self.tables["scenery"].get_children())
            self._scenery_table_key = None
            return
        show_deleted = self.layer("Deleted")
        self.fill_waypoint_table()
        self.fill_scenery_table()
        for e in m.spawns:
            d = m.sc.read(e)
            go = m.event_go[e]
            if not show_deleted and not m.active(go):
                continue
            p, r, _ = m.world(m.tr_of_go[go])
            t = m.tank_entry(e)
            self.tables["spawns"].insert("", "end", iid=str(go), values=(
                m.name(go), d["Key_Name"], self.spawn_side(e), tank_display(t) if t else "",
                f"{p[0]:.1f}", f"{p[2]:.1f}", f"{heading_of(r):.0f}",
                TRIGGER_TYPES.get(d["Trigger_Type"], d["Trigger_Type"]), f"{d['Trigger_Time']:g}",
                d["Respawn_Times"], "yes" if d["Is_Mission_Target"] else "",
                "active" if m.active(go) else "deleted"))
        for e in m.events:
            if e in m.spawns:
                continue
            d = m.sc.read(e)
            go = m.event_go[e]
            if not show_deleted and not m.active(go):
                continue
            self.tables["events"].insert("", "end", iid=str(go), values=(
                m.name(go), EVENT_TYPES.get(d["Event_Type"], d["Event_Type"]),
                TRIGGER_TYPES.get(d["Trigger_Type"], d["Trigger_Type"]), f"{d['Trigger_Time']:g}",
                len(d["Trigger_Tanks"]), d["Event_Message"].replace("\n", " "),
                "active" if m.active(go) else "deleted"))
        self.sync_table_selection()

    def fill_waypoint_table(self):
        m = self.model
        users = {}
        for e in m.spawns:
            pack = m.spawn_pack(e)
            if pack and m.active(m.event_go[e]):
                users.setdefault(pack, []).append(m.name(m.event_go[e]))
        tv = self.tables["waypoints"]
        for pack in m.packs:
            route = m.name(m.go_of_tr[pack])
            used = ", ".join(sorted(users.get(pack, [])))
            n = 0
            for c in m.tr[pack]["m_Children"]:
                trp = c["m_PathID"]
                if trp not in m.tr:
                    continue
                n += 1
                go = m.go_of_tr[trp]
                p = m.world(trp)[0]
                tv.insert("", "end", iid=str(go), values=(route, n, m.name(go), f"{p[0]:.1f}", f"{p[2]:.1f}", used))

    def fill_scenery_table(self):
        """One row per whole scenery object, filtered by the scenery layers. Only
        rebuilt while its page is shown, and only after an edit."""
        m = self.model
        tv = self.tables["scenery"]
        if self.docks["bottom"].active != "scenery":
            return
        cats = tuple(self.layer(c) for c in CATEGORY_COLOURS)
        key = (id(m), m.edits, self.layer("Deleted"), cats)
        if getattr(self, "_scenery_table_key", None) == key:
            return
        self._scenery_table_key = key
        tv.delete(*tv.get_children())
        show_deleted = self.layer("Deleted")
        roots = {m.object_root(g) for g in m.foot}
        rows = []
        for g in roots:
            active = m.active(g)
            if not show_deleted and not active:
                continue
            cat = category(m.name(g))
            if not self.layer(cat):
                continue
            trp = m.tr_of_go[g]
            p, r, _ = m.world(trp)
            sc = m.tr[trp]["m_LocalScale"]
            scale = f"{sc['x']:.2f}" if abs(sc["x"] - sc["y"]) < 1e-3 and abs(sc["x"] - sc["z"]) < 1e-3 else \
                f"{sc['x']:.2f} {sc['y']:.2f} {sc['z']:.2f}"
            rows.append((m.name(g).lower(), g, (m.name(g), cat, f"{p[0]:.1f}", f"{p[2]:.1f}",
                                                f"{heading_of(r):.0f}", scale, "active" if active else "deleted")))
        rows.sort()
        for _, g, values in rows:
            tv.insert("", "end", iid=str(g), values=values)

    def sync_table_selection(self):
        if not hasattr(self, "tables"):
            return
        self._table_sync = True
        try:
            for kind, tv in self.tables.items():
                if kind == "features":
                    continue
                want = [str(g) for g in self.sel if tv.exists(str(g))]
                if set(want) != set(tv.selection()):
                    tv.selection_set(want)
                if want:
                    tv.see(want[-1])
        finally:
            self.root.after_idle(lambda: setattr(self, "_table_sync", False))

    def on_table_select(self, tv):
        if self._table_sync or not self.model:
            return
        if self.tool in TERRAIN_TOOLS:
            self.set_tool("select")
        gos = [int(i) for i in tv.selection()]
        others = [g for g in self.sel if not tv.exists(str(g))]
        if set(gos) != set(self.sel) - set(others) or others:
            self.select_many(gos)

    def build_catalog(self, body):
        holder = bordered(body)
        holder.pack(fill="both", expand=True, padx=2, pady=2)
        self.catalog = ttk.Treeview(holder, show="tree", selectmode="browse")
        sb = ttk.Scrollbar(holder, command=self.catalog.yview)
        self.catalog.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.catalog.pack(side="left", fill="both", expand=True)
        self.catalog.bind("<Double-Button-1>", self.on_catalog_open)
        self.catalog.bind("<Return>", self.on_catalog_open)
        self.catalog.bind("<Button-3>", self.catalog_menu)

    def fill_catalog(self):
        c = self.catalog
        c.delete(*c.get_children())
        c.insert("", "end", iid="orig", text="Original missions", open=True)
        c.insert("", "end", iid="custom", text="Custom scenarios", open=True)
        for i, name in self.scenes:
            if i in self.custom_titles:
                c.insert("custom", "end", iid=f"s{i}", text=f"{self.custom_titles[i]}  (level{i})")
            else:
                c.insert("orig", "end", iid=f"s{i}", text=f"{name}  (level{i})")
        if self.model and c.exists(f"s{self.model.index}"):
            c.selection_set(f"s{self.model.index}")
            c.see(f"s{self.model.index}")

    def catalog_actions(self, iid):
        """(label, command, enabled) of the Catalog's right-click menu for item iid."""
        if not iid.startswith("s"):
            return []
        idx = int(iid[1:])
        is_open = bool(self.model and self.model.index == idx)
        actions = [("Open", self.on_catalog_open, not is_open)]
        entry = next((s for s in (self.scenarios.custom if self.scenarios else []) if s["battle_index"] == idx),
                     None)
        if entry is not None:
            actions.append(("Remove scenario...", lambda: self.remove_scenario(entry), True))
        return actions

    def catalog_menu(self, e):
        """Right-click on the Catalog: Open, and Remove scenario for a custom
        scenario."""
        iid = self.catalog.identify_row(e.y)
        if not iid:
            return
        self.catalog.selection_set(iid)
        self.catalog.focus(iid)
        actions = self.catalog_actions(iid)
        if not actions:
            return
        menu = tk.Menu(self.root, tearoff=0)
        for i, (label, cmd, enabled) in enumerate(actions):
            if i == 1:
                menu.add_separator()
            menu.add_command(label=label, command=cmd, state="normal" if enabled else "disabled",
                             font=UI_BOLD if i == 0 else None)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def on_catalog_open(self, _e=None):
        sel = self.catalog.selection()
        if not sel or not sel[0].startswith("s"):
            return
        idx = int(sel[0][1:])
        if self.model and idx == self.model.index:
            return
        if self.guard_unsaved():
            self.open_scene(idx)

    def build_properties(self, body):
        holder = bordered(body)
        holder.pack(fill="both", expand=True, padx=2, pady=2)
        self.prop_canvas = tk.Canvas(holder, highlightthickness=0, bd=0, background=FACE)
        sb = ttk.Scrollbar(holder, command=self.prop_canvas.yview)
        self.prop_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.prop_canvas.pack(side="left", fill="both", expand=True)
        self.props = tk.Frame(self.prop_canvas, padx=6, pady=4, bg=FACE)
        self._props_win = self.prop_canvas.create_window(0, 0, anchor="nw", window=self.props)
        self.props.bind("<Configure>", lambda e: self.prop_canvas.configure(
            scrollregion=self.prop_canvas.bbox("all")))
        self.prop_canvas.bind("<Configure>", lambda e: self.prop_canvas.itemconfig(self._props_win, width=e.width))

        def wheel(e):
            self.prop_canvas.yview_scroll(int(-e.delta / 120) * 3, "units")
        self.prop_canvas.bind("<Enter>", lambda e: self.prop_canvas.bind_all("<MouseWheel>", wheel))
        self.prop_canvas.bind("<Leave>", lambda e: self.prop_canvas.unbind_all("<MouseWheel>"))

    def bind_canvas(self):
        c = self.canvas
        c.bind("<Configure>", lambda e: self.request_render())
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_motion)
        c.bind("<ButtonRelease-1>", self.on_release)
        for b in ("2", "3"):
            c.bind(f"<ButtonPress-{b}>", self.on_pan_start)
            c.bind(f"<B{b}-Motion>", self.on_motion)
            c.bind(f"<ButtonRelease-{b}>", self.on_release)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Motion>", self.on_hover)
        c.bind("<Tab>", lambda e: (self.next_spawn(1), "break")[1])
        c.bind("<Shift-Tab>", lambda e: (self.next_spawn(-1), "break")[1])
        c.bind("<Enter>", lambda e: setattr(self, "mouse", (e.x, e.y)))
        c.bind("<Leave>", lambda e: setattr(self, "mouse", None))
        r = self.root

        def edit_key(fn):
            def handler(e):
                if self.typing():
                    return None
                fn()
                return "break"
            return handler
        for seq, fn in (("s", self.save), ("z", self.undo), ("d", self.duplicate_selected),
                        ("c", self.copy_selection), ("x", self.cut_selection), ("v", self.paste),
                        ("a", self.select_all)):
            r.bind_all(f"<Control-{seq}>", edit_key(fn))
            r.bind_all(f"<Control-{seq.upper()}>", edit_key(fn))
        r.bind_all("<Control-Insert>", edit_key(self.copy_selection))
        r.bind_all("<Shift-Insert>", edit_key(self.paste))
        r.bind_all("<Shift-Delete>", edit_key(self.cut_selection))
        r.bind("<Key>", self.on_key)
        r.bind("<KeyPress-space>", self.on_space, add="+")
        r.bind("<KeyRelease-space>", self.on_space, add="+")

    # ---- helpers -----------------------------------------------------

    def set_tool(self, tool, item=None):
        """Make `tool` the one active map tool. As in QGIS and ArcGIS it stays
        active until another tool is chosen; tabs and panels do not change it.
        The "add" tool places `item` (("template", key, label) or ("object",
        entry, label)) with each click on the map."""
        if self.sketch is not None:
            self.finish_sketch()
        self.tool = tool
        self.add_item = item if tool == "add" else None
        tree = getattr(self, "obj_tree", None)
        if tool != "add" and tree is not None and tree.selection():
            tree.selection_remove(*tree.selection())
        if tool not in TERRAIN_TOOLS:
            self.canvas.delete("brush")
        key = self.tool_key()
        for name, btn in self.tool_buttons.items():
            btn.set_active(name == key)
        self.canvas.config(cursor=self.cursor())
        self.tool_lbl.config(text=f"Tool: {self.tool_title()}")
        self.status(self.tool_hint())

    def press_tool(self, key, choose):
        """Toolbar tool button: choose the tool; the button of the active tool
        returns to Select / move."""
        if key != "select" and self.tool_key() == key and self.sketch is None:
            self.set_tool("select")
        else:
            choose()

    def stop_tool(self):
        """Back to Select / move (Esc, right-click > Stop, active button again)."""
        if self.tool != "select":
            title = self.tool_title()
            self.set_tool("select")
            self.status(f"{title} stopped. {self.tool_hint()}")

    def tool_key(self):
        """Toolbar button of the active tool."""
        if self.tool != "add":
            return self.tool
        kind, value, _ = self.add_item
        if kind == "template":
            return ADD_BUTTONS.get(tuple(value))
        return "add:object"

    def tool_title(self):
        return f"Add {self.add_item[2]}" if self.tool == "add" else TOOL_NAMES[self.tool]

    def cursor(self):
        if self.space_down:
            return TOOL_CURSORS["pan"]
        if self.sketch is not None:
            return TOOL_CURSORS["feature" if self.sketch.get("kind") == "terrain" else "add"]
        return TOOL_CURSORS[self.tool]

    def tool_hint(self):
        """Instructions of the active tool for the status bar."""
        hint = self.tool_instructions()
        if self.tool != "select" and self.sketch is None:
            hint += " Esc, right-click > Stop, or the Select / move tool ends it."
        return hint

    def tool_instructions(self):
        tool = self.tool
        if self.sketch is not None:
            return self.sketch_hint()
        if tool == "add":
            kind, value, title = self.add_item
            if kind == "template" and tuple(value) == ("route",):
                return ("New route: click its first waypoint, then the next ones; right-click, double-click or "
                        "Enter finishes the route.")
            if kind == "template" and tuple(value) == ("waypoint",):
                return "Waypoint: click on the map to add a waypoint after the selected one of the selected route."
            return f"Add {title}: click on the map to place one."
        if tool == "feature":
            return (f"Draw terrain with '{self.brush['name']}': click points or drag to draw; right-click, "
                    "double-click or Enter finishes (one point places a single stamp)." + self.ai_note(self.brush))
        if tool == "vertex":
            return ("Edit terrain features: click a feature to select it, drag a point or the feature to move "
                    "it, double-click its line to add a point; Del deletes it, right-click > Remove point "
                    "removes a point.")
        return {"select": "Select / move: click to select (Shift adds, Ctrl toggles), drag to move, drag on "
                          "empty map for a selection box.",
                "pan": "Pan: drag the map.",
                "zoomin": "Zoom in: click, or drag a box.",
                "zoomout": "Zoom out: click.",
                "rotate": "Rotate: drag around the selection (Shift snaps to the step)."}.get(tool, "")

    def use_template(self, key):
        """Choose the Add tool for an Objects pane template (tank, route,
        waypoint, event) and show it chosen in the Objects pane."""
        iid = next((i for i, k in self.obj_templates.items() if tuple(k) == tuple(key)), None)
        title = self.obj_item_label(iid) if iid else str(key)
        if iid:
            self._obj_sync = True
            self.obj_tree.selection_set(iid)
            self.obj_tree.see(iid)
            self.root.update_idletasks()
            self._obj_sync = False
        self.set_tool("add", ("template", tuple(key), title.lower() if key[0] == "spawn" else title))

    def tool_add_object(self):
        """Add object button: the Add tool for the object chosen in the Objects
        pane, else a list to choose one."""
        sel = self.obj_tree.selection()
        if sel and sel[0] in self.obj_entries:
            e = self.obj_entries[sel[0]]
            self.set_tool("add", ("object", e, e["name"]))
        else:
            self.add_object()

    def toggle_follow(self):
        self.follow_var.set(not self.follow_var.get())
        self.status(f"Terrain follow {'on' if self.follow_var.get() else 'off'}.")

    def status(self, text, kind=None, banner=False):
        fg = {"ok": OK_FG, "warn": WARN_FG}.get(kind, "#000000")
        self.status_lbl.config(text=text, fg=fg)
        if banner:
            self.show_banner(text, kind)

    def show_banner(self, text, kind=None):
        """Message bar along the top of the map (as in QGIS): information hides
        after a few seconds, a warning stays longer; x closes it."""
        bg, edge = MESSAGE_COLOURS.get(kind, MESSAGE_COLOURS[None])
        self.msg_bar.config(bg=bg, highlightbackground=edge)
        self.msg_lbl.config(text=text, bg=bg, fg={"warn": WARN_FG}.get(kind, "#000000"),
                            wraplength=max(200, self.canvas.winfo_width() - 60))
        self.msg_close.config(bg=bg)
        self.msg_bar.place(x=0, y=0, relwidth=1.0)
        self.msg_bar.lift()
        if self._banner_after:
            self.root.after_cancel(self._banner_after)
        self._banner_after = self.root.after(MESSAGE_MS.get(kind, 5000), self.hide_message)

    def hide_message(self):
        self._banner_after = None
        self.msg_bar.place_forget()

    def busy(self, text, banner=True):
        self.status(text)
        if banner:
            self.show_banner(text)
        self.root.config(cursor="watch")
        self.root.update()

    def idle(self):
        self.root.config(cursor="")

    def typing(self):
        w = self.root.focus_get()
        return isinstance(w, (tk.Entry, tk.Text, tk.Spinbox, ttk.Combobox, ttk.Entry))

    def layers_changed(self):
        self._base_key = None
        self._scenery_cache = (None, None)
        self.refresh_tables()
        self.request_render()

    def layer(self, name):
        return self.layers.get(name, True)

    def step(self):
        try:
            return float(self.rot_step.get() or 15)
        except ValueError:
            return 15.0

    # ---- scenes ------------------------------------------------------

    def refresh_scenes(self):
        self.scenes = [(i, self.ctx.scene_label(i)) for i in range(len(self.ctx.scene_names))
                       if is_mission_scene(self.ctx.scene_label(i))]
        self.custom_titles = {s["battle_index"]: s["title"] for s in self.scenarios.custom}
        self.fill_catalog()

    def game_closed(self, action):
        """False (with a message) while the game is running: the game reads its
        scene list only when it starts and may hold files open, so game files
        are changed only while it is closed."""
        if self.ctx and settings.game_running(self.ctx.root):
            messagebox.showwarning(
                "Game is running",
                f"Tank Battle Classic is running. Close the game before you {action}: the game reads its "
                "mission and scene lists only when it starts, so a change made now leaves it with an "
                "outdated list (a new or changed mission can then load as a grey screen).",
                parent=self.root)
            self.status(f"Close the game before you {action}.", "warn")
            return False
        return True

    def guard_unsaved(self):
        """True when it is fine to continue (saved, discarded or no changes)."""
        if not (self.model and self.model.dirty):
            return True
        ans = messagebox.askyesnocancel(
            "Unsaved changes", f"Save the changes to level{self.model.index} first?", parent=self.root)
        if ans is None:
            return False
        if ans:
            return self.save()
        return True

    def choose_scene(self):
        items = []
        for i, name in self.scenes:
            if i in self.custom_titles:
                items.append((f"level{i:<3}  {name}   [custom: {self.custom_titles[i]}]", i, "#006400"))
            else:
                items.append((f"level{i:<3}  {name}", i))
        cur = self.model.index if self.model else None
        idx = ListDialog(self.root, "Open mission", items, current=cur).run()
        if idx is not None and self.guard_unsaved():
            self.open_scene(idx)

    def open_scene(self, index):
        self.busy(f"Loading level{index} ({self.ctx.scene_label(index)})...")
        try:
            self.model = SceneModel(self.ctx, index)
        except Exception as e:
            self.idle()
            self.status(f"Failed to load level{index}: {e}", "warn", banner=True)
            return
        self.selected = None
        self._nav_sig = self.navmesh_signature()
        self._terrain_img = None
        self._terrain_rev += 1
        if self.model.terrain is not None:
            img = np.clip(self.model.terrain.image.astype(np.int32) * 5 // 4, 0, 255).astype(np.uint8)
            self._terrain_img = Image.fromarray(img)
        self.layers_changed()
        self.fit_view()
        self.idle()
        self.update_title()
        self.feature_sel = None
        self.load_terrain_features()
        self.refresh_terrain_page()
        self.fill_catalog()
        self.refresh_props()
        self.refresh_tables()
        self.status(f"Opened level{index}: {self.model.label} ({len(self.model.spawns)} spawn events).", "ok")

    def update_title(self):
        m = self.model
        if not hasattr(self, "toc"):
            return
        if not m:
            self.root.title("Tank Battle Classic - Mission Editor")
            self.toc.item("root", text="Layers")
            return
        name = self.custom_titles.get(m.index, m.label)
        star = " *" if m.dirty else ""
        self.root.title(f"level{m.index} {name}{star} - Tank Battle Classic Mission Editor")
        self.toc.item("root", text=f"{name}{star}")

    def fit_view(self):
        m = self.model
        if not m:
            return
        w, h = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        if m.terrain is not None:
            t = m.terrain
            x0, z0, x1, z1 = t.origin[0], t.origin[2], t.origin[0] + t.size_x, t.origin[2] + t.size_z
        else:
            pts = [m.world(m.tr_of_go[m.event_go[e]])[0] for e in m.spawns] or [np.zeros(3)]
            xs, zs = [p[0] for p in pts], [p[2] for p in pts]
            x0, x1, z0, z1 = min(xs) - 200, max(xs) + 200, min(zs) - 200, max(zs) + 200
        self.view.cx, self.view.cz = (x0 + x1) / 2, (z0 + z1) / 2
        self.view.scale = min(w / max(1, x1 - x0), h / max(1, z1 - z0)) * 0.95
        self.request_render()

    def backup_path(self, index=None):
        return os.path.join(self.backup_dir, f"level{self.model.index if index is None else index}.orig")

    def save(self):
        m = self.model
        if not m:
            return False
        if m.dirty and not self.game_closed("save"):
            return False
        if not m.dirty:
            self.status("Nothing to save - no changes since the last save.", banner=True)
            return True
        self.busy(f"Saving level{m.index}...")
        os.makedirs(self.backup_dir, exist_ok=True)
        bak = self.backup_path()
        try:
            if not os.path.exists(bak):
                shutil.copy2(m.sc.path, bak)
            if m.missing_event_texts():
                self.op("Connect message texts", m.repair_event_texts)
            if m.count_mismatches():
                self.op("Correct event list counts", m.repair_counts)
            nav_note = self.auto_rebuild_navmesh()
            m.save()
            self.save_terrain_features()
        except PermissionError:
            self.idle()
            self.status("Save failed: the level file is locked. Close the game and try again.", "warn", banner=True)
            return False
        except Exception as e:
            self.idle()
            self.status(f"Save failed: {e}", "warn", banner=True)
            return False
        self.idle()
        self.update_title()
        msg = f"Saved level{m.index} - {self.custom_titles.get(m.index, m.label)}. Original kept in backups/."
        if nav_note:
            msg += " " + nav_note
        if m.index in self.custom_titles:
            lines = self.run_briefing_sync()
            if lines:
                msg += " " + lines[0]
            self.backup_scenario(m.index)
        self.status(msg, "ok", banner=True)
        return True

    def run_briefing_sync(self):
        """Update the briefing screen from the open (saved) battle scene."""
        self.busy("Updating the briefing screen...")
        try:
            lines = sync_mission(self.ctx, self.scenarios, self.model, self.backup_dir)
        except PermissionError:
            self.idle()
            self.status("Updating the briefing failed: a game file is locked. Close the game first.", "warn",
                        banner=True)
            return None
        except Exception as e:
            self.idle()
            self.status(f"Updating the briefing failed: {e}", "warn", banner=True)
            return None
        self.idle()
        if len(lines) > 1:
            messagebox.showinfo("Briefing screen updated", "\n".join(lines), parent=self.root)
        return lines

    def update_briefing(self):
        m = self.model
        if not m:
            self.status("Open a mission first.", banner=True)
            return
        if self.scenarios.mission_for_scene(m.label) is None:
            self.status("This scene has no briefing screen.", banner=True)
            return
        if m.dirty:
            if not messagebox.askyesno("Update briefing screen", "Save the open mission first?", parent=self.root):
                return
            if not self.save():
                return
            if m.index in self.custom_titles:
                return
        if m.index not in self.custom_titles and not messagebox.askyesno(
                "Update briefing screen",
                "This changes the briefing scene of an original mission (unit list, counts and map symbols). "
                "A backup is kept and 'Restore original' undoes it. Continue?", parent=self.root):
            return
        lines = self.run_briefing_sync()
        if lines:
            self.status(lines[0], "ok", banner=True)

    def restore_original(self):
        if self.ctx and not self.game_closed("restore an original mission"):
            return
        if not self.model:
            return
        bak = self.backup_path()
        if not os.path.exists(bak):
            self.status("Nothing to restore - this level has never been saved by the editor.", banner=True)
            return
        if not messagebox.askyesno("Restore original",
                                   "Restore the original level file and discard all saved edits?",
                                   parent=self.root):
            return
        index = self.model.index
        self.busy(f"Restoring original level{index}...")
        try:
            shutil.copy2(bak, self.model.sc.path)
            found = self.scenarios.mission_for_scene(self.model.label)
            menu_idx = self.scenarios.scene_index(found[1]["Menu_Scene_Name"]) if found else None
            if menu_idx is not None and os.path.exists(self.backup_path(menu_idx)):
                shutil.copy2(self.backup_path(menu_idx), self.ctx.scene_path(menu_idx))
        except PermissionError:
            self.idle()
            self.status("Restore failed: the level file is locked. Close the game first.", "warn", banner=True)
            return
        self.open_scene(index)
        self.status(f"Restored original level{index}.", "ok", banner=True)

    # ---- scenarios ---------------------------------------------------

    def new_scenario(self):
        if self.ctx and not self.game_closed("create a scenario"):
            return
        try:
            missions = self.scenarios.missions()
        except Exception as e:
            self.status(f"Cannot read the mission list: {e}", "warn", banner=True)
            return
        sources = [(f"{d['Scene_Title']}   [{d['Battle_Scene_Name']}]", pid) for pid, d in missions]
        self.busy("Reading the terrains...")
        try:
            terrains = self.scenarios.terrains()
        except Exception:
            terrains = []
        self.idle()
        dlg = TextFormDialog(self.root, "New scenario", sources=sources, terrains=terrains,
                             briefing="Objective: Destroy all enemy units.")
        res = dlg.run()
        if not res or not self.guard_unsaved():
            return
        pid, title, briefing = res
        self.busy(f"Creating scenario '{title}'...")
        try:
            _, battle_idx = self.scenarios.create(pid, title, briefing)
        except PermissionError:
            self.idle()
            self.status("Creating failed: a game file is locked. Close the game and try again.", "warn", banner=True)
            return
        except Exception as e:
            self.idle()
            self.status(f"Creating failed: {e}", "warn", banner=True)
            return
        self.refresh_scenes()
        self.open_scene(battle_idx)
        if dlg.empty and self.model and self.model.index == battle_idx:
            self.busy("Clearing the terrain...")
            cleared = self.op("Empty terrain", self.model.clear_to_terrain)
            self.idle()
            if cleared is None or not self.save():
                return
        else:
            self.backup_scenario(battle_idx)
        self.status(f"Created '{title}' (level{battle_idx}). It is on the 'Custom Missions' page "
                    "of the mission select screen.", "ok", banner=True)

    def edit_mission_text(self):
        if self.ctx and not self.game_closed("change the mission text"):
            return
        if not self.model:
            self.status("Open a mission first.", banner=True)
            return
        found = self.scenarios.mission_for_scene(self.model.label)
        if found is None:
            self.status("This scene has no mission entry (title/briefing).", banner=True)
            return
        _, d = found
        res = TextFormDialog(self.root, "Mission text", title_text=d["Scene_Title"],
                             briefing=d["Scene_Briefing"]).run()
        if not res:
            return
        _, title, briefing = res
        try:
            self.scenarios.set_mission_text(self.model.label, title=title, briefing=briefing)
        except PermissionError:
            self.status("Saving the text failed: a game file is locked. Close the game first.", "warn", banner=True)
            return
        except Exception as e:
            self.status(f"Saving the text failed: {e}", "warn", banner=True)
            return
        self.refresh_scenes()
        self.update_title()
        if self.model.index in self.custom_titles:
            self.backup_scenario(self.model.index)
        self.status(f"Mission text saved: '{title}'.", "ok", banner=True)

    def close_scene(self):
        """Close the open mission (its scene is about to disappear)."""
        self.model = None
        self.selected = None
        self.sel = []
        self.canvas.delete("overlay")
        self.canvas.itemconfig(self._base_item, image="")
        self._base_key = None
        self.refresh_props()
        self.refresh_tables()
        self.update_title()

    def remove_scenario(self, entry=None):
        """Remove one custom scenario from the game (the others stay). entry:
        its scenario list entry; None asks which."""
        if not self.ctx or not self.game_closed("remove a scenario"):
            return
        custom = self.scenarios.custom
        if not custom:
            self.status("There are no custom scenarios to remove.", banner=True)
            return
        if entry is None:
            entry = ListDialog(self.root, "Remove scenario",
                               [(f"{s['title']}  (level{s['battle_index']})", s) for s in custom],
                               prompt="Choose the scenario to remove from the game.").run()
            if not entry:
                return
        keep = RemoveScenarioDialog(self.root, entry["title"], len(custom) - 1).run()
        if keep is None or not self.guard_unsaved():
            return
        open_id = None
        if self.model:
            cur = next((s for s in custom if s["battle_index"] == self.model.index), None)
            open_id = cur["scene_id"] if cur else None
        self.busy(f"Removing '{entry['title']}' and restoring the other scenarios...")
        try:
            done, lines = self.backups.remove_one(entry["scene_id"], keep_backup=keep)
        except PermissionError:
            self.idle()
            self.status("Removing failed: a game file is locked. Close the game and try again.", "warn", banner=True)
            return
        except Exception as e:
            self.idle()
            self.status(f"Removing failed: {e}", "warn", banner=True)
            return
        self.idle()
        if open_id is not None:
            self.close_scene()
        self.refresh_scenes()
        reopen = next((s for s in self.scenarios.custom if s["scene_id"] == open_id), None)
        if reopen is not None:
            self.open_scene(reopen["battle_index"])
        msg = f"Removed '{entry['title']}'."
        if keep:
            msg += " Its backup is kept (Scenario > Restore scenarios from backup)."
        if lines:
            messagebox.showinfo("Remove scenario", msg + "\n\n" + "\n".join(lines), parent=self.root)
        self.status(msg, "warn" if lines else "ok", banner=True)

    def remove_custom(self):
        if self.ctx and not self.game_closed("remove scenarios"):
            return
        removed, kept = self.scenarios.removable()
        other_copy = ("made by another copy of the editor (the installed program or the source version) "
                      "before this copy changed the game; remove them with that copy")
        if not self.scenarios.has_changes():
            if kept:
                self.status("The custom scenarios here were " + other_copy + ".", "warn", banner=True)
            else:
                self.status("There are no custom scenarios or mission text changes to remove.", banner=True)
            return
        n = len(removed)
        text = f"Remove {n} custom scenario(s) and undo mission text changes?"
        if removed:
            text += "\n\nRemoved: " + ", ".join(s["title"] for s in removed)
        if kept:
            text += "\n\nKept: " + ", ".join(s["title"] for s in kept) + " (" + other_copy + ")."
        if not messagebox.askyesno("Remove all custom scenarios", text, parent=self.root):
            return
        if not self.guard_unsaved():
            return
        base = self.scenarios.manifest.get("base_scene_count")
        self.busy("Removing custom scenarios...")
        try:
            self.scenarios.remove_all()
        except PermissionError:
            self.idle()
            self.status("Removing failed: a game file is locked. Close the game and try again.", "warn", banner=True)
            return
        except Exception as e:
            self.idle()
            self.status(f"Removing failed: {e}", "warn", banner=True)
            return
        self.idle()
        if self.model and base is not None and self.model.index >= base:
            self.close_scene()
        self.refresh_scenes()
        self.update_title()
        self.status(f"Removed {n} custom scenario(s) and restored the mission files. Their backups are kept: "
                    "Scenario > Restore scenarios from backup brings them back."
                    + (f" Kept: {', '.join(s['title'] for s in kept)}." if kept else ""), "ok", banner=True)

    # ---- objects -----------------------------------------------------

    def start_library_build(self, game):
        """Build the object library in a separate process (the editor started a
        second time with --build-library), so it never slows down the window or
        the open mission; the result is picked up from the library cache file."""
        self.stop_library_build()
        fd, progress = tempfile.mkstemp(prefix="tbme-library-", suffix=".txt")
        os.close(fd)
        cmd = [sys.executable] + ([] if getattr(sys, "frozen", False) else [os.path.abspath(__file__)])
        cmd += ["--build-library", game, self.backup_dir, progress]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        try:
            proc = subprocess.Popen(cmd, creationflags=flags, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.status(f"The object library could not be built in the background: {e}", "warn")
            return
        self._lib_job = {"game": game, "proc": proc, "progress_file": progress, "progress": (0, 0, "")}
        self.refresh_objects()
        self.root.after(300, self._poll_library)

    def stop_library_build(self):
        job, self._lib_job = self._lib_job, None
        if job and job["proc"].poll() is None:
            job["proc"].terminate()

    def _read_library_progress(self, job):
        try:
            with open(job["progress_file"], "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            return ""
        for line in reversed(lines):
            parts = line.split(" ", 2)
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                job["progress"] = (int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else "")
                break
        return "\n".join(l for l in lines if l.startswith("ERROR"))

    def library_progress_text(self):
        job = self._lib_job
        if not job:
            return ""
        n, total, _ = job["progress"]
        return (f"Building the object library in the background ({n + 1} of {total})..." if total
                else "Preparing the object library in the background...")

    def _poll_library(self):
        job = self._lib_job
        if job is None:
            return
        self._read_library_progress(job)
        if job["proc"].poll() is None:
            if self.library.entries is None:
                self.obj_hint.config(text=self.library_progress_text())
            self.root.after(300, self._poll_library)
            return
        self._finish_library(job)

    def _finish_library(self, job):
        if self._lib_job is job:
            self._lib_job = None
        error = self._read_library_progress(job)
        try:
            os.remove(job["progress_file"])
        except OSError:
            pass
        if not self.ctx or os.path.normcase(job["game"]) != os.path.normcase(self.ctx.root):
            return
        if job["proc"].returncode != 0 or not self.library.load():
            self.refresh_objects()
            self.status("Building the object library failed" + (f": {error[6:]}" if error else "."), "warn")
            return
        self.refresh_objects()
        self.status(f"Object library ready: {len(self.library.entries)} objects.", "ok")

    def ensure_library(self):
        """Library available? Waits for a running background build, or builds now."""
        if self.library.entries is not None or self.library.load():
            return True
        job = self._lib_job
        if job is not None:
            self.root.config(cursor="watch")
            while job["proc"].poll() is None:
                self._read_library_progress(job)
                self.status(self.library_progress_text())
                self.root.update()
                time.sleep(0.05)
            self.idle()
            self._finish_library(job)
            return self.library.entries is not None
        self.root.config(cursor="watch")

        def progress(n, total, name):
            self.status(f"Building object library: mission {n + 1} of {total} ({name})...")
            self.root.update()
        try:
            self.library.build(progress)
        except Exception as e:
            self.idle()
            self.status(f"Building the object library failed: {e}", "warn", banner=True)
            return False
        self.idle()
        self.refresh_objects()
        self.status(f"Object library built: {len(self.library.entries)} objects.", "ok")
        return True

    def add_object(self):
        m = self.model
        if not m:
            self.status("Open a mission first.", banner=True)
            return
        if not self.ensure_library():
            return
        self.refresh_objects()
        items = []
        for e in self.library.entries:
            src = self.library.source_label(e)
            items.append((f"{e['category']:<18} {e['name']:<42} {e['w']:>5.0f} x {e['d']:<5.0f} m   [{src}]", e))
        entry = ListDialog(self.root, "Add object", items,
                           prompt="Choose an object; then click on the map to place it.").run()
        if entry is not None:
            self.set_tool("add", ("object", entry, entry["name"]))

    def place_object(self, entry, wx, wz):
        m = self.model
        if not m:
            self.status("Open a mission first.", banner=True)
            return
        self.busy(f"Adding {entry['name']}...", banner=False)
        result = self.op("Add object", import_object, m, self.library, entry, wx, wz, self.follow_var.get())
        self.idle()
        if result is None:
            return
        root, cleared = result
        if root in m.areas:
            m.begin("Name trigger area")
            m.rename(root, m._unique_name("Trigger area"))
            m.commit()
        self.selected = root
        self.changed()
        note = f" {cleared} outside reference(s) were cleared." if cleared else ""
        self.status(f"Added '{m.name(root)}' from {self.library.source_label(entry)}. "
                    f"Drag it into place.{note}", "ok")

    def changed(self, props=True):
        self.update_title()
        self.refresh_feature_list()
        if props:
            self.refresh_props()
        self.refresh_tables()
        self.request_render()

    def op(self, text, fn, *args, **kwargs):
        m = self.model
        m.begin(text)
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            m.commit()
            self.status(f"{text} failed: {e}", "warn", banner=True)
            self.changed()
            return None
        m.commit()
        self.changed()
        return result

    def kind(self, go):
        m = self.model
        trp = m.tr_of_go.get(go)
        ev = m.event_of_go(go)
        if ev is not None:
            return "spawn" if ev in m.spawns else "event"
        if trp in m.waypoints:
            return "waypoint"
        if trp in m.packs:
            return "pack"
        return "object"

    def spawn_side(self, ev):
        d = self.model.sc.read(ev)
        if d["Tank_ID"] == 1:
            return "Player"
        return "Hostile" if d["Relationship"] == 1 else "Friendly"

    def spawn_colour(self, ev):
        if not self.model.active(self.model.event_go[ev]):
            return COL_HIDDEN
        return {"Player": COL_PLAYER, "Hostile": COL_HOSTILE, "Friendly": COL_FRIEND}[self.spawn_side(ev)]

    def spawn_visible(self, ev):
        m = self.model
        if not self.layer("Spawns") or not self.layer(self.spawn_side(ev)):
            return False
        return self.layer("Deleted") or m.active(m.event_go[ev])

    def event_visible(self, ev):
        m = self.model
        if ev in m.spawns or not self.layer("Events"):
            return False
        return self.layer("Deleted") or m.active(m.event_go[ev])

    def event_marks(self):
        """{event GameObject: (screen x, screen y)} for the visible events.
        Events at (nearly) the same spot, as in most shipped missions, are laid
        out side by side so that each stays visible and clickable."""
        m, v = self.model, self.view
        out, used = {}, {}
        for e in m.events:
            if not self.event_visible(e):
                continue
            go = m.event_go[e]
            p, _, _ = m.world(m.tr_of_go[go])
            x, y = v.w2s(p[0], p[2])
            cell = (round(x / EVENT_MARK), round(y / EVENT_MARK))
            n = used.get(cell, 0)
            used[cell] = n + 1
            out[go] = (x + n * (EVENT_MARK + 2), y)
        return out

    def event_image(self, event_type, deleted=False):
        key = ("mapevent", event_type, deleted)
        if key not in self._images:
            img = icons.event_icon(event_type, EVENT_MARK)
            if deleted:
                img = img.convert("LA").convert("RGBA")
            self._images[key] = ImageTk.PhotoImage(img)
        return self._images[key]

    def movable(self, gos=None):
        """Selected objects that can be moved / rotated / copied (no route packs),
        without children of other selected objects. An event's position only
        places its symbol on the map; the game does not use it."""
        m = self.model
        gos = [g for g in (self.sel if gos is None else gos)
               if g in m.go and self.kind(g) in ("spawn", "waypoint", "object", "event")]
        chosen = set(gos)
        out = []
        for g in gos:
            p = m.parent_go(g)
            while p is not None and p not in chosen:
                p = m.parent_go(p)
            if p is None:
                out.append(g)
        return out

    def rotate_group(self, gos, delta, base=None):
        """Rotate objects by delta degrees around their common centre (each one
        around itself when alone). base: {go: (pos, heading)} for absolute rotation."""
        m = self.model
        trs = [m.tr_of_go[g] for g in gos]
        if base is None:
            base = {g: (m.world(t)[0].copy(), heading_of(m.world(t)[1])) for g, t in zip(gos, trs)}
        cx = sum(base[g][0][0] for g in gos) / len(gos)
        cz = sum(base[g][0][2] for g in gos) / len(gos)
        a = math.radians(delta)
        ca, sa = math.cos(a), math.sin(a)
        for g, t in zip(gos, trs):
            p0, h0 = base[g]
            m.set_heading(t, (h0 + delta) % 360)
            if len(gos) > 1:
                dx, dz = p0[0] - cx, p0[2] - cz
                m.move_to(t, cx + dx * ca + dz * sa, cz - dx * sa + dz * ca, self.follow_var.get())

    def rotate_selected(self, sign, step=None):
        if not self.model:
            return
        gos = self.movable()
        if not gos:
            return
        self.op("Rotate", self.rotate_group, gos, sign * (step if step is not None else self.step()))

    def duplicate_selected(self):
        if not self.model or self.typing():
            return
        gos = self.movable()
        if not gos:
            if self.selected is not None and self.kind(self.selected) == "pack":
                self.add_route()
            return
        m = self.model
        new = self.op("Duplicate", lambda: [m.duplicate(g, mirror=self.kind(g) == "spawn") for g in gos])
        if new:
            self.select_many(new)
            self.changed()
            self.status(f"Duplicated {len(new)} object(s). Drag them into place.", "ok")

    def delete_selected(self):
        m = self.model
        if not m:
            return
        if self.feature_sel is not None and not self.sel:
            self.delete_feature()
            return
        gos = [g for g in self.sel if g in m.go and self.kind(g) != "pack"]
        if not gos:
            return
        if all(not m.active(g) for g in gos):
            def restore():
                for g in gos:
                    m.restore(g, reindex=False)
                m.reindex()
            self.op("Restore", restore)
            self.status(f"Restored {len(gos)} object(s).", "ok")
            return
        active = [g for g in gos if m.active(g)]

        def delete():
            for g in active:
                m.delete(g, reindex=False)
            m.reindex()
        self.op("Delete", delete)
        what = f"'{m.name(active[0])}'" if len(active) == 1 else f"{len(active)} objects"
        self.status(f"Deleted (deactivated) {what}. Undo restores.", "ok")
        if not self.layer("Deleted"):
            self.sel = []
            self.changed()

    # ---- clipboard ---------------------------------------------------

    def copy_selection(self, cut=False):
        m = self.model
        if not m:
            return
        gos = self.movable()
        if not gos:
            self.status("Nothing to copy: select tanks, waypoints or objects.", banner=True)
            return
        pos = [m.world(m.tr_of_go[g])[0] for g in gos]
        cx, cz = sum(p[0] for p in pos) / len(pos), sum(p[2] for p in pos) / len(pos)
        self.clipboard = {"game": self.ctx.root, "scene": m.index, "cut": False, "records": {},
                          "items": [(g, self.kind(g), base_name(m.name(g)), (p[0] - cx, p[2] - cz))
                                    for g, p in zip(gos, pos)]}
        if cut:
            records = {}

            def do():
                for g in gos:
                    if m.active(g):
                        records[g] = m.delete(g, reindex=False)
                m.reindex()
            self.op("Cut", do)
            self.clipboard["cut"] = True
            self.clipboard["records"] = records
            self.sel = []
            self.changed()
        self.status(f"{'Cut' if cut else 'Copied'} {len(gos)} object(s). Paste with Ctrl+V at the mouse "
                    "position or the map centre.", "ok")

    def cut_selection(self):
        self.copy_selection(cut=True)

    def paste_target(self):
        if self.mouse is not None:
            return self.view.s2w(*self.mouse)
        return self.view.cx, self.view.cz

    def paste(self):
        m = self.model
        cb = self.clipboard
        if not m:
            return
        if not cb:
            self.status("The clipboard is empty.", banner=True)
            return
        tx, tz = self.paste_target()
        where = "at the mouse position" if self.mouse is not None else "at the map centre"
        follow = self.follow_var.get()
        same = cb["scene"] == m.index and os.path.normcase(cb["game"]) == os.path.normcase(self.ctx.root)
        skipped, failed = [], []
        if same:
            def do():
                new = []
                for go, kind, name, (dx, dz) in cb["items"]:
                    if go not in m.go:
                        failed.append(name)
                        continue
                    if cb["cut"] and go in cb["records"] and not m.active(go):
                        m.restore(go, cb["records"][go])
                        ng = go
                    else:
                        ng = m.duplicate(go, offset=None, mirror=kind == "spawn")
                        if not m.active(ng):
                            m.restore(ng)
                    m.move_to(m.tr_of_go[ng], tx + dx, tz + dz, follow)
                    new.append(ng)
                return new
            new = self.op("Paste", do)
            cb["cut"] = False
        else:
            self.library._sources.pop(cb["scene"], None)

            def do():
                new = []
                for go, kind, name, (dx, dz) in cb["items"]:
                    if kind != "object":
                        skipped.append(name)
                        continue
                    try:
                        root, _ = import_object(m, self.library, {"scene": cb["scene"], "go": go, "name": name},
                                                tx + dx, tz + dz, follow)
                        new.append(root)
                    except ValueError:
                        failed.append(name)
                return new
            self.busy("Pasting objects from another mission...")
            new = self.op("Paste", do)
            self.idle()
        if new:
            self.select_many(new)
            self.changed()
        msg = f"Pasted {len(new or [])} object(s) {where}."
        if skipped:
            msg += f" {len(skipped)} tank(s)/waypoint(s) skipped: these can only be pasted in their own mission."
        if failed:
            msg += (f" {len(failed)} object(s) not found in the saved source mission"
                    " (save the source mission and copy again).")
        self.status(msg, "ok" if new else "warn", banner=bool(skipped or failed))

    # ---- insert: waypoints, routes, events ------------------------------

    def draw_rubber(self):
        """Dashed guide line from the last waypoint of the route being drawn to the pointer."""
        c = self.canvas
        c.delete("rubber")
        if self.sketch is None or not self.mouse or not self.model:
            return
        s = self.sketch
        if s.get("kind") == "terrain":
            pts = [self.view.w2s(x, z) for x, z in s["points"]]
            if not (self.drag and self.drag.get("mode") == "stroke"):
                pts.append(self.mouse)
            self.draw_stroke_shape(pts, s["brush"], self.brush_colour(s["brush"]), True, "rubber")
            return
        a = self.sketch_after()
        if a is None or a not in self.model.tr:
            return
        p = self.model.world(a)[0]
        ax, ay = self.view.w2s(p[0], p[2])
        c.create_line(ax, ay, *self.mouse, fill="#000000", width=3, dash=(6, 4), tags="rubber")
        c.create_line(ax, ay, *self.mouse, fill="#FFD040", width=1, dash=(6, 4), tags="rubber")

    def target_route(self):
        """(route transform, waypoint to insert after or None) for the selection."""
        m, g = self.model, self.selected
        if m is None or g is None or g not in m.go:
            return None
        k, trp = self.kind(g), m.tr_of_go.get(g)
        if k == "waypoint":
            return m.waypoints[trp], trp
        if k == "pack":
            return trp, None
        if k == "spawn":
            p = m.spawn_pack(m.event_of_go(g))
            return (p, None) if p else None
        return None

    def add_waypoint(self, at=None):
        """One waypoint at `at` after the selected one of the selected route; W
        and the Waypoint tool use it. Without `at`, the Waypoint tool is chosen.
        Returns False when no route is selected."""
        m = self.model
        if not m:
            return False
        if at is None:
            self.use_template(("waypoint",))
            return True
        route = self.target_route()
        if not route:
            self.status("Select a route, one of its waypoints, or a tank with a route first.", "warn", banner=True)
            return False
        pack, after = route
        new = self.op("Add waypoint", m.new_waypoint, pack, after, self.ground_point(*at))
        if new is not None:
            self.select(new)
            kids = [c["m_PathID"] for c in m.tr[pack]["m_Children"]]
            n = kids.index(m.tr_of_go[new]) + 1 if m.tr_of_go[new] in kids else len(kids)
            self.status(f"Added waypoint {n} of {m.name(m.go_of_tr[pack])}.", "ok")
        return True

    def ground_point(self, x, z):
        g = self.model.ground(x, z)
        return (x, 0.0 if g is None else g, z)

    def add_route(self, at=None):
        """New route with its first waypoint at `at`, then drawn on by clicking
        (see start_sketch). Without `at`, the New route tool is chosen."""
        m = self.model
        if not m:
            return
        if at is None:
            self.use_template(("route",))
            return
        start = len(m.undo_stack)
        new = self.op("Add route", m.new_route, self.ground_point(*at))
        if new is None:
            return
        self.select(new)
        self.start_sketch(m.waypoints[m.tr_of_go[new]], start, created=True)

    def assign_route(self, evs, pack):
        """Give the AI tanks (spawn events) a route (pack transform, 0 = none)."""
        m = self.model
        self.op("Set route", lambda: [m.assign_pack(e, pack) for e in evs])
        name = m.name(m.go_of_tr[pack]) if pack else "no route"
        self.status(f"{len(evs)} tank(s) now use {name}.", "ok")

    def new_route_for(self, evs):
        """New route that starts at the (first) tank and is assigned to the
        tanks; then each click adds the next waypoint (Enter, Esc or a
        right-click ends)."""
        m = self.model
        p = m.world(m.tr_of_go[m.event_go[evs[0]]])[0]

        def do():
            wp = m.new_route(self.ground_point(p[0], p[2]))
            pack = m.waypoints[m.tr_of_go[wp]]
            for e in evs:
                m.assign_pack(e, pack)
            return wp
        start = len(m.undo_stack)
        wp = self.op("New route", do)
        if wp is None:
            return
        self.select(wp)
        self.start_sketch(m.waypoints[m.tr_of_go[wp]], start, created=True)

    def continue_route(self, pack):
        """Draw on route `pack`: each click adds a waypoint after the selected
        waypoint of that route (or at the end)."""
        self.start_sketch(pack, len(self.model.undo_stack), created=False)

    def start_sketch(self, pack, start, created):
        """Route drawing, as a line in QGIS / ArcGIS: left click adds a waypoint,
        right-click, double-click or Enter finishes, Backspace removes the last
        waypoint, Esc cancels. `start` is the undo depth before the route's
        first edit (cancel undoes back to it); `created`: the route is new."""
        self.sketch = {"pack": pack, "start": start, "created": created, "last": None}
        self.canvas.config(cursor=self.cursor())
        self.status(self.sketch_hint())
        self.draw_rubber()

    def sketch_after(self):
        """Waypoint transform the next one is added after."""
        m, pack = self.model, self.sketch["pack"]
        trp = m.tr_of_go.get(self.selected) if self.selected in m.go else None
        if trp in m.waypoints and m.waypoints[trp] == pack:
            return trp
        kids = m.tr[pack]["m_Children"] if pack in m.tr else []
        return kids[-1]["m_PathID"] if kids else None

    def sketch_hint(self):
        s = self.sketch
        if s.get("kind") == "terrain":
            n = len(s["points"])
            return (f"Drawing '{s['brush']['name']}': {n} point(s), {length(s['points']):.0f} m. Click or drag to "
                    "continue; right-click, double-click or Enter finishes, Backspace removes the last point, "
                    "Esc cancels.")
        m, pack = self.model, self.sketch["pack"]
        n = len(m.tr[pack]["m_Children"]) if pack in m.tr else 0
        return (f"Route {m.name(m.go_of_tr[pack])}: {n} waypoint(s). Click the next waypoint; right-click, "
                "double-click or Enter finishes, Backspace removes the last, Esc cancels.")

    def sketch_click(self, e):
        s = self.sketch
        if s.get("kind") == "terrain":
            self.terrain_click(e)
            return
        now = getattr(e, "time", 0)
        last = s.get("last")
        if last and 0 <= now - last[2] < DOUBLE_CLICK_MS and abs(e.x - last[0]) < 6 and abs(e.y - last[1]) < 6:
            self.finish_sketch()
            return
        s["last"] = (e.x, e.y, now)
        m, pack = self.model, s["pack"]
        if pack not in m.tr:
            self.sketch = None
            return
        x, z = self.view.s2w(e.x, e.y)
        new = self.op("Add waypoint", m.new_waypoint, pack, self.sketch_after(), self.ground_point(x, z))
        if new is not None:
            self.select(new)
            self.status(self.sketch_hint())
        self.draw_rubber()

    def finish_sketch(self):
        s, self.sketch = self.sketch, None
        self.canvas.delete("rubber")
        self.canvas.config(cursor=self.cursor())
        if s and s.get("kind") == "terrain":
            self.drag = None
            self.add_terrain_stroke(s)
            return
        if s and self.model and s["pack"] in self.model.tr:
            m = self.model
            n = len(m.tr[s["pack"]]["m_Children"])
            self.status(f"Route {m.name(m.go_of_tr[s['pack']])} finished: {n} waypoint(s).", "ok")

    def cancel_sketch(self):
        """Esc: undo the route drawn so far (a new route disappears)."""
        s, self.sketch = self.sketch, None
        self.canvas.delete("rubber")
        self.canvas.config(cursor=self.cursor())
        if s.get("kind") == "terrain":
            self.drag = None
            self.status("Drawing cancelled.")
            return
        m = self.model
        while m is not None and len(m.undo_stack) > s["start"]:
            self.undo()
        self.status("Route drawing cancelled.")

    def sketch_backspace(self):
        """Backspace: remove the last waypoint drawn; the first waypoint of a new
        route cancels the route."""
        s, m = self.sketch, self.model
        if s.get("kind") == "terrain":
            s["points"].pop()
            s["last"] = None
            if s["points"]:
                self.status(self.sketch_hint())
                self.draw_rubber()
            else:
                self.cancel_sketch()
            return
        if len(m.undo_stack) > s["start"] + (1 if s["created"] else 0):
            self.undo()
            self.status(self.sketch_hint())
            self.draw_rubber()
        elif s["created"]:
            self.cancel_sketch()

    def add_event(self):
        m = self.model
        if not m:
            return
        if not self.ensure_library():
            return
        items = [(f"New event          {name}", ("new", t), "#006000")
                 for t, name in sorted(EVENT_TYPES.items()) if t != 0]
        for e in m.events:
            if e in m.spawns:
                continue
            d = m.sc.read(e)
            items.append((f"This mission       {EVENT_TYPES.get(d['Event_Type'], d['Event_Type'])!s:<20} "
                          f"{m.name(m.event_go[e])}", ("here", m.event_go[e]), "#000080"))
        for ent in self.library.events or []:
            if ent["scene"] == m.index:
                continue
            label_ = self.ctx.scene_label(ent["scene"])[:18]
            msg = f"  '{ent['message']}'" if ent.get("message") else ""
            items.append((f"{label_:<18} {EVENT_TYPES.get(ent['type'], ent['type'])!s:<20} {ent['name']}{msg}",
                          ("lib", ent)))
        choice = ListDialog(self.root, "Add event", items, prompt=(
            "Choose a new event, or an event to copy. A copy from this mission keeps its triggers and tanks; "
            "a new event or one from another mission starts without tanks.")).run()
        if not choice:
            return
        where, val = choice
        if where == "new":
            self.busy("Creating the event...", banner=False)
            new = self.op("Add event", self.create_event, val)
            self.idle()
            note = " Choose its trigger tanks before playing: a tank trigger without tanks fires at once."
        elif where == "here":
            new = self.op("Add event", lambda: m.duplicate(val, offset=None, mirror=False))
            note = ""
        else:
            self.busy("Copying the event from another mission...")
            cx, cz = self.view.cx, self.view.cz

            def do():
                root, _ = import_object(m, self.library, val, cx, cz, False)
                return root, reconnect_event_refs(m, root, self.library, val)
            res = self.op("Add event", do)
            self.idle()
            new, note = (res[0], "") if res else (None, "")
            if res and res[1][1]:
                note = f" {res[1][1]} reference(s) to the other mission have no counterpart here."
        if new is not None:
            self.select(new)
            self.show_page("right", "props")
            self.status(f"Added event '{m.name(new)}'. Set its trigger, time and tanks in Properties.{note}",
                        "ok", banner=True)

    def create_event(self, event_type):
        """New event of the given type, built from an event of that type in the
        object library (for sensible type-specific values) or else from any event
        in this mission, with all references and the message cleared."""
        m = self.model
        ent = next((e for e in self.library.events or [] if e["type"] == event_type), None)
        if ent is not None:
            go, _ = import_object(m, self.library, ent, self.view.cx, self.view.cz, False)
        else:
            src = next((e for e in m.events if e not in m.spawns), None) or m.events[0]
            go = m.duplicate(m.event_go[src], offset=None, mirror=False)
            if not m.go[go]["m_IsActive"]:
                m.set_field(go, "m_IsActive", True)
        m.reset_event(m.event_of_go(go), event_type)
        return go

    def prop_tanklist(self, title, ev, key, count_key):
        """Tank list of an event (names) with a button to choose the tanks."""
        m = self.model
        d = m.sc.read(ev)
        self.prop_info(title, self.tank_names(d.get(key) or []))
        r = self._next()
        ttk.Button(self.props, text=f"Choose {title.lower()}...",
                   command=lambda: self.choose_tanks(ev, key, count_key, title)).grid(row=r, column=1, sticky="w",
                                                                                     pady=(0, 4))

    def prop_eventlist(self, title, ev, key, count_key):
        """Event list of an event (names) with a button to choose the events."""
        m = self.model
        owner = {e: m.event_go[e] for e in m.events}
        names = [m.name(owner[r["m_PathID"]]) for r in m.sc.read(ev).get(key) or []
                 if r["m_FileID"] == 0 and r["m_PathID"] in owner]
        self.prop_info(title, summary(names, "none"))
        r = self._next()
        ttk.Button(self.props, text=f"Choose {title.lower()}...",
                   command=lambda: self.choose_events(ev, key, count_key, title)).grid(row=r, column=1, sticky="w",
                                                                                      pady=(0, 4))

    def choose_events(self, ev, key, count_key, title):
        m = self.model
        items = []
        for e in m.events:
            go = m.event_go[e]
            if e == ev or not m.active(go):
                continue
            d = m.sc.read(e)
            state = "" if d.get("m_Enabled", 1) else "disabled at start"
            items.append((f"{m.name(go):<28} {EVENT_TYPES.get(d['Event_Type'], d['Event_Type'])!s:<18} {state}", e))
        chosen = {r["m_PathID"] for r in m.sc.read(ev).get(key) or [] if r["m_FileID"] == 0}
        res = TankListDialog(self.root, title, items, chosen, noun="events").run()
        if res is None:
            return
        refs = [{"m_FileID": 0, "m_PathID": p} for p in res]

        def do():
            d = m.edit(ev)
            d[key] = refs
            if count_key in d:
                d[count_key] = len(refs)
            m._set(ev, d)
        self.op(f"Set {title.lower()}", do)
        self.status(f"{title}: {len(refs)} event(s).", "ok")

    def prop_arealist(self, ev):
        """Trigger areas of an event (names) with a button to choose them."""
        m = self.model
        owner = {pid: g for g, pid in m.areas.items()}
        names = [m.name(owner[r["m_PathID"]]) for r in m.sc.read(ev).get("Trigger_Collider_Scripts") or []
                 if r["m_FileID"] == 0 and r["m_PathID"] in owner]
        self.prop_info("Trigger areas", summary(names, "none"))
        r = self._next()
        ttk.Button(self.props, text="Choose trigger areas...", command=lambda: self.choose_areas(ev)).grid(
            row=r, column=1, sticky="w", pady=(0, 4))

    def choose_areas(self, ev):
        m = self.model
        items = [(f"{m.name(g):<32} {'shown' if m.area_visible(g) else 'hidden'} in game", pid)
                 for g, pid in m.areas.items() if m.active(g)]
        if not items:
            self.status("This mission has no trigger areas. Add one from the Objects pane (Trigger areas).",
                        "warn", banner=True)
            return
        chosen = {r["m_PathID"] for r in m.sc.read(ev).get("Trigger_Collider_Scripts") or [] if r["m_FileID"] == 0}
        res = TankListDialog(self.root, "Trigger areas", items, chosen, noun="trigger areas").run()
        if res is None:
            return
        refs = [{"m_FileID": 0, "m_PathID": p} for p in res]

        def do():
            d = m.edit(ev)
            d["Trigger_Collider_Scripts"] = refs
            if "Trigger_Collider_Num" in d:
                d["Trigger_Collider_Num"] = max(len(refs), 1)
            m._set(ev, d)
        self.op("Set trigger areas", do)
        self.status(f"Trigger areas: {len(refs)}.", "ok")

    def area_rows(self, go):
        """Size and visibility of a trigger area."""
        m = self.model
        self.prop_info("Category", "Trigger area")
        size = m.area_size(go)
        for key, title in (("width", "Width (m)"), ("depth", "Depth (m)"), ("height", "Height (m)"),
                           ("radius", "Radius (m)")):
            if key in size:
                self.prop_entry(title, f"{size[key]:.1f}",
                                lambda s, k=key: self.op("Resize trigger area", m.set_area_size, go,
                                                         **{k: float(s)}))
        self.prop_check("Shown in game (solid block, for testing)", m.area_visible(go),
                        lambda v: self.op("Show trigger area", m.set_area_visible, go, v))
        users = self.referenced_by(go)
        self.prop_info("Used by", ", ".join(users) if users else "(no event)")

    def event_props(self, ev):
        """Properties of an event, built from the description of its type and
        trigger (event_types.py): only fields that do something in the game,
        in the sections Event, What it does, When, Message and Links."""
        m = self.model
        d = m.sc.read(ev)
        t, trig = d["Event_Type"], d["Trigger_Type"]
        types = sorted(et.EVENT_NAMES)
        names = [et.EVENT_NAMES[k] for k in types]
        if t not in types:
            types.append(t)
            names.append(f"Type {t}")
        self.prop_combo("Event type", names, types.index(t),
                        lambda i: self.op("Set event type", m.set_field, ev, "Event_Type", types[i]))
        self.prop_enabled(ev)

        self.prop_heading("What it does", et.EVENT_HELP.get(t))
        if t in et.TARGET_TYPES:
            self.prop_tanklist("Affected tanks", ev, "Target_Tanks", "Target_Num")
        if t == 2:
            self.ai_rows(ev)
        if t == 13:
            self.prop_eventlist("Events to enable", ev, "Disabled_Events", "Disabled_Event_Num")
        if t == 4:
            self.prop_info("Battery", self.ref_name(d.get("Artillery_Script")))
            self.prop_info("Target", self.ref_name(d.get("Artillery_Target")))
        if t == 12:
            self.prop_info("Object", self.ref_name(d.get("Activation_Object")))
        self.schema_rows(ev, et.TYPE_FIELDS.get(t, []))

        self.prop_heading("When", et.TRIGGER_HELP.get(trig))
        trigs = sorted(et.TRIGGER_NAMES)
        if trig not in trigs:
            trigs.append(trig)
        self.prop_combo("Trigger", [et.TRIGGER_NAMES.get(k, f"Type {k}") for k in trigs], trigs.index(trig),
                        lambda i: self.op("Set trigger", m.set_field, ev, "Trigger_Type", trigs[i]))
        if trig == 0:
            self.prop_entry("After (s)", f"{d['Trigger_Time']:g}",
                            lambda s: self.op("Set trigger time", m.set_field, ev, "Trigger_Time", _number(s)),
                            tip="Seconds after the event is enabled")
        if trig == 2:
            self.prop_arealist(ev)
            keys = sorted(et.AREA_TANKS)
            cur = d.get("Trigger_Setting_Type", 0)
            self.prop_combo("Tanks that count", [et.AREA_TANKS[k] for k in keys], keys.index(cur) if cur in keys
                            else -1, lambda i: self.op("Set tanks that count", m.set_field, ev,
                                                       "Trigger_Setting_Type", keys[i]),
                            tip="Hostile and friendly are seen from the player")
        if et.uses_tank_list(d):
            self.prop_tanklist("Trigger tanks", ev, "Trigger_Tanks", "Trigger_Num")
            modes = ["all", "any", "count"] if trig == 1 else ["all", "any"]
            mode, number = et.tanks_needed(d)
            self.prop_combo("Tanks needed", [et.NEEDED[k] for k in modes], modes.index(mode) if mode in modes
                            else 0, lambda i: self.set_tanks_needed(ev, modes[i]))
            if mode == "count" and trig == 1:
                self.prop_entry("Number needed", str(number), lambda s: self.set_tanks_needed(ev, "count", int(s)))

        if et.shows_message(t):
            self.prop_heading("Message")
            text = d.get("Event_Message", "")
            self.prop_entry("Text", text, lambda s: self.set_message(ev, s),
                            tip="The Japanese text is set to the same text")
            n = len(text)
            over = n > et.MESSAGE_LIMIT
            self.prop_info("Length", f"{n} of {et.MESSAGE_LIMIT} characters" + (": may be cut off" if over else ""),
                           fg=WARN_FG if over else "#404040")
            self.schema_rows(ev, et.MESSAGE_FIELDS)

        self.prop_heading("Links")
        self.prop_eventlist("Events to cancel", ev, "Useless_Events", "Useless_Event_Num")
        enabled_by, cancelled_by = self.event_links(ev)
        self.prop_info("Enabled by", summary(enabled_by, "none"))
        self.prop_info("Cancelled by", summary(cancelled_by, "none"))

    def ai_rows(self, ev):
        """New AI settings of a 'Change AI settings' event: all are applied."""
        m = self.model
        d = m.sc.read(ev)
        targets = [r["m_PathID"] for r in d.get("Target_Tanks") or [] if r["m_FileID"] == 0]
        first = next((m.event_of_go(m.go_of_tr[p]) for p in targets if p in m.go_of_tr), None)
        if first is not None:
            r = self._next()
            ttk.Button(self.props, text=f"Take values from {m.name(m.event_go[first])}",
                       command=lambda: self.op("Take AI values", self.take_ai_values, ev, first)).grid(
                row=r, column=1, sticky="w", pady=(0, 4))
        packs = [0] + m.packs
        pnames = ["(none)"] + [m.name(m.go_of_tr[p]) for p in m.packs]
        cur = m.pack_tr(d["New_WayPoint_Pack"]["m_PathID"]) if d["New_WayPoint_Pack"]["m_PathID"] else 0
        self.prop_combo("Route", pnames, packs.index(cur) if cur in packs else 0,
                        lambda i: self.op("Set new route", m.set_field, ev, "New_WayPoint_Pack",
                                          {"m_FileID": 0, "m_PathID": m.go_of_tr[packs[i]] if packs[i] else 0}))
        self.prop_ref_combo("Follow target", [ev], "New_Follow_Target")
        self.schema_rows(ev, et.AI_FIELDS)

    def take_ai_values(self, ev, spawn):
        """Fill the New_ values of a Change AI event from a tank's settings."""
        m = self.model
        s = m.sc.read(spawn)
        d = m.edit(ev)
        for key, *_ in et.AI_FIELDS:
            src = et.ai_source_key(key)
            if key in d and src in s:
                d[key] = s[src]
        m._set(ev, d)

    def schema_rows(self, pid, fields):
        """Rows for event_types field descriptions."""
        m = self.model
        d = m.sc.read(pid)
        for field in fields:
            key, _, kind, _, tip = field
            if key not in d:
                continue
            title, val = et.label(field), d[key]
            if kind == "bool":
                self.prop_check(title, val, lambda v, k=key: self.op(f"Set {k}", m.set_field, pid, k, bool(v)),
                                tip=tip or None)
            elif kind == "int":
                self.prop_entry(title, val, lambda s, k=key: self.op(f"Set {k}", m.set_field, pid, k, int(s)),
                                tip=tip or None)
            elif kind == "float":
                self.prop_entry(title, f"{val:.4g}", lambda s, k=key: self.op(f"Set {k}", m.set_field, pid, k,
                                                                             _number(s)), tip=tip or None)
            else:
                self.prop_entry(title, val, lambda s, k=key: self.op(f"Set {k}", m.set_field, pid, k, s),
                                tip=tip or None)

    def set_tanks_needed(self, ev, mode, number=None):
        m = self.model
        if number is None:
            number = max(1, et.tanks_needed(m.sc.read(ev))[1] or 1)
        fields = et.needed_fields(mode, number)
        self.op("Set tanks needed", lambda: [m.set_field(ev, k, v) for k, v in fields.items()])

    def set_message(self, ev, text):
        m = self.model
        self.op("Set message", lambda: [m.set_field(ev, k, text) for k in ("Event_Message", "Event_Message_JPN")])

    def event_links(self, ev):
        """(names of events that enable ev, names of events that cancel ev)."""
        m = self.model
        enabled, cancelled = [], []
        for e in m.events:
            if e == ev or not m.active(m.event_go[e]):
                continue
            d = m.sc.read(e)
            if any(r["m_FileID"] == 0 and r["m_PathID"] == ev for r in d.get("Disabled_Events") or []):
                enabled.append(m.name(m.event_go[e]))
            if any(r["m_FileID"] == 0 and r["m_PathID"] == ev for r in d.get("Useless_Events") or []):
                cancelled.append(m.name(m.event_go[e]))
        return enabled, cancelled

    def ref_name(self, ref):
        """Name of the object a reference points to, or 'none'."""
        m = self.model
        if not ref or ref.get("m_FileID") or not ref.get("m_PathID"):
            return "none"
        pid = ref["m_PathID"]
        go = pid if pid in m.go else m.go_of_tr.get(pid)
        if go is None and pid in m.sc.all_pids():
            go = m.sc.read(pid, head_only=True).get("m_GameObject", {}).get("m_PathID")
        return m.name(go) if go in m.go else f"object {pid}"

    def prop_enabled(self, ev):
        """'Enabled at start' of an event; a disabled event waits for an Enable
        events event."""
        m = self.model
        d = m.sc.read(ev)
        if "m_Enabled" in d:
            self.prop_check("Enabled at start", bool(d["m_Enabled"]),
                            lambda v: self.op("Set enabled at start", m.set_field, ev, "m_Enabled", 1 if v else 0),
                            tip="Off: the event waits until an 'Enable events' event starts it")

    def choose_tanks(self, ev, key, count_key, title):
        m = self.model
        own = m.event_go.get(ev)
        items = []
        for e in m.spawns:
            go = m.event_go[e]
            if go == own:
                continue
            t = m.tank_entry(e)
            items.append((f"{m.name(go):<24} {self.spawn_side(e):<9} {tank_display(t) if t else ''}",
                          m.tr_of_go[go]))
        chosen = {r["m_PathID"] for r in m.sc.read(ev).get(key) or [] if r["m_FileID"] == 0}
        res = TankListDialog(self.root, title, items, chosen).run()
        if res is None:
            return
        refs = [{"m_FileID": 0, "m_PathID": p} for p in res]

        def do():
            d = m.edit(ev)
            d[key] = refs
            if count_key in d:
                d[count_key] = max(len(refs), 1) if count_key == "Trigger_Num" else len(refs)
            m._set(ev, d)
        self.op(f"Set {title.lower()}", do)
        self.status(f"{title}: {len(refs)} tank(s).", "ok")

    def select_all(self):
        m = self.model
        if not m:
            return
        prim = self.selected
        kind = self.kind(prim) if prim is not None and prim in m.go else "spawn"
        if kind == "waypoint":
            pack = m.waypoints[m.tr_of_go[prim]]
            gos = [m.go_of_tr[c["m_PathID"]] for c in m.tr[pack]["m_Children"] if c["m_PathID"] in m.tr]
            what = f"waypoints of {m.name(m.go_of_tr[pack])}"
        elif kind == "object":
            want = base_name(m.name(prim))
            gos = sorted({m.object_root(g) for g in self.scenery_arrays()[0]
                          if base_name(m.name(m.object_root(g))) == want})
            what = f"objects named {want}"
        else:
            gos = [m.event_go[e] for e in m.spawns if self.spawn_visible(e)]
            what = "visible tank spawns"
        self.select_many(gos)
        self.status(f"Selected {len(gos)} {what}.")

    def add_spawn(self, hostile, at=None):
        """New AI tank of the given side at `at` (default: the view centre),
        copied from a tank of that side (also a deleted one), else from any AI
        tank, else from the player's spawn. Returns the new GameObject."""
        m = self.model
        if not m:
            return None
        side = 1 if hostile else 0
        ai = [e for e in m.spawns if m.sc.read(e)["Tank_ID"] != 1]
        cands = ([e for e in ai if m.active(m.event_go[e]) and m.sc.read(e)["Relationship"] == side]
                 or [e for e in ai if m.sc.read(e)["Relationship"] == side]
                 or ai or list(m.spawns))
        if not cands:
            self.status("This mission has no tank spawn at all to build a tank from.", "warn", banner=True)
            return None
        go = m.event_go[cands[0]]
        cx, cz = at if at is not None else (self.view.cx, self.view.cz)

        def do():
            new = m.copy_spawn(go, side)
            m.move_to(m.tr_of_go[new], cx, cz, self.follow_var.get())
            return new

        new = self.op("Add spawn", do)
        if new is not None:
            self.selected = new
            self.changed()
            self.status(f"Added '{m.name(new)}' (copy of '{m.name(go)}').", "ok")
        return new

    def move_player_here(self, x, z):
        m = self.model
        player = next((e for e in m.spawns if m.sc.read(e)["Tank_ID"] == 1 and m.active(m.event_go[e])), None)
        if player is None:
            self.status("This mission has no player spawn.", "warn", banner=True)
            return
        go = m.event_go[player]
        self.op("Move player", m.move_to, m.tr_of_go[go], x, z, self.follow_var.get())
        self.select(go)

    def undo(self):
        if not self.model:
            return
        what = self.model.undo()
        if what:
            self.sel = [g for g in self.sel if g in self.model.go]
            self.changed()
            self.status(f"Undone: {what}")
        else:
            self.status("Nothing to undo.")

    def select(self, go, frame=False, mode="replace"):
        """Select an object. mode: replace, add (Shift) or toggle (Ctrl)."""
        if not self.model:
            return
        if go is not None:
            self.clear_feature_choice()
        if mode == "replace" or go is None:
            if mode == "replace":
                self.sel = [] if go is None else [go]
        elif mode == "add":
            if go in self.sel:
                self.sel.remove(go)
            self.sel.append(go)
        elif mode == "toggle":
            if go in self.sel:
                self.sel.remove(go)
            else:
                self.sel.append(go)
        if frame and go is not None and self.kind(go) in ("spawn", "waypoint", "object"):
            p, _, _ = self.model.world(self.model.tr_of_go[go])
            self.view.cx, self.view.cz = p[0], p[2]
        self.refresh_props()
        self.sync_table_selection()
        self.request_render()

    def select_many(self, gos, mode="replace"):
        if not self.model:
            return
        gos = list(dict.fromkeys(gos))
        if gos:
            self.clear_feature_choice()
        if mode == "replace":
            self.sel = gos
        elif mode == "add":
            self.sel = [g for g in self.sel if g not in gos] + gos
        else:
            cur = list(self.sel)
            for g in gos:
                if g in cur:
                    cur.remove(g)
                else:
                    cur.append(g)
            self.sel = cur
        self.refresh_props()
        self.sync_table_selection()
        self.request_render()

    def clear_selection(self):
        """Deselect all objects and the selected terrain feature."""
        if not self.model:
            return
        self.clear_feature_choice()
        self.select(None)
        self.status("Selection cleared.")

    def select_parent(self):
        if not self.model or self.selected is None:
            return
        p = self.model.parent_go(self.selected)
        if p is not None:
            self.select(p)
            self.status(f"Selected parent '{self.model.name(p)}'.")

    def frame_selection(self):
        self.frame_gos(self.sel)

    def frame_gos(self, gos):
        m = self.model
        if not m:
            return
        gos = [g for g in gos if g in m.go and g in m.tr_of_go]
        if not gos:
            return
        pts = [m.world(m.tr_of_go[g])[0] for g in gos]
        xs, zs = [p[0] for p in pts], [p[2] for p in pts]
        self.view.cx, self.view.cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        w, h = max(1.0, max(xs) - min(xs)), max(1.0, max(zs) - min(zs))
        if len(gos) > 1:
            self.view.scale = max(0.05, min(40.0, min(self.view.w / (w * 1.3), self.view.h / (h * 1.3))))
        else:
            self.view.scale = max(self.view.scale, 2.0)
        self.request_render()

    def next_spawn(self, step):
        m = self.model
        if not m:
            return
        sp = [m.event_go[e] for e in m.spawns if self.spawn_visible(e)]
        if not sp:
            return
        i = sp.index(self.selected) if self.selected in sp else -1
        self.select(sp[(i + step) % len(sp)], frame=True)

    def pick_event(self):
        m = self.model
        if not m:
            return
        items = []
        for e in m.events:
            d = m.sc.read(e)
            go = m.event_go[e]
            extra = ""
            colour = None
            if e in m.spawns:
                t = m.tank_entry(e)
                extra = f"   {tank_display(t) if t else ''}"
                colour = "#A00000" if d["Relationship"] == 1 else "#0000A0"
            if not m.active(go):
                colour = "#808080"
            items.append((f"{EVENT_TYPES.get(d['Event_Type'], d['Event_Type'])!s:<20} {m.name(go)}{extra}", go, colour))
        go = ListDialog(self.root, "Events in this mission", items, current=self.selected).run()
        if go is not None:
            self.select(go, frame=True)

    def zoom_by(self, f, sx=None, sy=None):
        if not self.model:
            return
        v = self.view
        sx = v.w / 2 if sx is None else sx
        sy = v.h / 2 if sy is None else sy
        wx, wz = v.s2w(sx, sy)
        v.scale = max(0.05, min(40.0, v.scale * f))
        nx, nz = v.s2w(sx, sy)
        v.cx += wx - nx
        v.cz += wz - nz
        self.request_render()

    # ---- mouse and keys ----------------------------------------------

    def hit(self, sx, sy, exact=False):
        m = self.model
        show_hidden = self.layer("Deleted")
        best, bd = None, 1e9
        for go, (x, y) in self.event_marks().items():
            d = max(abs(x - sx), abs(y - sy))
            if d <= EVENT_MARK // 2 + 1 and d < bd:
                best, bd = go, d
        if best is not None:
            return best
        for e in m.spawns:
            if not self.spawn_visible(e):
                continue
            go = m.event_go[e]
            p, _, _ = m.world(m.tr_of_go[go])
            x, y = self.view.w2s(p[0], p[2])
            d = math.hypot(x - sx, y - sy)
            if d < 11 and d < bd:
                best, bd = go, d
        if best is None and self.layer("Waypoints"):
            for trp in m.waypoints:
                go = m.go_of_tr[trp]
                if not show_hidden and not m.active(go):
                    continue
                p, _, _ = m.world(trp)
                x, y = self.view.w2s(p[0], p[2])
                d = math.hypot(x - sx, y - sy)
                if d < 9 and d < bd:
                    best, bd = go, d
        if best is None and self.layer("Scenery"):
            gos, polys, _ = self.scenery_arrays()
            if len(gos):
                wx, wz = self.view.s2w(sx, sy)
                idx = np.nonzero(points_in_polys(polys, wx, wz))[0]
                if len(idx):
                    best = gos[idx[int(np.argmin(poly_areas(polys[idx])))]]
                    if not exact:
                        best = m.object_root(best)
        return best

    def add_click(self, e):
        """Add tool: place one of the chosen item at the click."""
        kind, value, _ = self.add_item
        x, z = self.view.s2w(e.x, e.y)
        if kind == "template":
            self.create_at(value, x, z)
        else:
            self.place_object(value, x, z)

    def on_press(self, e):
        self.canvas.focus_set()
        if not self.model:
            return
        if self.space_down:
            self.on_pan_start(e)
            return
        if self.sketch is not None:
            self.sketch_click(e)
            return
        if self.tool == "add":
            self.add_click(e)
            return
        if self.tool == "feature":
            self.start_terrain_sketch(e)
        elif self.tool == "vertex":
            self.vertex_press(e)
        elif self.tool == "pan":
            self.on_pan_start(e)
        elif self.tool == "zoomin":
            self.drag = {"mode": "zoombox", "start": (e.x, e.y)}
        elif self.tool == "zoomout":
            self.zoom_by(0.5, e.x, e.y)
        elif self.tool == "rotate":
            self.start_rotate(e)
        else:
            self.start_select(e)

    def start_select(self, e):
        mode = "toggle" if e.state & CTRL_MASK else ("add" if e.state & SHIFT_MASK else "replace")
        target = self.hit(e.x, e.y, exact=bool(e.state & ALT_MASK))
        if target is None:
            self.drag = {"mode": "boxsel", "start": (e.x, e.y), "sel_mode": mode, "exact": bool(e.state & ALT_MASK)}
            return
        if mode != "replace":
            self.select(target, mode=mode)
            self.drag = None
            return
        if target not in self.sel:
            self.select(target)
        m = self.model
        wx, wz = self.view.s2w(e.x, e.y)
        items = []
        for g in self.movable():
            p, _, _ = m.world(m.tr_of_go[g])
            items.append((m.tr_of_go[g], (p[0] - wx, p[2] - wz)))
        self.drag = {"mode": "move", "items": items, "start": (e.x, e.y), "begun": False}

    def finish_box_select(self, d, x1, y1):
        m, v = self.model, self.view
        x0, y0 = d["start"]
        if abs(x1 - x0) < 4 and abs(y1 - y0) < 4:
            if d["sel_mode"] == "replace" and (self.sel or self.feature_sel is not None):
                self.clear_selection()
            return
        lx, hx, ly, hy = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)

        def inside(px, pz):
            sx, sy = v.w2s(px, pz)
            return lx <= sx <= hx and ly <= sy <= hy
        found = []
        for e in m.spawns:
            if self.spawn_visible(e):
                p, _, _ = m.world(m.tr_of_go[m.event_go[e]])
                if inside(p[0], p[2]):
                    found.append(m.event_go[e])
        if self.layer("Waypoints"):
            for trp in m.waypoints:
                g = m.go_of_tr[trp]
                if (self.layer("Deleted") or m.active(g)) and inside(*m.world(trp)[0][[0, 2]]):
                    found.append(g)
        if self.layer("Scenery"):
            gos, polys, _ = self.scenery_arrays()
            if len(gos):
                cen = polys.mean(1)
                sx = v.w / 2 + (cen[:, 0] - v.cx) * v.scale
                sy = v.h / 2 - (cen[:, 1] - v.cz) * v.scale
                idx = np.nonzero((sx >= lx) & (sx <= hx) & (sy >= ly) & (sy <= hy))[0]
                roots = [gos[i] if d["exact"] else m.object_root(gos[i]) for i in idx]
                found += list(dict.fromkeys(roots))
        self.select_many(found, d["sel_mode"])
        self.status(f"{len(self.sel)} object(s) selected.")

    def start_rotate(self, e):
        if not self.movable():
            target = self.hit(e.x, e.y, exact=bool(e.state & ALT_MASK))
            if target is None or self.kind(target) == "pack":
                self.status("Rotate tool: select objects first, then drag around them.")
                return
            self.select(target)
        m = self.model
        gos = self.movable()
        base = {g: (m.world(m.tr_of_go[g])[0].copy(), heading_of(m.world(m.tr_of_go[g])[1])) for g in gos}
        wx = sum(b[0][0] for b in base.values()) / len(base)
        wz = sum(b[0][2] for b in base.values()) / len(base)
        cx, cy = self.view.w2s(wx, wz)
        self.drag = {"mode": "rotate", "gos": gos, "base": base, "centre": (cx, cy), "begun": False,
                     "a0": math.degrees(math.atan2(e.x - cx, -(e.y - cy)))}

    def on_pan_start(self, e):
        self.canvas.focus_set()
        self.drag = {"mode": "pan", "start": (e.x, e.y), "c": (self.view.cx, self.view.cz), "moved": False}

    def on_motion(self, e):
        d = self.drag
        if not d or not self.model:
            return
        mode = d["mode"]
        if mode == "stroke":
            self.stroke_motion(e)
            return
        if mode in ("fvertex", "fmove"):
            self.feature_drag_motion(e)
            return
        if mode == "pan":
            if math.hypot(e.x - d["start"][0], e.y - d["start"][1]) >= 4:
                d["moved"] = True
            self.view.cx = d["c"][0] - (e.x - d["start"][0]) / self.view.scale
            self.view.cz = d["c"][1] + (e.y - d["start"][1]) / self.view.scale
            self.request_render()
        elif mode in ("zoombox", "boxsel"):
            self.canvas.delete("rubber")
            x0, y0 = d["start"]
            if mode == "boxsel":
                self.canvas.create_rectangle(x0, y0, e.x, e.y, outline=SEL_BG, fill="", width=1, tags="rubber")
            else:
                self.canvas.create_rectangle(x0, y0, e.x, e.y, outline="#000000", dash=(4, 3), tags="rubber")
        elif mode == "rotate":
            cx, cy = d["centre"]
            if math.hypot(e.x - cx, e.y - cy) < 6:
                return
            if not d["begun"]:
                self.model.begin("Rotate")
                d["begun"] = True
            a = math.degrees(math.atan2(e.x - cx, -(e.y - cy)))
            delta = a - d["a0"]
            if e.state & SHIFT_MASK:
                delta = round(delta / self.step()) * self.step()
            self.rotate_group(d["gos"], delta, d["base"])
            self.status(f"Rotate: {((delta + 180) % 360) - 180:+.1f} deg")
            self.request_render()
        elif mode == "move":
            if not d["begun"]:
                if math.hypot(e.x - d["start"][0], e.y - d["start"][1]) < 4:
                    return
                if not d["items"]:
                    self.drag = None
                    return
                self.model.begin("Move")
                d["begun"] = True
            wx, wz = self.view.s2w(e.x, e.y)
            for trp, (ox, oz) in d["items"]:
                self.model.move_to(trp, wx + ox, wz + oz, self.follow_var.get())
            self.request_render()
        self.on_hover(e)

    def on_release(self, e):
        d = self.drag
        if d and d["mode"] == "stroke":
            self.drag = None
            return
        if d and d["mode"] in ("fvertex", "fmove"):
            self.feature_drag_release()
            return
        self.drag = None
        if not d:
            return
        if d["mode"] == "boxsel":
            self.canvas.delete("rubber")
            self.finish_box_select(d, e.x, e.y)
        elif d["mode"] == "pan" and getattr(e, "num", 1) == 3 and not d.get("moved"):
            self.context_menu(e)
        elif d["mode"] == "zoombox":
            self.canvas.delete("rubber")
            x0, y0 = d["start"]
            if abs(e.x - x0) < 6 or abs(e.y - y0) < 6:
                self.zoom_by(2.0, e.x, e.y)
            else:
                v = self.view
                wa, wb = v.s2w(x0, y0), v.s2w(e.x, e.y)
                v.cx, v.cz = (wa[0] + wb[0]) / 2, (wa[1] + wb[1]) / 2
                v.scale = max(0.05, min(40.0, min(v.w / abs(wb[0] - wa[0]), v.h / abs(wb[1] - wa[1]))))
                self.request_render()
        elif d.get("begun"):
            self.model.commit()
            self.changed()

    def context_menu(self, e):
        """Right-click menu (Windows standard): selects the object under the
        pointer first when it is not selected yet. While a route is drawn, a
        right-click finishes it instead."""
        if not self.model:
            return
        if self.sketch is not None:
            self.finish_sketch()
            return
        if self.tool == "vertex":
            self.feature_menu(e)
            return
        stop = self.tool_title() if self.tool != "select" else None
        target = self.hit(e.x, e.y, exact=bool(e.state & ALT_MASK))
        if target is not None and target not in self.sel:
            self.select(target)
        self.mouse = (e.x, e.y)
        has_sel = bool(self.movable())
        menu = tk.Menu(self.root, tearoff=0)
        entries = [("Cut", self.cut_selection, "Ctrl+X", "cut", has_sel),
                   ("Copy", self.copy_selection, "Ctrl+C", "copy", has_sel),
                   ("Paste", self.paste, "Ctrl+V", "paste", bool(self.clipboard)),
                   ("Delete", self.delete_selected, "Del", "delete", bool(self.sel)), None,
                   ("Duplicate", self.duplicate_selected, "Ctrl+D", "dup", has_sel),
                   ("Rotate left", lambda: self.rotate_selected(-1), "Q", "rotl", has_sel),
                   ("Rotate right", lambda: self.rotate_selected(1), "E", "rotr", has_sel), None,
                   ("Select parent", self.select_parent, "P", "parent", self.selected is not None),
                   ("Select all", self.select_all, "Ctrl+A", None, True),
                   ("Clear selection", self.clear_selection, "Esc", "clearsel",
                    bool(self.sel) or self.feature_sel is not None),
                   ("Zoom to selection", self.frame_selection, "F", "zoomsel", bool(self.sel))]
        for en in entries:
            if en is None:
                menu.add_separator()
                continue
            text, cmd, acc, icon, enabled = en
            menu.add_command(label=text, command=cmd, accelerator=acc, compound="left",
                             image=self.image(icon) if icon else "", state="normal" if enabled else "disabled")
        wx, wz = self.view.s2w(e.x, e.y)
        add = tk.Menu(menu, tearoff=0)
        events = tk.Menu(add, tearoff=0)
        for group, text, icon, key in self.create_items():
            target = events if group == "Events" else add
            enabled = key != ("waypoint",) or bool(self.target_route())
            target.add_command(label=text, compound="left", image=self.legend(icon),
                               state="normal" if enabled else "disabled",
                               command=lambda k=key: self.create_at(k, wx, wz))
            if key == ("waypoint",):
                add.add_cascade(label="Event", menu=events, compound="left", image=self.legend(("event", 1)))
                add.add_separator()
        add.add_command(label="Move player here", command=lambda: self.move_player_here(wx, wz))
        menu.insert_cascade(0, label="Add here", menu=add, compound="left",
                            image=self.legend(("swatch", "triangle", COL_HOSTILE)))
        menu.insert_separator(1)
        m = self.model
        tanks = [m.event_of_go(g) for g in self.sel if g in m.go and self.kind(g) == "spawn"]
        tanks = [e for e in tanks if m.sc.read(e)["Tank_ID"] != 1]
        if tanks:
            routes = tk.Menu(menu, tearoff=0)
            current = {m.spawn_pack(e) for e in tanks}
            self._route_var = tk.IntVar(value=next(iter(current)) if len(current) == 1 else -1)
            routes.add_radiobutton(label="(none)", variable=self._route_var, value=0,
                                   command=lambda: self.assign_route(tanks, 0))
            for p in m.packs:
                routes.add_radiobutton(label=m.name(m.go_of_tr[p]), variable=self._route_var, value=p,
                                       command=lambda pk=p: self.assign_route(tanks, pk))
            routes.add_separator()
            routes.add_command(label="New route from here", command=lambda: self.new_route_for(tanks),
                               compound="left", image=self.legend(("swatch", "route", PACK_COLOURS[0])))
            menu.insert_cascade(2, label=f"Route ({len(tanks)} tank{'s' if len(tanks) > 1 else ''})",
                                menu=routes, compound="left", image=self.legend(("swatch", "route", PACK_COLOURS[0])))
            menu.insert_separator(3)
        if stop:
            menu.insert_separator(0)
            menu.insert_command(0, label=f"Stop {stop}", command=self.stop_tool, accelerator="Esc")
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def feature_menu(self, e):
        """Right-click menu of the Edit terrain features tool."""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Stop {self.tool_title()}", command=self.stop_tool, accelerator="Esc")
        hit = self.feature_hit(e.x, e.y)
        if hit is not None and hit != self.feature_sel:
            self.select_feature(hit)
        f = self.features()
        s = f.stroke(self.feature_sel) if f is not None and self.feature_sel is not None else None
        if s is not None:
            i = self.feature_vertex_at(s, e.x, e.y)
            menu.add_separator()
            menu.add_command(label="Remove point", command=lambda: self.remove_feature_point(s["id"], i),
                             state="normal" if i is not None and len(s["points"]) > 1 else "disabled")
            menu.add_command(label="Delete terrain feature", command=self.delete_feature, accelerator="Del")
            menu.add_command(label="Clear selection", command=self.clear_selection, compound="left",
                             image=self.image("clearsel"))
            menu.add_command(label="Zoom to terrain feature", command=lambda: self.frame_feature(s["id"]))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def on_wheel(self, e):
        self.zoom_by(1.2 ** (e.delta / 120), e.x, e.y)

    def draw_brush(self, x, y):
        c = self.canvas
        c.delete("brush")
        if self.tool != "feature" or not self.model:
            return
        r = max(3.0, self.brush["width"] / 2 * self.view.scale)
        c.create_oval(x - r, y - r, x + r, y + r, outline="#000000", width=4, tags="brush")
        c.create_oval(x - r, y - r, x + r, y + r, outline=self.brush_colour(self.brush), width=2, tags="brush")

    def on_hover(self, e):
        self.mouse = (e.x, e.y)
        self.draw_brush(e.x, e.y)
        if self.sketch is not None:
            self.draw_rubber()
        if not self.model:
            return
        wx, wz = self.view.s2w(e.x, e.y)
        g = self.model.ground(wx, wz)
        gtxt = f"   (ground {g:.1f})" if g is not None else ""
        self.coord_lbl.config(text=f"{wx:.2f}  {wz:.2f} Meters{gtxt}")

    def on_space(self, e):
        """Space held: temporary pan in every tool (as in QGIS)."""
        if self.typing():
            return None
        down = str(e.type) in ("KeyPress", "2")
        if down != self.space_down:
            self.space_down = down
            self.canvas.config(cursor=self.cursor())
        return "break"

    def on_key(self, e):
        if not self.model or self.typing():
            return
        k = e.keysym.lower()
        if k == "f5":
            self.start_game()
            return
        if self.sketch is not None and k in ("return", "kp_enter"):
            self.finish_sketch()
            return
        if self.sketch is not None and k == "backspace":
            self.sketch_backspace()
            return
        if e.state & 0x4:
            return
        shift = bool(e.state & SHIFT_MASK)
        if k == "delete":
            if not shift:
                self.delete_selected()
        elif k in ("q", "e"):
            self.rotate_selected(1 if k == "e" else -1, 1.0 if shift else None)
        elif k == "p":
            self.select_parent()
        elif k == "f":
            self.frame_selection()
        elif k == "home":
            self.fit_view()
        elif k == "t":
            self.toggle_follow()
        elif k == "l":
            self.pick_event()
        elif k == "o":
            self.choose_scene()
        elif k == "escape":
            if self.sketch is not None:
                self.cancel_sketch()
            elif self.drag and self.drag.get("mode") in ("boxsel", "zoombox"):
                self.drag = None
                self.canvas.delete("rubber")
            elif self.feature_sel is not None:
                self.select_feature(None)
            elif self.tool != "select":
                self.stop_tool()
            else:
                self.clear_selection()
        elif k == "w":
            if self.mouse is not None:
                self.add_waypoint(self.view.s2w(*self.mouse))
            else:
                self.add_waypoint()
        elif k in ("plus", "equal", "kp_add"):
            self.zoom_by(1.25)
        elif k in ("minus", "kp_subtract"):
            self.zoom_by(0.8)

    def quit(self):
        if self.guard_unsaved():
            self.stop_library_build()
            self.root.destroy()

    # ---- rendering ---------------------------------------------------

    def request_render(self):
        if not self._render_pending:
            self._render_pending = True
            self.root.after_idle(self._render)

    def scenery_arrays(self):
        m = self.model
        cats = tuple(self.layer(c) for c in CATEGORY_COLOURS)
        key = (id(m), m.foot_version, self.layer("Deleted"), cats)
        if self._scenery_cache[0] == key:
            return self._scenery_cache[1]
        gos, colours = [], []
        for g in m.foot:
            if not self.layer("Deleted") and not m.active(g):
                continue
            cat = category(m.name(g))
            if not self.layer(cat):
                continue
            gos.append(g)
            colours.append(CATEGORY_COLOURS[cat])
        polys = np.array([m.foot[g] for g in gos]) if gos else np.zeros((0, 4, 2))
        areas = poly_areas(polys) if len(gos) else np.zeros(0)
        active = np.array([m.active(g) for g in gos], dtype=bool)
        result = (gos, polys, (colours, areas, active))
        self._scenery_cache = (key, result)
        return result

    def terrain_display_image(self):
        """The terrain image with the optional elevation tint and contour lines."""
        base = self._terrain_img
        elev, cont = self.layer("Elevation"), self.layer("Contours")
        t = self.model.terrain if self.model else None
        if not (elev or cont) or t is None or base is None:
            return base
        key = (id(base), self._terrain_rev, elev, cont)
        if self._terrain_view[0] == key:
            return self._terrain_view[1]
        n = t.image_n
        step = max(1, (t.res - 1) // n)
        h = t.heights[::-1, :][: n * step: step, : n * step: step][:n, :n].astype(float)
        if h.shape != (n, n):
            h = np.pad(h, ((0, n - h.shape[0]), (0, n - h.shape[1])), mode="edge")
        arr = np.asarray(base, dtype=float)
        lo, hi = float(h.min()), float(h.max())
        if elev:
            f = (h - lo) / max(hi - lo, 1e-6)
            stops = [0.0, 0.35, 0.7, 1.0]
            ramp = np.stack([np.interp(f, stops, ch) for ch in ((40, 90, 200, 250), (90, 160, 180, 250),
                                                                 (160, 80, 110, 250))], axis=-1)
            arr = arr * 0.45 + ramp * 0.55
        if cont:
            span = max(hi - lo, 0.01)
            interval = next((iv for iv in (0.5, 1, 2, 5, 10, 20, 50) if span / iv <= 40), 100)
            self._contour_interval = interval
            for every, dark in ((interval, 0.55), (interval * 5, 0.2)):
                q = np.floor(h / every)
                edge = np.zeros(q.shape, dtype=bool)
                edge[:-1, :] |= q[:-1, :] != q[1:, :]
                edge[:, :-1] |= q[:, :-1] != q[:, 1:]
                arr[edge] *= dark
            self.refresh_toc_counts(force=True)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        self._terrain_view = (key, img)
        return img

    def render_base(self):
        m, v = self.model, self.view
        img = Image.new("RGB", (v.w, v.h), WORKSPACE)
        terrain_img = self.terrain_display_image() if self.model else None
        if self.layer("Terrain") and terrain_img is not None:
            t = m.terrain
            x0, y0 = v.w2s(t.origin[0], t.origin[2] + t.size_z)
            x1, y1 = v.w2s(t.origin[0] + t.size_x, t.origin[2])
            cx0, cy0 = max(0.0, x0), max(0.0, y0)
            cx1, cy1 = min(float(v.w), x1), min(float(v.h), y1)
            if cx1 > cx0 and cy1 > cy0:
                iw, ih = terrain_img.size
                sx, sy = iw / (x1 - x0), ih / (y1 - y0)
                box = ((cx0 - x0) * sx, (cy0 - y0) * sy, (cx1 - x0) * sx, (cy1 - y0) * sy)
                size = (max(1, int(round(cx1 - cx0))), max(1, int(round(cy1 - cy0))))
                part = terrain_img.resize(size, Image.BILINEAR, box=box)
                img.paste(part, (int(round(cx0)), int(round(cy0))))
        if self.layer("NavMesh") and self.model:
            over = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(over)
            for pts, area in self.navmesh_polygons():
                spts = [v.w2s(x, z) for x, z in pts]
                xs, ys = [p[0] for p in spts], [p[1] for p in spts]
                if max(xs) < 0 or min(xs) > v.w or max(ys) < 0 or min(ys) > v.h:
                    continue
                fill = (60, 200, 255, 70) if area == 0 else (255, 200, 60, 90)
                draw.polygon(spts, fill=fill, outline=(20, 120, 200, 160))
            img = Image.alpha_composite(img.convert("RGBA"), over).convert("RGB")
        if self.layer("Scenery"):
            gos, polys, extra = self.scenery_arrays()
            if len(gos):
                colours, areas, active = extra
                s = np.empty_like(polys)
                s[:, :, 0] = v.w / 2 + (polys[:, :, 0] - v.cx) * v.scale
                s[:, :, 1] = v.h / 2 - (polys[:, :, 1] - v.cz) * v.scale
                mn, mx = s.min(1), s.max(1)
                vis = (mx[:, 0] >= 0) & (mn[:, 0] <= v.w) & (mx[:, 1] >= 0) & (mn[:, 1] <= v.h)
                order = np.nonzero(vis)[0]
                order = order[np.argsort(-areas[order])]
                draw = ImageDraw.Draw(img)
                for i in order:
                    if colours[i] == AREA_COLOUR:
                        continue
                    col = colours[i] if active[i] else COL_HIDDEN
                    ext = mx[i] - mn[i]
                    pts = [tuple(p) for p in s[i]]
                    if ext[0] < 2.5 and ext[1] < 2.5:
                        x, y = pts[0]
                        draw.rectangle((x, y, x + 1, y + 1), fill=col)
                    elif areas[i] > LARGE_AREA:
                        draw.polygon(pts, outline=col)
                    elif ext[0] > 8 or ext[1] > 8:
                        draw.polygon(pts, fill=col, outline=tuple(c * 3 // 5 for c in col))
                    else:
                        draw.polygon(pts, fill=col)
        return img

    def _render(self):
        self._render_pending = False
        c = self.canvas
        v = self.view
        v.w, v.h = max(50, c.winfo_width()), max(50, c.winfo_height())
        c.delete("overlay")
        if not self.model:
            c.itemconfig(self._base_item, image="")
            c.create_text(v.w // 2, v.h // 2, text="No mission open. Double-click a mission in the Catalog.",
                          font=UI_FONT, fill="#000000", tags="overlay")
            return
        m = self.model
        key = (v.key(), id(m), m.foot_version, tuple(sorted(self.layers.items())))
        if key != self._base_key:
            self._photo = ImageTk.PhotoImage(self.render_base())
            c.itemconfig(self._base_item, image=self._photo)
            self._base_key = key
        self.draw_overlay()
        c.tag_raise("rubber")
        self.zoom_lbl.config(text=f"1 : {max(1, round(3779.5 / max(v.scale, 1e-6))):,}".replace(",", " "))

    def draw_overlay(self):
        c, m, v = self.canvas, self.model, self.view
        show_hidden = self.layer("Deleted")
        sel = self.selected if self.selected in m.go else None
        sel_ev = m.event_of_go(sel) if sel is not None else None
        sel_pack = None
        if sel_ev is not None and sel_ev in m.spawns:
            sel_pack = m.spawn_pack(sel_ev)
        elif sel is not None:
            trp = m.tr_of_go.get(sel)
            sel_pack = m.waypoints.get(trp, trp if trp in m.packs else None)

        users = {}
        for e in m.spawns:
            if self.spawn_visible(e):
                p = m.spawn_pack(e)
                if p:
                    users.setdefault(p, []).append(e)
        if self.layer("Waypoints"):
            for pi, pack in enumerate(m.packs):
                col = hexc(PACK_COLOURS[pi % len(PACK_COLOURS)])
                pts = []
                for ch in m.tr[pack]["m_Children"]:
                    trp = ch["m_PathID"]
                    if trp not in m.tr:
                        continue
                    if not show_hidden and not m.active(m.go_of_tr[trp]):
                        continue
                    p, _, _ = m.world(trp)
                    pts.append(v.w2s(p[0], p[2]))
                chosen = pack == sel_pack
                if len(pts) > 1:
                    flat = [q for pt in pts for q in pt]
                    c.create_line(*flat, fill="#000000", width=5 if chosen else 3, tags="overlay")
                    c.create_line(*flat, fill=col, width=3 if chosen else 1, tags="overlay")
                r = 7 if chosen else 5
                for i, (x, y) in enumerate(pts):
                    c.create_polygon(x, y - r, x + r, y, x, y + r, x - r, y, fill=col, outline="#000000",
                                     tags="overlay")
                    if chosen or v.scale > 1.5:
                        c.create_text(x + r + 2, y - r - 2, text=str(i + 1), anchor="sw", fill=col,
                                      font=MAP_FONT, tags="overlay")
                if pts and (chosen or v.scale > 0.6):
                    c.create_text(pts[0][0] + 10, pts[0][1] + 6, text=m.name(m.go_of_tr[pack]), anchor="nw",
                                  fill=col, font=MAP_FONT, tags="overlay")
                if pts:
                    for e in users.get(pack, []):
                        if e != sel_ev and not self.layer("RouteLinks"):
                            continue
                        sp, _, _ = m.world(m.tr_of_go[m.event_go[e]])
                        sx, sy = v.w2s(sp[0], sp[2])
                        near = min(pts, key=lambda q: (q[0] - sx) ** 2 + (q[1] - sy) ** 2)
                        c.create_line(sx, sy, near[0], near[1], fill=col, width=3 if e == sel_ev else 2,
                                      dash=(6, 4), tags="overlay")

        self.draw_features()
        if self.layer("Scenery") and self.layer("Trigger areas"):
            col = hexc(AREA_COLOUR)
            for g in m.areas:
                if not m.active(g) and not show_hidden:
                    continue
                shape = m.area_shape(g)
                if shape is None:
                    continue
                width = 3 if g == sel else 2
                if shape[0] == "box":
                    pts = [q for x, z in shape[1] for q in v.w2s(x, z)]
                    c.create_polygon(*pts, fill="", outline=col, width=width, dash=(8, 4), tags="overlay")
                    cx, cy = v.w2s(*shape[1].mean(0))
                else:
                    cx, cy = v.w2s(*shape[1])
                    r = shape[2] * v.scale
                    c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=col, width=width, dash=(8, 4),
                                  tags="overlay")
                if g == sel or v.scale > 0.4:
                    c.create_text(cx, cy, text=m.name(g), fill=col, font=MAP_FONT, tags="overlay")

        for e in m.spawns:
            if not self.spawn_visible(e):
                continue
            go = m.event_go[e]
            p, r, _ = m.world(m.tr_of_go[go])
            x, y = v.w2s(p[0], p[2])
            hd = math.radians(heading_of(r))
            col = hexc(self.spawn_colour(e))
            fx, fy = math.sin(hd), -math.cos(hd)
            px, py = -fy, fx
            c.create_polygon(x + fx * 12, y + fy * 12, x - fx * 7 + px * 7, y - fy * 7 + py * 7,
                             x - fx * 7 - px * 7, y - fy * 7 - py * 7, fill=col, outline="#000000",
                             tags="overlay")
            if m.sc.read(e)["Is_Mission_Target"]:
                c.create_oval(x - 14, y - 14, x + 14, y + 14, outline=hexc(COL_GOLD), tags="overlay")
            if v.scale > 0.9 or go == sel:
                t = m.tank_entry(e)
                c.create_text(x + 12, y + 6, text=f"{m.name(go)}  {tank_display(t) if t else ''}",
                              anchor="nw", fill=col, font=MAP_FONT, tags="overlay")

        for go, (x, y) in self.event_marks().items():
            ev = m.event_of_go(go)
            d = m.sc.read(ev)
            c.create_image(x, y, image=self.event_image(d["Event_Type"], not m.active(go)), tags="overlay")
            if go in self.sel:
                h = EVENT_MARK // 2 + 3
                c.create_rectangle(x - h, y - h, x + h, y + h, outline="#00FFFF", width=2, tags="overlay")
            if v.scale > 0.9 or go == sel:
                c.create_text(x + EVENT_MARK // 2 + 3, y + 4, text=m.name(go), anchor="nw", fill="#FFFFFF",
                              font=MAP_FONT, tags="overlay")

        for g_sel in [g for g in self.sel if g in m.go]:
            trp = m.tr_of_go.get(g_sel)
            if trp is None or self.kind(g_sel) == "event":
                continue
            for p in m.subtree(trp):
                g = m.go_of_tr[p]
                if g in m.foot:
                    flat = [q for x, z in m.foot[g] for q in v.w2s(x, z)]
                    c.create_polygon(*flat, fill="", outline="#00FFFF", width=2, tags="overlay")
            p, r, _ = m.world(trp)
            x, y = v.w2s(p[0], p[2])
            rad = 16 if g_sel == sel else 12
            c.create_oval(x - rad, y - rad, x + rad, y + rad, outline="#00FFFF", width=2, tags="overlay")
            if self.tool == "rotate":
                hd = math.radians(heading_of(r))
                c.create_line(x, y, x + math.sin(hd) * 40, y - math.cos(hd) * 40, fill="#00FFFF",
                              width=2, arrow="last", tags="overlay")

        bar_m = 10 ** math.floor(math.log10(max(1e-3, 150 / v.scale)))
        bar_px = bar_m * v.scale
        x0, x1, y = v.w - 30 - bar_px, v.w - 30, v.h - 14
        c.create_rectangle(x0 - 4, y - 18, x1 + 4, y + 5, fill="#FFFFFF", outline="#000000", tags="overlay")
        c.create_line(x0, y, x1, y, fill="#000000", width=2, tags="overlay")
        for xx in (x0, x1):
            c.create_line(xx, y - 4, xx, y + 3, fill="#000000", tags="overlay")
        c.create_text(x0, y - 4, text=f"{bar_m:g} m", anchor="sw", fill="#000000", font=MAP_FONT, tags="overlay")
        c.create_rectangle(v.w - 30, 6, v.w - 8, 28, fill="#FFFFFF", outline="#000000", tags="overlay")
        c.create_text(v.w - 19, 17, text="N", fill="#000000", font=UI_BOLD, tags="overlay")

    # ---- properties panel --------------------------------------------

    def refresh_props(self):
        for w in self.props.winfo_children():
            w.destroy()
        self._row = 0
        m = self.model
        go = self.selected
        if not m:
            self.prop_label("No mission open.")
            return
        sel = [g for g in self.sel if g in m.go]
        f = getattr(m, "terrain_features", None)
        if not sel and self.feature_sel is not None and f is not None and f.stroke(self.feature_sel):
            self.feature_props(f.stroke(self.feature_sel))
            return
        if len(sel) > 1:
            self.multi_props(sel)
            return
        if go is None or go not in m.go:
            self.mission_props()
            return
        kind = self.kind(go)
        head = kind.upper()
        if not m.active(go):
            head += "   (deleted - Delete restores)"
        tk.Label(self.props, text=head, font=UI_BOLD, anchor="w", padx=2).grid(
            row=self._next(), column=0, columnspan=2, sticky="ew")
        self.prop_sep()
        self.prop_entry("Name", m.name(go), lambda s: self.op("Rename", m.rename, go, s))

        if kind == "spawn":
            ev = m.event_of_go(go)
            names = [tank_display(t) for t in m.tanks]
            cur = m.tank_entry(ev)
            self.prop_combo("Tank", names, m.tanks.index(cur) if cur in m.tanks else -1,
                            lambda i: self.op("Set tank", m.set_tank, ev, m.tanks[i]))
            key = m.sc.read(ev)["Key_Name"]
            self.prop_entry("Unit group", key, lambda s: self.op("Set unit group", m.set_field, ev, "Key_Name", s))
            if key:
                same = [e for e in m.spawns if m.sc.read(e)["Key_Name"] == key and m.active(m.event_go[e])]
                kinds = {m.sc.read(e)["Tank_Prop"]["m_PathID"] for e in same}
                note = f"{len(same)} unit(s) in this group"
                if len(kinds) > 1:
                    note += "; mixed tank types - the briefing screen sets one type per group"
                self.prop_info("Group", note)
            self.transform_rows(go)
            packs = [0] + m.packs
            pnames = ["(none)"] + [m.name(m.go_of_tr[p]) for p in m.packs]
            curp = m.spawn_pack(ev)
            self.prop_combo("Waypoint pack", pnames, packs.index(curp) if curp in packs else 0,
                            lambda i: self.op("Set waypoint pack", m.assign_pack, ev, packs[i]))
            refs = self.referenced_by(go)
            if refs:
                self.prop_info("Referenced by", ", ".join(refs))
            self.prop_ref_combo("Follow target", [ev], "Follow_Target", exclude=go)
            self.prop_ref_combo("Commander", [ev], "Commander", exclude=go)
            self.prop_sep()
            self.prop_enabled(ev)
            self.field_rows(ev, SPAWN_FIELDS)
            self.prop_sep()
            self.prop_tanklist("Trigger tanks", ev, "Trigger_Tanks", "Trigger_Num")
            if m.sc.read(ev)["Trigger_Type"] == 2:
                self.field_rows(ev, AREA_FIELDS)
                self.prop_arealist(ev)
        elif kind == "event":
            self.event_props(m.event_of_go(go))
        else:
            if kind == "waypoint":
                pack = m.waypoints[m.tr_of_go[go]]
                kids = [c["m_PathID"] for c in m.tr[pack]["m_Children"]]
                idx = kids.index(m.tr_of_go[go]) + 1 if m.tr_of_go[go] in kids else "?"
                self.prop_info("Pack / order", f"{m.name(m.go_of_tr[pack])} #{idx}")
            area = kind == "object" and go in m.areas
            self.transform_rows(go, heading=(kind != "waypoint"), scale=(kind == "object" and not area))
            if area:
                self.area_rows(go)
            elif kind == "object":
                self.prop_info("Category", category(m.name(go)))
            self.prop_info("Path", m.path(go))

    def choose_player_tanks(self, entry):
        """Mission settings > Choose player tanks: which tanks the briefing screen
        of this custom scenario offers the player."""
        res = PlayerTankDialog(self.root, self.scenarios.player_tanks(entry)).run()
        if res is None or not self.game_closed("change the briefing screen"):
            return
        try:
            n = self.scenarios.set_player_tanks(entry, res)
        except ValueError as e:
            self.status(f"Player tanks not changed: {e}", "warn", banner=True)
            return
        self.backup_scenario(entry["battle_index"])
        self.refresh_props()
        self.status(f"Briefing screen updated: {n} tank(s) available to the player.", "ok", banner=True)

    def mission_settings_pid(self, type_name, script):
        m = self.model
        for pid in (m.mb if type_name == "MonoBehaviour" else m.sc.all_pids()):
            if m.sc.type_name(pid) != type_name:
                continue
            if script is None or m.script_class(pid) == script:
                return pid
        return None

    def mission_props(self):
        m = self.model
        tk.Label(self.props, text="MISSION SETTINGS", font=UI_BOLD, anchor="w", padx=2).grid(
            row=self._next(), column=0, columnspan=2, sticky="ew")
        self.prop_sep()
        self.prop_info("Mission", self.custom_titles.get(m.index, m.label))
        self.prop_info("Scene", f"level{m.index}")
        found = self.scenarios.mission_for_scene(m.label) if self.scenarios else None
        if found:
            self.prop_info("Allied AI tanks", f"{found[1].get('Allies_Count', 0)} (briefing count)")
        entry = next((s for s in self.scenarios.custom if s["battle_index"] == m.index), None) \
            if self.scenarios else None
        if entry is not None:
            try:
                tanks = self.scenarios.player_tanks(entry)
            except Exception:
                tanks = []
            if tanks:
                self.prop_info("Player tanks", f"{sum(t['available'] for t in tanks)} of {len(tanks)} available, "
                                               f"{sum(t['free'] for t in tanks if t['available'])} free")
                r = self._next()
                ttk.Button(self.props, text="Choose player tanks...",
                           command=lambda: self.choose_player_tanks(entry)).grid(row=r, column=1, sticky="w",
                                                                                pady=(0, 4))
        self.prop_sep()
        tk.Label(self.props, text="Lighting and fog", font=UI_BOLD, anchor="w").grid(
            row=self._next(), column=0, columnspan=2, sticky="w")
        self.lighting_rows()
        self.mission_field_rows(True)
        lighting = [e for e in m.events if m.sc.read(e)["Event_Type"] == 7]
        self.prop_info("Fog changes", ", ".join(m.name(m.event_go[e]) for e in lighting) or "none")
        self.prop_sep()
        self.mission_field_rows(False)
        ai = [e for e in m.events if m.sc.read(e)["Event_Type"] == 2]
        self.prop_info("AI changes", ", ".join(m.name(m.event_go[e]) for e in ai) or "none")
        self.prop_info("Hint", "Visibility sets how far tanks can see each other. 'Change lighting' events "
                               "(Table - Events) change fog density and visibility during the mission. Allied "
                               "groups: select the tanks and set a follow target (for example the Player) and "
                               "distance in Properties.")
        self.prop_sep()
        self.prop_info("Spawns", str(len(m.spawns)))
        self.prop_info("Other events", str(len(m.events) - len(m.spawns)))
        self.prop_info("Waypoints", str(len(m.waypoints)))
        self.prop_info("Scenery meshes", str(len(m.foot)))

    def mission_field_rows(self, fog):
        """Mission settings fields: the fog group (fog=True) or the others."""
        m = self.model
        for type_name, script, title, key, kind in MISSION_FIELDS:
            if (key in FOG_KEYS) != fog:
                continue
            pid = self.mission_settings_pid(type_name, script)
            if pid is None or key not in m.sc.read(pid):
                continue
            val = m.sc.read(pid)[key]
            if kind == "bool":
                self.prop_check(title, val, lambda v, p=pid, k=key: self.op(f"Set {k}", m.set_field, p, k, v))
            elif kind == "float":
                self.prop_entry(title, f"{val:g}", lambda s, p=pid, k=key: self.op(f"Set {k}", m.set_field, p, k, float(s)))
            elif kind == "colour":
                hexv = "#%02X%02X%02X" % tuple(int(round(val[c] * 255)) for c in "rgb")
                self.prop_entry(title, hexv, lambda s, p=pid, k=key, a=val.get("a", 1.0):
                                self.op(f"Set {k}", m.set_field, p, k, self.parse_colour(s, a)))

    def lighting_session(self):
        """Lighting of the open custom scenario (its source mission gives the
        day lighting), or None."""
        m, entry = self.model, self.scenario_entry()
        if m is None or entry is None:
            return None
        key = (id(m), entry["scene_id"])
        cached = getattr(self, "_lighting", None)
        if cached and cached[0] == key:
            return cached[1]
        day = source_scene(self.ctx, self.scenarios, entry)
        lt = Lighting(self.ctx, m, day) if day is not None else None
        self._lighting = (key, lt)
        return lt

    def lighting_rows(self):
        """Mission settings: the lighting preset and values (custom scenarios)."""
        lt = self.lighting_session()
        if lt is None:
            self.prop_info("Lighting", "Day, dusk and night: custom scenarios only.")
            return
        try:
            cur, preset = lt.lighting(), lt.preset()
        except Exception as ex:
            self.prop_info("Lighting", f"not available ({ex})")
            return
        names = [PRESET_NAMES[k] for k in ("day", "dusk", "night", "custom")]
        keys = ["day", "dusk", "night", "custom"]
        self.prop_combo("Lighting", names, keys.index(preset),
                        lambda i: self.set_lighting_preset(keys[i]) if keys[i] != "custom" else None)
        for title, key, kind in LIGHTING_FIELDS:
            v = cur[key]
            if kind == "colour":
                text = "#%02X%02X%02X" % tuple(int(round(min(1.0, max(0.0, c)) * 255)) for c in v)
                self.prop_entry(title, text, lambda s, k=key: self.set_lighting(
                    {k: _rgb_of(self.parse_colour(s))}, label=f"Set {k}"))
            else:
                self.prop_entry(title, f"{v:g}", lambda s, k=key: self.set_lighting(
                    {k: _positive(s)}, label=f"Set {k}"))

    def set_lighting(self, values, preset=False, label="Lighting"):
        """Change lighting values of the open custom scenario (one undo step)."""
        lt, m = self.lighting_session(), self.model
        if lt is None:
            return
        m.begin(label)
        try:
            undo = lt.apply(values, preset=preset)
        except Exception as ex:
            m.commit()
            self.status(f"{label} failed: {ex}", "warn", banner=True)
            return
        m.commit(undo_fn=undo)
        self.changed()

    def set_lighting_preset(self, name):
        lt, m = self.lighting_session(), self.model
        if lt is None:
            return
        if name == "day":
            m.begin("Lighting: Day")
            lt.apply_day()
            m.commit()
            self.changed()
        else:
            self.set_lighting(dict(PRESETS[name]), preset=True, label=f"Lighting: {PRESET_NAMES[name]}")
        self.status(f"Lighting: {PRESET_NAMES[name]}. Save to see it in the game.", "ok")

    @staticmethod
    def parse_colour(text, alpha=1.0):
        t = text.strip().lstrip("#")
        if len(t) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in t):
            raise ValueError("use #RRGGBB")
        r, g, b = (int(t[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        return {"r": r, "g": g, "b": b, "a": alpha}

    def spawn_ref_options(self, exclude=None):
        """[(label, transform pid)] of tank spawns for follow / commander references."""
        m = self.model
        ex = exclude if isinstance(exclude, (set, list, tuple)) else ({exclude} if exclude else set())
        opts = [("(none)", 0)]
        for e in m.spawns:
            go = m.event_go[e]
            if go in ex:
                continue
            side = self.spawn_side(e)
            opts.append((f"{m.name(go)}  [{side}]", m.tr_of_go[go]))
        return opts

    def prop_ref_combo(self, title, evs, key, exclude=None, tip=None):
        m = self.model
        opts = self.spawn_ref_options(exclude)
        cur = {m.sc.read(e)[key]["m_PathID"] for e in evs}
        values = [o[1] for o in opts]
        idx = values.index(next(iter(cur))) if len(cur) == 1 and next(iter(cur)) in values else -1

        def commit(i):
            ref = {"m_FileID": 0, "m_PathID": opts[i][1]}
            self.op(f"Set {key}", lambda: [m.set_field(e, key, dict(ref)) for e in evs])
        self.prop_combo(title, [o[0] for o in opts], idx, commit, tip=tip)

    def tank_names(self, refs):
        m = self.model
        names = []
        for r in refs:
            go = m.go_of_tr.get(r["m_PathID"], r["m_PathID"]) if r["m_FileID"] == 0 else None
            if go in m.go:
                names.append(m.name(go))
        return summary(names, "none")

    def multi_props(self, sel):
        m = self.model
        tk.Label(self.props, text=f"{len(sel)} OBJECTS SELECTED", font=UI_BOLD, anchor="w", padx=2).grid(
            row=self._next(), column=0, columnspan=2, sticky="ew")
        self.prop_sep()
        kinds = {}
        for g in sel:
            kinds[self.kind(g)] = kinds.get(self.kind(g), 0) + 1
        for k, n in sorted(kinds.items()):
            self.prop_info(k.capitalize() + "s", str(n))
        self.prop_info("Edit", "Drag, rotate (Q / E or the Rotate tool), copy, cut, paste, duplicate and delete "
                               "act on the whole selection.")
        evs = [m.event_of_go(g) for g in sel if self.kind(g) == "spawn"]
        if len(evs) != len(sel):
            return
        self.prop_sep()
        self.prop_label("Set for all selected tanks:")
        names = [tank_display(t) for t in m.tanks]
        cur = {m.tank_entry(e) for e in evs}
        one = cur.pop() if len(cur) == 1 else None
        self.prop_combo("Tank", names, m.tanks.index(one) if one in m.tanks else -1,
                        lambda i: self.op("Set tank", lambda: [m.set_tank(e, m.tanks[i]) for e in evs]))
        keys = [k for k in sorted(RELATIONSHIPS)]
        sides = {m.sc.read(e)["Relationship"] for e in evs}
        self.prop_combo("Side", [label(RELATIONSHIPS, k) for k in keys],
                        keys.index(next(iter(sides))) if len(sides) == 1 else -1,
                        lambda i: self.op("Set side", lambda: [m.set_field(e, "Relationship", keys[i]) for e in evs]))
        groups = {m.sc.read(e)["Key_Name"] for e in evs}
        self.prop_entry("Unit group", next(iter(groups)) if len(groups) == 1 else "",
                        lambda s: self.op("Set unit group", lambda: [m.set_field(e, "Key_Name", s) for e in evs]))
        targets = {bool(m.sc.read(e)["Is_Mission_Target"]) for e in evs}
        self.prop_check("Mission target", targets == {True},
                        lambda v: self.op("Set mission target",
                                          lambda: [m.set_field(e, "Is_Mission_Target", v) for e in evs]))
        packs = [0] + m.packs
        pnames = ["(none)"] + [m.name(m.go_of_tr[p]) for p in m.packs]
        curp = {m.spawn_pack(e) for e in evs}
        self.prop_combo("Waypoint pack", pnames, packs.index(next(iter(curp))) if len(curp) == 1 and
                        next(iter(curp)) in packs else -1,
                        lambda i: self.op("Set waypoint pack", lambda: [m.assign_pack(e, packs[i]) for e in evs]))
        self.prop_ref_combo("Follow target", evs, "Follow_Target", exclude=set(sel))
        dist = {m.sc.read(e)["Follow_Distance"] for e in evs}
        self.prop_entry("Follow distance", f"{next(iter(dist)):g}" if len(dist) == 1 else "",
                        lambda s: self.op("Set follow distance",
                                          lambda: [m.set_field(e, "Follow_Distance", float(s)) for e in evs]))
        fpe = {bool(m.sc.read(e)["Follow_Player_Closest_Enemy"]) for e in evs}
        self.prop_check("Follow player's enemy", fpe == {True},
                        lambda v: self.op("Set follow player's enemy",
                                          lambda: [m.set_field(e, "Follow_Player_Closest_Enemy", v) for e in evs]))
        resp = {m.sc.read(e)["Respawn_Times"] for e in evs}
        self.prop_entry("Respawn times", next(iter(resp)) if len(resp) == 1 else "",
                        lambda s: self.op("Set respawns", lambda: [m.set_field(e, "Respawn_Times", int(s)) for e in evs]))

    def _next(self):
        r = self._row
        self._row += 1
        return r

    def prop_label(self, text):
        tk.Label(self.props, text=text, anchor="w", justify="left").grid(
            row=self._next(), column=0, columnspan=2, sticky="w")

    def prop_sep(self):
        tk.Frame(self.props, height=1, bg="#D9D9D9").grid(
            row=self._next(), column=0, columnspan=2, sticky="ew", pady=6)

    def prop_info(self, name, text, tip=None, fg="#404040"):
        r = self._next()
        lbl = tk.Label(self.props, text=name, anchor="w")
        lbl.grid(row=r, column=0, sticky="nw", padx=(0, 6), pady=1)
        tk.Label(self.props, text=text, anchor="w", justify="left", wraplength=170, fg=fg).grid(
            row=r, column=1, sticky="w", pady=1)
        if tip:
            ToolTip(lbl, tip)

    def prop_heading(self, text, note=None):
        """A section heading, with an optional explanation below it."""
        tk.Label(self.props, text=text, font=UI_BOLD, anchor="w").grid(
            row=self._next(), column=0, columnspan=2, sticky="w", pady=(8, 0))
        if note:
            tk.Label(self.props, text=note, anchor="w", justify="left", wraplength=300, fg="#505050").grid(
                row=self._next(), column=0, columnspan=2, sticky="w", pady=(0, 2))

    def prop_entry(self, name, value, commit, tip=None):
        r = self._next()
        lbl = tk.Label(self.props, text=name, anchor="w")
        lbl.grid(row=r, column=0, sticky="w", padx=(0, 6), pady=1)
        if tip:
            ToolTip(lbl, tip)
        var = tk.StringVar(value=str(value))
        ent = ttk.Entry(self.props, textvariable=var, width=22)
        ent.grid(row=r, column=1, sticky="ew", pady=1)
        original = str(value)
        state = {"done": False}

        def apply(_e=None):
            s = var.get()
            if s == original or state["done"]:
                return
            state["done"] = True
            try:
                commit(s)
            except ValueError as ex:
                self.status(f"Invalid value for {name}: {ex}", "warn")
                state["done"] = False
                var.set(original)
        ent.bind("<Return>", apply)
        ent.bind("<FocusOut>", apply)
        self.props.columnconfigure(1, weight=1)
        return ent

    def prop_check(self, name, value, commit, tip=None):
        r = self._next()
        lbl = tk.Label(self.props, text=name, anchor="w")
        lbl.grid(row=r, column=0, sticky="w", padx=(0, 6), pady=1)
        if tip:
            ToolTip(lbl, tip)
        var = tk.BooleanVar(value=bool(value))
        ttk.Checkbutton(self.props, variable=var, command=lambda: commit(var.get())).grid(
            row=r, column=1, sticky="w")

    def prop_combo(self, name, values, current, commit, tip=None):
        r = self._next()
        lbl = tk.Label(self.props, text=name, anchor="w")
        lbl.grid(row=r, column=0, sticky="w", padx=(0, 6), pady=1)
        if tip:
            ToolTip(lbl, tip)
        cb = ttk.Combobox(self.props, state="readonly", values=values, width=22)
        if 0 <= current < len(values):
            cb.current(current)
        cb.grid(row=r, column=1, sticky="ew", pady=1)
        cb.bind("<<ComboboxSelected>>", lambda e: commit(cb.current()))

    def field_rows(self, pid, specs):
        m = self.model
        d = m.sc.read(pid)
        for title, key, kind in specs:
            if key not in d:
                continue
            val = d[key]
            if kind == "bool":
                self.prop_check(title, val, lambda v, k=key: self.op(f"Set {k}", m.set_field, pid, k, v))
            elif isinstance(kind, dict):
                keys = sorted(kind)
                if val not in keys:
                    keys.append(val)
                names = [label(kind, k) for k in keys]
                self.prop_combo(title, names, keys.index(val),
                                lambda i, k=key, ks=keys: self.op(f"Set {k}", m.set_field, pid, k, ks[i]))
            elif kind == "int":
                self.prop_entry(title, val, lambda s, k=key: self.op(f"Set {k}", m.set_field, pid, k, int(s)))
            elif kind == "float":
                self.prop_entry(title, f"{val:.4g}",
                                lambda s, k=key: self.op(f"Set {k}", m.set_field, pid, k, float(s)))
            else:
                self.prop_entry(title, val, lambda s, k=key: self.op(f"Set {k}", m.set_field, pid, k, s))

    def transform_rows(self, go, heading=True, scale=False):
        m = self.model
        trp = m.tr_of_go[go]
        p, r, _ = m.world(trp)

        def set_axis(axis, s):
            pos = m.world(trp)[0].copy()
            pos[axis] = float(s)
            self.op("Move", m.set_world_position, trp, pos)

        self.prop_entry("Position X (east)", f"{p[0]:.2f}", lambda s: set_axis(0, s))
        self.prop_entry("Position Z (north)", f"{p[2]:.2f}", lambda s: set_axis(2, s))
        self.prop_entry("Height Y", f"{p[1]:.2f}", lambda s: set_axis(1, s))
        g = m.ground(p[0], p[2])
        if g is not None:
            self.prop_info("Above ground", f"{p[1] - g:+.2f} m")
        if heading:
            self.prop_entry("Heading (deg)", f"{heading_of(r):.1f}",
                            lambda s: self.op("Rotate", m.set_heading, trp, float(s)))
        if scale:
            sc = m.tr[trp]["m_LocalScale"]
            self.prop_info("Scale (local)", f"{sc['x']:.2f}  {sc['y']:.2f}  {sc['z']:.2f}")
            self.prop_entry("Multiply scale by", "1.0", lambda s: self.op("Scale", m.set_scale, trp, float(s)))

    def referenced_by(self, go):
        m = self.model
        trp = m.tr_of_go[go]
        refs = {go, trp} | set(m.comps.get(go, []))
        names = []
        for e in m.events:
            if m.event_go[e] == go:
                continue
            d = m.sc.read(e)
            for lst in ("Trigger_Tanks", "Target_Tanks", "Disabled_Events", "Useless_Events",
                        "Trigger_Collider_Scripts"):
                if any(r["m_FileID"] == 0 and r["m_PathID"] in refs for r in d.get(lst) or []):
                    names.append(m.name(m.event_go[e]).replace("Event (", "").rstrip(")"))
                    break
        return names


def selftest(game, out_path):
    """Load the game data, a mission with terrain, the tank list and an object
    source scene, create and close the main window, and write the result to
    out_path. Used to check a packaged build (its native libraries included)."""
    import time
    import traceback
    from scene_model import tank_catalogue
    lines, ok = [f"{APP_NAME} {__version__} selftest"], True
    try:
        t = time.time()
        game = game or settings.load_settings().get("game") or (settings.detect_game() or [None])[0]
        valid, msg = settings.validate_game(game)
        lines.append(f"game: {game} valid={valid} {'' if valid else msg}")
        if not valid:
            raise RuntimeError(msg)
        ctx = GameContext(game)
        lines.append(f"scripts: {len(ctx.scripts)}, scenes: {len(ctx.scene_names)}, layouts: "
                     f"{len(ctx.layouts)} ({ctx.layout_status()})")
        idx = next(i for i in range(len(ctx.scene_names))
                   if ctx.scene_label(i) == "10_Urban_Area_Close_Combat_Basic")
        m = SceneModel(ctx, idx)
        lines.append(f"scene {idx}: spawns={len(m.spawns)} footprints={len(m.foot)} "
                     f"terrain={'yes' if m.terrain is not None and m.terrain.image is not None else 'no'}")
        lines.append(f"tanks: {len(tank_catalogue(ctx))}")
        root = tk.Tk()
        root.withdraw()
        app = EditorApp(root, game, idx, auto_library=False)
        root.update()
        lines.append(f"main window: model={'yes' if app.model else 'no'}, toolbar icons={len(app._images)}")
        root.destroy()
        lines.append(f"time: {time.time() - t:.1f} s")
    except Exception:
        ok = False
        lines.append(traceback.format_exc())
    lines.append("RESULT: " + ("OK" if ok else "FAILED"))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


def build_library_process(game, backup_dir, progress_path):
    """Entry point of the background library build (--build-library)."""
    with open(progress_path, "a", encoding="utf-8") as out:
        try:
            lib = ObjectLibrary(GameContext(game), backup_dir)

            def progress(n, total, name):
                out.write(f"{n} {total} {name}\n")
                out.flush()
            lib.build(progress)
            return 0
        except Exception as e:
            out.write(f"ERROR {e}\n")
            return 1


def main():
    ap = argparse.ArgumentParser(description="Tank Battle Classic mission editor")
    ap.add_argument("--game", default=os.environ.get("TBC_GAME"),
                    help="game folder (holds GameAssembly.dll); default: last used or detected in Steam")
    ap.add_argument("--scene", type=int, default=None, help="scene index to open (levelN)")
    ap.add_argument("--selftest", metavar="RESULT_FILE", help="check the installation and exit")
    ap.add_argument("--build-library", nargs=3, metavar=("GAME", "BACKUP_DIR", "PROGRESS_FILE"),
                    help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.build_library:
        sys.exit(build_library_process(*args.build_library))
    if args.selftest:
        sys.exit(selftest(args.game, args.selftest))
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    merged, moved, adopted, merge_error = [], [], None, None
    try:
        moved = settings.move_appdata_store()
        adopted = settings.adopt_sibling_store()
        merged = settings.merge_source_store()
    except Exception as e:
        merge_error = e
    root = tk.Tk()
    app = EditorApp(root, args.game, args.scene)
    if moved:
        app.status(f"Settings and backups now live next to the program, in {settings.DATA_DIR} "
                   f"(moved from {settings.APPDATA_DIR}).", "ok", banner=True)
    elif adopted:
        app.status(f"Settings and backups were copied from {adopted} into this program's folder.",
                   "ok", banner=True)
    elif merged:
        app.status("The source version's old backups were merged into the data folder "
                   f"({settings.DATA_DIR}).", "ok", banner=True)
    elif merge_error is not None:
        app.status(f"Moving the backups to the data folder failed: {merge_error}", "warn", banner=True)
    elif settings.DATA_REASON == "read-only program folder":
        app.status(f"The program folder cannot be written; settings and backups are kept in "
                   f"{settings.DATA_DIR}. Move the program to a folder of your own to keep them next to it.",
                   "warn", banner=True)
    root.mainloop()


if __name__ == "__main__":
    main()
