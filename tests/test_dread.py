import json
import unittest

from rpg_engine import dread, prose_lint


def fresh():
    return {"cast": {"nori": {"name": "Хаясэ Нори", "fears": "потерять дело"},
                     "tetsu": {"name": "Тэцу"}},
            "scene": {"present": ["shiki", "nori", "tetsu"]}}


def kinds(h):
    return [c["kind"] for c in h.get("_checks", [])]


class LadderTest(unittest.TestCase):
    def test_calibration_then_one_step_per_turn(self):
        h = fresh()
        dread.apply(h, "nori", {"saw": "s04.text.020: зашил Тэцу без инструментов", "reads": "он не обычный медик",
                                "stage": 1}, "t1", "0006-10-01T07:00:00")
        dread.apply(h, "nori", {"saw": "прочитал пакет не вскрывая", "reads": "он знает больше, чем говорит",
                                "stage": 4}, "t2", "0006-10-01T07:10:00")
        state = h["_psyche"]["nori"]["dread"]
        self.assertEqual(state["stage"], 2)
        self.assertIn("horror", kinds(h))

    def test_growth_requires_saw_and_action_from_third_step(self):
        h = fresh()
        self.assertFalse(dread.apply(h, "nori", {"reads": "страшно"}, "t1", "0006-10-01T07:00:00"))
        dread.apply(h, "nori", {"saw": "x", "reads": "y", "stage": 2}, "t2", "0006-10-01T07:00:00")
        dread.apply(h, "nori", {"saw": "x2", "reads": "y2"}, "t3", "0006-10-01T07:00:00")
        self.assertEqual(h["_psyche"]["nori"]["dread"]["stage"], 3)
        self.assertTrue(any("без does" in c["text"] for c in h["_checks"]))

    def test_hold_down_and_rumor(self):
        h = fresh()
        dread.apply(h, "nori", {"saw": "x", "reads": "y", "stage": 3, "does": "d", "tells": ["kase"]},
                    "t1", "0006-10-01T07:00:00")
        dread.apply(h, "nori", {"saw": "x", "hold": True}, "t2", "0006-10-01T07:00:00")
        self.assertEqual(h["_psyche"]["nori"]["dread"]["stage"], 3)
        dread.apply(h, "nori", {"saw": "x", "down": "получила над ним рычаг"}, "t3", "0006-10-01T07:00:00")
        self.assertEqual(h["_psyche"]["nori"]["dread"]["stage"], 2)
        self.assertEqual(h["_psyche"]["kase"]["dread"]["stage"], 1)
        self.assertTrue(h["_psyche"]["kase"]["dread"]["rumor"])


class ObligationTest(unittest.TestCase):
    def test_quiet_turns_raise_sticky_due_until_fed(self):
        h = fresh()
        for n in range(1, 4):
            dread.after_turn(h, {"id": f"t{n}", "gm": "Нори кивнула."}, {}, {})
        self.assertEqual(h["_horror"]["due"]["npcs"], ["nori", "tetsu"])
        dread.after_turn(h, {"id": "t4", "gm": "…"}, {}, {})
        self.assertEqual(h["_horror"]["due"]["since"], "t3")
        brief = dread.brief(h, ["shiki", "nori", "tetsu"])
        self.assertEqual(brief["due"]["quiet"], 4)
        self.assertIsNone(brief["npcs"]["nori"]["stage"])
        self.assertEqual(brief["npcs"]["nori"]["lever"], "потерять дело")
        # Общая заметка d.horror не заменяет реакцию людей рядом с Шики.
        dread.after_turn(h, {"id": "t5", "gm": "…"}, {}, {"horror": "печать на двери оказалась вскрыта изнутри"})
        self.assertIn("due", h["_horror"])
        self.assertEqual([k for k, _ in dread.pending(h, ["shiki", "nori", "tetsu"])], ["nori", "tetsu"])
        reaction = {"nori": {"dread": {"saw": "Шики вскрыл печать без инструмента", "reads": "он опаснее её дела",
                                       "does": "переписала акт", "stage": 1}}}
        dread.after_turn(h, {"id": "t6", "gm": "…"}, reaction, {})
        self.assertNotIn("due", h["_horror"])
        self.assertEqual(h["_horror"]["quiet"], 0)
        self.assertEqual([k for k, _ in dread.pending(h, ["shiki", "nori", "tetsu"])], ["tetsu"])
        self.assertIn("впечатление", dread.summary(h, "nori") + dread.LADDER[0][0])

    def test_general_horror_note_counts_only_without_people(self):
        h = fresh()
        h["scene"]["present"] = ["shiki"]
        dread.after_turn(h, {"id": "t1", "gm": "…"}, {}, {"horror": "в доме кто-то был"})
        self.assertEqual(h["_horror"].get("quiet", 0), 0)

    def test_scene_without_npcs_does_not_count(self):
        h = fresh()
        h["scene"]["present"] = ["shiki"]
        for n in range(5):
            dread.after_turn(h, {"id": f"t{n}", "gm": "Ночь."}, {}, {})
        self.assertNotIn("due", h["_horror"])


