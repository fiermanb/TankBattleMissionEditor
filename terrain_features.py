# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Terrain features: the ground of a custom scenario drawn with profile brushes.

A brush is a cross-section with a few parameters (width, height change, side
slope, rim, ground texture). A stroke is a brush drawn along a line of points,
or at one point. The scenario keeps its strokes and the ground they were drawn
on (the base); the ground is always computed from the base and all strokes, so
a stroke can be changed, moved or removed later, and drawing over the same
place twice gives the same ground as drawing it once:

  1. level and smooth strokes, in the order drawn, bring the ground to a
     height (a fixed one, or the smoothed ground) inside their width; outside
     it the ground meets them at the side slope,
  2. raising strokes: where several overlap, the highest counts,
  3. cutting strokes: where several overlap, the deepest counts; their rims
     (the spoil along the edges) apply where no stroke cuts.

Ground textures are painted in the order drawn. Cutting strokes can leave
undug crossings for the AI at a fixed spacing (the AI route map treats ground
steeper than AI_MAX_SLOPE as blocked).

Stored per scenario in its backup folder: BASE_FILE (heights and weight maps
of the base) and FEATURES_FILE (the strokes, and a checksum of the ground they
give, to notice a ground changed by other means).
"""

import hashlib
import io
import json
import math
import os

import numpy as np

from terrain_edit import HEIGHT_MAX

KINDS = ("shape", "level", "smooth", "paint")
KIND_NAMES = {"shape": "Cut or raise", "level": "Level to a height", "smooth": "Smooth", "paint": "Texture only"}
CROSSING_WIDTH = 16.0
LEVEL_REACH = 60.0
TEXTURE_EDGE = 1.0
AI_MAX_SLOPE = 20.0
BASE_FILE = "terrain_base.npz"
FEATURES_FILE = "terrain_features.json"
FORMAT = 1
LIMITS = {"width": (0.5, 400.0), "height": (-30.0, 30.0), "slope": (1.0, 89.0), "rim": (0.0, 5.0),
          "crossings": (0.0, 2000.0)}
BRUSH_DEFAULTS = {"kind": "shape", "width": 4.0, "height": -1.0, "slope": 45.0, "rim": 0.0, "texture": "",
                  "crossings": 0.0, "level": None}
DEFAULT_BRUSHES = (
    {"name": "Trench", "kind": "shape", "width": 2.4, "height": -1.6, "slope": 70.0, "rim": 0.3,
     "texture": "Mud", "crossings": 90.0},
    {"name": "Anti-tank ditch", "kind": "shape", "width": 7.0, "height": -2.5, "slope": 60.0, "rim": 0.6,
     "texture": "Mud", "crossings": 0.0},
    {"name": "Tank pit", "kind": "shape", "width": 9.0, "height": -1.6, "slope": 30.0, "rim": 0.4,
     "texture": "Mud"},
    {"name": "Crater", "kind": "shape", "width": 6.0, "height": -1.0, "slope": 35.0, "rim": 0.4, "texture": "Mud"},
    {"name": "Road", "kind": "smooth", "width": 7.0, "slope": 15.0, "texture": "Road"},
    {"name": "Embankment", "kind": "shape", "width": 12.0, "height": 2.0, "slope": 30.0},
    {"name": "Low ridge", "kind": "shape", "width": 40.0, "height": 3.0, "slope": 10.0},
    {"name": "Level ground", "kind": "level", "width": 20.0, "slope": 20.0},
    {"name": "Smooth", "kind": "smooth", "width": 10.0, "slope": 20.0},
    {"name": "Paint", "kind": "paint", "width": 8.0, "texture": "Mud"},
)


def clean_brush(b):
    """A brush dict with every parameter present and within its limits."""
    out = dict(BRUSH_DEFAULTS)
    out.update({k: v for k, v in b.items() if k in BRUSH_DEFAULTS or k == "name"})
    if out["kind"] not in KINDS:
        raise ValueError(f"unknown brush kind '{out['kind']}'")
    for key, (lo, hi) in LIMITS.items():
        v = float(out[key])
        if not math.isfinite(v):
            raise ValueError(f"{key} must be a number")
        out[key] = min(max(v, lo), hi)
    out["texture"] = str(out["texture"] or "")
    if out["level"] is not None:
        out["level"] = float(out["level"])
    return out


def default_brushes():
    return [clean_brush(b) for b in DEFAULT_BRUSHES]


def layer_label(name):
    """'Terrain_Layer (Mud)' -> 'Mud'."""
    if "(" in name and name.endswith(")"):
        return name[name.index("(") + 1:-1]
    return name


def find_layer(names, texture):
    """Index of the terrain layer called `texture` (as layer_label shows it), or None."""
    if not texture:
        return None
    t = texture.strip().lower()
    labels = [layer_label(n).lower() for n in names]
    if t in labels:
        return labels.index(t)
    hits = [i for i, n in enumerate(labels) if t in n]
    return hits[0] if hits else None


def rim_band(rim):
    """Half width of the rim bump outside the brush edge, in metres."""
    return max(1.0, 4.0 * rim) if rim > 0 else 0.0


def half_width(s):
    return s["width"] / 2.0


def reach(s):
    """Distance from the stroke's line within which it changes the ground."""
    w = half_width(s)
    if s["kind"] == "shape":
        return w + (2 * rim_band(s["rim"]) if s["height"] < 0 else 0.0) + 0.5
    if s["kind"] in ("level", "smooth"):
        return w + LEVEL_REACH
    return w + TEXTURE_EDGE


