"""Честный бой с именными противниками (поправка игрока 23.11.2026)."""
import unittest

from rpg_engine import fight

STATS = {"shunshin_no_jutsu": 5.5, "reflex_and_coordination": 5, "taijutsu": 4.5, "ninjutsu": 4.7, "genjutsu": 4.6}


def hot_with(foes=None):
    return {"cursor": {"id": "s11.text.045", "at": "0010-03-15T08:45:00"},
            "shiki": {"stats": STATS, "chakra": {"value": 90}},
            "_fight": {"active": {"id": "f1", "exchanges": 0, "foes": foes or {}, "rolls": [], "bursts": {}}}}


def turn(tid="s11.text.045"):
    return {"id": tid, "at": "0010-03-15T08:45:00", "kind": "play"}


class NamedFoesTest(unittest.TestCase):
    def test_roster_raises_lowballed_skills(self):
        hot = hot_with()
        notes = fight.apply(hot, {"fight": {"foes": {"itachi_puppet": {"name": "Итачи", "skills": {"genjutsu": 3.0}}}}},
                            turn(), fight.dice(hot))
        card = hot["_fight"]["active"]["foes"]["itachi_puppet"]
        self.assertTrue(card["named"])
        self.assertEqual(card["skills"]["genjutsu"], 5.0)
        self.assertTrue(any("подняты" in n["text"] for n in notes))

    def test_card_found_by_name(self):
        ref, card = fight.canon_card("m1", {"name": "Кисамэ"})
        self.assertEqual(ref, "kisame")

    def test_edge_capped_against_named(self):
        hot = hot_with({"x": {"name": "Ниндзя", "rank": "chunin", "status": "active"},
                        "k": {"name": "Кабуто", "canon": "kabuto", "named": True, "status": "active",
                              "skills": {"speed": 1.0}}})
        self.assertEqual(fight.edge(hot, {"skill": "speed", "vs": "x"})[1], 95)
        total, p, parts = fight.edge(hot, {"skill": "speed", "vs": "k"})
        self.assertEqual((total, p), (2.0, 80))
        self.assertIn("именной противник", parts)

    def test_foe_roll_and_interrupt(self):
        hot = hot_with({"i": {"name": "Итачи", "named": True, "status": "active",
                              "skills": {"speed": 5.2, "genjutsu": 6.0}}})
        total, p, _ = fight.foe_edge(hot, {"by": "i", "skill": "genjutsu"})
        self.assertEqual((total, p), (1.4, 71))
        total, p, parts = fight.foe_edge(hot, {"by": "i", "interrupt": True, "long": True})
        self.assertEqual(p, 60)
        self.assertIn("длинное действие Шики", parts)

    def test_named_foe_must_act_and_be_rolled_against(self):
        hot = hot_with({"i": {"name": "Итачи", "named": True, "status": "active", "skills": {"speed": 5.2}}})
        notes = fight.apply(hot, {"fight": {"foe_state": {}}}, turn(), fight.dice(hot))
        texts = " ".join(n["text"] for n in notes)
        self.assertIn("не действовал", texts)
        self.assertIn("нет ни одной кости", texts)
        pool = fight.dice(hot)
        hot2 = hot_with({"i": {"name": "Итачи", "named": True, "status": "active", "skills": {"speed": 5.2}}})
        notes = fight.apply(hot2, {"fight": {"foe_state": {}}, "rolls": [
            {"what": "прервать печати", "by": "i", "interrupt": True, "long": True, "value": pool[0]}]},
            turn("s11.text.046"), pool)
        self.assertFalse(any("не действовал" in n["text"] for n in notes))
        self.assertEqual(hot2["_fight"]["active"]["rolls"][-1]["by"], "i")

    def test_fight_turn_without_record(self):
        hot = hot_with({"i": {"name": "Итачи", "named": True, "status": "active"}})
        notes = fight.apply(hot, {}, turn(), fight.dice(hot))
        self.assertTrue(any("d.fight в ходе нет" in n["text"] for n in notes))

    def test_tempo_in_brief(self):
        hot = hot_with({"i": {"name": "Итачи", "named": True, "status": "active", "skills": {"speed": 5.2, "reaction": 5.6}}})
        line = fight.brief(hot)["active"]["foes"]["i"]["tempo"]
        self.assertIn("ещё один между шагами заявки", line)
        self.assertIn("прервать длинное действие Шики — p 60", line)


if __name__ == "__main__":
    unittest.main()


class ArsenalNemeraProseTest(unittest.TestCase):
    def test_arsenal_used_and_left(self):
        hot = hot_with({"sasori": {"name": "Сасори", "canon": "sasori", "named": True, "status": "active"}})
        hot["scene"] = {"present": ["chiyo"]}
        hot["cast"] = {"chiyo": {"name": "Тиё"}}
        fight.track_arsenal(hot, {"id": "s11.text.061", "gm": "Хируко ударил хвостом. Сенбоны ушли залпом."})
        used = hot["_fight"]["active"]["used"]["sasori"]
        self.assertIn("Хируко: хвост с ядом, броня", used)
        left = fight.arsenal_left(hot, "sasori", fight.roster()["sasori"])
        self.assertNotIn("Хируко: хвост с ядом, броня", left)
        brief = fight.brief(hot)["active"]
        self.assertIn("chiyo", brief["allies"])
        self.assertIn("десять кукол Тикамацу", brief["allies"]["chiyo"]["arsenal_left"])
        self.assertIn("Немера", brief["nemera"])

    def test_nemera_acts_every_exchange_and_speaks_on_her_own(self):
        hot = hot_with({"s": {"name": "Сасори", "named": True, "status": "active"}})
        hot["cast"] = {"nemera": {"name": "Немера"}}
        idle = {"id": "s11.text.061", "gm": "Сасори поднял руку.", "d": {"fight": {"foe_state": {}}}}
        self.assertIn("бездействовала", fight.nemera_turn(hot, idle)[0]["text"])
        busy = {"id": "s11.text.062", "gm": "Отросток Немеры перехватил клинок.", "d": {"fight": {}}}
        self.assertEqual(fight.nemera_turn(hot, busy), [])
        calm = hot_with()
        calm["_fight"] = {}
        calm["cast"] = {"nemera": {"name": "Немера"}}
        notes = [fight.nemera_turn(calm, {"id": f"s11.text.{i:03d}", "gm": "Тишина."}) for i in range(1, 11)]
        self.assertEqual(sum(1 for n in notes if n), 1)
        self.assertIn("молчит 10 ходов", notes[-1][0]["text"])

    def test_mechanics_numbers_in_prose(self):
        from rpg_engine import hot as hot_module, prose_lint
        self.assertIsNotNone(prose_lint.mechanics("За две секунды двадцать единиц её чакры перешли к Шики."))
        self.assertIsNone(prose_lint.mechanics("Холод второй чакры пошёл по каналам. Три метра до выхода."))
        report = hot_module._lint("Чакра Шики: 87/100. " + "Он ждал у выхода. " * 40)
        self.assertEqual(report["verdict"], "переписать")
        self.assertIn("mechanics", report["by_rule"])