class MotifTest(unittest.TestCase):
    def test_look_substitution_and_stock_reactions(self):
        text = ("Взгляд офицера задержался на чёрном капюшоне. Под пелериной угадывались корневидные линии. "
                "Шики стоял у стола, тёмные сосуды проступали на запястье.\n\n"
                "Охранник сглотнул. Он замер, плечи напряглись, пальцы задрожали.")
        found = prose_lint.motifs(text)
        self.assertEqual(len(found["look"]), 3)
        self.assertTrue(found["look"][0].startswith("взгляд-по-облику"))
        self.assertGreaterEqual(len(found["soma"]), 3)
        report = prose_lint.lint(text)
        self.assertIn("look", report["by_rule"])
        self.assertIn("soma", report["by_rule"])

    def test_medical_and_scene_words_are_not_look(self):
        text = ("Диагностическая чакра Шики вошла через предплечье Кагэ, распространяясь по сосудистым сплетениям. "
                "На мокром камне остался короткий серебристый росчерк. Нори ждала у ворот.")
        self.assertEqual(prose_lint.motifs(text)["look"], [])

    def test_worn_motifs_across_turns(self):
        h = fresh()
        dread.after_turn(h, {"id": "t1", "gm": "Взгляд скользнул к капюшону."}, {}, {})
        dread.after_turn(h, {"id": "t2", "gm": "Чёрный капюшон качнулся."}, {}, {})
        self.assertIn("взгляд-по-облику", " ".join(m for x in h["_horror"]["motifs"] for m in x["look"]))
        self.assertIn("капюшон", h["_horror"]["worn"])


if __name__ == "__main__":
    unittest.main()

class IndividualReactionTest(unittest.TestCase):
    def test_repeated_observation_does_not_raise_dread(self):
        h = fresh()
        at = "0006-10-01T07:00:00"
        dread.apply(h, "nori", {"saw": "видела рубец", "reads": "знакомая техника", "stage": 0}, "t1", at)
        for i in range(2, 8):
            dread.apply(h, "nori", {"saw": "видела рубец", "reads": "знакомая техника"}, f"t{i}", at)
        self.assertEqual(h["_psyche"]["nori"]["dread"]["stage"], 0)
        dread.apply(h, "nori", {"saw": "новый документ", "reads": "проверю", "stage": 0}, "t8", at)
        self.assertEqual(h["_psyche"]["nori"]["dread"]["stage"], 0)

    def test_neutral_first_hold_and_different_actor_trajectory(self):
        h = fresh()
        at = "0006-10-01T07:00:00"
        dread.apply(h, "nori", {"saw": "вошёл", "hold": True, "reads": "обычный врач"}, "t1", at)
        dread.apply(h, "tetsu", {"saw": "увидел старый шов", "reads": "не понимаю", "stage": 2}, "t1", at)
        self.assertEqual(h["_psyche"]["nori"]["dread"]["stage"], 0)
        self.assertEqual(h["_psyche"]["tetsu"]["dread"]["stage"], 2)
        self.assertIn("hold/down", dread.brief(h, ["nori"])["npcs"]["nori"]["next"])

    def test_absent_reaction_does_not_satisfy_present_obligation(self):
        h = fresh()
        for i in range(1, 4):
            dread.after_turn(h, {"id": f"t{i}", "gm": "Тихо."},
                             {"elsewhere": {"dread": {"saw": "письмо", "reads": "опасно"}}}, {})
        self.assertIn("due", h["_horror"])
        self.assertEqual(h["_horror"]["due"]["npcs"], ["nori", "tetsu"])
