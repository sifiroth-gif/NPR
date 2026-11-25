import copy
import unittest
from rpg_engine import news, will, obligations, nextpack, aging


def state():
    return {"cursor": {"id": "s14.text.023", "at": "0010-04-11T21:30:00"},
            "scene": {"present": ["shiki"]},
            "_will": {"happened": []}, "_psyche": {}}


def event(i=0):
    return {"npc": "a", "plan": "p", "at": "0010-04-10T10:00:00",
            "turn": f"s14.text.{i+1:03}", "status": "fulfilled", "outcome": f"Письмо {i} доставлено",
            "against_shiki": "интерес", "shown": 3}


class WorldDeliveryTest(unittest.TestCase):
    def test_same_plan_at_same_time_distinct_events(self):
        h = state()
        h["_will"]["happened"] = [event(0), event(1)]
        news.migrate(h)
        ids = [x["id"] for x in h["_will"]["happened"]]
        self.assertEqual(len(set(ids)), 2)
        news.migrate(h)
        self.assertEqual(ids, [x["id"] for x in h["_will"]["happened"]])

    def test_name_is_not_delivery_and_receipt_idempotent(self):
        h = state()
        e = event()
        h["_will"]["happened"] = [e]
        state_ = news.migrate(h)
        state_["current"] = dict(e, text="Письмо доставлено", markers=["минас"], set=1, set_at=e["at"])
        h["cursor"].update(id="s14.text.024", at="0010-05-01T10:00:00")
        news.step(h, [{"id": "s14.text.024", "gm": "**Врач:** — Минасэ."}])
        self.assertIn("current", state_)
        self.assertEqual(state_["delivered"], 0)
        quote = "Письмо доставлено."
        turn = {"id": "s14.text.025", "at": "0010-05-01T10:01:00", "gm": "**Врач:** — " + quote,
                "d": {"news_delivered": [{"id": e["id"], "channel": "told_in_scene", "quote": quote}]}}
        news.accept(h, turn)
        news.accept(h, turn)
        self.assertEqual(state_["delivered"], 1)
        self.assertNotIn("current", state_)
        self.assertEqual(e["news"], "delivered")

    def test_invalid_quote_or_unknown_event_rejected(self):
        h = state()
        for gm, ident in (("**Мугами Шики:** — Письмо доставлено.", news.event_id(event())),
                          ("**Врач:** — Письмо доставлено.", news.event_id(event()))):
            with self.assertRaises(ValueError):
                news.accept(h, {"id": "s14.text.024", "at": "0010-04-11T21:31:00", "gm": gm,
                                "d": {"news_delivered": [{"id": ident, "channel": "told_in_scene",
                                                           "quote": "Письмо доставлено."}]}})

    def test_important_events_survive_long_skip_and_history_limit(self):
        h = state()
        h["_will"]["happened"] = [event(i) for i in range(40)]
        h["cursor"]["at"] = "0012-04-11T21:30:00"
        will.advance(h)
        self.assertEqual(len(news.pending(h)), 40)
        self.assertEqual(len(h["_will"]["happened"]), 40)

    def test_orphaned_legacy_offer_retained(self):
        h = state()
        h["_will"]["news"] = {"current": {"text": "Старая весть", "npc": "a", "plan": "p", "at": "0010-04-10T10:00:00"}}
        state_ = news.migrate(h)
        ident = state_["current"]["id"]
        self.assertTrue(news.EVENT_ID.fullmatch(ident))
        self.assertEqual(news.migrate(h)["current"]["id"], ident)
        self.assertTrue(state_["current"]["legacy_offer"])

    def test_queue_not_truncated_and_ack_is_stable(self):
        h = state()
        candidates = [("scene", f"обязанность {i}", None) for i in range(7)]
        texts, ids, rest = obligations.select(h, candidates)
        self.assertEqual((len(texts), len(rest)), (3, 4))
        self.assertEqual(len(h["_will"]["obligations"]), 7)
        obligations.accept(h, {"id": "s14.text.024", "d": {"must_done": ids}})
        h["cursor"]["id"] = "s14.text.024"
        _, next_ids, rest = obligations.select(h, candidates)
        self.assertFalse(set(ids) & set(next_ids))
        self.assertEqual(len(rest), 1)

    def test_all_due_plans_reach_queue(self):
        h = state()
        h["scene"]["present"] += ["a"]
        h["_psyche"]["a"] = {"intent": {str(i): {"due": "0010-04-11T20:00:00", "next": str(i)}
                                        for i in range(6)}}
        pack = nextpack.build(h, {"cursor": h["cursor"]}, {}, [])
        self.assertEqual(len(pack["must"]), 3)
        self.assertGreaterEqual(len(pack["must_pending"]["items"]), 3)
        self.assertEqual(sum(row["kind"] == "due" for row in h["_will"]["obligations"].values()), 6)
