# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Bring a mission's briefing (menu) scene in line with its battle scene.

The briefing scene of a mission is separate from the battle scene. It lists the
unit groups (spawn events sharing a Key_Name) with a default tank and a unit
count, and shows one 3D symbol per unit on its map at the spawn positions. The
mission asset stores the number of allied AI tanks (Allies_Count). Copying a
mission or editing its units leaves all of these at the old values; this module
rebuilds them from the battle scene:

  - one tank-list row per unit group: key name, default tank (most common tank
    in the group), tank name text and "xN" count; rows are added (copied from a
    row of the same side) or deactivated as groups appear or disappear,
  - one symbol per active unit, moved to its spawn position and heading;
    symbols are added (copied) or deactivated as needed,
  - Allies_Count = number of active friendly AI spawns.
"""

import collections
import os
import shutil

import numpy as np

from scene_model import SceneModel, heading_of

ELEMENT = "Menu_Tank_Element_CS"
SIDES = ("Player", "Friend", "Enemy")


def _side_of_symbol(name):
    n = name.lower()
    if "player" in n:
        return "Player"
    if "friend" in n or "ally" in n:
        return "Friend"
    return "Enemy"


def battle_groups(battle):
    """{key: {"side", "units": [(pos, heading)], "tanks": Counter(entry)}} for active spawns."""
    groups = collections.OrderedDict()
    for e in battle.spawns:
        go = battle.event_go[e]
        if not battle.active(go):
            continue
        d = battle.sc.read(e)
        key = d["Key_Name"]
        if not key:
            continue
        side = "Player" if d["Tank_ID"] == 1 else ("Friend" if d["Relationship"] == 0 else "Enemy")
        g = groups.setdefault(key, {"side": side, "units": [], "tanks": collections.Counter()})
        p, r, _ = battle.world(battle.tr_of_go[go])
        g["units"].append((p, heading_of(r)))
        t = battle.tank_entry(e)
        if t:
            g["tanks"][t] += 1
    return groups


class BriefingSync:
    def __init__(self, ctx, menu_index):
        self.ctx = ctx
        self.menu = SceneModel(ctx, menu_index, terrain=None)
        self.report = []

    # ---- menu structure ----------------------------------------------

    def comps_of(self, go, cls=None, type_name=None):
        m = self.menu
        out = []
        for c in m.comps.get(go, []):
            t = m.sc.type_name(c)
            if type_name and t == type_name:
                out.append(c)
            elif cls and t == "MonoBehaviour" and m.script_class(c) == cls:
                out.append(c)
        return out

    def rows(self):
        """{key: (row GameObject, element pid)} of the tank list."""
        m = self.menu
        out = collections.OrderedDict()
        for el in m.mb:
            if m.script_class(el) != ELEMENT:
                continue
            go = m.sc.read(el, head_only=True)["m_GameObject"]["m_PathID"]
            out[m.sc.read(el)["This_Tank_Element"]["Key_Name"]] = (go, el)
        return out

    def symbol_gos(self, el):
        m = self.menu
        gos = []
        for r in m.sc.read(el)["symbolAnimators"]:
            pid = r["m_PathID"]
            if r["m_FileID"] == 0 and (pid in m.sc.objects or pid in m.sc.new_objects):
                gos.append(m.sc.read(pid)["m_GameObject"]["m_PathID"])
        return gos

    def row_side(self, key, el):
        if key == "Player":
            return "Player"
        syms = self.symbol_gos(el)
        return _side_of_symbol(self.menu.name(syms[0])) if syms else "Enemy"

    def text_under(self, row, name):
        m = self.menu
        for trp in m.subtree(m.tr_of_go[row]):
            g = m.go_of_tr[trp]
            if m.name(g) == name:
                for c in self.comps_of(g, cls="Text"):
                    return c
        return None

    def set_text(self, text_pid, value):
        if text_pid is None:
            return
        m = self.menu
        d = m.edit(text_pid)
        if d.get("m_Text") != value:
            d["m_Text"] = value
            m._set(text_pid, d)

    def set_active(self, go, on):
        m = self.menu
        if bool(m.go[go]["m_IsActive"]) != bool(on):
            d = m.edit(go)
            d["m_IsActive"] = bool(on)
            m._set(go, d)

    # ---- synchronisation ---------------------------------------------

    def run(self, battle):
        m = self.menu
        groups = battle_groups(battle)
        rows = self.rows()
        m.begin("Sync briefing")
        try:
            sides = {key: self.row_side(key, el) for key, (row, el) in rows.items()}
            template_row = {}
            template_symbol = {}
            for key, (row, el) in rows.items():
                template_row.setdefault(sides[key], (row, el))
                syms = self.symbol_gos(el)
                if syms:
                    template_symbol.setdefault(sides[key], syms[0])

            for key, (row, el) in rows.items():
                if key not in groups:
                    self.set_active(row, False)
                    for s in self.symbol_gos(el):
                        self.set_active(s, False)
                    self.report.append(f"Removed group '{key}' from the briefing (no units left).")

            for key, g in groups.items():
                side = g["side"]
                if key in rows:
                    row, el = rows[key]
                    self.set_active(row, True)
                    current = self.symbol_gos(el)
                else:
                    tpl = template_row.get(side) or template_row.get("Enemy") or next(iter(template_row.values()), None)
                    if tpl is None:
                        self.report.append(f"Group '{key}' could not be added: the briefing has no rows.")
                        continue
                    row = m.duplicate(tpl[0], offset=None)
                    el = next(iter(self.comps_of(row, cls=ELEMENT)))
                    m.rename(row, f"Text (Key Name) ({key})")
                    current = []
                    self.report.append(f"Added group '{key}' to the briefing.")
                self.update_row(key, row, el, g, current, template_symbol.get(side) or
                                template_symbol.get("Enemy" if side != "Enemy" else "Friend"))
        finally:
            m.commit()
        return groups

    def update_row(self, key, row, el, group, current, template_symbol):
        m = self.menu
        n = len(group["units"])
        d = m.edit(el)
        te = dict(d["This_Tank_Element"])
        te["Key_Name"] = key
        if group["tanks"]:
            entry, count = group["tanks"].most_common(1)[0]
            te["Tank_Prop"] = {"m_FileID": m.sc.ensure_external_path(entry[0]), "m_PathID": entry[1]}
            self.set_text(d["tankNameText"]["m_PathID"] if d["tankNameText"]["m_PathID"] else None, entry[3])
            if len(group["tanks"]) > 1:
                self.report.append(f"Group '{key}' mixes tank types; the briefing offers one type per group "
                                   f"(default {entry[3]}).")
        d["This_Tank_Element"] = te
        self.set_text(d["keyNameText"]["m_PathID"] if d["keyNameText"]["m_PathID"] else None, key)
        units_text = self.text_under(row, "Text (Units)")
        self.set_text(units_text, f"x{n}")

        symbols = list(current)
        source = symbols[0] if symbols else template_symbol
        while len(symbols) < n and source is not None:
            symbols.append(m.duplicate(source, offset=None))
        for extra in symbols[n:]:
            self.set_active(extra, False)
        symbols = symbols[:n]
        animators = []
        for s, (pos, heading) in zip(symbols, group["units"]):
            self.set_active(s, True)
            trp = m.tr_of_go[s]
            y = m.world(trp)[0][1]
            m.set_world_position(trp, np.array((pos[0], y, pos[2])))
            m.set_heading(trp, heading)
            an = self.comps_of(s, type_name="Animator")
            if an:
                animators.append({"m_FileID": 0, "m_PathID": an[0]})
        if len(symbols) < n:
            self.report.append(f"Group '{key}': no symbol to copy; the briefing map shows {len(symbols)} of {n}.")
        d["symbolAnimators"] = animators
        m._set(el, d)


def allies_count(battle):
    n = 0
    for e in battle.spawns:
        d = battle.sc.read(e)
        if battle.active(battle.event_go[e]) and d["Relationship"] == 0 and d["Tank_ID"] != 1:
            n += 1
    return n


def sync_mission(ctx, scenarios, battle, backup_dir):
    """Update the briefing scene and Allies_Count of the mission whose battle
    scene is `battle` (a SceneModel reflecting the saved state). Returns a
    list of report lines. The briefing scene is backed up once like any level."""
    found = scenarios.mission_for_scene(battle.label)
    if found is None:
        raise ValueError("this scene has no mission entry")
    _, mission = found
    menu_name = mission["Menu_Scene_Name"]
    menu_index = scenarios.scene_index(menu_name)
    if menu_index is None:
        raise ValueError(f"briefing scene {menu_name} is not in the build list")
    sync = BriefingSync(ctx, menu_index)
    groups = sync.run(battle)
    path = ctx.scene_path(menu_index)
    if sync.menu.dirty:
        os.makedirs(backup_dir, exist_ok=True)
        bak = os.path.join(backup_dir, f"level{menu_index}.orig")
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
        sync.menu.save()
    allies = allies_count(battle)
    if mission.get("Allies_Count") != allies:
        scenarios.set_mission_fields(battle.label, Allies_Count=allies)
    summary = ", ".join(f"{k} x{len(g['units'])}" for k, g in groups.items())
    return [f"Briefing updated: {summary}; allies {allies}."] + sync.report


__all__ = ["sync_mission", "battle_groups", "allies_count", "BriefingSync"]
