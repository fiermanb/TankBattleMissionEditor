# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Lighting of a custom scenario: day, dusk and night.

The look of a battle scene comes from its directional light (the sun, or the
moon at night), the ambient light and the fog in RenderSettings, and the sky:
a skybox material shared by the missions. A darker sky needs its own material,
so this module copies the scene's day skybox into the scenario's own asset
file (sharedassetsN.assets, as for the terrain copy) and changes its exposure
and tint there. The ambient light is set as a flat colour: RenderSettings
holds the precomputed ambient probe the game uses, whose first coefficient per
colour channel is that colour.

Values (Lighting.lighting()/apply()), colours as (r, g, b) 0..1:
  sun_colour, sun_intensity, ambient, fog_colour, sky_brightness (skybox
  exposure, 1 = day), sky_colour (skybox tint; white = unchanged), visibility
  (AI visibility in metres), dust (dust particle multiplier).

The fog density stays as it is: every mission has fog (light haze, or dense
fog in the fog missions), and 'Change lighting' events change its density and
the visibility during a mission. A preset never raises the visibility, so
night on a fog scenario keeps its short visibility. Dust particles are not
darkened by the light, so the darker presets turn them down (the fog missions
use 0.1 too).

Day is the lighting of the mission the scenario was made from. The sky copy is
not part of the battle scene, so backups record it (sky_record()) and a
restore makes it again (restore_sky()).
"""

import copy
import os

from scene_io import SceneFile

SKY_NAME = "Night Skybox"
PRESETS = {
    "dusk": {"sun_colour": (0.78, 0.82, 0.95), "sun_intensity": 0.3, "ambient": (0.06, 0.062, 0.072),
             "fog_colour": (0.03, 0.035, 0.05), "sky_brightness": 0.12, "sky_colour": (0.72, 0.8, 1.0),
             "visibility": 1000.0, "dust": 0.3},
    "night": {"sun_colour": (0.78, 0.82, 0.95), "sun_intensity": 0.18, "ambient": (0.04, 0.042, 0.05),
              "fog_colour": (0.03, 0.035, 0.05), "sky_brightness": 0.08, "sky_colour": (0.72, 0.8, 1.0),
              "visibility": 600.0, "dust": 0.1},
}
PRESET_NAMES = {"day": "Day", "dusk": "Dusk", "night": "Night", "custom": "Custom"}
NIGHT_REFLECTION = 0.1
NIGHT_SHADOW_STRENGTH = 0.6
TOLERANCE = 0.004


def _rgb(c):
    return (float(c["r"]), float(c["g"]), float(c["b"]))


def _colour(rgb, alpha=1.0):
    return {"r": float(rgb[0]), "g": float(rgb[1]), "b": float(rgb[2]), "a": float(alpha)}


def _props(mat):
    p = mat["m_SavedProperties"]
    return dict(p["m_Floats"]), dict(p["m_Colors"])


def _with_props(mat, exposure, tint):
    p = dict(mat["m_SavedProperties"])
    p["m_Floats"] = [(k, float(exposure) if k == "_Exposure" else v) for k, v in p["m_Floats"]]
    p["m_Colors"] = [(k, dict(tint) if k == "_Tint" else v) for k, v in p["m_Colors"]]
    return dict(mat, m_SavedProperties=p)


class Lighting:
    """Lighting of SceneModel `model`; `day`: SceneFile of the battle scene the
    scenario was made from (its day lighting and sky)."""

    def __init__(self, ctx, model, day):
        self.ctx = ctx
        self.m = model
        self.day = day
        self.own = os.path.basename(model.own_assets().path)

    # ---- objects of a scene -----------------------------------------------------

    @staticmethod
    def parts(sc):
        """(sun light pid, RenderSettings pid, Lighting_Control_CS pid) of a scene file."""
        sun = rs = ctrl = None
        for p in sc.all_pids():
            t = sc.type_name(p)
            if t == "Light" and sc.read(p)["m_Type"] == 1 and sun is None:
                sun = p
            elif t == "RenderSettings":
                rs = p
            elif t == "MonoBehaviour" and sc.script_class(p) == "Lighting_Control_CS":
                ctrl = p
        return sun, rs, ctrl

    def day_sky(self):
        """(file, pid) of the day skybox material of the source scene."""
        _, rs, _ = self.parts(self.day)
        ref = self.day.read(rs)["m_SkyboxMaterial"]
        return self.day.externals[ref["m_FileID"] - 1], ref["m_PathID"]

    def own_sky(self):
        """pid of the scenario's own sky material, or None."""
        m = self.m
        _, rs, _ = self.parts(m.sc)
        ref = m.sc.read(rs)["m_SkyboxMaterial"]
        if not ref["m_FileID"] or m.sc.externals[ref["m_FileID"] - 1] != self.own:
            return None
        assets = m.own_assets()
        pid = ref["m_PathID"]
        if pid not in assets.all_pids() or assets.type_name(pid) != "Material":
            return None
        return pid

    # ---- reading ------------------------------------------------------------------

    def _values(self, sc, sky):
        """Lighting values of scene file sc whose sky material data is `sky`."""
        sun, rs, ctrl = self.parts(sc)
        s, r = sc.read(sun), sc.read(rs)
        c = sc.read(ctrl) if ctrl else {}
        floats, colours = _props(sky)
        tint = _rgb(colours.get("_Tint", {"r": 0.5, "g": 0.5, "b": 0.5}))
        return {"sun_colour": _rgb(s["m_Color"]), "sun_intensity": float(s["m_Intensity"]),
                "ambient": _rgb(r["m_AmbientSkyColor"]), "ambient_mode": r["m_AmbientMode"],
                "fog_colour": _rgb(r["m_FogColor"]), "sky_brightness": float(floats.get("_Exposure", 1.0)),
                "sky_colour": tuple(min(1.0, x * 2) for x in tint),
                "visibility": float(c["Current_Visibility"]) if "Current_Visibility" in c else None,
                "dust": float(c["Dust_Particle_Multiplier"]) if "Dust_Particle_Multiplier" in c else None}

    def _day_sky_data(self):
        f, pid = self.day_sky()
        return SceneFile(self.ctx, os.path.join(self.ctx.data_dir, f)).read(pid)

    def lighting(self):
        """The scene's lighting values."""
        own = self.own_sky()
        sky = self.m.own_assets().read(own) if own is not None else self._day_sky_data()
        return self._values(self.m.sc, sky)

    def day_values(self):
        """Lighting values of the source mission."""
        return self._values(self.day, self._day_sky_data())

    def preset(self):
        """'day', 'dusk', 'night' or 'custom' for the current values."""
        cur = self.lighting()
        day = self.day_values()
        keys = ("sun_colour", "sun_intensity", "ambient", "fog_colour", "sky_brightness", "sky_colour")
        if self.own_sky() is None and cur["ambient_mode"] == day["ambient_mode"] and self._same(cur, day, keys):
            return "day"
        for name, values in PRESETS.items():
            if self._same(cur, values, keys):
                return name
        return "custom"

    @staticmethod
    def _same(a, b, keys):
        for k in keys:
            x, y = a.get(k), b.get(k)
            if x is None or y is None:
                continue
            xs = tuple(x) if isinstance(x, (tuple, list)) else (x,)
            ys = tuple(y) if isinstance(y, (tuple, list)) else (y,)
            if any(abs(p - q) > TOLERANCE for p, q in zip(xs, ys)):
                return False
        return True

    # ---- changing (inside model.begin()/commit()) ------------------------------------

    def apply(self, values, preset=False):
        """Set lighting values: only the keys given change (a preset gives all;
        with preset=True its visibility and dust only lower the current ones).
        Returns an undo function for the scenario's own sky material, or None."""
        cur = self.lighting()
        v = dict(values)
        if preset:
            for k in ("visibility", "dust"):
                if cur.get(k) is not None and v.get(k) is not None:
                    v[k] = min(cur[k], v[k])
        m = self.m
        sun, rs, ctrl = self.parts(m.sc)
        if "sun_colour" in v or "sun_intensity" in v:
            d = m.edit(sun)
            if "sun_colour" in v:
                d["m_Color"] = _colour(v["sun_colour"])
            if "sun_intensity" in v:
                d["m_Intensity"] = float(v["sun_intensity"])
            if preset:
                d["m_Shadows"] = dict(d["m_Shadows"], m_Strength=NIGHT_SHADOW_STRENGTH)
            m._set(sun, d)
        if "ambient" in v or "fog_colour" in v or "sky_brightness" in v:
            d = m.edit(rs)
            if "ambient" in v:
                d["m_AmbientMode"] = 3
                for key in ("m_AmbientSkyColor", "m_AmbientEquatorColor", "m_AmbientGroundColor"):
                    d[key] = _colour(v["ambient"])
                probe = {k: 0.0 for k in d["m_AmbientProbe"]}
                probe["sh[ 0]"], probe["sh[ 9]"], probe["sh[18]"] = v["ambient"]
                d["m_AmbientProbe"] = probe
            if "fog_colour" in v:
                d["m_FogColor"] = _colour(v["fog_colour"], d["m_FogColor"].get("a", 1.0))
            if preset:
                d["m_ReflectionIntensity"] = NIGHT_REFLECTION
            m._set(rs, d)
        if ctrl is not None and ("visibility" in v or "dust" in v):
            c = m.edit(ctrl)
            if v.get("visibility"):
                c["Current_Visibility"] = float(v["visibility"])
            if v.get("dust") is not None and "Dust_Particle_Multiplier" in c:
                c["Dust_Particle_Multiplier"] = float(v["dust"])
            m._set(ctrl, c)
        if "sky_brightness" in v or "sky_colour" in v:
            return self._set_sky(v.get("sky_brightness", cur["sky_brightness"]),
                                 v.get("sky_colour", cur["sky_colour"]))
        return None

    def _set_sky(self, brightness, colour):
        """Give the scene its own sky material with this exposure and colour."""
        m = self.m
        assets = m.own_assets()
        tint = _colour(tuple(c / 2 for c in colour), 0.5)
        pid = self.own_sky()
        if pid is not None:
            before = copy.deepcopy(assets.read(pid))
            assets.write(pid, _with_props(assets.read(pid), brightness, tint))

            def undo():
                assets.write(pid, before)
            return undo
        f, src_pid = self.day_sky()
        src = SceneFile(self.ctx, os.path.join(self.ctx.data_dir, f))
        mat = copy.deepcopy(src.read(src_pid))
        mat["m_Name"] = SKY_NAME

        def remap(x):
            if isinstance(x, dict):
                if set(x) == {"m_FileID", "m_PathID"}:
                    if not x["m_PathID"]:
                        return x
                    path = f if x["m_FileID"] == 0 else src.externals[x["m_FileID"] - 1]
                    return {"m_FileID": assets.ensure_external_path(path), "m_PathID": x["m_PathID"]}
                return {k: remap(y) for k, y in x.items()}
            if isinstance(x, list):
                return [remap(y) for y in x]
            if isinstance(x, tuple):
                return tuple(remap(y) for y in x)
            return x
        tid = assets.ensure_type(src, src.type_id(src_pid))
        new = assets.add_foreign(tid, "Material", src.nodes_for(src_pid), _with_props(remap(mat), brightness, tint))
        _, rs, _ = self.parts(m.sc)
        d = m.edit(rs)
        d["m_SkyboxMaterial"] = {"m_FileID": m.sc.ensure_external_path(self.own), "m_PathID": new}
        m._set(rs, d)

        def undo():
            assets.remove_new(new)
        return undo

    def apply_day(self):
        """Back to the lighting of the source mission (its sun, ambient light,
        fog colour, reflections, sky and visibility)."""
        m = self.m
        sun, rs, ctrl = self.parts(m.sc)
        dsun, drs, dctrl = self.parts(self.day)
        d = m.edit(sun)
        src = self.day.read(dsun)
        for k in ("m_Color", "m_Intensity", "m_Shadows"):
            d[k] = copy.deepcopy(src[k])
        m._set(sun, d)
        d = m.edit(rs)
        src = self.day.read(drs)
        for k in ("m_AmbientMode", "m_AmbientSkyColor", "m_AmbientEquatorColor", "m_AmbientGroundColor",
                  "m_AmbientIntensity", "m_AmbientProbe", "m_FogColor", "m_ReflectionIntensity"):
            d[k] = copy.deepcopy(src[k])
        f, pid = self.day_sky()
        d["m_SkyboxMaterial"] = {"m_FileID": m.sc.ensure_external_path(f), "m_PathID": pid}
        m._set(rs, d)
        if ctrl is not None and dctrl is not None:
            c = m.edit(ctrl)
            for k in ("Current_Visibility", "Dust_Particle_Multiplier"):
                if k in c and k in self.day.read(dctrl):
                    c[k] = self.day.read(dctrl)[k]
            m._set(ctrl, c)

    # ---- backups ----------------------------------------------------------------

    def sky_record(self):
        """The own sky's settings for a backup, or None (the day sky)."""
        pid = self.own_sky()
        if pid is None:
            return None
        floats, colours = _props(self.m.own_assets().read(pid))
        return {"exposure": float(floats.get("_Exposure", 1.0)), "tint": _rgb(colours.get("_Tint", {}))}

    def restore_sky(self, record):
        """After a restore: the sky reference of the converted scene may point
        into the old scenario's asset file. Make the own sky again from the
        source mission's skybox (record from sky_record()), or go back to the
        day sky when there is none. Inside model.begin()/commit()."""
        m = self.m
        _, rs, _ = self.parts(m.sc)
        ref = m.sc.read(rs)["m_SkyboxMaterial"]
        f, pid = self.day_sky()
        if not record and ref["m_FileID"] and m.sc.externals[ref["m_FileID"] - 1] == f and ref["m_PathID"] == pid:
            return
        d = m.edit(rs)
        d["m_SkyboxMaterial"] = {"m_FileID": m.sc.ensure_external_path(f), "m_PathID": pid}
        m._set(rs, d)
        if record:
            self._set_sky(record["exposure"], tuple(min(1.0, c * 2) for c in record["tint"]))


