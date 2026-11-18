"""Whole-game integration probes: context, time, NPC autonomy and memory quality."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from rpg_engine import (context_dispatcher, general_memory, live_scene,
                        memory_quality, npc_dynamics)


def card(key, kind="npc", label=None, aliases=None, facts=None, projections=None):
    return {"id": key, "kind": kind, "label": label or key,
            "aliases": aliases or [key], "facts": facts or [],
            "projections": projections or [], "mentions": [],
            "sources": [f"archive/session-001-turns.jsonl:{key}"]}


def state():
    return {
        "cursor": {"id": "s25.text.042", "at": "0006-11-18T22:00:00",
                   "place": "Central Hospital, theatre 3"},
        "scene": {"present": ["shiki", "doctor", "enemy"],
                  "positions": "Шики near recipient6; the doctor near enemy.",
                  "situation": "recipient6 is in surgery; enemy threatens doctor.",
                  "pressure": "Immediate risk of ventilation failure."},
        "cast": {
            "doctor": {"name": "Doctor", "role": "ICU surgeon",
                       "wants": "Protect patient", "fears": "Public exposure"},
            "enemy": {"name": "Enemy", "role": "former infiltrator",
                      "wants": "Hide a crime", "fears": "Arrest"},
        },
        "_psyche": {
            "doctor": {
                "psyche": {"values": "saving lives", "needs": "professional standing",
                           "mask": "total competence", "deep_fear": "failure",
                           "boundary": "never leave a patient", "contradiction": "not enough medicine"},
                "intent": {
                    "protect": {"goal": "Keep the patient alive", "status": "committed",
                                "turn": "s20.text.006"}},
                "affect": {"feel": "anger", "until": "0006-11-19T00:00:00"},
            },
            "enemy": {
                "intent": {"escape": {"goal": "Destroy evidence", "status": "committed",
                                      "turn": "s22.text.009"}},
            },
            "ghost": {"intent": {"plot": {"goal": "Intercept convoy",
                                        "status": "committed", "turn": "s10.text.001"}}},
        },
        "threads": {"case": {"state": "unresolved", "turn": "s12.text.020"}},
        "things": {"vial": {"holder": "doctor", "turn": "s24.text.001"}},
        "offscreen": {
            "clocks": {
                "ambush": {"owner": "ghost", "what": "Intercept the convoy",
                           "due": "0006-11-18T21:00:00", "turn": "s22.text.011"},
                "lab": {"owner": "doctor", "what": "Receive test result",
                        "due": "0006-11-18T23:00:00", "turn": "s24.text.002"},
            },
            "truth": {},
        },
    }


def index():
    age = {"id": "r6.age", "field": "age_at_discovery", "value": "16",
           "source": "s05.text.019"}
    out = {
        "schema": general_memory.FORMAT,
        "cursor": "s25.text.042",
        "source_turns_indexed": 1356,
        "conflicts": [],
        "links": [{"from": "vial", "to": "doctor", "type": "held_by",
                   "source": "s24.text.001", "status": "projected"}],
        "entities": {
            "recipient6": card("recipient6", "entity", "Recipient 6",
                               ["recipient6"], [age]),
            "doctor": card("doctor", "npc", "Doctor", ["doctor"],
                           projections=[{"role": "ICU surgeon", "body": "white coat"}]),
            "enemy": card("enemy", "npc", "Enemy", ["enemy"]),
            "ghost": card("ghost", "npc", "Ghost", ["ghost"]),
            "vial": card("vial", "item", "vial", ["vial"]),
            "place:current": card("place:current", "place",
                                  "Central Hospital, theatre 3", ["Central Hospital"]),
        },
    }
    for i in range(90):
        key = f"place:historical:{i:03}"
        out["entities"][key] = card(
            key, "place", f"Central Hospital, old ward {i}",
            ["Central Hospital", f"old ward {i}"])
    return out


class WholeGameIntegration(unittest.TestCase):
    def setUp(self):
        self.hot = state()
        self.index = index()

    def test_patient_and_all_current_npcs_survive_old_place_noise(self):
        focused, route = context_dispatcher.route(self.hot, self.index)
        ids = [x["id"] for x in focused["activated"]]
        self.assertIn("recipient6", ids)
        self.assertIn("doctor", ids)
        self.assertIn("enemy", ids)
        self.assertEqual(route["unindexed_present_actors"], [])
        self.assertEqual(route["omitted_present_actors"], [])
        self.assertLessEqual(route["focus_bytes"], route["budget_bytes"])
        self.assertEqual(sum(x["kind"] == "place" for x in focused["activated"]), 1)

    def test_historical_actor_and_action_drive_context_in_every_scene(self):
        focused, route = context_dispatcher.route(
            self.hot, self.index, action="Что раньше обещал Ghost?")
        self.assertEqual(route["mode"], "historical_claim")
        self.assertTrue(route["needs_primary_source_review"])
        self.assertEqual(focused["activated"][0]["id"], "ghost")
        self.assertEqual(focused["activated"][0]["status"], "no_verified_facts")

    def test_each_scene_type_is_detected_without_a_new_store(self):
        cases = {
            "Жду через 5 дней": "time_advance",
            "Я атакую врага": "combat",
            "Лечу пациента": "medical",
            "Стою у окна": "routine",
            "Когда раньше говорил врач": "historical_claim",
        }
        for action, expected in cases.items():
            with self.subTest(action=action):
                _, route = context_dispatcher.route(self.hot, self.index, action=action)
                self.assertEqual(route["mode"], expected)
        _, route = context_dispatcher.route(self.hot, self.index,
                                            action="Жду через 5 дней")
        self.assertTrue(route["time_advance_requires_world_review"])

    def test_npc_dynamics_are_read_only_and_do_not_direct_shiki(self):
        before = copy.deepcopy(self.hot)
        outlook = npc_dynamics.view(self.hot)
        self.assertEqual(self.hot, before)
        doctors = {x["id"]: x for x in outlook["actors"]}
        self.assertIn("doctor", doctors)
        self.assertIn("ghost", doctors)  # Due offscreen clock, no scene presence.
        self.assertEqual(doctors["doctor"]["pressure_pairs"][0]["status"],
                         "possibility_to_test_not_a_fact")
        self.assertIn("protect", doctors["doctor"]["committed_or_paused_intents"][0]["id"])
        self.assertEqual(doctors["ghost"]["world_clocks"][0]["status"], "overdue")
        self.assertNotIn("shiki", doctors)
        self.assertIn("escape", doctors["enemy"]["unclocked_intents"])
        self.assertFalse(any(k in self.hot for k in ("npc_actions", "choices")))

    def test_character_pressure_works_from_historical_card_without_psyche(self):
        self.hot["_psyche"]["doctor"] = {}
        outlook = npc_dynamics.view(self.hot, index=self.index)
        doctor = next(x for x in outlook["actors"] if x["id"] == "doctor")
        self.assertEqual(doctor["pressure_pairs"][0]["kind"], "goal_at_risk")
        self.assertEqual(doctor["unoperationalized_goal"], None)  # doctor has future lab clock
        self.assertEqual(doctor["identity_status"],
                         "operational_or_archival_projection_not_new_fact")

    def test_nonpresent_due_soon_npc_uses_archival_profile(self):
        self.index["entities"]["future_actor"] = card(
            "future_actor", "npc", "Historian", ["Historian"],
            projections=[{"role": "archivist", "wants": "hide ledger",
                          "fears": "discovery", "stance": "fearful"}])
        self.hot["offscreen"]["clocks"]["future"] = {
            "owner": "future_actor", "what": "Visit the archive",
            "due": "0006-11-19T01:00:00", "turn": "s23.text.001"}
        outlook = npc_dynamics.view(self.hot, index=self.index)
        npc = next(x for x in outlook["actors"] if x["id"] == "future_actor")
        self.assertFalse(npc["present"])
        self.assertEqual(npc["identity_projection"]["wants"], "hide ledger")
        self.assertEqual(npc["identity_source"],
                         "archive/session-001-turns.jsonl:future_actor")
        self.assertEqual(npc["world_clocks"][0]["status"], "pending")
        self.assertTrue(npc["pressure_pairs"])
        self.assertNotIn("future_actor", self.hot["cast"])

    def test_dynamics_omissions_disclosed_for_crowded_scenes(self):
        self.hot["scene"]["present"].extend(f"actor_{i}" for i in range(25))
        outlook = npc_dynamics.view(self.hot)
        self.assertLessEqual(len(outlook["actors"]), npc_dynamics.MAX_ACTORS)
        self.assertGreater(len(outlook["omitted_actor_ids"]), 0)

    def test_full_brief_has_routing_dynamics_and_provenance(self):
        packet = live_scene.build(self.hot, [], memory_index=self.index)
        self.assertEqual(packet["context_route"]["cursor"], self.hot["cursor"]["id"])
        self.assertIn("recipient6", packet["context_route"]["focus"])
        self.assertIn("npc_dynamics", packet)
        self.assertTrue(any(x["id"] == "ghost" and not x["present"]
                            for x in packet["npc_dynamics"]["actors"]))
        self.assertFalse(packet["guards"].get("automatic_npc_actions", False))
        self.assertEqual(packet["general_memory"]["activated"][0]["facts"][0]["source"],
                         "s05.text.019")

    def test_quality_checks_are_transparent_and_deterministic(self):
        first = memory_quality.audit(self.index, self.hot)
        second = memory_quality.audit(self.index, self.hot)
        self.assertEqual(first, second)
        self.assertEqual(first["cursor"], "s25.text.042")
        self.assertGreater(first["self_query_count"], 2)
        self.assertGreater(first["index_entities"], 90)
        self.assertLessEqual(first["focus_bytes"], first["focus_byte_limit"])
        self.assertIn("not free-form semantic accuracy", first["notes"][0])
        self.assertEqual(first["missing_critical"], [])

    def test_quality_flags_lost_participants(self):
        self.hot["scene"]["present"].append("a_missing_actor")
        result = memory_quality.audit(self.index, self.hot)
        self.assertEqual(result["status"], "review_required")
        self.assertIn("a_missing_actor", result["missing_critical"])

    def test_quality_reports_stale_index_and_never_mutates_state(self):
        base = copy.deepcopy(self.hot)
        self.index["cursor"] = "s24.text.101"
        result = memory_quality.audit(self.index, self.hot)
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(self.hot, base)

    def test_audit_save_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            one = memory_quality.save(d, self.hot, self.index)
            path = Path(d) / memory_quality.PATH
            first = path.read_bytes()
            two = memory_quality.save(d, self.hot, self.index)
            self.assertEqual(one, two)
            self.assertEqual(first, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
