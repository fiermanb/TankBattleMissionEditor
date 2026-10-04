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
from object_import import ObjectLibrary, base_name, category, import_object, reconnect_event_refs
from scenarios import ScenarioManager
from scene_io import GameContext
from scene_model import (
    EVENT_TYPES, RELATIONSHIPS, TRIGGER_TYPES, SceneModel, heading_of, label, tank_display,
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
}
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
    ("Scenery", "Scenery", None, None, None),
] + [(cat, cat, "square", col, "Scenery") for cat, col in CATEGORY_COLOURS.items()] + [
    ("Terrain", "Terrain", "terrain", None, None),
    ("Deleted", "Deleted objects", "square", COL_HIDDEN, None),
]
TOC_DEFAULT_OFF = {"Deleted"}

TOOLS = ("select", "pan", "zoomin", "zoomout", "rotate")
TOOL_CURSORS = {"select": "arrow", "pan": "fleur", "zoomin": "crosshair", "zoomout": "crosshair",
                "rotate": "exchange"}
TOOL_NAMES = {"select": "Select / move", "pan": "Pan", "zoomin": "Zoom in", "zoomout": "Zoom out",
              "rotate": "Rotate"}

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
EVENT_FIELDS = [
    ("Trigger", "Trigger_Type", TRIGGER_TYPES),
    ("Trigger time (s)", "Trigger_Time", "float"),
    ("All triggers needed", "All_Trigger_Flag", "bool"),
    ("Necessary count", "Necessary_Num", "int"),
    ("Message", "Event_Message", "text"),
    ("Message (JPN)", "Event_Message_JPN", "text"),
    ("Message time (s)", "Event_Message_Time", "float"),
]
EVENT_TYPE_FIELDS = {
    4: [("Artillery shells", "Artillery_Num", "int")],
    7: [("New visibility (m)", "Visibility", "float"),
        ("New fog density", "Fog_Density", "float"),
        ("Change duration (s)", "Change_Lighting_Duration", "float")],
    2: [("New: mission target", "New_Is_Mission_Target", "bool"),
        ("New: respawn times", "New_Respawn_Times", "int"),
        ("New: no attack", "New_No_Attack", "bool"),
        ("New: breakthrough", "New_Breakthrough", "bool"),
        ("New: follow closest enemy", "New_Follow_Closest_Enemy", "bool"),
        ("New: follow player's enemy", "New_Follow_Player_Closest_Enemy", "bool"),
        ("New: follow distance", "New_Follow_Distance", "float"),
        ("New: patrol type", "New_Patrol_Type", "int"),
        ("New: attack distance", "New_Attack_Distance", "float"),
        ("New: approach distance", "New_Approach_Distance", "float"),
        ("New: open fire distance", "New_OpenFire_Distance", "float"),
        ("New: lost count", "New_Lost_Count", "float"),
        ("New: dead angle", "New_Dead_Angle", "float"),
        ("New: patrol speed rate", "New_Patrol_Speed_Rate", "float"),
        ("New: combat speed rate", "New_Combat_Speed_Rate", "float")],
}
MISSION_FIELDS = [
    ("RenderSettings", None, "Fog", "m_Fog", "bool"),
    ("RenderSettings", None, "Fog density", "m_FogDensity", "float"),
    ("RenderSettings", None, "Fog colour", "m_FogColor", "colour"),
    ("MonoBehaviour", "Lighting_Control_CS", "Visibility (m)", "Current_Visibility", "float"),
    ("MonoBehaviour", "Lighting_Control_CS", "Dust particles", "Dust_Particle_Multiplier", "float"),
    ("MonoBehaviour", "Game_Controller_CS", "Recon plane available", "Available_Recon_Plane", "bool"),
    ("MonoBehaviour", "Score_Manager_CS", "Assist score", "Enable_Assist_Score", "bool"),
]


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

    def __init__(self, parent, title, items, chosen):
        super().__init__(parent, title)
        tk.Label(self.body, text="Select the tanks (Shift / Ctrl + click for several):", anchor="w").pack(fill="x")
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
        self.place = None
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

    def report_game_update(self, update):
        """Tell the user what a game update (or file verification) changed."""
        lines = ["The game files were replaced since the editor last changed them (game update, or "
                 "Steam's 'Verify integrity of game files')."]
        if update["archive"]:
            lines.append(f"\nThe old backups belong to the previous game version and were set aside in:\n"
                         f"{update['archive']}\nThey will not be restored. New backups are made on the next save.")
        if update["orphaned"]:
            lines.append("\nThese custom scenarios are no longer in the game: " + ", ".join(update["orphaned"]) +
                         ". Recreate them with Scenario > New scenario.")
        if update["leftovers"]:
            lines.append("\nTheir leftover files (" + ", ".join(update["leftovers"]) + ") are not used by "
                         "the game any more.\n\nDelete these leftover files now?")
            if messagebox.askyesno("Game updated", "\n".join(lines), parent=self.root):
                done = self.scenarios.delete_leftovers(update["leftovers"])
                self.status(f"Deleted {len(done)} leftover file(s) of removed scenarios.", "ok")
        else:
            messagebox.showinfo("Game updated", "\n".join(lines), parent=self.root)
        self.refresh_scenes()

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
        self.library = ObjectLibrary(ctx, self.backup_dir)
        update = None
        try:
            update = self.scenarios.check_game_update()
        except OSError as e:
            self.status(f"Checking the backups failed: {e}", "warn")
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
                      ("Select game folder...", self.select_game_folder, "", "gamefolder"), None,
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
                           ("Clear selection", lambda: self.select(None), "Esc")])
        menu("Insert", [("Object...", self.add_object, "", "addobj"),
                        ("Hostile tank", lambda: self.add_spawn(True), "", "addhost"),
                        ("Friendly tank", lambda: self.add_spawn(False), "", "addfriend"), None,
                        ("Waypoint", self.add_waypoint, "W", "addwp"),
                        ("Route", self.add_route, "", "addroute"),
                        ("Event...", self.add_event, "", "addevent")])
        menu("Scenario", [("New scenario...", self.new_scenario, "", "newscen"),
                          ("Mission text...", self.edit_mission_text),
                          ("Update briefing screen", self.update_briefing), None,
                          ("Remove custom scenarios...", self.remove_custom)])
        menu("Windows", [("Table Of Contents", lambda: self.show_page("left", "toc"), "", "toc"),
                         ("Objects", lambda: self.show_page("left", "objects"), "", "objects"),
                         ("Create", lambda: self.show_page("left", "create"), "", "addhost"),
                         ("Properties", lambda: self.show_page("right", "props"), "", "props"),
                         ("Catalog", lambda: self.show_page("right", "catalog"), "", "catalog"),
                         ("Spawns table", lambda: self.show_page("bottom", "spawns"), "", "table"),
                         ("Events table", lambda: self.show_page("bottom", "events"), "", "events"),
                         ("Waypoints table", lambda: self.show_page("bottom", "waypoints"), "", "addwp"),
                         ("Scenery table", lambda: self.show_page("bottom", "scenery"), "", "objects")])
        menu("Help", [("Controls", self.show_controls), ("About", self.show_about)])

    def show_controls(self):
        messagebox.showinfo("Controls", (
            "Tools (toolbar): Select / move, Pan, Zoom in (click or drag a box), Zoom out, Rotate (drag around "
            "the selection; Shift snaps to the step).\n\n"
            "Always: middle or right drag pans, the mouse wheel zooms.\n"
            "Alt + click selects a single part instead of the whole object.\n\n"
            "Create pane: choose a template, then click on the map (repeatedly; Esc ends); double-click "
            "places at the map centre. Right-click > Add here places at the pointer.\n"
            "Insert: W adds a waypoint at the cursor to the selected route; Insert > Route starts a new route "
            "(click its waypoints, Esc ends); "
            "Insert > Event... creates a new event or copies one from this or another mission.\n\n"
            "Tables: double-click a cell (or F2 for the name) to edit it; Enter applies, Esc cancels. "
            "Deleted rows are shown when the Deleted objects layer is on. Insert adds a row: a tank of the "
            "selected side, an event of the selected type, or a waypoint after the selected one. "
            "The Scenery table follows the scenery layers.\n\n"
            "Keys: Q / E rotate by the step (Shift: 1 deg), Del delete / restore, Ctrl+D duplicate, Ctrl+Z undo, "
            "Ctrl+S save, P select parent, F zoom to selection, Home full extent, Tab next spawn, "
            "L events list, T terrain follow, Esc clear selection."), parent=self.root)

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
        self.build_create(self.docks["left"].add_page("create", "Create", self.image("addhost")))

        self.vpane = tk.PanedWindow(self.hpane, orient="vertical", sashwidth=4, sashrelief="flat",
                                    bd=0, bg=FACE, opaqueresize=True)
        self.mapframe = tk.Frame(self.vpane, bd=0, highlightthickness=1, highlightbackground=BORDER)
        self.canvas = tk.Canvas(self.mapframe, background=WORKSPACE, highlightthickness=0, bd=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        self._base_item = self.canvas.create_image(0, 0, anchor="nw")
        self.docks["bottom"] = DockArea(self.vpane, lambda: self.hide_dock("bottom"))
        self.build_table(self.docks["bottom"].add_page("spawns", "Table - Spawns", self.image("table")), "spawns")
        self.build_table(self.docks["bottom"].add_page("events", "Table - Events", self.image("events")), "events")
        self.build_table(self.docks["bottom"].add_page("waypoints", "Table - Waypoints", self.image("addwp")),
                         "waypoints")
        self.build_table(self.docks["bottom"].add_page("scenery", "Table - Scenery", self.image("objects")),
                         "scenery")
        self.docks["bottom"].on_select = lambda key: self.refresh_tables()

        self.docks["right"] = DockArea(self.hpane, lambda: self.hide_dock("right"))
        self.build_properties(self.docks["right"].add_page("props", "Properties", self.image("props")))
        self.build_catalog(self.docks["right"].add_page("catalog", "Catalog", self.image("catalog")))
        self.docks["right"].select("catalog")

        self.vpane.add(self.mapframe, minsize=200, stretch="always")
        self.vpane.add(self.docks["bottom"], minsize=90, height=230, stretch="never")
        self.hpane.add(self.docks["left"], minsize=170, width=270, stretch="never")
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
                    self.hpane.add(pane, before=self.vpane, minsize=170, width=270, stretch="never")
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
        block("Insert", [("addobj", "Add object...", self.add_object),
                         ("addhost", "Add hostile tank", lambda: self.add_spawn(True)),
                         ("addfriend", "Add friendly tank", lambda: self.add_spawn(False)), None,
                         ("addwp", "Add waypoint: click on the map (W)", self.add_waypoint),
                         ("addroute", "Add route (copy of the selected route)", self.add_route),
                         ("addevent", "Add event...", self.add_event), None,
                         ("newscen", "New scenario...", self.new_scenario)])

    def build_toc(self, body):
        holder = bordered(body)
        holder.pack(fill="both", expand=True, padx=2, pady=2)
        self.toc = ttk.Treeview(holder, show="tree", selectmode="none")
        sb = ttk.Scrollbar(holder, command=self.toc.yview)
        self.toc.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.toc.pack(side="left", fill="both", expand=True)
        self.toc.bind("<Button-1>", self.on_toc_click)
        self.toc_images = {}
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
            self.toc_images[k] = ImageTk.PhotoImage(icons.swatch(spec[2], spec[3] or (0, 0, 0), self.icon_size))
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

    def on_toc_click(self, e):
        item = self.toc.identify_row(e.y)
        if not item or item == "root" or item.endswith(":sym"):
            return
        if self.toc.identify_element(e.x, e.y) != "image":
            return
        new = not self.layers[item]
        self.layers[item] = new
        self.toc.item(item, image=self.toc_image(item, new))
        for c in self.toc.get_children(item):
            if c in self.layers:
                self.layers[c] = new
                self.toc.item(c, image=self.toc_image(c, new))
        parent = self.toc.parent(item)
        if parent and parent != "root" and new and not self.layers[parent]:
            self.layers[parent] = True
            self.toc.item(parent, image=self.toc_image(parent, True))
        self.layers_changed()
        return "break"

    def build_objects(self, body):
        top = tk.Frame(body)
        top.pack(side="top", fill="x", padx=2, pady=(2, 0))
        tk.Label(top, text="Filter:").pack(side="left")
        self.obj_filter = tk.StringVar()
        ttk.Entry(top, textvariable=self.obj_filter).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.obj_filter.trace_add("write", lambda *a: self.refresh_objects())
        bottom = tk.Frame(body)
        bottom.pack(side="bottom", fill="x", padx=2, pady=2)
        self.obj_build_btn = ttk.Button(bottom, text="Build object library", command=self.build_library)
        self.obj_place_btn = ttk.Button(bottom, text="Place at map centre", command=self.place_selected_object)
        self.obj_place_btn.pack(side="left")
        self.obj_hint = tk.Label(body, text="Double-click an object, or drag it onto the map.", anchor="w",
                                 fg="#404040")
        self.obj_hint.pack(side="bottom", fill="x", padx=4)
        holder = bordered(body)
        holder.pack(fill="both", expand=True, padx=2, pady=2)
        self.obj_tree = ttk.Treeview(holder, columns=("size", "source"), show="tree headings", selectmode="browse")
        self.obj_tree.heading("#0", text="Object", anchor="w")
        self.obj_tree.heading("size", text="Size (m)", anchor="w")
        self.obj_tree.heading("source", text="From mission", anchor="w")
        self.obj_tree.column("#0", width=150, stretch=True)
        self.obj_tree.column("size", width=60, stretch=False)
        self.obj_tree.column("source", width=120, stretch=False)
        sb = ttk.Scrollbar(holder, command=self.obj_tree.yview)
        self.obj_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.obj_tree.pack(side="left", fill="both", expand=True)
        self.obj_tree.bind("<Double-Button-1>", lambda e: self.place_selected_object())
        self.obj_tree.bind("<ButtonPress-1>", self.on_obj_press, add="+")
        self.obj_tree.bind("<B1-Motion>", self.on_obj_motion)
        self.obj_tree.bind("<ButtonRelease-1>", self.on_obj_release, add="+")
        self.obj_entries = {}

    def create_items(self):
        """(group, label, icon, key) of the Create pane."""
        items = [("Tanks", "Hostile tank", "addhost", ("spawn", 1)),
                 ("Tanks", "Friendly tank", "addfriend", ("spawn", 0)),
                 ("Routes", "New route", "addroute", ("route",)),
                 ("Routes", "Waypoint (selected route)", "addwp", ("waypoint",))]
        items += [("Events", name, "addevent", ("event", t)) for t, name in sorted(EVENT_TYPES.items()) if t != 0]
        return items

    def build_create(self, body):
        hint = tk.Label(body, anchor="w", justify="left", fg="#404040", wraplength=250, text=(
            "Choose a template, then click on the map (repeatedly; Esc ends). Double-click places at the "
            "map centre; dragging onto the map also works."))
        hint.pack(side="bottom", fill="x", padx=4, pady=2)
        holder = bordered(body)
        holder.pack(fill="both", expand=True, padx=2, pady=2)
        t = ttk.Treeview(holder, show="tree", selectmode="browse")
        sb = ttk.Scrollbar(holder, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        t.pack(side="left", fill="both", expand=True)
        self.create_tree = t
        self.create_keys = {}
        groups = {}
        for group, text, icon, key in self.create_items():
            if group not in groups:
                groups[group] = t.insert("", "end", text=" " + group, open=True)
            iid = t.insert(groups[group], "end", text=" " + text, image=self.image(icon))
            self.create_keys[iid] = key
        t.bind("<<TreeviewSelect>>", lambda e: self.on_create_select())
        t.bind("<Double-Button-1>", self.on_create_double)
        t.bind("<ButtonRelease-1>", self.on_create_release, add="+")

    def on_create_select(self):
        sel = self.create_tree.selection()
        key = self.create_keys.get(sel[0]) if sel else None
        if key is None or not self.model:
            return
        if key == ("waypoint",) and not self.target_route():
            self.status("Select a route, one of its waypoints, or a tank with a route first.", "warn", banner=True)
            self.create_tree.selection_remove(sel)
            return
        text = self.create_tree.item(sel[0], "text").strip()
        if key == ("route",):
            self.start_place("Click the first waypoint of the new route.",
                             lambda x, z: self.create_at(key, x, z))
        else:
            self.start_place(f"Click on the map to place: {text}.", lambda x, z: self.create_at(key, x, z),
                             repeat=True)

    def on_create_double(self, e):
        key = self.create_keys.get(self.create_tree.identify_row(e.y))
        if key is not None and self.model:
            if key[0] != "route":
                self.end_place()
            self.create_at(key, self.view.cx, self.view.cz)
        return "break"

    def on_create_release(self, e):
        key = self.create_keys.get(self.create_tree.identify_row(e.y)) if self.model else None
        if key is None or self.root.winfo_containing(e.x_root, e.y_root) is not self.canvas:
            return
        x = e.x_root - self.canvas.winfo_rootx()
        y = e.y_root - self.canvas.winfo_rooty()
        if key[0] != "route":
            self.end_place()
        self.create_at(key, *self.view.s2w(x, y))

    def create_at(self, key, x, z):
        """Create the object of a Create pane template or 'Add here' entry at (x, z)."""
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
            self.busy("Creating the event...")
            new = self.op("Add event", do)
            self.idle()
            if new is not None:
                self.select(new)
                self.show_page("right", "props")
                tanks = m.sc.read(m.event_of_go(new))["Trigger_Type"] == 1
                self.status(f"Added '{m.name(new)}'. Set its trigger and tanks in Properties." +
                            (" A tank trigger without tanks fires at once." if tanks else ""), "ok", banner=True)

    def refresh_objects(self):
        t = self.obj_tree
        t.delete(*t.get_children())
        self.obj_entries = {}
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
        self.obj_hint.config(text="Double-click an object, or drag it onto the map.")
        f = self.obj_filter.get().lower()
        cats = {}
        for i, e in enumerate(lib.entries):
            if f and f not in e["name"].lower() and f not in e["category"].lower():
                continue
            if e["category"] not in cats:
                cats[e["category"]] = t.insert("", "end", text=" " + e["category"], open=bool(f))
            iid = t.insert(cats[e["category"]], "end", text=" " + e["name"],
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

    def place_selected_object(self):
        e = self.selected_object_entry()
        if e is None:
            self.status("Select an object in the Objects pane first.", banner=True)
            return
        self.place_object(e, self.view.cx, self.view.cz)

    def on_obj_press(self, e):
        item = self.obj_tree.identify_row(e.y)
        self.obj_drag = {"item": item, "start": (e.x_root, e.y_root), "active": False} if item in self.obj_entries else None

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
        if not d or not d["active"]:
            return
        if self.root.winfo_containing(e.x_root, e.y_root) is not self.canvas:
            self.status("Object not placed (released outside the map).")
            return
        x = e.x_root - self.canvas.winfo_rootx()
        y = e.y_root - self.canvas.winfo_rooty()
        wx, wz = self.view.s2w(x, y)
        self.place_object(self.obj_entries[d["item"]], wx, wz)

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
                return None, d["Event_Message"], field("Event_Message")
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
        if not hasattr(self, "tables"):
            return
        m = self.model
        for kind, tv in self.tables.items():
            if kind != "scenery":
                tv.delete(*tv.get_children())
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
            for tv in self.tables.values():
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

    # ---- helpers -----------------------------------------------------

    def set_tool(self, tool):
        self.tool = tool
        for name, btn in self.tool_buttons.items():
            btn.set_active(name == tool)
        self.canvas.config(cursor=TOOL_CURSORS[tool])
        self.tool_lbl.config(text=f"Tool: {TOOL_NAMES[tool]}")

    def toggle_follow(self):
        self.follow_var.set(not self.follow_var.get())
        self.status(f"Terrain follow {'on' if self.follow_var.get() else 'off'}.")

    def status(self, text, kind=None, banner=False):
        fg = {"ok": OK_FG, "warn": WARN_FG}.get(kind, "#000000")
        self.status_lbl.config(text=text, fg=fg)
        if banner:
            self.show_banner(text, kind)

    def show_banner(self, text, kind=None):
        c = self.canvas
        c.delete("banner")
        w = max(200, c.winfo_width())
        t = c.create_text(w // 2, 26, text=text, font=UI_FONT, anchor="n", tags="banner",
                          fill={"warn": WARN_FG}.get(kind, "#000000"), width=w - 80)
        x0, y0, x1, y1 = c.bbox(t)
        r = c.create_rectangle(x0 - 12, y0 - 7, x1 + 12, y1 + 7, fill=INFO_BG, outline="#000000", tags="banner")
        c.tag_lower(r, t)
        if self._banner_after:
            self.root.after_cancel(self._banner_after)
        self._banner_after = self.root.after(4000, lambda: c.delete("banner"))

    def busy(self, text):
        self.status(text)
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
        self._terrain_img = None
        if self.model.terrain is not None:
            img = np.clip(self.model.terrain.image.astype(np.int32) * 5 // 4, 0, 255).astype(np.uint8)
            self._terrain_img = Image.fromarray(img)
        self.layers_changed()
        self.fit_view()
        self.idle()
        self.update_title()
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
        if not m.dirty:
            self.status("Nothing to save - no changes since the last save.", banner=True)
            return True
        self.busy(f"Saving level{m.index}...")
        os.makedirs(self.backup_dir, exist_ok=True)
        bak = self.backup_path()
        try:
            if not os.path.exists(bak):
                shutil.copy2(m.sc.path, bak)
            m.save()
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
        if m.index in self.custom_titles:
            lines = self.run_briefing_sync()
            if lines:
                msg += " " + lines[0]
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
        self.status(f"Created '{title}' (level{battle_idx}). It is on the 'Custom Missions' page "
                    "of the mission select screen.", "ok", banner=True)

    def edit_mission_text(self):
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
        self.status(f"Mission text saved: '{title}'.", "ok", banner=True)

    def remove_custom(self):
        if not self.scenarios.has_changes():
            self.status("There are no custom scenarios or mission text changes to remove.", banner=True)
            return
        n = len(self.scenarios.custom)
        if not messagebox.askyesno("Remove custom scenarios",
                                   f"Remove all {n} custom scenario(s) and undo mission text changes?",
                                   parent=self.root):
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
            self.model = None
            self.selected = None
            self.canvas.delete("overlay")
            self.canvas.itemconfig(self._base_item, image="")
            self._base_key = None
            self.refresh_props()
            self.refresh_tables()
        self.refresh_scenes()
        self.update_title()
        self.status(f"Removed {n} custom scenario(s) and restored the mission files.", "ok", banner=True)

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
                           prompt="Choose an object; it is placed at the centre of the map view.").run()
        if entry is not None:
            self.place_object(entry, self.view.cx, self.view.cz)

    def place_object(self, entry, wx, wz):
        m = self.model
        if not m:
            self.status("Open a mission first.", banner=True)
            return
        self.busy(f"Adding {entry['name']}...")
        result = self.op("Add object", import_object, m, self.library, entry, wx, wz, self.follow_var.get())
        self.idle()
        if result is None:
            return
        root, cleared = result
        self.selected = root
        self.changed()
        note = f" {cleared} outside reference(s) were cleared." if cleared else ""
        self.status(f"Added '{m.name(root)}' from {self.library.source_label(entry)}. "
                    f"Drag it into place.{note}", "ok", banner=True)

    def changed(self, props=True):
        self.update_title()
        if props:
            self.refresh_props()
        self.refresh_tables()
        self.request_render()

    def op(self, text, fn, *args):
        m = self.model
        m.begin(text)
        try:
            result = fn(*args)
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

    def movable(self, gos=None):
        """Selected objects that can be moved / rotated / copied (no packs or
        position-less events), without children of other selected objects."""
        m = self.model
        gos = [g for g in (self.sel if gos is None else gos) if g in m.go and self.kind(g) in ("spawn", "waypoint", "object")]
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
                        ng = m.duplicate(go, offset=None)
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

    def start_place(self, text, fn, repeat=False):
        """Next left click on the map calls fn(x, z) (every click with repeat);
        Esc ends."""
        self.place = {"fn": fn, "repeat": repeat}
        self.canvas.config(cursor="crosshair")
        self.status(text + (" (Esc ends)" if repeat else " (Esc cancels)"), banner=True)

    def end_place(self, text=None):
        self.place = None
        self.canvas.config(cursor=TOOL_CURSORS[self.tool])
        if getattr(self, "create_tree", None) is not None and self.create_tree.selection():
            self.create_tree.selection_remove(self.create_tree.selection())
        if text:
            self.status(text)

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
        m = self.model
        if not m:
            return
        route = self.target_route()
        if not route:
            self.status("Select a route, one of its waypoints, or a tank with a route first.", "warn", banner=True)
            return
        if at is None:
            self.start_place("Click on the map to place the new waypoint.", lambda x, z: self.add_waypoint((x, z)))
            return
        pack, after = route
        new = self.op("Add waypoint", m.new_waypoint, pack, after, self.ground_point(*at))
        if new is not None:
            self.select(new)
            kids = [c["m_PathID"] for c in m.tr[pack]["m_Children"]]
            n = kids.index(m.tr_of_go[new]) + 1 if m.tr_of_go[new] in kids else len(kids)
            self.status(f"Added waypoint {n} of {m.name(m.go_of_tr[pack])}. Press W over the map for the next one.",
                        "ok")

    def ground_point(self, x, z):
        g = self.model.ground(x, z)
        return (x, 0.0 if g is None else g, z)

    def add_route(self, at=None):
        """New route: each click on the map adds the next waypoint; Esc ends."""
        m = self.model
        if not m:
            return
        if at is None:
            self.start_place("Click the first waypoint of the new route.", lambda x, z: self.add_route((x, z)))
            return
        new = self.op("Add route", m.new_route, self.ground_point(*at))
        if new is None:
            return
        self.select(new)
        pack = m.waypoints[m.tr_of_go[new]]
        self.continue_route(pack)

    def continue_route(self, pack):
        m = self.model
        n = len(m.tr[pack]["m_Children"])
        self.status(f"{m.name(m.go_of_tr[pack])}: {n} waypoint(s). Click the next waypoint; Esc ends the route. "
                    "Assign the route to tanks in Properties (Waypoint pack).", "ok", banner=True)

        def next_point(x, z):
            if not self.model or pack not in m.tr:
                return
            last = m.tr[pack]["m_Children"][-1]["m_PathID"] if m.tr[pack]["m_Children"] else None
            new = self.op("Add waypoint", m.new_waypoint, pack, last, self.ground_point(x, z))
            if new is not None:
                self.select(new)
                self.continue_route(pack)
        self.place = {"fn": next_point}
        self.canvas.config(cursor="crosshair")

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
            self.busy("Creating the event...")
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

    def select_parent(self):
        if not self.model or self.selected is None:
            return
        p = self.model.parent_go(self.selected)
        if p is not None:
            self.select(p)
            self.status(f"Selected parent '{self.model.name(p)}'.")

    def frame_selection(self):
        m = self.model
        if not m:
            return
        gos = [g for g in self.sel if g in m.go and g in m.tr_of_go]
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

    def on_press(self, e):
        self.canvas.focus_set()
        if not self.model:
            return
        if self.place:
            place = self.place
            if not place.get("repeat"):
                self.place = None
                self.canvas.config(cursor=TOOL_CURSORS[self.tool])
            place["fn"](*self.view.s2w(e.x, e.y))
            return
        if self.tool == "pan":
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
            if d["sel_mode"] == "replace" and self.sel:
                self.select(None)
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
        pointer first when it is not selected yet."""
        if not self.model:
            return
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
            target.add_command(label=text, compound="left", image=self.image(icon),
                               state="normal" if enabled else "disabled",
                               command=lambda k=key: self.create_at(k, wx, wz))
            if key == ("waypoint",):
                add.add_cascade(label="Event", menu=events, compound="left", image=self.image("addevent"))
                add.add_separator()
        add.add_command(label="Move player here", command=lambda: self.move_player_here(wx, wz))
        menu.insert_cascade(0, label="Add here", menu=add, compound="left", image=self.image("addhost"))
        menu.insert_separator(1)
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def on_wheel(self, e):
        self.zoom_by(1.2 ** (e.delta / 120), e.x, e.y)

    def on_hover(self, e):
        self.mouse = (e.x, e.y)
        if not self.model:
            return
        wx, wz = self.view.s2w(e.x, e.y)
        g = self.model.ground(wx, wz)
        gtxt = f"   (ground {g:.1f})" if g is not None else ""
        self.coord_lbl.config(text=f"{wx:.2f}  {wz:.2f} Meters{gtxt}")

    def on_key(self, e):
        if not self.model or self.typing():
            return
        k = e.keysym.lower()
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
            if self.place:
                self.end_place("Placement ended.")
            else:
                self.select(None)
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

    def render_base(self):
        m, v = self.model, self.view
        img = Image.new("RGB", (v.w, v.h), WORKSPACE)
        if self.layer("Terrain") and self._terrain_img is not None:
            t = m.terrain
            x0, y0 = v.w2s(t.origin[0], t.origin[2] + t.size_z)
            x1, y1 = v.w2s(t.origin[0] + t.size_x, t.origin[2])
            cx0, cy0 = max(0.0, x0), max(0.0, y0)
            cx1, cy1 = min(float(v.w), x1), min(float(v.h), y1)
            if cx1 > cx0 and cy1 > cy0:
                iw, ih = self._terrain_img.size
                sx, sy = iw / (x1 - x0), ih / (y1 - y0)
                box = ((cx0 - x0) * sx, (cy0 - y0) * sy, (cx1 - x0) * sx, (cy1 - y0) * sy)
                size = (max(1, int(round(cx1 - cx0))), max(1, int(round(cy1 - cy0))))
                part = self._terrain_img.resize(size, Image.BILINEAR, box=box)
                img.paste(part, (int(round(cx0)), int(round(cy0))))
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
        c.tag_raise("banner")
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
                if chosen and sel_ev is not None and sel_ev in m.spawns and pts:
                    sp, _, _ = m.world(m.tr_of_go[m.event_go[sel_ev]])
                    sx, sy = v.w2s(sp[0], sp[2])
                    near = min(pts, key=lambda q: (q[0] - sx) ** 2 + (q[1] - sy) ** 2)
                    c.create_line(sx, sy, near[0], near[1], fill=col, width=2, dash=(6, 4), tags="overlay")

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

        for g_sel in [g for g in self.sel if g in m.go]:
            trp = m.tr_of_go.get(g_sel)
            if trp is None:
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
            self.field_rows(ev, SPAWN_FIELDS)
            self.prop_sep()
            self.prop_tanklist("Trigger tanks", ev, "Trigger_Tanks", "Trigger_Num")
        elif kind == "event":
            ev = m.event_of_go(go)
            d = m.sc.read(ev)
            self.prop_info("Event type", label(EVENT_TYPES, d["Event_Type"]))
            self.field_rows(ev, EVENT_FIELDS)
            self.prop_tanklist("Trigger tanks", ev, "Trigger_Tanks", "Trigger_Num")
            if d["Event_Type"] in (2, 3, 6):
                self.prop_tanklist("Affected tanks", ev, "Target_Tanks", "Target_Num")
            extra = EVENT_TYPE_FIELDS.get(d["Event_Type"])
            if extra:
                self.prop_sep()
                if d["Event_Type"] == 2:
                    self.prop_ref_combo("New: follow target", [ev], "New_Follow_Target")
                    packs = [0] + m.packs
                    pnames = ["(no change)"] + [m.name(m.go_of_tr[p]) for p in m.packs]
                    cur = m.pack_tr(d["New_WayPoint_Pack"]["m_PathID"]) if d["New_WayPoint_Pack"]["m_PathID"] else 0
                    self.prop_combo("New: waypoint pack", pnames, packs.index(cur) if cur in packs else 0,
                                    lambda i: self.op("Set new waypoint pack", m.set_field, ev, "New_WayPoint_Pack",
                                                      {"m_FileID": 0, "m_PathID": m.go_of_tr[packs[i]] if packs[i] else 0}))
                self.field_rows(ev, extra)
        else:
            if kind == "waypoint":
                pack = m.waypoints[m.tr_of_go[go]]
                kids = [c["m_PathID"] for c in m.tr[pack]["m_Children"]]
                idx = kids.index(m.tr_of_go[go]) + 1 if m.tr_of_go[go] in kids else "?"
                self.prop_info("Pack / order", f"{m.name(m.go_of_tr[pack])} #{idx}")
            self.transform_rows(go, heading=(kind != "waypoint"), scale=(kind == "object"))
            if kind == "object":
                self.prop_info("Category", category(m.name(go)))
            self.prop_info("Path", m.path(go))

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
        self.prop_sep()
        for type_name, script, title, key, kind in MISSION_FIELDS:
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
        lighting = [e for e in m.events if m.sc.read(e)["Event_Type"] == 7]
        ai = [e for e in m.events if m.sc.read(e)["Event_Type"] == 2]
        self.prop_sep()
        self.prop_info("Fog changes", ", ".join(m.name(m.event_go[e]) for e in lighting) or "none")
        self.prop_info("AI changes", ", ".join(m.name(m.event_go[e]) for e in ai) or "none")
        self.prop_info("Hint", "Fog density and colour set the look; visibility sets how far tanks can see "
                               "each other. 'Change lighting' events (Table - Events) change both during "
                               "the mission. Allied groups: select the tanks and set a follow target "
                               "(for example the Player) and distance in Properties.")
        self.prop_sep()
        self.prop_info("Spawns", str(len(m.spawns)))
        self.prop_info("Other events", str(len(m.events) - len(m.spawns)))
        self.prop_info("Waypoints", str(len(m.waypoints)))
        self.prop_info("Scenery meshes", str(len(m.foot)))

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

    def prop_ref_combo(self, title, evs, key, exclude=None):
        m = self.model
        opts = self.spawn_ref_options(exclude)
        cur = {m.sc.read(e)[key]["m_PathID"] for e in evs}
        values = [o[1] for o in opts]
        idx = values.index(next(iter(cur))) if len(cur) == 1 and next(iter(cur)) in values else -1

        def commit(i):
            ref = {"m_FileID": 0, "m_PathID": opts[i][1]}
            self.op(f"Set {key}", lambda: [m.set_field(e, key, dict(ref)) for e in evs])
        self.prop_combo(title, [o[0] for o in opts], idx, commit)

    def tank_names(self, refs):
        m = self.model
        names = []
        for r in refs:
            go = m.go_of_tr.get(r["m_PathID"], r["m_PathID"]) if r["m_FileID"] == 0 else None
            if go in m.go:
                names.append(m.name(go))
        return ", ".join(names) if names else "none"

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

    def prop_info(self, name, text):
        r = self._next()
        tk.Label(self.props, text=name, anchor="w").grid(row=r, column=0, sticky="nw", padx=(0, 6), pady=1)
        tk.Label(self.props, text=text, anchor="w", justify="left", wraplength=170, fg="#404040").grid(
            row=r, column=1, sticky="w", pady=1)

    def prop_entry(self, name, value, commit):
        r = self._next()
        tk.Label(self.props, text=name, anchor="w").grid(row=r, column=0, sticky="w", padx=(0, 6), pady=1)
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

    def prop_check(self, name, value, commit):
        r = self._next()
        tk.Label(self.props, text=name, anchor="w").grid(row=r, column=0, sticky="w", padx=(0, 6), pady=1)
        var = tk.BooleanVar(value=bool(value))
        ttk.Checkbutton(self.props, variable=var, command=lambda: commit(var.get())).grid(
            row=r, column=1, sticky="w")

    def prop_combo(self, name, values, current, commit):
        r = self._next()
        tk.Label(self.props, text=name, anchor="w").grid(row=r, column=0, sticky="w", padx=(0, 6), pady=1)
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
            for lst in ("Trigger_Tanks", "Target_Tanks", "Disabled_Events", "Useless_Events"):
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
    root = tk.Tk()
    EditorApp(root, args.game, args.scene)
    root.mainloop()


if __name__ == "__main__":
    main()
