"""The recipient-6 bug must stay fixed after session folding and context compression."""
import json
from pathlib import Path
import tempfile
import unittest

from rpg_engine import entity_memory, live_scene


def seed():
    return {
        "schema": entity_memory.SCHEMA,
        "entities": {
            "recipient6": {
                "label": "Реципиент № 6",
                "aliases": ["реципиент №6", "шестого", "recipient6"],
                "facts": [
                    {"id": "s05.recipient6.age", "field": "age_at_discovery",
                     "value": "примерно 16 лет",
                     "source": "archive/session-005-source.md:L3057"},
                    {"id": "s08.recipient6.sex", "field": "sex",
                     "value": "мальчик",
                     "source": "archive/shiki-canon.jsonl:s08.text.005"},
                ],
            }
        },
    }


def scene():
    return {"situation": "Respiratory crisis", "present": ["kadziura_satoru"],
            "positions": "Shiki at recipient6 bedside", "pressure": "Urgent care"}


class EntityMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = self.root / entity_memory.SEED_PATH
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(seed(), ensure_ascii=False), encoding="utf-8")

    def test_unregistered_scene_patient_is_activated_without_npc_card(self):
        # Patient is deliberately NOT in scene.present or HOT.cast.
        ledger = entity_memory.rebuild(self.root)
        packet = entity_memory.activate(
            ledger, scene=scene(), cursor={"place": "Палата №6"},
            action="обследую шестого")
        self.assertEqual(packet["activated"][0]["id"], "recipient6")
        facts = {f["field"]: f for f in packet["activated"][0]["facts"]}
        self.assertEqual(facts["sex"]["value"], "мальчик")
        self.assertEqual(facts["age_at_discovery"]["value"], "примерно 16 лет")
        self.assertEqual(facts["age_at_discovery"]["source"],
                         "archive/session-005-source.md:L3057")

    def test_entity_memory_is_in_a_gm_brief_even_without_player_action(self):
        ledger = entity_memory.rebuild(self.root)
        h = {"cursor": {"id": "s08.text.005", "at": "0006-10-03T01:50:00",
                        "place": "hospital"},
             "scene": scene(), "cast": {"kadziura_satoru": {"name": "Кадзиура"}}}
        brief = live_scene.build(h, [], entity_ledger=ledger)
        self.assertEqual(brief["entity_memory"]["activated"][0]["id"], "recipient6")
        self.assertIn("age_at_discovery",
                      [f["field"] for f in brief["entity_memory"]["activated"][0]["facts"]])

    def test_no_unsupported_candidate_promoted_from_nearby_text(self):
        ledger = entity_memory.rebuild(self.root)
        packet = entity_memory.activate(
            ledger, action="обследую третьего",
            scene={"situation": "Some other patient", "present": []})
        self.assertEqual(packet["activated"], [])

    def test_unreviewed_age_rewrite_is_rejected(self):
        ledger = entity_memory.rebuild(self.root)
        proposed = {"id": "s08.text.009", "at": "0006-10-03T02:01:00",
                    "d": {"entity_facts": {"recipient6": {"facts+": [
                        {"field": "age_at_discovery", "value": "30 лет",
                         "source": "s08.text.009"}]}}}}
        errors = entity_memory.claim_errors(ledger, proposed)
        self.assertTrue(any("age_at_discovery" in x for x in errors), errors)
        proposed["d"]["entity_facts"]["recipient6"]["facts+"][0][
            "supersedes"] = ["s05.recipient6.age"]
        self.assertEqual(entity_memory.claim_errors(ledger, proposed), [])

    def test_fifty_empty_turns_and_rebuilds_do_not_erase_identity(self):
        archive = self.root / "archive"
        archive.mkdir()
        for n in range(1, 7):
            with (archive / f"session-{n:03}-turns.jsonl").open("w", encoding="utf-8") as stream:
                for i in range(10):
                    stream.write(json.dumps({
                        "id": f"s{n:02}.text.{i:03}", "d": {"scene": {"positions": "irrelevant"}},
                        "at": "0006-10-03T01:00:00",
                    }, ensure_ascii=False) + "\n")
        for _ in range(3):
            ledger = entity_memory.rebuild(self.root)
            self.assertEqual(len(ledger["entities"]["recipient6"]["facts"]), 2)
            self.assertEqual(entity_memory.activate(
                ledger, scene=scene())["activated"][0]["facts"][0]["value"],
                "примерно 16 лет")

    def test_submitted_turn_before_async_fold_already_visible(self):
        entity_memory.rebuild(self.root)
        pending = {"id": "s08.text.006", "at": "0006-10-03T02:10:00",
                   "d": {"entity_facts": {"recipient6": {"facts+": [
                       {"field": "test_status", "value": "assays pending",
                        "source": "s08.text.006"}]}}}}
        target = self.root / "recovery/TURN.json"
        target.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
        ledger = entity_memory.load(self.root)
        card = ledger["entities"]["recipient6"]
        self.assertIn("assays pending", [x["value"] for x in card["facts"]])
        # Derived ledger is not rewritten by a read; official fold owns writes.
        saved = json.loads((self.root / entity_memory.LEDGER_PATH).read_text(encoding="utf-8"))
        self.assertEqual(len(saved["entities"]["recipient6"]["facts"]), 2)

    def test_structured_turn_fact_survives_regeneration(self):
        archive = self.root / "archive"
        archive.mkdir()
        item = {"id": "s09.text.004", "at": "0006-10-04T01:00:00",
                "d": {"entity_facts": {"recipient6": {"facts+": [
                    {"field": "medical_status", "value": "под наблюдением",
                     "source": "s09.text.004"}]}}}}
        (archive / "session-009-turns.jsonl").write_text(
            json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
        ledger = entity_memory.rebuild(self.root)
        self.assertEqual(len(ledger["entities"]["recipient6"]["facts"]), 3)
        self.assertEqual(entity_memory.rebuild(self.root), ledger)


if __name__ == "__main__":
    unittest.main()
