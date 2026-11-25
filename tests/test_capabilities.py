import unittest
from pathlib import Path
from rpg_engine import capabilities, nextpack

ROOT = Path(__file__).resolve().parents[1]


class CapabilityIndexTest(unittest.TestCase):
    def test_sterilization_after_unrelated_scene_is_not_missing(self):
        data = capabilities.load(ROOT, "naruto-shiki")
        result = capabilities.for_request(data, "#стерилизация и упаковка всех инструментов#")
        self.assertIn("medical", result["routes"])
        self.assertIn("items", result["routes"])
        item = next(x for x in result["entries"] if x["id"] == "medical.sterilize")
        self.assertIn("s10.text.018", item["sources"])
        self.assertIn("без аппарата", item["summary"])

    def test_multiple_domains_and_no_cross_campaign_leak(self):
        data = capabilities.load(ROOT, "naruto-shiki")
        ids = {x["id"] for x in capabilities.for_request(data, "#шуншин и лечение#")["entries"]}
        self.assertTrue({"sage.permanent", "medical.reconstruct", "seals.speed"} <= ids)
        self.assertIsNone(capabilities.packet(ROOT, "other-setting"))

    def test_next_contains_persistent_index_without_active_fight(self):
        pack = nextpack.build({"cursor": {"id": "s14.text.023"}, "scene": {}},
                              {"cursor": {"id": "s14.text.023"}}, {}, [],
                              extra={"capabilities": capabilities.packet(ROOT, "naruto-shiki")})
        self.assertIn("capabilities", pack)
        self.assertIn("medical.sterilize", {x["id"] for x in pack["capabilities"]["items"]})
        self.assertNotIn("battle_passives", pack)
