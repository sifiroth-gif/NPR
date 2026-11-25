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

    def test_current_request_overrides_scene_and_aliases_activate_domains(self):
        data = capabilities.load(ROOT, "shiki")
        scene = {"situation": "Врач лечит пациента в лазарете."}
        plan = capabilities.for_request(data, "#Магацухи#", scene=scene)
        self.assertIn("eyes.magatsuhi", plan["matched"])
        self.assertIn("combat", plan["routes"])
        self.assertNotIn("medical", plan["routes"])
        self.assertEqual(plan["basis"], "current_request")
        self.assertIn("fight/dice", plan["required"])
        self.assertEqual(len(plan["read"]), len(set(plan["read"])))

    def test_continuation_inherits_verified_scene_not_private_unread(self):
        data = capabilities.load(ROOT, "shiki")
        scene = {"situation": "Врач лечит пациента.", "unread": "атакую врага"}
        plan = capabilities.for_request(data, "Дальше по игре", scene=scene)
        self.assertIn("medical", plan["routes"])
        self.assertNotIn("combat", plan["routes"])
        self.assertEqual(plan["basis"], "scene_continuation")
        self.assertIn("injuries/function", plan["required"])
        self.assertIn("combat", capabilities.for_request(data, "говорю", scene=scene,
                                                        active_fight=True)["routes"])

    def test_boundaries_and_mixed_domains(self):
        data = capabilities.load(ROOT, "shiki")
        plan = capabilities.for_request(data, "замечаю охрану")
        self.assertNotIn("combat", plan["routes"])
        self.assertNotIn("medical", plan["routes"])
        plan = capabilities.for_request(data, "#достаю инструменты, лечу рану, спрашиваю врача#")
        self.assertTrue({"items", "medical", "dialogue"} <= set(plan["routes"]))
        self.assertIn("medical.sterilize", {r["id"] for r in plan["entries"]})

    def test_unavailable_alias_is_not_a_grant(self):
        data = capabilities.load(ROOT, "shiki")
        plan = capabilities.for_request(data, "Совместный покров")
        self.assertIn("nemera.cloak", plan["matched"])
        self.assertIn("nemera.cloak", plan["unavailable"])

    def test_next_actual_bytes_and_scene_sources(self):
        hot = {"campaign": "shiki", "cursor": {"id": "s14.text.023"},
               "scene": {"as_of": "s14.text.023", "situation": "Врач лечит пациента."},
               "things": {"shiki_medical_kit": {"holder": "shiki", "state": "упакован", "turn": "s14.text.023"}}}
        pack = nextpack.build(hot, {"cursor": hot["cursor"]}, {}, [],
                              extra={"capabilities": capabilities.packet(ROOT, "shiki", hot)})
        self.assertIn("medical", pack["capabilities"]["scene_hint"]["routes"])
        self.assertEqual(pack["state_sources"]["medical_kit"]["state"], "упакован")
        self.assertEqual(json.loads(nextpack._body(pack)), pack)
        self.assertEqual(nextpack._size(pack), len(nextpack._body(pack).encode()))

    def test_compact_registry_source_pointers_and_autonomous_activation_survive(self):
        data = capabilities.load(ROOT, "shiki")
        pack = capabilities.packet(ROOT, "shiki")
        rows = {row["id"]: row for row in data["entries"]}
        def expand(src):
            return src if capabilities.TURN_ID.fullmatch(src) else pack["source_base"] + src
        for row in pack["items"]:
            original = rows[row["id"]]
            self.assertEqual([expand(x) for x in row["sources"]], original["sources"])
            self.assertEqual(row.get("status", "mastered"), original["status"])
        for key, route in pack["routes"].items():
            self.assertEqual([expand(x) for x in route["read"]], data["routes"][key]["read"])
            self.assertEqual(route["required"], data["routes"][key]["required"])
        for row in pack["catalog"]:
            original = rows[row["id"]]
            self.assertEqual(row.get("status", "mastered"), original["status"])
            self.assertEqual(row.get("activation", "declared"), original["activation"])
