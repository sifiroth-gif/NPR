import copy
import unittest

from rpg_engine import body, fight, medical, nextpack, overlay


class MedicalContextTest(unittest.TestCase):
    def setUp(self):
        self.h = {"cursor": {"id": "s14.text.023", "at": "0010-04-11T21:30:00"},
                  "scene": {"present": ["shiki"]},
                  "injuries": {"patient": [{"where": "спинной мозг", "severity": "тяжёлая",
                                           "lost": "парез; ходьба не проверена", "turn": "s14.text.007",
                                           "bleed": False, "recovered_at": "s14.text.023"}],
                               "other": [{"where": "рука", "turn": "s09.text.001", "lost": "слабость"}]},
                  "body": {"patient": {"loss": 1, "stage": "слабость"}},
                  "things": {"shiki_medical_kit": {"holder": "shiki", "state": "упакован", "turn": "s14.text.023"}}}
    def test_closed_fight_keeps_residual_and_blood_loss(self):
        packet = nextpack.build(self.h, {"cursor": self.h["cursor"]}, {}, [],
                                extra={"capabilities": {"items": []}})
        self.assertNotIn("fight", packet)
        block = packet["medical"]
        row = dict(zip(block["fields"], block["patients"]["patient"]["w"][0]))
        self.assertEqual(row["lost"], "парез; ходьба не проверена")
        self.assertIs(row["bleed"], False)
        self.assertEqual(block["patients"]["patient"]["loss"]["loss"], 1)
        self.assertIn("other", block["other_ids"])
        self.assertEqual(packet["state_sources"]["medical_kit"]["state"], "упакован")

    def test_on_demand_context_no_fabricated_equipment_or_measurements(self):
        del self.h["things"]
        before = copy.deepcopy(self.h)
        view = medical.context(self.h)
        self.assertEqual(view["kit"], {"status": "unknown"})
        self.assertEqual(view["patients"]["other"]["wounds"][0]["lost"], "слабость")
        self.assertNotIn("bleed", view["patients"]["other"]["wounds"][0])
        self.assertEqual(self.h, before)

    def test_haemostasis_anchor_and_recovery_after_full_repair(self):
        h = {"injuries": {"p": [{"where": "бок", "bleed": True}]},
             "body": {"p": {"loss": 2, "stage": body.LOSS[2], "at": "0010-04-11T10:00:00"}}}
        fight._heal(h, {"who": "p", "where": "бок"}, "s14.text.024", at="0010-04-11T17:00:00")
        self.assertNotIn("p", h["injuries"])
        self.assertEqual(body.stage(h, "p"), 2)
        body.check(h, {"id": "s14.text.024", "at": "0010-04-11T17:00:00"}, {})
        self.assertEqual(body.stage(h, "p"), 2)
        body.check(h, {"id": "s14.text.025", "at": "0010-04-11T23:00:00"}, {})
        self.assertEqual(body.stage(h, "p"), 1)

    def test_stop_and_full_heal_are_not_blood_replacement(self):
        h = {"injuries": {"p": [{"where": "бок", "bleed": True}]},
             "body": {"p": {"loss": 2, "stage": body.LOSS[2], "at": "0010-04-11T10:00:00"}}}
        fight._heal(h, {"who": "p", "where": "бок", "stop": True},
                    "s14.text.024", at="0010-04-11T17:00:00")
        self.assertEqual(h["body"]["p"]["at"], "0010-04-11T17:00:00")
        self.assertFalse(h["injuries"]["p"][0]["bleed"])
        self.assertEqual(body.stage(h, "p"), 2)
        fight._heal(h, {"who": "p", "blood": True}, "s14.text.025", at="0010-04-11T17:15:00")
        self.assertEqual(body.stage(h, "p"), 1)

    def test_ambiguous_all_selector_does_not_change_patient(self):
        before = copy.deepcopy(self.h)
        notes = []
        fight._heal(self.h, {"who": "patient", "where": "спинной мозг", "all": True},
                    "s14.text.024", notes)
        self.assertEqual(self.h, before)
        self.assertTrue(notes)

    def test_legacy_body_clock_unchanged(self):
        h = {"injuries": {"p": [{"where": "бок", "bleed": True}]},
             "body": {"p": {"loss": 2, "stage": body.LOSS[2], "at": "0010-04-11T10:00:00"}}}
        fight._heal(h, {"who": "p", "where": "бок", "stop": True}, "s14.text.023",
                    at="0010-04-11T17:00:00")
        self.assertEqual(h["body"]["p"]["at"], "0010-04-11T10:00:00")

    def test_blocked_projection_clears_medical_context(self):
        packet = {"medical": medical.context(self.h, compact=True), "fold_errors": ["bad"]}
        self.assertNotIn("medical", overlay.project(packet, []))
