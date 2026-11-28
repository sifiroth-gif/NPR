import copy
import unittest
from unittest.mock import patch

from rpg_engine import cognition, hot


STIMULUS = "На столе лежат три подписанных договора."
VISIBLE = "Канкуро удивлённо присвистнул."


def state():
    return {"scene": {"present": ["shiki", "kankuro"]},
            "cast": {"kankuro": {"name": "Канкуро"}},
            "_psyche": {}}


def record(**changes):
    return dict(topic="suna_agreement", domain="politics", novelty="new",
                evidence="signs", stimulus=STIMULUS, outcome="received",
                affect="surprise", visible=VISIBLE, **{}) | changes


def turn(row=None, *, ident="s18.text.102"):
    d = {"scene": {"present": ["shiki", "kankuro"]},
         "cast": {"kankuro": {"knows+": ["Увидел три соглашения."]}}}
    if row is not None:
        d["cognition"] = {"kankuro": row}
    return {"schema": "npr-turn/2", "id": ident, "at": "0010-05-16T08:33:00",
            "place": "Суна", "player": "Показываю документы.",
            "gm": STIMULUS + "\n\n" + VISIBLE, "d": d}


class CognitionTests(unittest.TestCase):
    def test_affect_is_separate_and_stakes_matter(self):
        self.assertEqual(cognition.affect_strength(20, "kankuro", "routine"), 0)
        self.assertEqual(cognition.affect_strength(8, "kankuro", "disruptive"), 1)
        self.assertEqual(cognition.affect_strength(16, "kankuro", "disruptive"), 2)
        self.assertEqual(cognition.affect_strength(16, "gaara", "disruptive"), 1)
        self.assertEqual(cognition.affect_strength(10, "kankuro", "new", "existential"), 1)
        self.assertEqual(cognition.affect_strength(20, "kankuro", "disruptive", repeated=True), 0)
        with patch.object(cognition, "draw", return_value=1), patch.object(cognition, "affect_draw", return_value=20):
            errors, accepted = cognition.review(state(), turn(record(novelty="disruptive")))
            self.assertFalse(errors)
            self.assertEqual(accepted["kankuro"]["outcome"], "received")
            self.assertEqual(accepted["kankuro"]["affect_strength"], 2)
            errors, _ = cognition.review(state(), turn(record(novelty="disruptive", affect="none", visible="")))
            self.assertTrue(any("сила аффекта" in e for e in errors))

    def test_known_new_actor_gets_own_draw(self):
        t = turn(record())
        h = state()
        h["scene"]["present"] = ["shiki"]
        packet = {"present": {}, "cognition": cognition.newcomer_packet(t["id"])}
        self.assertEqual(cognition.inspect(packet, t), cognition.review(h, t)[0])
        self.assertEqual(packet["cognition"]["new_profiles"]["kankuro"]["roll"], cognition.draw(t["id"], "kankuro"))

    def test_domain_specific_and_individual(self):
        self.assertEqual(cognition.ceiling(20, "kankuro", "puppetry", "disruptive", "explicit"), 3)
        self.assertEqual(cognition.ceiling(20, "kankuro", "politics", "disruptive", "explicit"), 2)
        self.assertEqual(cognition.ceiling(18, "shikamaru", "strategy", "disruptive", "explicit"), 3)
        self.assertEqual(cognition.ceiling(18, "unknown", "strategy", "disruptive", "explicit"), 1)
        self.assertEqual(cognition.ceiling(1, "kankuro", "puppetry", "disruptive", "explicit"), 0)

    def test_no_hidden_truth_from_high_roll(self):
        self.assertEqual(cognition.ceiling(20, "orochimaru", "research", "new", "signs"), 2)
        self.assertEqual(cognition.ceiling(20, "orochimaru", "research", "new", "opaque"), 1)

    def test_deterministic_separate_roll(self):
        a = cognition.draw("s18.text.102", "kankuro")
        self.assertEqual(a, cognition.draw("s18.text.102", "kankuro"))
        self.assertIn(a, range(1, 21))
        self.assertGreater(len({cognition.draw("s18.text.102", k) for k in cognition.PROFILES}), 1)

    def test_direct_receipt_and_surprise_independent_of_roll(self):
        with patch.object(cognition, "draw", return_value=1):
            errors, accepted = cognition.review(state(), turn(record()))
        self.assertEqual(errors, [])
        self.assertEqual(accepted["kankuro"]["outcome"], "received")
        self.assertEqual(accepted["kankuro"]["affect"], "surprise")
        with patch.object(cognition, "draw", return_value=20):
            row = record(evidence="explicit", outcome="understood", interpretation="Договоры защищают гостя.")
            errors, accepted = cognition.review(state(), turn(row))
        self.assertEqual(errors, [])
        self.assertEqual(accepted["kankuro"]["affect"], "surprise")

    def test_no_reroll_same_evidence(self):
        h = state()
        t = turn(record())
        with patch.object(cognition, "draw", return_value=1):
            cognition.apply(h, t, h)
        new = turn(record(evidence="explicit", outcome="understood",
                          interpretation="Договоры защищают гостя."), ident="s18.text.103")
        with patch.object(cognition, "draw", return_value=20):
            errors, accepted = cognition.review(h, new)
        self.assertTrue(any("предел понимания received" in x for x in errors))
        self.assertEqual(accepted, {})

    def test_new_source_can_change_understanding(self):
        h = state()
        cognition.apply(h, turn(record()), h)
        t = turn(record(evidence="explicit", outcome="understood",
                        stimulus="Гаара объяснил юридический смысл.",
                        interpretation="Договоры защищают гостя."), ident="s18.text.103")
        t["gm"] += "\n\nГаара объяснил юридический смысл."
        with patch.object(cognition, "draw", return_value=20):
            errors, accepted = cognition.review(h, t)
        self.assertEqual(errors, [])
        self.assertEqual(len(accepted["kankuro"]["evidence_ids"]), 2)

    def test_hypothesis_not_fact(self):
        t = turn(record(outcome="hypothesis", interpretation="Шики уже договорился с Кири."))
        t["d"]["cast"]["kankuro"]["knows+"].append("Шики уже договорился с Кири.")
        with patch.object(cognition, "draw", return_value=20):
            errors, accepted = cognition.review(state(), t)
        self.assertTrue(any("достоверный knows+" in x for x in errors))
        self.assertFalse(accepted)

    def test_new_information_requires_record_and_old_turns_are_unchanged(self):
        self.assertTrue(cognition.review(state(), turn())[0])
        self.assertEqual(cognition.review(state(), turn(ident="s18.text.101")), ([], {}))

    def test_textual_backstop(self):
        t = turn()
        t["d"]["cast"] = {}
        t["gm"] += "\n\n**Канкуро:** — Значит, ты договорился с Туманом."
        self.assertTrue(cognition.review(state(), t)[0])
        t["gm"] = "**Канкуро:** — Значит, ты договорился с Туманом?"
        self.assertFalse(cognition.review(state(), t)[0])

    def test_disruptive_calm_needs_reason_and_visible_reaction(self):
        t = turn(record(novelty="disruptive", affect="calm"))
        self.assertTrue(any("спокойствие" in x for x in cognition.review(state(), t)[0]))
        t["d"]["cognition"]["kankuro"]["basis"] = "Знакомый тип документа, проверенная практика."
        self.assertFalse(cognition.review(state(), t)[0])
        t["d"]["cognition"]["kankuro"]["visible"] = "Не сыграно."
        self.assertTrue(cognition.review(state(), t)[0])

    def test_absent_actor_and_fake_stimulus(self):
        t = turn(record(stimulus="Шики сообщил тайну, которой в сцене нет."))
        self.assertTrue(any("stimulus" in x for x in cognition.review(state(), t)[0]))
        t["d"]["cognition"]["gaara"] = t["d"]["cognition"].pop("kankuro")
        self.assertTrue(any("без присутствия" in x for x in cognition.review(state(), t)[0]))

    def test_private_ledger_and_public_separation(self):
        h = state()
        t = turn(record())
        cognition.apply(h, t, h)
        public, private = hot._split_state(h)
        self.assertNotIn("_psyche", public)
        self.assertIn("suna_agreement", private["psyche"]["kankuro"]["cognition"])
        self.assertNotIn("cognition", public["cast"]["kankuro"])

    def test_preflight_matches_fold_with_fresh_packet(self):
        h = state()
        t = turn(record())
        packet = {"present": {"kankuro": {"name": "Канкуро", "cognition": cognition.view(h, "kankuro", t["id"])}},
                  "cognition": {"for": t["id"], "new_roll": cognition.draw(t["id"])}}
        before = copy.deepcopy(h)
        self.assertEqual(cognition.inspect(packet, t), cognition.review(h, t)[0])
        self.assertEqual(h, before)
        t["d"]["cognition"]["kankuro"].update(outcome="hypothesis", interpretation="Догадка.")
        packet["cognition"]["for"] = "s18.text.101"
        self.assertTrue(any("нет свежего" in x for x in cognition.inspect(packet, t)))

    def test_malformed_record_is_rejected(self):
        t = turn(record(novelty=[]))
        with self.assertRaises(ValueError):
            cognition.validate(t)

    def test_ordinary_turn_not_taxed(self):
        t = turn()
        t["d"]["cast"] = {}
        t["gm"] = "**Канкуро:** — Чаю?"
        self.assertEqual(cognition.review(state(), t), ([], {}))


if __name__ == "__main__":
    unittest.main()
