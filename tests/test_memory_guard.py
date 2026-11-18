"""World facts, NPC epistemics and visible narration are not interchangeable."""
import unittest

from rpg_engine import memory_guard


def ledger():
    return {
        "schema": "npr-entity-ledger/1", "conflicts": [],
        "entities": {
            "patient6": {
                "label": "Реципиент № 6",
                "aliases": ["реципиент №6", "patient6"],
                "facts": [
                    {"id": "s05.sex", "field": "sex", "value": "мальчик",
                     "source": "archive/session-005-source.md:L3057"},
                    {"id": "s05.age", "field": "age_at_discovery",
                     "value": "примерно шестнадцать лет",
                     "source": "archive/session-005-source.md:L3057",
                     "observed_at": "0006-10-01T22:12:00"},
                ],
            },
            "doctor": {
                "label": "Кадзиура",
                "aliases": ["врач Кадзиура"],
                "facts": [{"id": "s02.role", "field": "role",
                           "value": "медик", "source": "s02.text.004"}],
            },
            "artifact": {
                "label": "Флакон Н",
                "aliases": ["artifact"],
                "facts": [{"id": "s01.material", "field": "material",
                           "value": "стекло", "source": "s01.text.002"}],
            },
        },
    }


def turn(gm="", claims=None):
    return {
        "id": "s08.text.009", "at": "0006-10-03T01:55:00",
        "gm": gm, "review": {"entity_claims": claims or []}, "d": {},
    }


class MemoryGuardTests(unittest.TestCase):
    def test_literal_inaccurate_narrator_gender_or_age_is_caught(self):
        result = memory_guard.review(
            ledger(), turn("Реципиент № 6 — худощавый взрослый мужчина."))
        self.assertFalse(result["ok"])
        self.assertTrue(any("age" in e for e in result["errors"]))

    def test_another_adult_near_patient_is_not_misidentified(self):
        result = memory_guard.review(
            ledger(), turn("У реципиента № 6 стоял взрослый мужчина."))
        self.assertTrue(result["ok"], result["errors"])

    def test_npc_can_make_false_claims_without_changing_world(self):
        t = turn("— Это взрослый мужчина, реципиент № 6, — ошибся дежурный.",
                 [{"entity": "patient6", "field": "sex", "value": "женщина",
                   "source": "s08.text.009", "actor": "doctor",
                   "channel": "reported"}])
        result = memory_guard.review(ledger(), t)
        self.assertTrue(result["ok"], result["errors"])

    def test_structured_narrator_fact_must_be_sourced(self):
        bad = turn("Он открыл флакон.", [{"entity": "artifact",
            "field": "material", "value": "металл", "source": "s01.text.002"}])
        self.assertFalse(memory_guard.review(ledger(), bad)["ok"])
        good = turn("Он открыл флакон.", [{"entity": "artifact",
            "field": "material", "value": "стекло", "source": "s01.text.002"}])
        self.assertTrue(memory_guard.review(ledger(), good)["ok"])

    def test_new_externally_stated_fact_requires_same_turn_delta(self):
        t = turn("Обследование завершено.", [
            {"entity": "patient6", "field": "new_test", "value": "positive",
             "source": "s08.text.009"}])
        self.assertFalse(memory_guard.review(ledger(), t)["ok"])
        t["d"]["entity_facts"] = {"patient6": {"facts+": [
            {"field": "new_test", "value": "positive", "source": "s08.text.009"}]}}
        self.assertTrue(memory_guard.review(ledger(), t)["ok"])

    def test_age_at_discovery_does_not_prevent_adulthood_decades_later(self):
        t = turn("Реципиент № 6 — взрослый мужчина.")
        t["at"] = "0025-10-03T01:55:00"
        self.assertTrue(memory_guard.review(ledger(), t)["ok"])

    def test_morphological_person_terms_in_russian(self):
        data = ledger()
        data["entities"]["doctor"]["facts"].append(
            {"id": "s01.sex", "field": "sex", "value": "мужской пол",
             "source": "s01.text.001"})
        t = turn("Кадзиура — уставшая женщина.")
        self.assertTrue(any("doctor.sex" in e for e in
                            memory_guard.review(data, t)["errors"]))


if __name__ == "__main__":
    unittest.main()
