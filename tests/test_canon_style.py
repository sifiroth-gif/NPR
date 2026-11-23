import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import canon, hot, limits, style

ROOT = Path(__file__).resolve().parents[1]
CANON = [
    {"turn": "s09.text.068", "rule": "Левый глаз «Мумё» — гендзюцу через контакт взгляда."},
    {"turn": "s09.text.068", "rule": "Меч «Суми» 84 см, Смертный надрез."},
    {"turn": "s09.text.070", "rule": "Отменить «Мумё»: левый глаз — Магацухи, правый — Ёмоцу-Хэгуи."},
    {"turn": "s10.text.088", "rule": "Женщина от Минаги с надорванной мочкой — Тамура Рэйка."},
    {"turn": "s10.text.129", "rule": "Шики — нукенин Кусагакуре класса S, награда 49 000 000 рё."},
]
CURATED = {"consolidated": "s10.text.070",
           "forbid": [{"pattern": "Мумё|Кёкай", "why": "отменены s09.text.070"}],
           "identities": [{"a": "Тамура Рэйка", "b": "женщина от Минаги", "source": "s10.text.088"}]}


def _state():
    state = Path(tempfile.mkdtemp())
    (state / "archive").mkdir()
    (state / "memory/_curated").mkdir(parents=True)
    (state / canon.SOURCE).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in CANON), encoding="utf-8")
    (state / canon.CURATED).write_text(json.dumps(CURATED, ensure_ascii=False), encoding="utf-8")
    return state


class CanonTest(unittest.TestCase):
    def test_digest_marks_replaced_and_lists_fresh(self):
        state = _state()
        report = canon.digest(state)
        text = (state / canon.DIGEST).read_text(encoding="utf-8")
        self.assertEqual(report["superseded"], 1)
        self.assertIn("~~s09.text.068#1~~ заменено s09.text.070#1", text)
        self.assertIn("**Тамура Рэйка** = женщина от Минаги", text)
        self.assertIn("s10.text.129: Шики — нукенин", text)
        self.assertNotIn("s09.text.068: Меч", text, "до сведения — в правилах, не в своде")

    def test_scene_lines_by_present_and_techniques_without_repeats(self):
        state = _state()
        h = {"cursor": {"id": "s11.text.003", "place": "Куса"}, "scene": {"present": ["shiki", "tamura_reika"]},
             "cast": {"tamura_reika": {"name": "Тамура Рэйка"}}}
        turn = {"id": "s11.text.003", "player": "Складываю печати Хётона: ледяные зеркала."}
        got = canon.for_scene(state, h, [turn], ROOT)
        joined = " ".join(got["items"])
        self.assertIn("Тамура Рэйка = женщина от Минаги", joined)
        self.assertIn("Хётон: Ледяные зеркала", joined)
        h["cursor"]["id"] = "s11.text.004"
        again = canon.for_scene(state, h, [dict(turn, id="s11.text.004", player="Иду.")], ROOT)
        self.assertFalse(again and any("Минаги" in i for i in again["items"]), "та же строка — не раньше REPEAT")

    def test_guard_and_review(self):
        state = _state()
        notes = canon.guard(state, {"id": "s11.text.005", "gm": "Шики смотрит — «Мумё» срабатывает."})
        self.assertIn("отменены s09.text.070", notes[0]["text"])
        self.assertEqual(canon.guard(state, {"id": "s11.text.006", "kind": "ooc", "gm": "Мумё?"}), [])
        self.assertIn("перечитай memory/CANON.md", canon.review(state, {"cursor": {"id": "s11.text.020"}}))
        self.assertIsNone(canon.review(state, {"cursor": {"id": "s11.text.021"}}))


