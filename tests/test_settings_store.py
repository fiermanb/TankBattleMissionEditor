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


class DataFolderTest(unittest.TestCase):
    """The data folder lives next to the program; the old %LOCALAPPDATA% store
    moves into it once."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        names = ("DATA_DIR", "BACKUP_ROOT", "SETTINGS_PATH", "APPDATA_DIR", "PROGRAM_DIR", "FROZEN",
                 "LOCATION_FILE")
        self.saved = {n: getattr(settings, n) for n in names}
        self.env = os.environ.pop("TBME_DATA_DIR", None)

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(settings, n, v)
        if self.env is not None:
            os.environ["TBME_DATA_DIR"] = self.env

    def test_choose_data_dir(self):
        settings.FROZEN = True
        settings.PROGRAM_DIR = os.path.join(self.tmp, "program")
        settings.APPDATA_DIR = os.path.join(self.tmp, "appdata")
        self.assertEqual(settings.choose_data_dir(), (settings.PROGRAM_DIR, "program"))
        writable = settings.writable
        settings.writable = lambda folder: False
        try:
            self.assertEqual(settings.choose_data_dir(), (settings.APPDATA_DIR, "read-only program folder"))
        finally:
            settings.writable = writable
        settings.FROZEN = False
        settings.LOCATION_FILE = os.path.join(self.tmp, "data_location.txt")
        self.assertEqual(settings.choose_data_dir(), (settings.APPDATA_DIR, "default"))
        write(settings.LOCATION_FILE, settings.PROGRAM_DIR + "\n")
        self.assertEqual(settings.choose_data_dir(), (settings.PROGRAM_DIR, "data_location.txt"))
        os.environ["TBME_DATA_DIR"] = self.tmp
        try:
            self.assertEqual(settings.choose_data_dir(), (self.tmp, "environment"))
        finally:
            del os.environ["TBME_DATA_DIR"]

    def test_move_appdata_store(self):
        old = settings.APPDATA_DIR = os.path.join(self.tmp, "appdata")
        settings.DATA_DIR = os.path.join(self.tmp, "program")
        settings.BACKUP_ROOT = os.path.join(settings.DATA_DIR, "backups")
        settings.SETTINGS_PATH = os.path.join(settings.DATA_DIR, "settings.json")
        inst = os.path.join(old, "backups", "game-1")
        write(os.path.join(inst, "scenarios.json"), json.dumps({"scenarios": [scen("Kursk", 70)], "pages": [701]}))
        write(os.path.join(inst, "level70.orig"), b"orig")
        write(os.path.join(inst, "shared", "level2.orig"), b"shared")
        write(os.path.join(inst, "scenarios", "Kursk", "meta.json"), json.dumps({"saved": "2026-10-04"}))
        write(os.path.join(inst, "unknown.bin"), b"keep me")
        write(os.path.join(old, "settings.json"), json.dumps({"game": "X"}))
        write(os.path.join(old, "legacy-source-backups-1", "x"), b"1")
        write(os.path.join(settings.BACKUP_ROOT, "game-1", "scenarios", "nbb", "meta.json"),
              json.dumps({"saved": "2026-10-05"}))
        self.assertEqual(settings.move_appdata_store(), ["game-1"])
        dst = os.path.join(settings.BACKUP_ROOT, "game-1")
        for rel in ("level70.orig", os.path.join("shared", "level2.orig"), os.path.join("scenarios", "Kursk", "meta.json"),
                    os.path.join("scenarios", "nbb", "meta.json"), "unknown.bin", "scenarios.json"):
            self.assertTrue(os.path.exists(os.path.join(dst, rel)), rel)
        self.assertEqual(json.load(open(settings.SETTINGS_PATH))["game"], "X")
        self.assertTrue(os.path.exists(os.path.join(settings.DATA_DIR, "legacy-source-backups-1", "x")))
        self.assertFalse(os.path.exists(old), "nothing is left in the old folder")
        self.assertEqual(settings.move_appdata_store(), [], "moving happens once")
        settings.APPDATA_DIR = settings.DATA_DIR
        self.assertEqual(settings.move_appdata_store(), [], "no move when the data folder is the old one")

    def test_adopt_sibling_store(self):
        parent = os.path.join(self.tmp, "games")
        old = os.path.join(parent, "TankBattleMissionEditor-1.3.0-win64")
        write(os.path.join(old, "backups", "game-1", "scenarios", "Kursk", "meta.json"), json.dumps({"saved": "x"}))
        write(os.path.join(old, "settings.json"), json.dumps({"game": "X"}))
        settings.FROZEN = True
        settings.PROGRAM_DIR = settings.DATA_DIR = os.path.join(parent, "TankBattleMissionEditor")
        settings.BACKUP_ROOT = os.path.join(settings.DATA_DIR, "backups")
        settings.SETTINGS_PATH = os.path.join(settings.DATA_DIR, "settings.json")
        reason = settings.DATA_REASON
        settings.DATA_REASON = "program"
        try:
            self.assertEqual(settings.adopt_sibling_store(), old)
            self.assertTrue(os.path.exists(os.path.join(settings.BACKUP_ROOT, "game-1", "scenarios", "Kursk",
                                                        "meta.json")))
            self.assertEqual(json.load(open(settings.SETTINGS_PATH))["game"], "X")
            self.assertTrue(os.path.isdir(os.path.join(old, "backups")), "the old folder keeps its own copy")
            self.assertIsNone(settings.adopt_sibling_store(), "only when the folder has no backups yet")
        finally:
            settings.DATA_REASON = reason


if __name__ == "__main__":
    unittest.main()
