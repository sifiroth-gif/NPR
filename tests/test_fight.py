import json
import unittest

from rpg_engine import fight, hot
from tests import test_hot
from tests.test_hot import base, turn

STATS = {"shunshin_no_jutsu": 5.5, "reflex_and_coordination": 5, "taijutsu": 4.5, "ninjutsu": 4.7,
         "genjutsu": 4.6, "medical_ninjutsu": 5, "physical_strength": 3.8}


def state():
    h = {"cursor": {"id": "s04.text.044", "at": "0006-10-01T07:10:00"}, "last": {"id": "s04.text.044", "gm": "…"},
         "shiki": {"stats": dict(STATS), "chakra": {"value": 80.0, "at": "0006-10-01T07:10:00"}}}
    return h


def play(h, number, d, at="0006-10-01T07:12:00"):
    t = {"id": f"s04.text.{number:03}", "at": at, "place": "двор"}
    pool = fight.dice(h)
    notes = fight.apply(h, d, t, pool)
    h["cursor"] = {"id": t["id"], "at": at}
    h["last"] = {"id": t["id"], "gm": f"ход {number}"}
    return pool, [n["text"] for n in notes]


class FightTest(unittest.TestCase):
    def test_dice_depend_only_on_folded_state(self):
        h = state()
        self.assertEqual(fight.dice(h), fight.dice(json.loads(json.dumps(h))))
        self.assertEqual(len(fight.dice(h)), fight.DICE)
        self.assertTrue(all(0 <= v < 100 for v in fight.dice(h)))
        other = state()
        other["cursor"]["id"] = "s04.text.045"
        self.assertNotEqual(fight.dice(h), fight.dice(other))

    def test_threshold_formula_and_outcomes(self):
        h = state()
        h["_fight"] = {"active": {"foes": {"guard": {"rank": "jonin"}}}}
        total, p, parts = fight.edge(h, {"skill": "taijutsu", "vs": "guard", "mods": {"внезапность": 1}})
        self.assertEqual((total, p), (2.0, 80))
        self.assertEqual(parts["противник"], -3.5)
        _total, p, _ = fight.edge(h, {"skill": "taijutsu", "vs": "guard", "mods": {"внезапность": 5}})
        self.assertEqual(p, 80, "поправка обрезается до своего предела")
        self.assertEqual([fight.outcome(60, v) for v in (10, 50, 70, 80)], ["чисто", "успех", "с ценой", "провал"])

    def test_wounds_chakra_and_foe_morale_shift_the_edge(self):
        h = state()
        h["_fight"] = {"active": {"foes": {"guard": {"rank": "jonin", "morale": "shaken"}}}}
        h["injuries"] = {"shiki": [{"where": "левое бедро", "severity": "тяжёлая"}],
                         "guard": [{"where": "плечо", "severity": "средняя"}]}
        h["shiki"]["chakra"]["value"] = 20
        total, _p, parts = fight.edge(h, {"skill": "taijutsu", "vs": "guard"})
        self.assertEqual(parts["раны Шики"], -1)
        self.assertNotIn("чакра", parts)
        self.assertEqual(parts["мораль противника"], 0.5)
        self.assertEqual(total, 4.5 - 3.5 - 1 + 0.5 + 0.5)
        _t, _p, parts = fight.edge(h, {"skill": "ninjutsu", "vs": "guard"})
        self.assertEqual(parts["чакра"], -1)
        self.assertNotIn("раны Шики", parts)

    def test_rolls_must_follow_the_dice_order_and_formula(self):
        h = state()
        pool = fight.dice(h)
        good = {"what": "сенбон", "skill": "weapons", "vs": 3.5, "p": 73, "value": pool[0],
                "result": fight.outcome(73, pool[0])}
        bad = {"what": "уход", "skill": "speed", "vs": 3.5, "p": 95, "value": (pool[1] + 1) % 100, "result": "успех"}
        _pool, notes = play(h, 45, {"rolls": [good, bad]})
        self.assertEqual(len([n for n in notes if "«сенбон»" in n]), 0)
        self.assertTrue(any("«уход»: взята кость" in n for n in notes))
        self.assertTrue(any("«уход»: порог 95, по формуле 80" in n for n in notes))

    def test_fight_lifecycle_wounds_exchanges_and_end(self):
        h = state()
        play(h, 45, {"fight": {"start": {"id": "dvor"}, "foes": {"m1": {"name": "Маска", "rank": "chunin"}},
                               "wounds": [{"who": "m1", "where": "правое плечо", "severity": "критическая",
                                           "lost": "рука не держит нож"}]}})
        notes = play(h, 46, {"fight": {"foes": {}}})[1]
        self.assertTrue(any("критической раной всё ещё действует" in n for n in notes))
        for n in range(47, 51):
            notes = play(h, n, {"fight": {"bursts": 1}})[1]
        self.assertTrue(any("идёт 6 обменов" in n for n in notes))
        notes = play(h, 51, {"fight": {"foe_state": {"m1": {"status": "captive"}}}})[1]
        self.assertTrue(any("закрой бой" in n for n in notes))
        play(h, 52, {"fight": {"end": "Маска взята живой"}, "heal": [{"who": "m1"}]})
        self.assertNotIn("active", h["_fight"])
        self.assertEqual(h["_fight"]["closed"][-1]["outcome"], "Маска взята живой")
        self.assertNotIn("m1", h.get("injuries", {}))

    def test_bursts_beyond_the_sheet_are_flagged_and_cost_chakra(self):
        h = state()
        notes = play(h, 45, {"fight": {"start": "x", "bursts": 5}})[1]
        self.assertTrue(any("5 жёстких рывков" in n for n in notes))
        self.assertEqual(h["shiki"]["chakra"]["value"], 80 + 6 * (2 / 60) - 15)

    def test_chakra_spend_collapse_lockout_and_regen(self):
        h = state()
        play(h, 45, {"chakra": {"spend": [{"what": "Бётон: Заражение", "rank": "B"},
                                          {"what": "регенерация", "cost": 70}]}}, at="0006-10-01T07:10:00")
        self.assertEqual(h["shiki"]["chakra"]["value"], 0.0)
        notes = play(h, 46, {"chakra": {"collapse": True}}, at="0006-10-01T07:10:00")[1]
        self.assertEqual(notes, [])
        self.assertEqual(h["shiki"]["chakra"]["locked_until"], "0006-10-01T09:10:00")
        play(h, 47, {}, at="0006-10-01T09:00:00")
        self.assertEqual(h["shiki"]["chakra"]["value"], 0.0, "до конца блокировки чакра не идёт")
        play(h, 48, {}, at="0006-10-01T11:10:00")
        self.assertEqual(h["shiki"]["chakra"]["value"], 12.0)
        self.assertEqual(h["shiki"]["chakra"]["state"], "на исходе")
        self.assertNotIn("locked_until", h["shiki"]["chakra"])

    def test_spending_past_zero_asks_for_collapse(self):
        h = state()
        h["shiki"]["chakra"]["value"] = 10
        notes = play(h, 45, {"chakra": {"spend": [{"what": "Плачущие Бубоны", "rank": "B"}]}},
                     at="0006-10-01T07:10:00")[1]
        self.assertTrue(any("исчерпана" in n for n in notes))

    def test_brief_and_scene_lines(self):
        h = state()
        play(h, 45, {"fight": {"start": {"id": "dvor"}, "foes": {"m1": {"rank": "elite"}},
                               "wounds": [{"who": "shiki", "where": "левое предплечье", "severity": "лёгкая"}]}})
        data = fight.brief(h)
        self.assertEqual(data["dice"], fight.dice(h))
        self.assertEqual(data["active"]["foes"]["m1"]["levels"]["taijutsu"], 4.2)
        self.assertEqual(data["active"]["exchanges_left"], 5)
        lines = fight.scene_lines(h)
        self.assertTrue(any("идёт бой `dvor`" in x for x in lines))
        self.assertTrue(any("левое предплечье" in x for x in lines))


