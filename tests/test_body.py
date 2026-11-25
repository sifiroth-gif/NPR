"""Игрок, 25.11.2026 (бой в Йокогаме, Bleach III S32): кости решали, усталость — от раны, не от энергии."""
import unittest

from rpg_engine import body, fight


def _hot(endurance=4.6):
    return {"cursor": {"id": "s13.text.030", "at": "0010-03-30T10:00:00"},
            "shiki": {"stats": {"physical_endurance": endurance, "taijutsu": 4.5, "ninjutsu": 4.7}},
            "_fight": {"active": {"id": "f1", "foes": {"oni": {"name": "Они", "rank": "jonin"}},
                                  "rolls": [], "bursts": {}, "exchanges": 1}}}


def _turn(n, minute=0):
    return {"id": f"s13.text.{n:03}", "at": f"0010-03-30T10:{minute:02}:00"}


class BleedTest(unittest.TestCase):
    def test_new_cut_bleeds_old_record_does_not(self):
        self.assertTrue(body.bleeds({"severity": "тяжёлая", "what": "удар"}))
        self.assertTrue(body.bleeds({"severity": "средняя", "what": "клинок рассёк бок"}))
        self.assertFalse(body.bleeds({"severity": "средняя", "what": "удар основанием ладони"}))
        self.assertFalse(body.bleeds({"severity": "тяжёлая", "bleed": False}))
        hot = _hot()
        hot["injuries"] = {"shiki": [{"where": "бок", "severity": "тяжёлая", "at": "0010-03-29T10:00:00"}]}
        self.assertEqual(body.check(hot, _turn(31, 30), {"chakra": {"spend": [{"rank": "B"}]}}), [],
                         "старая запись без bleed — тело не бросает")

    def test_exertion_rolls_state_and_worsening_reaches_rolls(self):
        hot = _hot()
        fight._wounds(hot, [{"who": "shiki", "where": "правый бок", "what": "меч рассёк мышцы",
                             "severity": "тяжёлая"}], "s13.text.030", "0010-03-30T10:00:00")
        self.assertTrue(hot["injuries"]["shiki"][0]["bleed"])
        worse = None
        for n in range(31, 60):
            hot["cursor"]["id"] = f"s13.text.{n:03}"
            rows = body.check(hot, _turn(n, 0), {"rolls": [{"skill": "ninjutsu"}]})
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["die"], body._die(f"s13.text.{n:03}", "shiki", 0), "кость — хеш хода")
            if row["after"] > row["before"]:
                worse = n
                break
        self.assertIsNotNone(worse, "под нагрузкой тяжёлая рана рано или поздно берёт своё")
        item = body.must(hot)
        self.assertIn("проверка состояния", item)
        self.assertIn("без чисел", item)
        _total, _p, parts = fight.edge(hot, {"skill": "ninjutsu", "vs": "oni"})
        self.assertIn("кровопотеря", parts, "печати тоже страдают — не только тело")

    def test_stop_keeps_wound_and_blood_restores(self):
        hot = _hot()
        fight._wounds(hot, [{"who": "shiki", "where": "бок", "what": "клинок", "severity": "тяжёлая"}],
                      "s13.text.030", "0010-03-30T10:00:00")
        hot["body"] = {"shiki": {"loss": 2, "stage": body.LOSS[2], "at": "0010-03-30T10:00:00"}}
        fight._heal(hot, {"who": "shiki", "where": "бок", "stop": True}, "s13.text.031")
        self.assertFalse(hot["injuries"]["shiki"][0]["bleed"])
        self.assertNotIn("healed", hot["injuries"]["shiki"][0], "кровь остановлена, рана осталась")
        self.assertEqual(body.check(hot, _turn(32, 5), {"rolls": [{"skill": "ninjutsu"}]}), [])
        fight._heal(hot, {"who": "shiki", "blood": True}, "s13.text.033")
        self.assertEqual(body.stage(hot, "shiki"), 1)

    def test_quiet_bleeding_checks_by_time(self):
        hot = _hot()
        fight._wounds(hot, [{"who": "shiki", "where": "бедро", "what": "кунай", "severity": "тяжёлая"}],
                      "s13.text.030", "0010-03-30T10:00:00")
        self.assertEqual(body.check(hot, _turn(31, 5), {}), [], "пять минут покоя — рано")
        self.assertEqual(len(body.check(hot, _turn(32, 25), {})), 1)
        self.assertNotIn("body", _hot(), "без крови — без записи")


if __name__ == "__main__":
    unittest.main()
