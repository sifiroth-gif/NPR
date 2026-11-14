"""P0 regression: Shiki's psychological presence is not optional in GM packets."""
import unittest

from rpg_engine.preparation import psychological_presence_contract


class ShikiPsychologicalPresenceTests(unittest.TestCase):
    def test_mandatory_for_shiki_with_distinct_named_npcs(self):
        npc = [{"id": "nori", "name": "Хаясэ Нори"},
               {"id": "captive", "name": "Пленный"}]
        contract = psychological_presence_contract("naruto-shiki", npc)
        self.assertTrue(contract["required"])
        self.assertEqual(contract["priority"], "P0")
        self.assertEqual([x["id"] for x in contract["actors"]],
                         ["nori", "captive"])
        self.assertIn("gaze", contract["instruction"])
        self.assertIn("knowledge-bound", contract["instruction"])
        self.assertIn("No uniform awe", contract["instruction"])
        self.assertIsNone(contract["empty_cast_warning"])

    def test_missing_cast_does_not_suppress_rule(self):
        contract = psychological_presence_contract("naruto-shiki", [])
        self.assertTrue(contract["required"])
        self.assertTrue(contract["empty_cast_warning"])

    def test_unrelated_canon_not_forced_into_shiki_contract(self):
        self.assertIsNone(psychological_presence_contract(
            "other-game", [{"id": "npc", "name": "Other"}]))


if __name__ == "__main__":
    unittest.main()