class LookTest(unittest.TestCase):
    LOOKS = [{"who": "Мугами Шики", "names": "Шики|Мугами", "nouns": "подрост\\w*|юнош\\w*",
              "wrong": "темноволос\\w*|(тёмн|темн|чёрн|черн)\\w* волос", "trait": "серебристо-белые волосы с 1278",
              "source": "s01.text.093"},
             {"who": "Гаара", "names": "Гаар", "wrong": "рыж\\w*", "trait": "красные волосы", "source": "s11.text.053"}]

    def _state(self):
        state = _state()
        (state / canon.CURATED).write_text(json.dumps(dict(CURATED, looks=self.LOOKS), ensure_ascii=False),
                                           encoding="utf-8")
        return state

    def test_wrong_trait_is_caught_in_old_photo_too(self):
        # s11.text.077: старое фото «темноволосого подростка» Шики, хотя волосы белые с s01.text.093.
        state = self._state()
        gm = ("Какаши развернул книгу к Шики.\n\nНа пожелтевшем листе находилась старая фотография.\n\n"
              "Пятнадцатилетний темноволосый подросток с протектором Кусагакуре.")
        notes = canon.guard(state, {"id": "s11.text.077", "gm": gm})
        self.assertEqual(len(notes), 1)
        self.assertIn("серебристо-белые", notes[0]["text"])
        self.assertIn("фото", notes[0]["text"])
        self.assertEqual(canon.guard(state, {"id": "s11.text.078", "gm": "Гаара поднял руку. Рыжий закат."}), [],
                         "другое предложение — не о нём")
        self.assertTrue(canon.guard(state, {"id": "s11.text.079", "gm": "Рыжие волосы Гаары слиплись."}))
        self.assertEqual(canon.guard(state, {"id": "s11.text.080", "gm": "У Шики за спиной стояла темноволосая женщина."}),
                         [], "темноволосая женщина рядом с Шики — не он")
        # near: однозначная черта — хватит имени рядом (s11.text.076: «его рыжих волос» после поправки s11.text.053).
        looks = [dict(self.LOOKS[1], wrong="рыж\\w* волос\\w*", near=True)]
        (state / canon.CURATED).write_text(json.dumps(dict(CURATED, looks=looks), ensure_ascii=False), encoding="utf-8")
        self.assertTrue(canon.guard(state, {"id": "s11.text.081", "gm": "Гаара лежал на камне.\n\nСолнце касалось его рыжих волос."}))

    def test_digest_and_scene_line(self):
        state = self._state()
        canon.digest(state)
        text = (state / canon.DIGEST).read_text(encoding="utf-8")
        self.assertIn("## Облик — постоянные черты", text)
        h = {"cursor": {"id": "s11.text.003"}, "scene": {"present": ["shiki", "gaara"]}, "cast": {"gaara": {"name": "Гаара"}}}
        got = canon.for_scene(state, h, [], ROOT)
        self.assertEqual(sum(i.startswith("Облик") for i in got["items"]), 1, "одна строка облика за ход")


class ChatTest(unittest.TestCase):
    def test_long_chat_is_told(self):
        h = {"cursor": {"id": "s11.text.046"}}
        hot._count_chat(h, [{"id": f"s11.text.{n:03}"} for n in range(1, 47)])
        self.assertEqual(h["fold"]["chat"]["turns"], 46)
        state = Path(tempfile.mkdtemp())
        report = limits.check(state, h, hot_bytes=1, secret_bytes=1, engine=Path(tempfile.mkdtemp()))
        self.assertIn("chat", [i["key"] for i in report["items"]])
        hot._count_chat(h, [{"id": "s11.text.047", "chat": "new"}])
        self.assertEqual(h["fold"]["chat"]["turns"], 1)


SCENE = ("<text color=\"secondary\" size=\"xs\">8 марта</text>\n\n### Куса\n\n"
         + "\n\n".join(["Дождь шёл над крышами Кусы, и вода стекала по желобам в чёрные бочки у стен, где спали "
                        "собаки и стояли пустые корзины торговцев. Где-то хлопнула ставня." for _ in range(6)])
         + "\n\n**Сэйна:** — Ты вернулся.\n\n**Мугами Шики:** — Ненадолго.\n\n**Ход Шики.**")


class StyleTest(unittest.TestCase):
    def test_tag_sample_and_drift(self):
        state = Path(tempfile.mkdtemp())
        (state / "archive").mkdir()
        rows = [{"id": "s11.text.001", "gm": SCENE}, {"id": "s11.text.002", "gm": SCENE.replace("Сэйна", "Нори")}]
        (state / "archive/session-011-turns.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        added = style.tag(state, [{"id": "s11.text.002", "player": "#эталон, иду дальше"},
                                  {"id": "s11.text.003", "player": "#эталон s11.text.002"}])
        self.assertEqual(added, ["s11.text.001", "s11.text.002"])
        self.assertNotIn("<text", style.load(state)[0]["text"])
        h = {"cursor": {"id": "s11.text.003", "place": "Куса. Улица"}, "scene": {"situation": "дождь в Кусе"}}
        got = style.sample(state, h, [{"id": "s11.text.003", "player": "Иду под дождём"}])
        self.assertIn("Дождь", got["sample"])
        style.seed_baseline(state, ["s11.text.001", "s11.text.002"], "тест")
        choppy = "\n\n".join(["Тихо.", "Он ждёт.", "Капля.", "Ещё одна.", "Шорох.", "Никого.", "Снова тихо."] * 2)
        notes = style.drift(state, {"id": "s11.text.004", "gm": choppy})
        self.assertIn("Манера ушла", notes[0]["text"])
        self.assertEqual(style.drift(state, {"id": "s11.text.005", "gm": SCENE}), [])


if __name__ == "__main__":
    unittest.main()