def sky_record(ctx, sc, own_file):
    """The own sky's settings of battle scene file sc for a backup (exposure,
    tint), or None when it uses a game sky."""
    rs = next((p for p in sc.all_pids() if sc.type_name(p) == "RenderSettings"), None)
    if rs is None:
        return None
    ref = sc.read(rs)["m_SkyboxMaterial"]
    if not ref["m_FileID"] or sc.externals[ref["m_FileID"] - 1] != own_file:
        return None
    path = os.path.join(ctx.data_dir, own_file)
    if not os.path.exists(path):
        return None
    assets = SceneFile(ctx, path)
    if ref["m_PathID"] not in assets.all_pids() or assets.type_name(ref["m_PathID"]) != "Material":
        return None
    floats, colours = _props(assets.read(ref["m_PathID"]))
    return {"exposure": float(floats.get("_Exposure", 1.0)), "tint": list(_rgb(colours.get("_Tint", {})))}


def source_scene(ctx, scenarios, entry):
    """SceneFile of the battle scene of the mission a custom scenario was made
    from, or None."""
    src = next((d for _, d in scenarios.missions() if d["Scene_ID"] == entry.get("source")), None)
    if src is None:
        return None
    idx = scenarios.scene_index(src["Battle_Scene_Name"])
    return SceneFile(ctx, ctx.scene_path(idx)) if idx is not None else None


__all__ = ["Lighting", "PRESETS", "PRESET_NAMES", "source_scene", "sky_record"]
