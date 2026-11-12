import unittest

from rpg_engine.referent_guard import scoped_errors


POLICY = {
    "player_actor": "shiki",
    "episode_scopes": {
        "gaara.forest_death_actions": {
            "focus_entity": "gaara",
            "known_non_witnesses": ["ren", "aki", "shiki"],
            "witnessed_by": ["gaara", "temari", "kankuro"],
        },
        "gaara.hinata.prelim": {
            "focus_entity": "gaara",
            "witnessed_by": ["ren", "aki", "shiki"],
        },
        "shiki.rain_genin.forest_encounter": {
            "focus_entity": "shiki",
            "witnessed_by": ["ren", "shiki"],
        },
    },
}


class ReferenceLeakTests(unittest.TestCase):
    def check(self, quote, annotations, focus="gaara"):
        return scoped_errors(
            [{"speaker": "Рэн", "text": quote}],
            [{"actor": "ren", "text": quote, "referent_audit": annotations}],
            POLICY,
            focus,
        )

    def mark(self, quote, event):
        return {
            "quote": quote,
            "episode_id": event,
            "knowledge_type": "witnessed",
            "source_event": "played:july7",
        }

    def test_regression_ren_did_not_see_gaara_in_forest(self):
        quote = "И видел, что произошло в лесу."
        errors = self.check(
            quote,
            [self.mark(quote, "gaara.forest_death_actions")],
        )
        self.assertTrue(any("never witnessed" in e for e in errors))

    def test_do_not_switch_ambiguous_gaara_reference_to_shiki_event(self):
        quote = "И видел, что произошло в лесу."
        errors = self.check(
            quote,
            [self.mark(quote, "shiki.rain_genin.forest_encounter")],
        )
        self.assertTrue(any("does not match" in e for e in errors))

    def test_unannotated_witness_claim_fails_closed(self):
        errors = scoped_errors(
            [{"speaker": "Рэн", "text": "И видел, что произошло в лесу."}],
            [{"actor": "ren", "text": "И видел, что произошло в лесу.", "claims": []}],
            POLICY,
            "gaara",
        )
        self.assertTrue(any("referent_audit" in e for e in errors))

    def test_ren_may_recount_witnessed_gaara_hinata_fight(self):
        quote = "Я видел, что Гаара сделал с Хинатой."
        self.assertEqual([], self.check(
            quote, [self.mark(quote, "gaara.hinata.prelim")]
        ))

    def test_ren_may_reference_rain_fight_when_explicitly_switching(self):
        quote = "Я видел, как Шики сражался с дождевиками."
        self.assertEqual([], self.check(
            quote, [self.mark(quote, "shiki.rain_genin.forest_encounter")]
        ))

    def test_multi_clause_one_annotation_is_insufficient(self):
        quote = ("Я видел, что Гаара сделал с Хинатой. "
                 "И видел, что произошло в лесу.")
        errors = self.check(quote, [
            self.mark("Я видел, что Гаара сделал с Хинатой.", "gaara.hinata.prelim")
        ])
        self.assertTrue(any("exactly as many" in e for e in errors))

    def test_simple_unwitnessed_dialogue_does_not_require_audit(self):
        self.assertEqual([], scoped_errors(
            [{"speaker": "Рэн", "text": "Видимо, Гаара опасен."}],
            [{"actor": "ren", "text": "Видимо, Гаара опасен.", "claims": []}],
            POLICY,
            "gaara",
        ))


if __name__ == "__main__":
    unittest.main()
