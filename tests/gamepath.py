# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Installed game used by the tests: TBC_GAME, else the editor's setting, else
the first installation found in the Steam libraries."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402

GAME = (os.environ.get("TBC_GAME") or settings.load_settings().get("game")
        or (settings.detect_game() or [""])[0])
