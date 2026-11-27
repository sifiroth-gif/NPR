import copy
import tempfile
import unittest
from pathlib import Path

from rpg_engine import aging, obligations, scene_packet, capabilities, fusion

class ContextOptimizationTests(unittest.TestCase):
    def test_inventory_projection_keeps_sources_and_current_gear(self):
        items = {
            "sumi_sword": {"holder": "shiki", "state": "drawn", "turn": "s17.text.002"},
            "old_letter": {"holder": "shiki", "state": "old" * 1000, "turn": "s10.text.001"},
            "other_item": {"holder": "npc", "state": "current"},
            "new_item": {"holder": "shiki", "state": "new", "turn": "s17.text.133"},
        }
        hot = {"cursor": {"id": "s17.text.133"}, "scene": {"situation": "quiet"}, "things": items}
        before = copy.deepcopy(hot)
        packet = {"continuity": {"things": copy.deepcopy(items)}}
        scene_packet._continuity(packet, hot)
        self.assertEqual(hot, before)
        self.assertEqual(set(packet["continuity"]["things"]), {"sumi_sword", "other_item", "new_item"})
        self.assertEqual(packet["continuity"]["things_index"]["old_letter"]["turn"], "s10.text.001")
        self.assertIn("HOT.json", packet["continuity"]["things_ref"])
        self.assertLess(scene_packet._bytes(packet), scene_packet._bytes({"continuity": {"things": items}}))

    def test_inactive_obligation_archived_and_reactivated(self):
        old = "s16.text.100"
        text = "old cue " * 200
        ident = obligations.key("scene", text)
        row = {"kind": "scene", "text": text, "since": old, "last": old,
               "inactive": old, "offered": 3, "offered_at": old}
        current = {"kind": "scene", "text": "active", "since": "s17.text.133"}
        h = {"cursor": {"id": "s17.text.133"}, "_will": {"obligations": {
            ident: copy.deepcopy(row), "active": current}}}
        with tempfile.TemporaryDirectory() as root:
            # Ordinary live turns must keep inactive text intact.
            self.assertNotIn("obligations_inactive", aging._pass(root, h, aging.TIGHT))
            self.assertEqual(h["_will"]["obligations"][ident], row)
            h["handoff"] = {"closed": 17, "next_session": 18}
            report = aging._pass(root, h, aging.TIGHT)
            self.assertEqual(report["obligations_inactive"], 1)
            archived = aging.load(root, "obligations-inactive.GM_ONLY.jsonl")
            self.assertEqual(next(iter(archived.values()))["value"], row)
            self.assertNotIn("text", h["_will"]["obligations"][ident])
            self.assertNotIn("done", h["_will"]["obligations"][ident])
            self.assertEqual(h["_will"]["obligations"]["active"], current)
            aging._pass(root, h, aging.TIGHT)
            self.assertEqual(len(aging.load(root, "obligations-inactive.GM_ONLY.jsonl")), 1)
            texts, ids, _ = obligations.select(h, [("scene", text, ident)])
            self.assertEqual(ids, [ident])
            self.assertEqual(texts, [text])
            self.assertEqual(h["_will"]["obligations"][ident]["offered"], 4)

    def test_aging_keeps_equal_latest_record(self):
        row = {"turn": "s16.text.001", "outcome": "same"}
        old, keep = aging._split_keep({"cursor": {"id": "s17.text.001"}}, [row, row], 1)
        self.assertEqual(old, [row])
        self.assertEqual(keep, [row])

    def test_registry_switch_is_source_gated(self):
        root = Path(__file__).resolve().parents[1]
        hot = {"cursor": {"at": "0010-04-23T14:09:00"},
               "shiki": {"fusion_profile": {"id": fusion.PROFILE}}}
        data = capabilities.load(root, "shiki", hot)
        self.assertEqual(capabilities.resolve(data, "риннешаринган")["id"], "eyes.ems")
        hot["cursor"]["at"] = "0010-04-22T14:09:00"
        self.assertEqual(capabilities.registry_path(hot), capabilities.FUSION_PATH)

if __name__ == "__main__":
    unittest.main()
