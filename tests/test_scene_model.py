# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Edit/save/reload tests for scene_model against the installed game.

Run: python -m unittest discover tests
"""

import collections
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scene_io import GameContext, SceneFile  # noqa: E402
from scene_model import SceneModel  # noqa: E402

from gamepath import GAME  # noqa: E402
SCENE = 18


@unittest.skipUnless(os.path.isdir(GAME), "game not installed")
class SceneModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = GameContext(GAME)

    def test_all_component_types_reserialize_exactly(self):
        sc = SceneFile(self.ctx, self.ctx.scene_path(SCENE))
        seen = collections.Counter()
        for pid in sc.all_pids():
            t = sc.type_name(pid)
            if seen[t] >= 40:
                continue
            if t == "MonoBehaviour" and not sc.script_class(pid):
                continue
            seen[t] += 1
            sc.read(pid)
            self.assertEqual(sc.serialize(pid), sc.objects[pid].get_raw_data(), f"{t} {pid}")

    def check_integrity(self, m):
        for trp, t in m.tr.items():
            for c in t["m_Children"]:
                self.assertIn(c["m_PathID"], m.tr)
                self.assertEqual(m.tr[c["m_PathID"]]["m_Father"]["m_PathID"], trp)
        for g, comps in m.comps.items():
            for c in comps:
                d = m.sc.read(c, head_only=True) if m.sc.type_name(c) == "MonoBehaviour" else m.sc.read(c)
                self.assertEqual(d["m_GameObject"]["m_PathID"], g)

    def test_waypoint_packs(self):
        m = SceneModel(self.ctx, SCENE)
        self.assertTrue(m.packs)
        for trp in m.waypoints:
            self.assertTrue(m.active(m.go_of_tr[trp]), "shipped waypoints must count as active")
        routed = [e for e in m.spawns if m.spawn_pack(e)]
        self.assertTrue(routed)
        self.assertIn(m.spawn_pack(routed[0]), m.packs)
        m.begin("pack")
        m.assign_pack(routed[0], 0)
        self.assertEqual(m.spawn_pack(routed[0]), 0)
        m.assign_pack(routed[0], m.packs[0])
        m.commit()
        self.assertEqual(m.spawn_pack(routed[0]), m.packs[0])
        self.assertEqual(m.sc.read(routed[0])["WayPoint_Pack"]["m_PathID"], m.go_of_tr[m.packs[0]])

    def test_delete_restore_keeps_trigger_refs(self):
        m = SceneModel(self.ctx, SCENE, terrain=None)
        complete = [e for e in m.events if m.sc.read(e)["Event_Type"] == 10]
        hostile = next(e for e in m.spawns if m.sc.read(e)["Relationship"] == 1)
        go = m.event_go[hostile]
        before = {e: list(m.sc.read(e)["Trigger_Tanks"]) for e in complete}
        m.begin("cut")
        record = m.delete(go)
        m.commit()
        self.assertFalse(m.active(go))
        self.assertTrue(any(len(m.sc.read(e)["Trigger_Tanks"]) < len(before[e]) for e in complete))
        m.begin("paste")
        m.restore(go, record)
        m.commit()
        self.assertTrue(m.active(go))
        for e in complete:
            self.assertEqual(sorted(r["m_PathID"] for r in m.sc.read(e)["Trigger_Tanks"]),
                             sorted(r["m_PathID"] for r in before[e]))

    def test_routes_waypoints_and_event_copies(self):
        m = SceneModel(self.ctx, SCENE, terrain=None)
        pack = m.packs[0]
        kids = [c["m_PathID"] for c in m.tr[pack]["m_Children"]]
        complete = next(e for e in m.events if m.sc.read(e)["Event_Type"] == 10)
        refs_before = len(m.sc.read(complete)["Trigger_Tanks"])
        m.begin("insert")
        wp = m.duplicate(m.go_of_tr[kids[0]], offset=None, mirror=False)
        new_pack = m.duplicate(m.go_of_tr[pack], offset=None, mirror=False)
        ev_copy = m.duplicate(m.event_go[complete], offset=None, mirror=False)
        m.commit()
        order = [c["m_PathID"] for c in m.tr[pack]["m_Children"]]
        self.assertEqual(order.index(m.tr_of_go[wp]), 1, "new waypoint follows the copied one")
        self.assertIn(m.tr_of_go[new_pack], m.packs)
        self.assertEqual(len(m.tr[m.tr_of_go[new_pack]]["m_Children"]), len(order))
        self.assertIn(m.event_of_go(ev_copy), m.events)
        self.assertEqual(len(m.sc.read(complete)["Trigger_Tanks"]), refs_before, "event copies are not mirrored")
        m.undo()
        self.assertEqual([c["m_PathID"] for c in m.tr[pack]["m_Children"]], kids)

    def test_edit_save_reload(self):
        m = SceneModel(self.ctx, SCENE)
        hostile = [e for e in m.spawns if m.sc.read(e)["Relationship"] == 1]
        complete = [e for e in m.events if m.sc.read(e)["Event_Type"] == 10]
        before = {e: len(m.sc.read(e)["Trigger_Tanks"]) for e in complete}

        m.begin("dup spawn")
        new_go = m.duplicate(m.event_go[hostile[0]])
        m.commit()
        new_ev = m.event_of_go(new_go)
        m.begin("tank")
        m.set_tank(new_ev, m.tanks[0])
        m.rotate(m.tr_of_go[new_go], 90)
        m.commit()

        building = max(m.foot, key=lambda g: len(m.subtree(m.tr_of_go[g])))
        m.begin("dup building")
        new_b = m.duplicate(building, offset=(30, 0))
        m.commit()

        victim = m.event_go[hostile[1]]
        m.begin("delete")
        m.delete(victim)
        m.commit()
        self.check_integrity(m)

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, f"level{SCENE}")
            m.sc.save(out)
            sc2 = SceneFile(self.ctx, out)
            for pid in list(m.sc.changed) + list(m.sc.new_objects):
                o = sc2.objects[pid]
                nodes = sc2._nodes(pid)
                o.read_typetree(nodes=nodes or o._get_typetree_node(), wrap=False, check_read=True)
            d = sc2.read(new_ev)
            self.assertEqual(d["Tank_Prop"]["m_PathID"], m.tanks[0][1])
            self.assertFalse(sc2.read(victim)["m_IsActive"])
            for e in complete:
                n = len(sc2.read(e)["Trigger_Tanks"])
                self.assertEqual(n, before[e])
            self.assertTrue(sc2.read(new_b)["m_IsActive"])

        for _ in range(4):
            m.undo()
        self.assertEqual(m.sc.build(), m.sc.raw)

    def test_new_route_waypoint_and_event(self):
        m = SceneModel(self.ctx, SCENE, terrain=None)
        m.begin("new")
        w1 = m.new_route((10.0, 1.0, 20.0))
        pack = m.waypoints[m.tr_of_go[w1]]
        w3 = m.new_waypoint(pack, m.tr_of_go[w1], (50.0, 1.0, 20.0))
        w2 = m.new_waypoint(pack, m.tr_of_go[w1], (30.0, 1.0, 20.0))
        ev = m.event_of_go(m.duplicate(m.event_go[m.spawns[0]], offset=None, mirror=False))
        m.reset_event(ev, 1)
        m.commit()
        self.check_integrity(m)
        self.assertIn(pack, m.packs)
        order = [m.go_of_tr[c["m_PathID"]] for c in m.tr[pack]["m_Children"]]
        self.assertEqual(order, [w1, w2, w3])
        self.assertTrue(all(m.active(g) for g in order))
        self.assertAlmostEqual(float(m.world(m.tr_of_go[w2])[0][0]), 30.0, places=3)
        d = m.sc.read(ev)
        self.assertEqual((d["Event_Type"], d["Trigger_Tanks"], d["Event_Message"]), (1, [], "New message"))
        self.assertNotIn(ev, m.spawns)

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, f"level{SCENE}")
            m.sc.save(out)
            sc2 = SceneFile(self.ctx, out)
            for pid in list(m.sc.changed) + list(m.sc.new_objects):
                self.assertEqual(sc2.read(pid), m.sc.read(pid))
        m.undo()
        self.assertEqual(m.sc.build(), m.sc.raw)

    def test_clear_to_terrain(self):
        m = SceneModel(self.ctx, SCENE, terrain="heights")
        m.begin("empty")
        m.clear_to_terrain()
        m.commit()
        self.check_integrity(m)
        live = [e for e in m.events if m.active(m.event_go[e])]
        spawns = [e for e in live if m.sc.read(e)["Event_Type"] == 0]
        self.assertEqual([m.sc.read(e)["Tank_ID"] for e in spawns], [1], "only the player spawn stays")
        self.assertTrue(all(m.sc.read(e)["Event_Type"] in (0, 11) for e in live))
        holes = m.terrain.holes
        for g in m.mesh_of_go:
            if m.active(g) and g in m.foot and len(holes):
                lo, hi = m.foot[g].min(0) - 50, m.foot[g].max(0) + 50
                self.assertTrue(((holes >= lo) & (holes <= hi)).all(1).any(),
                                f"{m.path(g)} is far from any terrain hole")

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, f"level{SCENE}")
            m.sc.save(out)
            sc2 = SceneFile(self.ctx, out)
            for pid in m.sc.changed:
                self.assertEqual(sc2.read(pid), m.sc.read(pid))

        deleted = next(e for e in m.spawns if not m.active(m.event_go[e]) and m.sc.read(e)["Tank_ID"] != 1)
        m.begin("add")
        new = m.copy_spawn(m.event_go[deleted], 1)
        m.commit()
        ev = m.event_of_go(new)
        self.assertTrue(m.active(new))
        self.assertEqual(m.sc.read(ev)["Relationship"], 1)
        self.assertFalse(m.drop_dead_refs(ev), "no references to deleted objects remain")
        self.check_integrity(m)

        m.undo()
        m.undo()
        self.assertEqual(m.sc.build(), m.sc.raw)


if __name__ == "__main__":
    unittest.main()
