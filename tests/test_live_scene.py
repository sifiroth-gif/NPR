"""Integrated live scene memory, actor knowledge and world time gates."""
import copy
import json
import unittest

from rpg_engine import live_scene


def source(num, line, gm):
    return {"id": f"s07.text.{num:03}", "at": f"0006-10-02T23:{num:02}:00",
            "place": "office", "player": "Ask", "line": line, "gm": gm}


def hot():
    return {
        "cursor": {"id": "s07.text.029", "at": "0006-10-03T00:53:00",
                   "place": "office", "date": "Вторник", "time": "00:53"},
        "last": {"player": "Will the suppliers come?", "gm": "He watched the door."},
        "scene": {"present": ["arakawa"], "situation": "office discussion",
                  "npc_disclosures_to_player": {"arakawa": [
                      {"turn": "s07.text.012", "source": "archive/session-007-turns.jsonl"},
                      {"turn": "s07.text.013", "source": "archive/session-007-turns.jsonl"},
                  ]}},
        "cast": {"arakawa": {"name": "Аракава Дзюн", "wants": "survive",
                             "knows": ["Own medicine schedule"],
                             "hides": "Does not want to give a signed confession"}},
        "offscreen": {"truth": {"donor": {"unknown": True}},
                      "clocks": {"delivery": {
                          "owner": "supplier", "what": "check missed S-17",
                          "due": "0006-10-03T00:54:00",
                          "signs": "missed call",
                          "if_unopposed": "followup requested"}}},
        "threads": {"v7": {"state": "pending investigation"}},
        "shiki": {"body": "healed"},
    }


def turn(gm="The inspector moved his hand.", player="I look at him"):
    return {
        "schema": "npr-turn/2", "id": "s07.text.030",
        "at": "0006-10-03T00:55:00", "place": "office",
        "player": player, "gm": gm, "line": "The inspector moved his hand.",
        "d": {"scene": {}},
        "review": {"predecessor": "s07.text.029", "sources": ["s07.text.012", "s07.text.013"]},
    }


