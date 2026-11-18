import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import places, wiki_eval


class WikiEvalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "memory/npc").mkdir(parents=True)
        (self.root / "archive").mkdir()
        (self.root / "memory/INDEX.md").write_text(
            "# Карта\n- Уцуро · `npc/utsuro` · хранитель Нижнего госпиталя\n", encoding="utf-8")
        (self.root / "memory/npc/utsuro.md").write_text(
            "# Уцуро\n- внешность: глаза под чёрной повязкой\n## Поправки игрока\n"
            "- s04.text.025: Шики запечатал сущность, а не её пространство.\n", encoding="utf-8")
        (self.root / "archive/shiki-canon.jsonl").write_text(
            json.dumps({"turn": "s04.text.025", "rule": "Шики запечатал сущность, а не её пространство."},
                       ensure_ascii=False) + "\n" +
            json.dumps({"turn": "s05.text.001", "rule": "Поправка, которую никуда не поставили."},
                       ensure_ascii=False) + "\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def write_eval(self, *items):
        (self.root / wiki_eval.EVAL).write_text(
            "\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")

    def test_answer_is_found_through_index(self):
        self.write_eval({"q": "облик Уцуро", "who": "Уцуро", "expect": ["Чёрной повязкой"]},
                        {"q": "ложный", "who": "Уцуро", "expect": ["розовые волосы"]},
                        {"q": "нет страницы", "who": "Никто", "expect": ["x"]})
        report = wiki_eval.run(self.root)
        self.assertEqual((report["total"], report["passed"]), (5, 2))
        reasons = {f["q"]: f["why"] for f in report["failed"]}
        self.assertIn("розовые волосы", reasons["ложный"])
        self.assertEqual(reasons["нет страницы"], "страница не найдена по INDEX")
        self.assertEqual(reasons["Поправка игрока s05.text.001"], "поправка не стоит ни на одной странице")


class PlacesTest(unittest.TestCase):
    def test_slug_and_setting_excerpt(self):
        self.assertEqual(places.slug("Кусагакуре, Центральный госпиталь"), "kusagakure-tsentralnyy-gospital")
        gm = ("**Кадзиура:** — Входите.\n\n"
              "Он перевернул лист и долго смотрел на Шики, не говоря ни слова, будто ждал оправданий, "
              "которых не будет, и считал удары собственного сердца.\n\n"
              "Коридор госпиталя тянулся вдоль стены из старого кафеля; свет ламп ложился на пол холодными "
              "прямоугольниками, и в воздухе стоял запах хлорки.")
        text = places._description(gm, "Кусагакуре, Центральный госпиталь")
        self.assertTrue(text.startswith("Коридор госпиталя"))


if __name__ == "__main__":
    unittest.main()
