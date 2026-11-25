import copy
import json
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

    def test_registry_has_conditions_aliases_and_unavailable_states(self):
        data = capabilities.load(ROOT, "naruto-shiki")
        self.assertGreaterEqual(len(data["entries"]), 35)
        for name, ident, status in (
            ("ВМШ", "eyes.ems", "mastered"), ("чёрная чакра", "chakra.black", "mastered"),
            ("совместный покров", "nemera.cloak", "learning"),
            ("Сикоцумьяку", "genome.kaguya", "experimental"),
            ("трёхтомоэ", "restricted.cancelled", "retired"),
            ("Хакугэки", "restricted.unlearned", "unlearned")):
            row = capabilities.resolve(data, name)
            self.assertEqual(row["id"], ident)
            self.assertEqual(row["status"], status)
            self.assertEqual(row["activation"] == "unavailable", status != "mastered")
            self.assertTrue(row["requires"] and row["limits"])
        self.assertIsNone(capabilities.resolve(data, "неизвестная техника"))

    def test_validation_rejects_bad_sources_aliases_and_status(self):
        data = capabilities.load(ROOT, "naruto-shiki")
        for field, value in (("sources", ["campaigns/shiki/no-such-file.md"]),
                             ("aliases", data["entries"][1]["aliases"]),
                             ("activation", "unavailable"), ("requires", "")):
            broken = copy.deepcopy(data)
            broken["entries"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                capabilities.validate(broken, ROOT)

    def test_packet_keeps_core_and_catalog_without_full_registry_duplication(self):
        data = capabilities.load(ROOT, "shiki")
        pack = capabilities.packet(ROOT, "shiki")
        ids = [x["id"] for x in pack["items"] + pack["catalog"]]
        self.assertEqual(set(ids), {x["id"] for x in data["entries"]})
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(pack["items"]), 12)
        self.assertLess(len(json.dumps(pack, ensure_ascii=False).encode()), 17000)
        self.assertFalse(any("requires" in x for x in pack["catalog"]))
