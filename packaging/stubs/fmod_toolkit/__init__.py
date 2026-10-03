# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Placeholder for fmod_toolkit in the packaged editor.

UnityPy imports fmod_toolkit for audio conversion, which the editor never uses.
The real package bundles the proprietary FMOD library, which must not be
redistributed without a licence, so the executable ships this placeholder.
"""


def raw_to_wav(*args, **kwargs):
    raise RuntimeError("Audio conversion is not available in this build.")


sound_to_wav = subsound_to_wav = raw_to_wav