class FightFoldTest(unittest.TestCase):
    setUp, tearDown = test_hot.HotFilesTest.setUp, test_hot.HotFilesTest.tearDown
    git, commit, put = test_hot.HotFilesTest.git, test_hot.HotFilesTest.commit, test_hot.HotFilesTest.put

    def test_brief_dice_are_the_ones_checked_on_the_next_turn(self):
        self.put(turn(45, "0006-10-01T07:14:00",
                      d={"fight": {"start": {"id": "dvor"}, "foes": {"m1": {"name": "Маска", "rank": "chunin"}}}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        self.commit("fold 45")
        brief = json.loads((self.root / "recovery/GM_BRIEF.json").read_text(encoding="utf-8"))
        dice = brief["fight"]["dice"]
        self.assertEqual(brief["fight"]["active"]["foes"]["m1"]["name"], "Маска")
        self.put(turn(46, "0006-10-01T07:15:00",
                      d={"rolls": [{"what": "удар", "skill": "taijutsu", "vs": "m1", "level": 4.5,
                                    "p": 80, "value": dice[0], "result": fight.outcome(80, dice[0])}],
                         "fight": {"wounds": [{"who": "m1", "where": "колено", "severity": "тяжёлая"}]}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        checks = json.loads((self.root / hot.CHECKS_PATH).read_text(encoding="utf-8"))["checks"]
        self.assertFalse([c for c in checks if c["kind"] == "fight"], checks)
        secret = json.loads((self.root / hot.SECRET_PATH).read_text(encoding="utf-8"))
        self.assertEqual(secret["fight"]["active"]["rolls"][-1]["value"], dice[0])
        public = json.loads((self.root / hot.HOT_PATH).read_text(encoding="utf-8"))
        self.assertNotIn("_fight", public)
        self.assertEqual(public["injuries"]["m1"][0]["where"], "колено")
        scene = (self.root / "memory/SCENE.md").read_text(encoding="utf-8")
        self.assertIn("идёт бой `dvor`", scene)


if __name__ == "__main__":
    unittest.main()