def texture_reach(s):
    """(half width, soft edge) of the stroke's ground texture."""
    w = half_width(s)
    if s["kind"] == "shape" and s["height"] < 0:
        w += 2 * rim_band(s["rim"])
    if s["kind"] == "paint":
        return w, max(TEXTURE_EDGE, 0.3 * w)
    return w, TEXTURE_EDGE


def length(points):
    p = np.asarray(points, dtype=float)
    return float(np.hypot(*np.diff(p, axis=0).T).sum()) if len(p) > 1 else 0.0


def line_distance(p, points):
    """(distance, segment index, nearest point) of world point p to the line
    through `points`."""
    best = (math.inf, 0, tuple(points[0]))
    pts = [tuple(q) for q in points]
    for i, (a, b) in enumerate(zip(pts, pts[1:] or pts)):
        dx, dz = b[0] - a[0], b[1] - a[1]
        u = min(max(((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / max(dx * dx + dz * dz, 1e-12), 0.0), 1.0)
        q = (a[0] + u * dx, a[1] + u * dz)
        d = math.hypot(p[0] - q[0], p[1] - q[1])
        if d < best[0]:
            best = (d, i, q)
    return best


def bbox(s, extra=None):
    p = np.asarray(s["points"], dtype=float)
    r = reach(s) if extra is None else extra
    return p[:, 0].min() - r, p[:, 1].min() - r, p[:, 0].max() + r, p[:, 1].max() + r


def _overlaps(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def distance_field(points, xs, zs, limit):
    """(d, t, length) on the grid of world columns xs and rows zs (ascending):
    distance to the line through `points` (inf beyond `limit`), the distance
    along the line of the nearest point, and the line's length."""
    d = np.full((len(zs), len(xs)), np.inf)
    t = np.zeros_like(d)
    pts = np.asarray(points, dtype=float)
    segs = list(zip(pts[:-1], pts[1:])) if len(pts) > 1 else [(pts[0], pts[0])]
    acc = 0.0
    for a, b in segs:
        seg = float(np.hypot(*(b - a)))
        c0 = np.searchsorted(xs, min(a[0], b[0]) - limit)
        c1 = np.searchsorted(xs, max(a[0], b[0]) + limit, side="right")
        r0 = np.searchsorted(zs, min(a[1], b[1]) - limit)
        r1 = np.searchsorted(zs, max(a[1], b[1]) + limit, side="right")
        if c0 < c1 and r0 < r1:
            X, Z = xs[None, c0:c1], zs[r0:r1, None]
            dx, dz = b - a
            u = np.clip(((X - a[0]) * dx + (Z - a[1]) * dz) / max(seg * seg, 1e-12), 0.0, 1.0)
            dd = np.hypot(X - (a[0] + u * dx), Z - (a[1] + u * dz))
            dd = np.where(dd <= limit, dd, np.inf)
            sub, tsub = d[r0:r1, c0:c1], t[r0:r1, c0:c1]
            better = dd < sub
            sub[better] = dd[better]
            tsub[better] = (acc + u * seg + np.zeros_like(dd))[better]
        acc += seg
    return d, t, acc


def box_blur(a, k):
    """Two passes of a (2k+1) box filter along both axes (edges extended)."""
    for _ in range(2):
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (k + 1, k)
            c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis)
            hi = np.take(c, range(2 * k + 1, c.shape[axis]), axis=axis)
            lo = np.take(c, range(0, c.shape[axis] - 2 * k - 1), axis=axis)
            a = (hi - lo) / (2 * k + 1)
    return a


def checksum(heights, alphas):
    h = hashlib.sha1(np.ascontiguousarray(heights, dtype=np.int32).tobytes())
    for a in alphas:
        h.update(np.ascontiguousarray(a, dtype=np.uint8).tobytes())
    return h.hexdigest()


class TerrainFeatures:
    """The strokes of one scenario on its base ground. Geometry `geo`: origin
    (x, y, z) and size (x, z) of the terrain in metres, height_scale (metres
    of the full height range)."""

    def __init__(self, base_heights, base_alphas, geo, strokes=None, next_id=1):
        self.base = np.asarray(base_heights, dtype=np.int32)
        self.base_alphas = [np.asarray(a, dtype=np.uint8) for a in base_alphas]
        self.origin = tuple(float(v) for v in geo["origin"])
        self.size = tuple(float(v) for v in geo["size"])
        self.height_scale = float(geo["height_scale"])
        self.strokes = list(strokes or [])
        self.next_id = next_id
        self.dirty = False

    @property
    def geo(self):
        return {"origin": list(self.origin), "size": list(self.size), "height_scale": self.height_scale}

    # ---- strokes ------------------------------------------------------------

    def make_stroke(self, brush, points, texture_index):
        """A new stroke (not yet added) of `brush` through world points [(x, z)].
        A level brush without a height levels to the ground at the first
        point."""
        b = clean_brush(brush)
        s = {k: b[k] for k in ("kind", "width", "height", "slope", "rim", "crossings")}
        s.update({"id": self.next_id, "brush": b.get("name", ""), "texture": texture_index,
                  "points": [[float(x), float(z)] for x, z in points]})
        if b["kind"] == "level":
            s["level"] = b["level"] if b["level"] is not None else self.ground_height(*points[0])
        self.next_id += 1
        return s

    def ground_height(self, x, z):
        """World height of the computed ground at (x, z) in metres."""
        res = self.base.shape[0]
        c = int(round(min(max((x - self.origin[0]) / self.size[0] * (res - 1), 0), res - 1)))
        r = int(round(min(max((z - self.origin[2]) / self.size[1] * (res - 1), 0), res - 1)))
        return float(self.heights_block(r, r + 1, c, c + 1)[0, 0]) / HEIGHT_MAX * self.height_scale + self.origin[1]

    def stroke(self, sid):
        return next((s for s in self.strokes if s["id"] == sid), None)

    # ---- computing the ground ----------------------------------------------

    def _cells(self, n):
        """World coordinates of heightmap samples 0..n-1 along x and z."""
        return (self.origin[0] + np.arange(n) * self.size[0] / (n - 1),
                self.origin[2] + np.arange(n) * self.size[1] / (n - 1))

    def heights_block(self, r0, r1, c0, c1):
        """Heightmap values (int32) of rows r0:r1 and columns c0:c1 computed from
        the base and all strokes."""
        res = self.base.shape[0]
        cell = self.size[0] / (res - 1)
        xs_all, zs_all = self._cells(res)
        box = (xs_all[c0] - 0.01, zs_all[r0] - 0.01, xs_all[c1 - 1] + 0.01, zs_all[r1 - 1] + 0.01)
        touching = [s for s in self.strokes if s["kind"] != "paint" and _overlaps(bbox(s), box)]
        if not touching:
            return self.base[r0:r1, c0:c1].copy()
        smooth = [s for s in touching if s["kind"] == "smooth"]
        kmax = max((self._blur_k(s, cell) for s in smooth), default=0)
        pad = 2 * kmax + 2 if smooth else 0
        R0, R1, C0, C1 = max(0, r0 - pad), min(res, r1 + pad), max(0, c0 - pad), min(res, c1 + pad)
        xs, zs = xs_all[C0:C1], zs_all[R0:R1]
        unit = self.height_scale / HEIGHT_MAX
        g = self.base[R0:R1, C0:C1].astype(np.float64) * unit
        wbox = (xs[0], zs[0], xs[-1], zs[-1])
        touching = [s for s in self.strokes if s["kind"] != "paint" and _overlaps(bbox(s), wbox)]
        for s in touching:
            if s["kind"] not in ("level", "smooth"):
                continue
            d, _, _ = distance_field(s["points"], xs, zs, reach(s))
            near = np.isfinite(d)
            if not near.any():
                continue
            w, sl = half_width(s), math.tan(math.radians(s["slope"]))
            if s["kind"] == "level":
                target = s["level"] - self.origin[1]
            else:
                target = box_blur(g, self._blur_k(s, cell))
            lim = np.maximum(np.where(near, d, 0.0) - w, 0.0) * sl
            g = np.where(near, np.clip(g, target - lim, target + lim), g)
        raised = np.zeros_like(g)
        cut = np.zeros_like(g)
        rims = np.zeros_like(g)
        for s in touching:
            if s["kind"] != "shape" or s["height"] == 0:
                continue
            d, t, total = distance_field(s["points"], xs, zs, reach(s))
            if not np.isfinite(d).any():
                continue
            w, sl = half_width(s), math.tan(math.radians(s["slope"]))
            prof = np.clip((w - d) * sl, 0.0, abs(s["height"]))
            if s["height"] > 0:
                np.maximum(raised, prof, out=raised)
                continue
            gap = s["crossings"]
            if gap > 0 and total >= gap / 2:
                centres = np.arange(gap / 2, total, gap)
                dc = np.abs(t[..., None] - centres).min(-1)
                prof = np.minimum(prof, np.clip((dc - CROSSING_WIDTH / 2) * sl, 0.0, None))
            np.minimum(cut, -prof, out=cut)
            if s["rim"] > 0:
                rb = rim_band(s["rim"])
                x = (d - w) / (2 * rb)
                bump = np.where((x > 0) & (x < 1), s["rim"] * np.sin(np.pi * np.clip(x, 0, 1)), 0.0)
                np.maximum(rims, bump, out=rims)
        g = g + raised + np.where(cut < 0, cut, rims)
        g = g[r0 - R0:r1 - R0, c0 - C0:c1 - C0]
        return np.clip(np.round(g / unit), 0, HEIGHT_MAX).astype(np.int32)

    @staticmethod
    def _blur_k(s, cell):
        return max(2, int(round(1.5 * half_width(s) / cell)))

    def alphas_block(self, r0, r1, c0, c1):
        """Weight maps (list of (h, w, 4) uint8) of pixel rows r0:r1 and columns
        c0:c1 computed from the base and all textured strokes."""
        res = self.base_alphas[0].shape[0]
        n = 4 * len(self.base_alphas)
        xs = self.origin[0] + (np.arange(c0, c1) + 0.5) * self.size[0] / res
        zs = self.origin[2] + (np.arange(r0, r1) + 0.5) * self.size[1] / res
        base = np.concatenate([a[r0:r1, c0:c1] for a in self.base_alphas], axis=2)
        wbox = (xs[0], zs[0], xs[-1], zs[-1])
        w = base.astype(np.float64)
        touched = np.zeros(base.shape[:2], dtype=bool)
        for s in self.strokes:
            tex = s.get("texture")
            if tex is None or not 0 <= tex < n:
                continue
            half, edge = texture_reach(s)
            if not _overlaps(bbox(s, half + edge), wbox):
                continue
            d, _, _ = distance_field(s["points"], xs, zs, half + edge)
            f = np.clip((half + edge / 2 - d) / edge, 0.0, 1.0)
            hit = f > 0
            if not hit.any():
                continue
            w *= (1.0 - f)[..., None]
            w[..., tex] += 255.0 * f
            touched |= hit
        out = base.copy()
        if touched.any():
            v = w[touched]
            v = v * (255.0 / np.maximum(v.sum(axis=1, keepdims=True), 1e-9))
            q = np.floor(v)
            short = (255 - q.sum(axis=1)).astype(int)
            order = np.argsort(-(v - q), axis=1)
            for k in range(n):
                q[np.arange(len(q)), order[:, k]] += (short > k)
            out[touched] = q.astype(np.uint8)
        return [out[..., 4 * i:4 * i + 4] for i in range(len(self.base_alphas))]

    def regions(self, strokes):
        """(heightmap (rows, cols), weight map (rows, cols)) ranges covering the
        ground that `strokes` can change, or None when they are off the map."""
        if not strokes:
            return None
        boxes = [bbox(s) for s in strokes]
        x0, z0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, z1 = max(b[2] for b in boxes), max(b[3] for b in boxes)

        def span(lo, hi, origin, size, n, cells):
            a = int(math.floor((lo - origin) / size * cells)) - 1
            b = int(math.ceil((hi - origin) / size * cells)) + 2
            return max(0, a), min(n, b)
        hres, ares = self.base.shape[0], self.base_alphas[0].shape[0]
        hr = span(z0, z1, self.origin[2], self.size[1], hres, hres - 1)
        hc = span(x0, x1, self.origin[0], self.size[0], hres, hres - 1)
        ar = span(z0, z1, self.origin[2], self.size[1], ares, ares)
        ac = span(x0, x1, self.origin[0], self.size[0], ares, ares)
        if hr[0] >= hr[1] or hc[0] >= hc[1]:
            return None
        return (hr, hc), (ar, ac)

    def apply(self, heights, alphas, strokes):
        """Recompute the ground that `strokes` (old and new versions of the
        changed strokes) can affect into the arrays heights and alphas. Returns
        (heightmap (rows, cols), weight map (rows, cols)) changed, or None."""
        reg = self.regions(strokes)
        if reg is None:
            return None
        ((r0, r1), (c0, c1)), ((a0, a1), (b0, b1)) = reg
        heights[r0:r1, c0:c1] = self.heights_block(r0, r1, c0, c1)
        if a0 < a1 and b0 < b1:
            for a, block in zip(alphas, self.alphas_block(a0, a1, b0, b1)):
                a[a0:a1, b0:b1] = block
        return reg

    def steep(self, s):
        """True when the stroke makes ground steeper than the AI can drive."""
        return s["kind"] in ("shape", "level", "smooth") and s["slope"] > AI_MAX_SLOPE and \
            (s["kind"] != "shape" or abs(s["height"]) > 0.3)

    # ---- storage --------------------------------------------------------------

    def save(self, folder, heights, alphas):
        """Write the strokes (and the base, when it is not stored yet) to
        `folder`; heights and alphas: the ground as saved in the game."""
        os.makedirs(folder, exist_ok=True)
        base_path = os.path.join(folder, BASE_FILE)
        if not os.path.exists(base_path):
            buf = io.BytesIO()
            np.savez_compressed(buf, *self.base_alphas, heights=self.base.astype(np.uint16),
                                geo=np.array(json.dumps(self.geo)))
            with open(base_path + ".tmp", "wb") as f:
                f.write(buf.getvalue())
            os.replace(base_path + ".tmp", base_path)
        path = os.path.join(folder, FEATURES_FILE)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"format": FORMAT, "next_id": self.next_id, "strokes": self.strokes,
                       "ground": checksum(heights, alphas)}, f, indent=1)
        os.replace(path + ".tmp", path)
        self.dirty = False

    @classmethod
    def load(cls, folder, heights, alphas):
        """(features, None) when the stored features of a scenario belong to its
        ground (`heights`, `alphas` as in the game); (None, reason) when they
        are out of date (the ground was changed otherwise); (None, None) when
        nothing is stored. Raises ValueError when they cannot be read."""
        path = os.path.join(folder, FEATURES_FILE)
        base_path = os.path.join(folder, BASE_FILE)
        if not (os.path.exists(path) and os.path.exists(base_path)):
            return None, None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with np.load(base_path) as z:
                base_alphas = [z[k] for k in sorted((k for k in z.files if k.startswith("arr_")),
                                                    key=lambda k: int(k.split("_")[-1]))]
                base = z["heights"].astype(np.int32)
                geo = json.loads(str(z["geo"]))
        except (OSError, ValueError, KeyError) as e:
            raise ValueError(f"the stored terrain features could not be read ({e})") from None
        if data.get("format") != FORMAT:
            raise ValueError("the stored terrain features were made by a newer editor version")
        if base.shape != heights.shape or [a.shape for a in base_alphas] != [a.shape for a in alphas]:
            return None, "the terrain's resolution changed"
        if data.get("ground") != checksum(heights, alphas):
            return None, "the ground was changed outside the terrain features"
        return cls(base, base_alphas, geo, data.get("strokes", []), data.get("next_id", 1)), None

    @staticmethod
    def discard(folder):
        for name in (BASE_FILE, FEATURES_FILE):
            p = os.path.join(folder, name)
            if os.path.exists(p):
                os.remove(p)


__all__ = ["TerrainFeatures", "KINDS", "KIND_NAMES", "DEFAULT_BRUSHES", "default_brushes", "clean_brush",
           "layer_label", "find_layer", "length", "line_distance", "AI_MAX_SLOPE", "BASE_FILE", "FEATURES_FILE",
           "checksum"]
