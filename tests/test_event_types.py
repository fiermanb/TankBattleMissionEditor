# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""The description of event and trigger types (event_types.py).

Run: python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import event_types as et  # noqa: E402


class EventTypesTest(unittest.TestCase):
    def test_tanks_needed_round_trip(self):
        for mode, number in (("all", 0), ("any", 1), ("count", 3)):
            fields = et.needed_fields(mode, number or 1)
            got = et.tanks_needed(dict(fields, Necessary_Num=fields.get("Necessary_Num", 1)))
            self.assertEqual(got[0], mode)
        self.assertEqual(et.tanks_needed({"Operator_Type": 0, "All_Trigger_Flag": False, "Necessary_Num": 3}),
                         ("count", 3))
        self.assertEqual(et.tanks_needed({"Operator_Type": 1, "All_Trigger_Flag": False, "Necessary_Num": 3})[0],
                         "any", "'any listed tank' overrides the count")

    def test_fields_per_type(self):
        self.assertTrue(et.shows_message(1) and et.shows_message(10) and et.shows_message(11))
        self.assertFalse(et.shows_message(2) or et.shows_message(13))
        self.assertTrue(et.uses_tank_list({"Trigger_Type": 1}))
        self.assertTrue(et.uses_tank_list({"Trigger_Type": 2, "Trigger_Setting_Type": 0}))
        self.assertFalse(et.uses_tank_list({"Trigger_Type": 2, "Trigger_Setting_Type": 1}))
        self.assertFalse(et.uses_tank_list({"Trigger_Type": 0}))
        self.assertEqual(et.label(("Visibility", "New visibility", "float", "m", "")), "New visibility (m)")
        self.assertEqual(et.ai_source_key("New_Attack_Distance"), "Attack_Distance")
        for field in et.AI_FIELDS:
            self.assertTrue(field[0].startswith("New_"), field)


if __name__ == "__main__":
    unittest.main()
