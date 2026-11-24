"""Возраст от дня рождения по календарю игры (поправка игрока 24.11.2026, s12.text.059)."""
import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import canon

AGES = [
    {"who": "Наруто Узумаки", "names": "Наруто", "born": "1266-10-10", "source": "s12.text.059"},
    {"who": "Сакура Харуно", "names": "Сакур", "born": "1266-03-28", "source": "s12.text.059"},
    {"who": "Мугами Шики", "names": "Шики|Мугами", "born": "1263-03-26", "born_before": "1263-07-01",
     "source": "SHIKI.md; s01, s10.text.051, s12.text.059"},
    {"who": "Какаши Хатакэ", "names": "Какаши", "born": "1252-09-15", "source": "канон: Наруто + 14", "derived": True},
]


def _state():
    state = Path(tempfile.mkdtemp())
    (state / "memory/_curated").mkdir(parents=True)
    (state / canon.CURATED).write_text(json.dumps({"ages": AGES}, ensure_ascii=False), encoding="utf-8")
    return state


def _hot(at):
    return {"cursor": {"at": at}, "calendar": {"year_offset": 1277},
            "scene": {"present": ["shiki", "naruto", "sakura"]},
            "cast": {"naruto": {"name": "Наруто Узумаки"}, "sakura": {"name": "Сакура Харуно"}}}


class AgeTest(unittest.TestCase):
    def test_count_goes_on_with_the_calendar(self):
        state = _state()
        now = canon.ages_now(state, _hot("0010-03-25T12:04:40"))
        self.assertTrue(now["Наруто Узумаки"].startswith("20 (род. 10.10.1266; 21 — с 10.10.1287)"))
        self.assertTrue(now["Сакура Харуно"].startswith("20 "), "21 — только с 28.03")
        later = canon.ages_now(state, _hot("0010-03-29T08:00:00"))
        self.assertTrue(later["Сакура Харуно"].startswith("21 "))
        self.assertNotIn("Какаши Хатакэ", now, "не в сцене")

    def test_unknown_birthday_asks_the_player(self):
        state = _state()
        self.assertTrue(canon.ages_now(state, _hot("0010-03-25T00:00:00"))["Мугами Шики"].startswith("23 (род. между"))
        self.assertIn("спроси игрока", canon.ages_now(state, _hot("0010-04-10T00:00:00"))["Мугами Шики"])

    def test_wrong_age_next_to_the_name(self):
        state = _state()
        turn = {"id": "s12.text.059", "at": "0010-03-25T12:04:40",
                "gm": "Пятнадцатилетний Наруто шагнул вперёд.\n\nСакуре было двадцать лет. Какаши — тридцать пять лет."}
        notes = [n for n in canon.guard(state, turn) if n["text"].startswith("Возраст")]
        self.assertEqual(len(notes), 1)
        self.assertIn("Наруто Узумаки: 20", notes[0]["text"])
        self.assertIn("не 15", notes[0]["text"])

    def test_digest_lists_birthdays(self):
        state = _state()
        canon.digest(state)
        text = (state / canon.DIGEST).read_text(encoding="utf-8")
        self.assertIn("## Возраст — от дня рождения", text)
        self.assertIn("выведено из канона", text)


if __name__ == "__main__":
    unittest.main()
