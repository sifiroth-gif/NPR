"""Бой по образцу Йокогамы (Bleach III S32, LIVE/2): соотношение сил → полоса, d20 → категория."""
import unittest

from rpg_engine import bands, body, fight

STATS = {"shunshin_no_jutsu": 5.5, "reflex_and_coordination": 5, "taijutsu": 4.5, "ninjutsu": 4.7,
         "genjutsu": 4.6, "chakra_reserves": 4.8, "physical_endurance": 4.6}


def hot(foes):
    return {"cursor": {"id": "s13.text.070", "at": "0010-04-03T13:00:00"},
            "shiki": {"stats": dict(STATS), "chakra": {"value": 90}},
            "_fight": {"active": {"id": "f", "exchanges": 1, "foes": foes, "rolls": [], "bursts": {}}}}


class YokohamaDiceTest(unittest.TestCase):
    def test_played_rolls_map_as_in_the_session(self):
        self.assertEqual(bands.from_die([4, 5, 6], 4), 4, "Курохицуги: полоса 4–6, d20 4 — встречный срыв")
        self.assertEqual(bands.from_die([5, 6, 7], 3), 5, "Фумяку: полоса 5–7, d20 3 — ограниченный успех")
        self.assertEqual(bands.from_die([5, 6, 7], 15), 7)
        self.assertEqual(bands.from_die([6], 1), 6, "одна категория — без куба")
        self.assertEqual([bands.from_die([2, 3, 4, 5], v) for v in (5, 6, 15, 16)], [2, 3, 4, 5])

    def test_category_thresholds_and_axis_steps(self):
        self.assertEqual([bands.category(m) for m in (-9, -5, -2, 0, 2, 5, 9)], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(bands.axis(0), (0, 0))
        self.assertEqual(bands.axis(0.5), (0, 1), "у границы — неуверенность")
        self.assertEqual(bands.axis(1.0), (1, 1))
        self.assertEqual(bands.axis(-1.6), (-2, -1))
        self.assertEqual(bands.axis(3), (2, 2))
        self.assertEqual(bands.parse("0:1"), (0, 1))
        self.assertEqual(bands.parse(5), (2, 2))


class PowerRatioTest(unittest.TestCase):
    def test_the_same_blow_lands_differently_by_who_takes_it(self):
        h = hot({"c": {"name": "Чунин", "rank": "chunin", "status": "active"},
                 "j": {"name": "Дзёнин", "rank": "jonin", "status": "active"},
                 "k": {"name": "Каге", "rank": "kage", "status": "active"}})
        weak = fight.resolve(h, {"skill": "taijutsu", "vs": "c"})["band"]
        mid = fight.resolve(h, {"skill": "taijutsu", "vs": "j"})["band"]
        top = fight.resolve(h, {"skill": "taijutsu", "vs": "k"})["band"]
        self.assertGreater(weak[0], top[0])
        self.assertGreaterEqual(weak[-1], mid[-1])
        self.assertGreater(len(top), 1, "каге — всегда бросок")
        self.assertIn(4, top + [x for x in range(top[0], top[-1] + 1)], "равный по силе — встречный срыв возможен")

    def test_named_card_and_foe_roll(self):
        h = hot({"i": {"name": "Итачи", "named": True, "status": "active",
                       "skills": {"speed": 5.2, "genjutsu": 6.0, "reaction": 5.6, "reserves": 4.5}}})
        theirs = fight.resolve(h, {"by": "i", "skill": "genjutsu"})
        self.assertEqual(theirs["axes"]["A"], (1, 2), "гендзюцу Итачи 6,0 против 4,6 Шики")
        slow = fight.resolve(h, {"by": "i", "skill": "speed", "interrupt": True, "long": True})
        quick = fight.resolve(h, {"by": "i", "skill": "speed", "interrupt": True})
        self.assertEqual(slow["axes"]["D"][0], quick["axes"]["D"][0] + 1, "длинное действие Шики — окно")
        seals = fight.resolve(h, {"by": "i", "skill": "speed", "interrupt": True, "long": True, "long_kind": "handseals"})
        self.assertEqual(seals["axes"]["D"], quick["axes"]["D"], "освоенные быстрые печати не дают окна")
        mine = fight.resolve(h, {"skill": "genjutsu", "vs": "i"})
        self.assertGreater(len(mine["band"]), 1)

    def test_wounds_shift_both_ways(self):
        h = hot({"j": {"name": "Дзёнин", "rank": "jonin", "status": "active"}})
        clean = fight.resolve(h, {"skill": "ninjutsu", "vs": "j"})["M"]
        h["injuries"] = {"shiki": [{"where": "бок", "severity": "тяжёлая"}]}
        hurt = fight.resolve(h, {"skill": "ninjutsu", "vs": "j"})["M"]
        self.assertEqual(hurt[0], clean[0] - 1, "рана Шики — ось F")
        h["injuries"] = {"j": [{"where": "плечо", "severity": "тяжёлая"}]}
        open_ = fight.resolve(h, {"skill": "ninjutsu", "vs": "j"})["M"]
        self.assertEqual(open_[0], clean[0] + 1, "рана цели — прибавка к E")
        h["injuries"] = {}
        h["body"] = {"shiki": {"loss": 3}}
        self.assertEqual(fight.burden(h, "shiki"), (-2, -2), "шок от кровопотери")


class RollNotesTest(unittest.TestCase):
    def test_risk_and_aftermath(self):
        h = hot({"k": {"name": "Каге", "rank": "kage", "status": "active"}})
        # The permanent Sage bonus means the original uninjured fixture no
        # longer reaches category 1. A critical wound supplies the actual risk.
        h["injuries"] = {"shiki": [{"where": "бок", "severity": "критическая"}]}
        pool = fight.dice(h)
        t = {"id": "s13.text.071", "at": "0010-04-03T13:00:10", "place": "мост"}
        notes = fight.apply(h, {"fight": {"foe_actions": {"k": "удар"}}, "rolls": [
            {"what": "гроб", "skill": "taijutsu", "vs": "k", "axes": {"B": -1, "C": -2, "D": -2, "E": -2},
             "value": pool[0]}]}, t, pool)
        self.assertTrue(any("risk" in n["text"] for n in notes), "в полосе 1 — ущерб называют до броска")
        t2 = {"id": "s13.text.072", "at": "0010-04-03T13:00:20", "place": "мост"}
        fight.apply(h, {"fight": {"end": "каге отступил"}}, t2, fight.dice(h))
        h["cursor"]["id"] = "s13.text.072"
        self.assertIn("счёт за бой", fight.aftermath(h))
        h["cursor"]["id"] = "s13.text.073"
        self.assertIsNone(fight.aftermath(h))

    def test_body_check_uses_bands(self):
        h = hot({})
        fight._wounds(h, [{"who": "shiki", "where": "бок", "what": "клинок", "severity": "тяжёлая"}],
                      "s13.text.070", "0010-04-03T13:00:00")
        rows = body.check(h, {"id": "s13.text.071", "at": "0010-04-03T13:00:05"}, {"rolls": [{"skill": "ninjutsu"}]})
        self.assertEqual(rows[0]["band"], [3, 4, 5], "постоянный режим отшельника повышает выносливость")
        self.assertIn(rows[0]["cat"], rows[0]["band"])


if __name__ == "__main__":
    unittest.main()
