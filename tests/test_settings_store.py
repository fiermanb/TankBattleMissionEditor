# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Merging the source version's data store into the shared one.

Run: python -m unittest discover tests
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "w" if isinstance(data, str) else "wb"
    with open(path, mode) as f:
        f.write(data)


def scen(title, idx):
    sid = title.replace(" ", "_")
    return {"title": title, "scene_id": sid, "menu_scene": f"90_{sid}_Menu", "battle_scene": f"90_{sid}",
            "menu_index": idx - 1, "battle_index": idx, "mission_asset": idx, "source": None}


class StoreMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.saved = (settings.DATA_DIR, settings.BACKUP_ROOT, settings.SOURCE_STORE, settings.SETTINGS_PATH)
        settings.DATA_DIR = os.path.join(self.tmp, "shared")
        settings.BACKUP_ROOT = os.path.join(settings.DATA_DIR, "backups")
        settings.SETTINGS_PATH = os.path.join(settings.DATA_DIR, "settings.json")
        settings.SOURCE_STORE = os.path.join(self.tmp, "source", "backups")

    def tearDown(self):
        settings.DATA_DIR, settings.BACKUP_ROOT, settings.SOURCE_STORE, settings.SETTINGS_PATH = self.saved

    def test_merge(self):
        src, dst = os.path.join(settings.SOURCE_STORE, "game-1"), os.path.join(settings.BACKUP_ROOT, "game-1")
        write(os.path.join(src, "scenarios.json"), json.dumps(
            {"base_scene_count": 71, "scenarios": [scen("nbb", 72)], "pages": [701],
             "orphaned": [scen("Old one", 68)]}))
        write(os.path.join(src, "shared", "level2.orig"), b"source")
        write(os.path.join(src, "level70.orig"), b"older")
        write(os.path.join(src, "scenarios", "nbb", "meta.json"), json.dumps({"saved": "2026-10-05"}))
        write(os.path.join(src, "scenarios", "Kursk", "meta.json"), json.dumps({"saved": "2026-10-01"}))
        write(os.path.join(src, "object_library.json"), "{}")
        write(os.path.join(src, "before-update-1", "x"), b"1")
        write(os.path.join(settings.SOURCE_STORE, "game-2", "scenarios.json"), json.dumps({"scenarios": []}))
        write(os.path.join(self.tmp, "source", "settings.json"), json.dumps({"game": "X"}))
        time.sleep(0.05)
        write(os.path.join(dst, "scenarios.json"), json.dumps(
            {"base_scene_count": 67, "scenarios": [scen("Hold the line", 68), scen("Kursk", 70)], "pages": [701]}))
        write(os.path.join(dst, "shared", "level2.orig"), b"shared")
        write(os.path.join(dst, "level70.orig"), b"newer")
        write(os.path.join(dst, "scenarios", "Kursk", "meta.json"), json.dumps({"saved": "2026-10-04"}))

        here = settings.HERE
        settings.HERE = os.path.join(self.tmp, "source")
        try:
            done = settings.merge_source_store()
        finally:
            settings.HERE = here
        self.assertEqual(done, ["game-1", "game-2"])
        m = json.load(open(os.path.join(dst, "scenarios.json")))
        self.assertEqual([s["title"] for s in m["scenarios"]], ["Hold the line", "Kursk", "nbb"])
        self.assertEqual(m["base_scene_count"], 67)
        self.assertEqual(m["pages"], [701])
        self.assertEqual([s["title"] for s in m["orphaned"]], ["Old one"])
        self.assertEqual(open(os.path.join(dst, "shared", "level2.orig"), "rb").read(), b"shared",
                         "the shared files of the copy that changed the game first are kept")
        self.assertEqual(open(os.path.join(dst, "level70.orig"), "rb").read(), b"older",
                         "the oldest original of a level is kept")
        self.assertEqual(json.load(open(os.path.join(dst, "scenarios", "Kursk", "meta.json")))["saved"],
                         "2026-10-04", "the newer backup of a scenario is kept")
        self.assertTrue(os.path.exists(os.path.join(dst, "scenarios", "nbb", "meta.json")))
        self.assertTrue(os.path.exists(os.path.join(dst, "object_library.json")))
        self.assertTrue(os.path.exists(os.path.join(dst, "before-update-1", "x")))
        self.assertTrue(os.path.exists(os.path.join(settings.BACKUP_ROOT, "game-2", "scenarios.json")))
        self.assertTrue(os.path.exists(settings.SETTINGS_PATH))
        self.assertFalse(os.path.exists(settings.SOURCE_STORE), "the old store is moved away")
        legacy = [d for d in os.listdir(settings.DATA_DIR) if d.startswith("legacy-source-backups-")]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(settings.merge_source_store(), [], "merging happens once")


if __name__ == "__main__":
    unittest.main()
