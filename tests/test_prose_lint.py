import unittest

from rpg_engine import prose_lint

BAD = """**Воскресенье, 1 октября 1283 года. 07:07.**
*Кусагакуре. Дом Мугами Шики.*

**Старший прибывшей группы:**
— Поняли, Мугами-сан. Остаёмся за воротами. Наша задача — не отнять у вас пациента.

Произнёс он это ровно, хотя последнее слово было выбрано намеренно. Он признавал право Шики распоряжаться лечением, не признавая за ним власти над самой служебной проверкой.

Однако уже потерянная кровь не вернулась в сосудистое русло чудесным образом: Тэцу сохранял слабость.

Пакет, таким образом, оказался прежним официальным вызовом.

**Хаясэ Нори:**
— Мугами-сан. Это моя группа. Без вашего согласия никто не войдёт.

— Я пришла не спорить о том, кому он принадлежит.

Нори осталась у ворот. Даже теперь она не потребовала открыть дверь. В голосе не было ничего церемониального.
"""

GOOD = """**Воскресенье, 1 октября 1283 года. 07:10.**
*Кусагакуре. Дом Мугами Шики.*

Калитка ударила о столб. Нори вошла во двор, не дожидаясь ответа, и присела у крыльца. Потрогала доску, растёрла бурое между пальцами.

**Хаясэ Нори:**
— Сколько он потерял?

Трое за её спиной разошлись вдоль забора. Младший встал так, чтобы видеть окно.

— Он мне нужен говорящим, Мугами. Через час Центральное пришлёт своих, и тогда его заберут у нас обоих.

Тэцу на полу подтянул колено к животу. Губы у него были серые.
"""


class ProseLintTest(unittest.TestCase):
    def test_real_failing_scene_is_flagged_by_family(self):
        report = prose_lint.lint(BAD)
        self.assertEqual(report["verdict"], "переписать")
        for rule in ("deference", "explain", "caveat", "absent"):
            self.assertIn(rule, report["by_rule"], rule)
        self.assertGreaterEqual(report["by_rule"]["deference"], 3)
        self.assertIn("Без вашего согласия никто не войдёт.", [h["quote"] for h in report["hits"]])

    def test_direct_scene_is_clean(self):
        report = prose_lint.lint(GOOD)
        self.assertEqual(report["hits"], [])
        self.assertEqual(report["verdict"], "чисто")

    def test_inserted_documents_and_physical_borders_are_not_flagged(self):
        text = ("<box border>\nУведомление не является приказом. Без вашего согласия вход запрещён.\n</box>\n\n"
                "За её границей заключённое существо молчало. Пленница обследовала свои границы.")
        self.assertEqual(prose_lint.lint(text)["hits"], [])

    def test_narration_rules_do_not_fire_inside_dialogue(self):
        self.assertEqual(prose_lint.lint("— Таким образом, пакет старый. То есть вчерашний.")["hits"], [])
        self.assertTrue(prose_lint.lint("Таким образом, пакет оказался старым.")["hits"])

    def test_player_character_inner_life_is_flagged_but_perception_is_not(self):
        self.assertIn("pc_inner", prose_lint.lint("Шики почувствовал жалость к раненому.")["by_rule"])
        self.assertIn("pc_inner", prose_lint.lint("Шики решил впустить капитана.")["by_rule"])
        self.assertEqual(prose_lint.lint("Шики ощутил слабый восходящий ток чакры у шестого узла.")["hits"], [])

    def test_therapeutic_voice_is_flagged(self):
        report = prose_lint.lint("— Я понимаю, как тебе тяжело. Ты не один. Всё будет хорошо, решать тебе.")
        self.assertGreaterEqual(report["by_rule"]["therapy"], 3)
        self.assertIn("deference", report["by_rule"])

    def test_negative_parallelism_density(self):
        text = " ".join(["Он смотрел не на дверь, а на руки врача."] * 12 + ["Дождь шёл с ночи."] * 30)
        self.assertIn("neg_parallel", prose_lint.lint(text)["by_rule"])

    def test_render_lists_advice(self):
        rendered = prose_lint.render(prose_lint.lint(BAD))
        self.assertIn("[deference]", rendered)
        self.assertIn("добивается своего", rendered)


if __name__ == "__main__":
    unittest.main()
