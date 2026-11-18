"""Regress generic episodic memory: NPCs, things, places, time, sources."""
import json
from pathlib import Path
import tempfile
import unittest

from rpg_engine import general_memory, entity_memory, live_scene


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class GeneralMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hot = {
            "cursor": {"id": "s08.text.005", "at": "0006-10-03T01:50:00",
                       "place": "Кусагакуре, Центральный госпиталь, реанимация"},
            "scene": {"present": ["shiki", "doctor_aka"],
                      "situation": "The sample remains in the clinic.",
                      "positions": "Шики у койки recipient6; doctor_aka у входа.",
                      "pressure": "Immediate examination"},
            "cast": {"doctor_aka": {"name": "Доктор Ака",
                                    "wants": "Prevent a dangerous treatment"}},
            "things": {
                "clinical_vial": {"holder": "doctor_aka",
                                  "turn": "s07.text.009"},
                "sample_a": {"holder": "shiki", "turn": "s08.text.004"},
            },
            "threads": {"investigation": {"state": "Patient monitoring still active",
                                         "turn": "s07.text.011"}},
            "offscreen": {"clocks": {"sample_due": {
                "owner": "doctor_aka", "what": "Delayed medical result",
                "due": "0006-10-03T02:40:00"}}},
        }
        _write(self.root / "recovery/cast/doctor_aka.json", {
            "schema": "npr-cast-card/2", "key": "doctor_aka",
            "name": "Доктор Ака", "role": "physician", "state": "preparing report",
            "body": "An older medic", "memory": ["case X"],
        })
        _write(self.root / "recovery/cast/old_enemy.json", {
            "schema": "npr-cast-card/2", "key": "old_enemy",
            "name": "Нацуми Рей", "role": "former antagonist",
            "state": "offscreen", "wants": "Locate Shiki",
        })
        _write(self.root / entity_memory.SEED_PATH, {
            "schema": entity_memory.SCHEMA,
            "entities": {
                "recipient6": {"label": "Реципиент № 6",
                               "aliases": ["шестого", "recipient6"],
                               "facts": [
                                   {"id": "r6.sex", "field": "sex", "value": "мальчик",
                                    "source": "archive/session-005-source.md:L3057"},
                                   {"id": "r6.age", "field": "age_at_discovery", "value": "16",
                                    "source": "archive/session-005-source.md:L3057"}]},
                "shiki": {"label": "Мугами Шики", "aliases": ["Shiki"],
                          "facts": []},
            },
        })
        log = self.root / "archive/session-005-turns.jsonl"
        log.parent.mkdir(parents=True)
        lines = [
            {"id": "s05.text.002", "at": "0006-09-29T09:00:00",
             "gm": "Доктор Ака передал образец Шики, реципиент № 6 лежал в камере.",
             "player": "Проверяю пациента", "line": "medical sample"},
            {"id": "s05.text.003", "at": "0006-09-29T09:01:00",
             "gm": "Нацуми Рей отказалась от предложения и исчезла в коридоре.",
             "player": "Смотрю", "line": "old antagonist"},
        ]
        log.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines),
                       encoding="utf-8")

    def _index(self):
        return general_memory.build(self.root, self.hot)

    def test_build_indexes_all_classes_and_disappeared_npc(self):
        data = self._index()
        keys = data["entities"]
        self.assertIn("old_enemy", keys)  # Not in HOT.cast or scene.present.
        self.assertEqual(keys["old_enemy"]["kind"], "npc")
        self.assertEqual(keys["clinical_vial"]["kind"], "item")
        self.assertIn("thread:investigation", keys)
        self.assertIn("clock:sample_due", keys)
        self.assertTrue(any(k.startswith("place:") for k in keys))
        self.assertEqual(keys["recipient6"]["facts"][0]["value"], "мальчик")

    def test_precise_source_is_candidate_not_fact(self):
        data = self._index()
        card = data["entities"]["old_enemy"]
        self.assertGreater(card["mention_count"], 0)
        self.assertEqual(card["facts"], [])
        self.assertEqual(card["mentions"][0]["turn"], "s05.text.003")
        self.assertEqual(card["mentions"][0]["status"], "source_candidate_only")

    def test_brief_includes_patient_not_present_and_provenance(self):
        idx = self._index()
        focus = general_memory.retrieve(idx, scene=self.hot["scene"], cursor=self.hot["cursor"])
        patient = next(x for x in focus["activated"] if x["id"] == "recipient6")
        assert {f["field"] for f in patient["facts"]} == {"sex", "age_at_discovery"}
        self.assertEqual(patient["status"], "verified_facts")
        self.assertEqual(patient["facts"][0]["source"], "archive/session-005-source.md:L3057")
        self.assertNotIn("source_candidates", patient)  # L0 is small
        brief = live_scene.build(self.hot, [], entity_ledger=entity_memory.load(self.root),
                                 memory_index=idx)
        self.assertIn("general_memory", brief)
        self.assertIn("recipient6",
                      [x["id"] for x in brief["general_memory"]["activated"]])

    def test_three_layer_expansion_keeps_exact_evidence(self):
        idx = self._index()
        base = general_memory.retrieve(idx, action="Нацуми Рей", level=0)
        self.assertEqual(base["activated"][0]["id"], "old_enemy")
        self.assertNotIn("projections", base["activated"][0])
        detail = general_memory.retrieve(idx, action="Нацуми Рей", level=2)
        card = detail["activated"][0]
        self.assertIn("projections", card)
        self.assertEqual(card["source_candidates"][0]["source"],
                         "archive/session-005-turns.jsonl")
        self.assertEqual(card["source_candidates"][0]["turn"], "s05.text.003")

    def test_generic_owner_graph_is_candidate_link_only(self):
        idx = self._index()
        seen = general_memory.retrieve(idx, action="clinical_vial", level=2)
        result = {x["id"]: x for x in seen["activated"]}
        self.assertIn("doctor_aka", result)
        self.assertIn("explicit_edge_candidate", result["doctor_aka"]["channels"])
        links = result["clinical_vial"]["links"]
        self.assertTrue(any(e["to"] == "doctor_aka" and
                            e["status"] == "projected" for e in links))

    def test_time_bound_facts_and_conflicts(self):
        data = self._index()
        data["entities"]["recipient6"]["facts"].append({
            "id": "r6.symptom", "field": "symptom", "value": "fever",
            "source": "s07.text.007", "valid_until": "0006-10-03T01:00:00",
        })
        present = general_memory.retrieve(data, action="recipient6",
                                          cursor=self.hot["cursor"])
        self.assertNotIn("fever", [x["value"] for x in present["activated"][0]["facts"]])
        old = general_memory.retrieve(data, action="recipient6",
                                      cursor={"at": "0006-10-02T00:00:00"})
        self.assertIn("fever", [x["value"] for x in old["activated"][0]["facts"]])

    def test_unfolded_turn_is_candidate_immediately(self):
        _write(self.root / "recovery/TURN.json", {
            "id": "s08.text.006", "at": "0006-10-03T02:00:00",
            "gm": "Доктор Ака передал новый отчёт Нацуми Рей.",
            "line": "doctor handed a report", "player": "Осматриваю",
        })
        idx = self._index()
        self.assertTrue(any(m["turn"] == "s08.text.006"
                            for m in idx["entities"]["old_enemy"]["mentions"]))

    def test_unstructured_first_session_source_is_addressable(self):
        path = self.root / "archive/session-001.md"
        path.write_text(
            "Глава 1\nДоктор Ака видел обстоятельства случившегося.\n"
            "Нацуми Рей исчезла в конце коридора.\n", encoding="utf-8")
        idx = self._index()
        card = idx["entities"]["old_enemy"]
        self.assertTrue(any(ref["source"].startswith("archive/session-001.md:L")
                            for ref in card["mentions"]), card["mentions"])

    def test_long_gone_items_and_locations_are_still_queryable(self):
        old = {"id": "s04.text.001", "at": "0006-09-27T10:00:00",
               "place": "Старая башня, восточное крыло", "gm": "Обыск завершён.",
               "d": {"things": {"red_sarcophagus": {"now": None}},
                     "cast": {"npc_gone": {"name": "Фуруяма"}},
                     "threads": {"old_survey": {"closed": True}},
                     "canon+": ["Башня сгорела при первом нападении."]}}
        path = self.root / "archive/session-004-turns.jsonl"
        path.write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
        idx = self._index()
        self.assertIn("red_sarcophagus", idx["entities"])
        self.assertIn("npc_gone", idx["entities"])
        self.assertIn("thread:old_survey", idx["entities"])
        self.assertTrue(any(v["label"] == "Старая башня, восточное крыло"
                            for v in idx["entities"].values()))
        self.assertTrue(any(v["kind"] == "canon"
                            for v in idx["entities"].values()))
        self.assertEqual(idx["entities"]["red_sarcophagus"]["sources"], ["s04.text.001"])

    def test_historical_locations_cannot_evict_known_patient(self):
        idx = self._index()
        for number in range(70):
            ident = f"place:old_{number:03}"
            idx["entities"][ident] = {
                "id": ident, "kind": "place",
                "label": f"Кусагакуре, Центральный госпиталь, палата {number}",
                "aliases": ["Кусагакуре", "Центральный госпиталь", f"палата {number}"],
                "facts": [], "projections": [], "mentions": [],
                "sources": ["s04.text.001"],
            }
        current = general_memory.retrieve(
            idx, scene=self.hot["scene"], cursor=self.hot["cursor"], limit=7)
        ids = [x["id"] for x in current["activated"]]
        self.assertIn("recipient6", ids)
        self.assertIn("doctor_aka", ids)
        self.assertTrue(all(not x.startswith("place:old_") for x in ids), ids)
        self.assertLessEqual(len([x for x in current["activated"]
                                  if x["kind"] == "place"]), 1)

    def test_save_and_rebuild_are_deterministic_and_readable(self):
        a = general_memory.save(self.root, self.hot)
        first = (self.root / general_memory.PATH).read_bytes()
        b = general_memory.save(self.root, self.hot)
        self.assertEqual(a, b)
        self.assertEqual(first, (self.root / general_memory.PATH).read_bytes())
        self.assertEqual(general_memory.load(self.root, hot=self.hot), a)

    def test_returning_old_npc_keeps_body_and_role_without_canon_upgrade(self):
        index = self._index()
        packet = general_memory.retrieve(index, action="Нацуми Рей", level=0)
        npc = packet["activated"][0]
        self.assertEqual(npc["id"], "old_enemy")
        self.assertEqual(npc["facts"], [])
        self.assertEqual(npc["operational_cues"]["values"]["role"], "former antagonist")
        self.assertEqual(npc["operational_cues"]["status"],
                         "projection_not_authoritative_fact")
        self.assertIn("recovery/cast/old_enemy.json",
                      npc["operational_cues"]["source"])

    def test_limited_scene_reports_unloaded_critical_entities(self):
        index = self._index()
        for i in range(10):
            ident = f"cast_member_{i}"
            index["entities"][ident] = {
                "id": ident, "kind": "npc", "label": ident,
                "aliases": [ident], "facts": [
                    {"id": f"{ident}.sex", "field": "sex",
                     "value": "male", "source": "s07.text.001"}],
                "projections": [], "sources": ["s07.text.001"], "mentions": [],
            }
        scene = {"present": [f"cast_member_{i}" for i in range(10)]}
        packet = general_memory.retrieve(index, scene=scene, limit=5)
        self.assertEqual(len(packet["activated"]), 5)
        self.assertEqual(len(packet["omitted_priority_entities"]), 5)

    def test_80_npcs_bounded_and_no_fact_dropped_for_priority(self):
        for i in range(80):
            _write(self.root / f"recovery/cast/npc_{i:03}.json", {
                "key": f"npc_{i:03}", "name": f"Испытуемый {i:03}",
                "body": "Detailed description " * 20,
            })
        idx = self._index()
        self.assertGreaterEqual(len(idx["entities"]), 80)
        focused = general_memory.retrieve(idx, action="шестого",
                                          scene=self.hot["scene"], cursor=self.hot["cursor"],
                                          limit=7)
        self.assertEqual(focused["activated"][0]["id"], "recipient6")
        compact = general_memory.compact(focused, 2400)
        self.assertEqual(compact["activated"][0]["id"], "recipient6")
        self.assertEqual(len(compact["activated"][0]["facts"]), 2)


if __name__ == "__main__":
    unittest.main()
