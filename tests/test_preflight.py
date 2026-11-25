import copy
import unittest

from rpg_engine import hot, preflight, fight
from tests.test_hot import turn


def packet():
    return {"cursor": "s14.text.023", "next_id": "s14.text.024",
            "lag": {"at": "0010-04-11T21:30:00", "recorded_turn": "s14.text.023",
                    "dice_for": "s14.text.024", "chakra_at": "0010-04-11T21:30:00"},
            "turn_file": "sha", "dice": [5, 18], "chakra": {"value": 2},
            "capabilities": {"catalog": [{"id": "restricted.unlearned", "activation": "unavailable"},
                                         {"id": "medical.field"}]},
            "medical": {"fields": ["where", "turn"], "patients": {"p": {"w": [["рука", "s14.text.001"],
                                                                              ["рука", "s14.text.002"]]}}}}


def draft(**extra):
    t = turn(24, "0010-04-11T21:30:00", id="s14.text.024",
             review={"predecessor": "s14.text.023"},
             d={"scene": {"present": ["shiki"]}},
             player="#открываю дверь# Где врач?", gm="Шики открыл дверь.\n**Мугами Шики:** — Где врач?")
    t.update(extra)
    return t


class DraftPreflightTest(unittest.TestCase):
    def test_read_only_clean_scene(self):
        p, t = packet(), draft()
        old = copy.deepcopy((p, t))
        result = preflight.inspect(p, t)
        self.assertTrue(result["mechanical_ok"], result)
        self.assertEqual(len(result["checklist"]), 6)
        self.assertEqual((p, t), old)
        self.assertFalse(any("дословную" in x for x in result["review"]))

    def test_stale_predecessor_and_blocked_packet(self):
        p = packet()
        p["fold_errors"] = ["bad"]
        result = preflight.inspect(p, draft(review={"predecessor": "s14.text.022"}))
        self.assertFalse(result["mechanical_ok"])
        self.assertGreaterEqual(len(result["errors"]), 2)

    def test_wrong_die_and_overdraw(self):
        t = draft(d={"scene": {"present": ["shiki"]}, "rolls": [{"what": "удар", "value": 18, "band": [2, 3], "cat": 2}],
                     "chakra": {"spend": [{"what": "x", "rank": "S"}]}})
        result = preflight.inspect(packet(), t)
        self.assertFalse(result["mechanical_ok"], result)
        self.assertTrue(any("кость" in x for x in result["errors"]))
        self.assertTrue(any("collapse" in x for x in result["errors"]))
        self.assertEqual(result["cost"]["spend"], fight.COST["S"] * fight.TECHNIQUE_COST_SCALE)

    def test_unavailable_only_if_explicitly_used(self):
        t = draft(gm="Шики открыл дверь.\n**Мугами Шики:** — Где врач?\nВрач назвал неизвестную технику.")
        self.assertTrue(preflight.inspect(packet(), t)["mechanical_ok"])
        self.assertFalse(preflight.inspect(packet(), t, used=["restricted.unlearned"])["mechanical_ok"])

    def test_ambiguous_wound_and_missing_scene(self):
        t = draft(d={"heal": [{"who": "p", "where": "рука"}]})
        result = preflight.inspect(packet(), t)
        self.assertFalse(result["mechanical_ok"])
        self.assertTrue(any("injury_turn" in x for x in result["errors"]))
        self.assertTrue(any("present" in x for x in result["errors"]))

    def test_speech_and_mechanics_distinct_checks(self):
        result = preflight.inspect(packet(), draft(gm="— Где врач?\nШики потратил 10 единиц чакры."))
        self.assertFalse(result["mechanical_ok"])
        self.assertTrue(any("реплик без подписи" in x for x in result["errors"]))
        self.assertTrue(any("дословную" in x for x in result["review"]))

    def test_ooc_stops_clock(self):
        result = preflight.inspect(packet(), draft(kind="ooc", at="0010-04-11T21:31:00", player="Мастеру: вопрос"))
        self.assertFalse(result["mechanical_ok"])
        self.assertTrue(any("OOC" in x for x in result["errors"]))

    def test_confirmed_pending_receipt_uses_new_cursor(self):
        first = draft()
        second = draft(id="s14.text.025", review={"predecessor": first["id"]})
        result = preflight.inspect(packet(), second, receipts=[{"turn": first, "sha": "new"}])
        self.assertTrue(result["mechanical_ok"], result)
        self.assertTrue(any("проекция" in x for x in result["review"]))

    def test_private_knowledge_cannot_be_copied_into_public_claims(self):
        t = draft(d={"scene": {"present": ["shiki"]},
                     "cast": {"p": {"knows+": ["тайна"]}},
                     "knowledge_events": [{"actor": "p", "claim": "тайна", "source": "s14.text.001",
                                           "channel": "document", "visibility": "gm_only"}]})
        result = preflight.inspect(packet(), t)
        self.assertFalse(result["mechanical_ok"])
        self.assertTrue(any("Закрытое" in x for x in result["errors"]))

    def test_wrong_review_type_is_diagnostic_not_exception(self):
        self.assertFalse(preflight.inspect(packet(), draft(review="invalid"))["mechanical_ok"])

    def test_news_requires_receipt_not_acknowledgment(self):
        p = packet()
        p["news_id"] = "evt_" + "a" * 20
        t = draft(d={"scene": {"present": ["shiki"]}, "must_done": [p["news_id"]]})
        self.assertFalse(preflight.inspect(p, t)["mechanical_ok"])