class LiveSceneTests(unittest.TestCase):
    def setUp(self):
        self.h = hot()
        self.old = [source(12, "Аракава признал зависимость от С-17", "A confession"),
                    source(13, "Аракава предъявил папку с запросом В-7", "An old document")]
        self.policy = {"enabled": True, "enforce_due": True}

    def test_compact_brief_has_source_bound_memories_and_private_clocks(self):
        brief = live_scene.build(self.h, self.old)
        self.assertEqual(brief["format"], live_scene.FORMAT)
        self.assertIsNone(brief["handoff"])
        self.assertEqual(brief["cursor"]["id"], "s07.text.029")
        prior = brief["present_npcs"]["arakawa"]["prior_disclosures"]
        self.assertEqual([x["turn"] for x in prior], ["s07.text.012", "s07.text.013"])
        self.assertEqual(brief["source_passages"]["s07.text.012"]["gm_excerpt"], "A confession")
        self.assertEqual(brief["world"]["clocks"][0]["status"], "scheduled")
        self.assertEqual(brief["world"]["secret_topics"], ["donor"])
        self.assertTrue(all(edge["status"] == "source_order_only"
                            for edge in brief["evidence_graph"]["edges"]))
        self.assertEqual(brief["digest"], live_scene._digest({
            key: value for key, value in brief.items() if key != "digest"}))

    def test_npc_role_does_not_imply_universal_technical_comprehension(self):
        self.h["cast"]["arakawa"]["role"] = "Senior government inspector"
        brief = live_scene.build(self.h, self.old)
        npc = brief["present_npcs"]["arakawa"]
        self.assertNotIn("competence", npc["profile"])
        self.assertNotIn("limits", npc["profile"])
        self.assertIn("Hearing or repeating terminology is not understanding",
                      npc["epistemic_limit"])
        self.assertIn("bluffing", npc["epistemic_limit"])

    def test_explicit_competence_gaps_and_misconceptions_remain_separate(self):
        npc = self.h["cast"]["arakawa"]
        npc["competence"] = ["official paperwork", "procedural threats"]
        npc["limits"] = ["genetic engineering", "biochemistry"]
        npc["misconceptions"] = ["thinks an experimental carrier is an established medicine"]
        brief = live_scene.build(self.h, self.old)
        profile = brief["present_npcs"]["arakawa"]["profile"]
        self.assertEqual(profile["competence"], npc["competence"])
        self.assertEqual(profile["limits"], npc["limits"])
        self.assertEqual(profile["misconceptions"], npc["misconceptions"])
        self.assertNotIn(npc["misconceptions"][0], profile["knows"])

    def test_completed_legacy_clocks_are_not_overdue_or_blocking(self):
        self.h["offscreen"]["clocks"]["finished_old"] = {
            "owner": "captain", "what": "prior escort",
            "due": "0006-10-03T00:53:30", "if_unopposed": "report filed",
            "closed": "completed", "signs": "already reported"}
        brief = live_scene.build(self.h, self.old)
        self.assertNotIn("finished_old",
                         [c["id"] for c in brief["world"]["clocks"]])
        self.assertNotIn("finished_old", brief["world"]["due_now"])
        t = turn()
        t["d"]["world_reviewed"] = ["delivery"]
        self.assertEqual(live_scene.validate_admission(self.h, t,
                         policy=self.policy, accepted=self.old), [])

    def test_missing_review_rejected_for_sensitive_question(self):
        t = turn(player="Аракава, о чём вы прежде молчали?")
        t.pop("review")
        errors = live_scene.validate_admission(self.h, t, policy=self.policy, accepted=self.old)
        self.assertTrue(any("predecessor" in e for e in errors), errors)
        self.assertTrue(any("review.sources" in e for e in errors), errors)

    def test_source_replay_must_cover_prior_actor_disclosures(self):
        t = turn(player="Аракава, что вы сказали мне раньше?")
        t["review"]["sources"] = ["s07.text.013"]
        t["d"]["world_reviewed"] = ["delivery"]
        errors = live_scene.validate_admission(self.h, t, policy=self.policy, accepted=self.old)
        self.assertTrue(any("s07.text.012" in e for e in errors), errors)
        t["review"]["sources"] = ["s07.text.012", "s07.text.013"]
        self.assertEqual(live_scene.validate_admission(self.h, t, policy=self.policy,
                                                      accepted=self.old), [])

    def test_false_novelty_claim_needs_comparison(self):
        t = turn(gm="Аракава впервые признал, что ему нужен С-17.",
                 player="Зачем вы хотели В-7?")
        t["d"]["world_reviewed"] = ["delivery"]
        errors = live_scene.validate_admission(self.h, t, policy=self.policy, accepted=self.old)
        self.assertTrue(any("novelty_contrast" in e for e in errors))
        t["review"]["novelty_contrast"] = (
            "The old source already mentions S-17, the new source would have "
            "to provide a distinct document or change in testimony.")
        self.assertEqual(live_scene.validate_admission(self.h, t, policy=self.policy,
                                                      accepted=self.old), [])

    def test_remote_npc_knowledge_needs_delivery_channel(self):
        t = turn()
        t["d"]["world_reviewed"] = ["delivery"]
        t["d"]["cast"] = {"tetsu": {"knows+": ["Shiki is in office"]}}
        errors = live_scene.validate_admission(self.h, t, policy=self.policy, accepted=self.old)
        self.assertTrue(any("offscreen knows+" in e for e in errors))
        t["d"]["knowledge_events"] = [
            {"actor": "tetsu", "channel": "message", "claim": "Shiki is in office", "source": "s07.text.013"}]
        self.assertEqual(live_scene.validate_admission(self.h, t, policy=self.policy,
                                                      accepted=self.old), [])

    def test_world_deadline_needs_adjudication_on_jump(self):
        t = turn()
        errors = live_scene.validate_admission(self.h, t, policy=self.policy, accepted=self.old)
        self.assertTrue(any("delivery" in e for e in errors))
        t["d"]["world_reviewed"] = ["delivery"]
        self.assertEqual(live_scene.validate_admission(self.h, t, policy=self.policy,
                                                      accepted=self.old), [])
        t["d"]["world_reviewed"] = []
        t["d"]["clocks"] = {"delivery": {"done": "NPC returned and reported"}}
        self.assertEqual(live_scene.validate_admission(self.h, t, policy=self.policy,
                                                      accepted=self.old), [])

    def test_hidden_belief_has_provenance_without_public_contradiction(self):
        t = turn()
        t["d"]["world_reviewed"] = ["delivery"]
        t["d"]["knowledge_events"] = [{
            "actor": "arakawa", "source": "s07.text.013",
            "channel": "document", "claim": "A private hidden name",
            "visibility": "gm_only", "certainty": 0.8,
        }]
        self.assertEqual(live_scene.validate_admission(self.h, t, policy=self.policy,
                                                      accepted=self.old), [])
        t["d"]["cast"] = {"arakawa": {"knows+": ["A private hidden name"]}}
        errs = live_scene.validate_admission(self.h, t, policy=self.policy,
                                             accepted=self.old)
        self.assertTrue(any("secret NPC belief" in e for e in errs), errs)

    def test_historical_graph_candidates_do_not_grant_npc_knowledge(self):
        brief = live_scene.build(self.h, self.old)
        historic = {"matches": [{
            "id": "s01:archive:451", "source": "archive/session-001.md",
            "lines": [5, 9], "source_sha256": "a" * 64,
            "at": "0006-09-26T01:00:00",
            "headline": "Аракава в архиве", "preview": "Аракава видел шкаф",
            "score": 0.7, "status": "candidate_only"
        }]}
        live_scene.attach_history_graph(brief, historic)
        node = next(x for x in brief["evidence_graph"]["nodes"]
                    if x["kind"] == "historical_candidate")
        self.assertEqual(node["source"], "archive/session-001.md")
        edges = [x for x in brief["evidence_graph"]["edges"]
                 if x["from"] == node["id"]]
        self.assertTrue(edges)
        self.assertTrue(all(x["status"] == "candidate_only" for x in edges))

    def test_inert_policy_maintains_legacy_compatibility(self):
        t = turn()
        t.pop("review")
        self.assertEqual(live_scene.validate_admission(self.h, t, policy={"enabled": False},
                                                      accepted=self.old), [])

    def test_ooc_never_moves_clock_or_needs_review(self):
        t = turn()
        t["kind"] = "ooc"
        t.pop("review")
        self.assertEqual(live_scene.validate_admission(self.h, t, policy=self.policy,
                                                      accepted=self.old), [])


if __name__ == "__main__":
    unittest.main()
