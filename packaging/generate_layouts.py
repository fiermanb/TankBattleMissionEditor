# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Generate layouts/tbc_layouts.json.gz: the field layouts of all game scripts.

    pip install TypeTreeGeneratorAPI==0.0.10
    python packaging/generate_layouts.py [--game "<installed game folder>"]

Run this after a game update and release a new editor version. It is the only
place that needs TypeTreeGeneratorAPI; the editor and the executable read the
generated file. The file records the game's Unity version and a fingerprint of
its script metadata, so the editor can tell when the game has changed.
"""

import argparse
import datetime
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

import settings  # noqa: E402
from scene_io import LAYOUTS_FILE, GameContext, fixed_layout, script_key  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--game", help="installed game folder (default: last used or detected)")
    args = ap.parse_args()
    game = args.game or settings.load_settings().get("game") or (settings.detect_game() or [None])[0]
    ok, msg = settings.validate_game(game)
    if not ok:
        sys.exit(f"Game folder: {msg}")
    ctx = GameContext(game)
    gen = ctx.generator or ctx.live_generator()
    if gen is None:
        sys.exit("TypeTreeGeneratorAPI is not installed: pip install TypeTreeGeneratorAPI==0.0.10")
    layouts, failed = {}, 0
    for asm, ns, cls in sorted(set(ctx.scripts.values())):
        try:
            base = gen.get_nodes(asm + ".dll", f"{ns}.{cls}" if ns else cls)
        except Exception:
            base = None
        if base:
            layouts[script_key(asm, ns, cls)] = fixed_layout(base)
        else:
            failed += 1
    data = {"info": {"unity_version": ctx.unity_version, "metadata_sha256": ctx.metadata_sha256,
                     "generated": datetime.date.today().isoformat(), "count": len(layouts)},
            "layouts": layouts}
    out = os.path.join(SRC, LAYOUTS_FILE)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"{len(layouts)} script layouts written to {out} ({os.path.getsize(out) / 1e3:.0f} kB); "
          f"{failed} scripts without a layout (engine classes the generator cannot describe).")


if __name__ == "__main__":
    main()
