import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import wiki


def hot_state(present=("kadziura",)):
    return {"cursor": {"id": "s09.text.002", "at": "0006-10-04T10:00:00", "date": "Среда", "time": "10:00",
                       "place": "Госпиталь"},
            "scene": {"present": list(present), "situation": "обход"},
            "cast": {}, "threads": {"s17": {"state": "лечение идёт"}}, "things": {},
            "shiki": {"look": "серебристые волосы"}, "pact": {"stage": 3, "label": "Истощённый силуэт"},
            "last": {"id": "s09.text.002", "gm": "Кадзиура кивнул."},
            "offscreen": {"clocks": {"audit": {"owner": "Кадзиура Сатору", "what": "проверка бумаг",
                                               "due": "0006-10-04T12:00:00", "signs": "папка",
                                               "if_unopposed": "найдёт подлог"}}},
            "_hides": {"kadziura": "прячет журнал доз"}, "_psyche": {}}


def turn(number, d, gm="Кадзиура кивнул."):
    return {"id": f"s09.text.{number:03}", "at": f"0006-10-04T10:{number:02}:00", "place": "Госпиталь",
            "player": "…", "gm": gm, "d": d}


class WikiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.source = Path(self.tmp.name) / "source"
        (self.source / "campaigns/shiki").mkdir(parents=True)
        (self.source / wiki.ROSTER).write_text(json.dumps({"cards": [
            {"id": "kabuto", "name": "Кабуто Якуши", "appearance": "очки", "role": "генин",
             "gm_only": "работает на Орочимару"}]}, ensure_ascii=False), encoding="utf-8")
        self.state.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, turns, hot=None):
        wiki.record_deltas(self.state, turns)
        return wiki.write(self.state, hot or hot_state(), source_root=self.source, new_turns=turns)

    def page(self, rel):
        return (self.state / wiki.DIR / rel).read_text(encoding="utf-8")

    def test_new_character_gets_page_with_permanent_facts_and_index_line(self):
        self.build([turn(1, {"cast": {"kadziura": {
            "name": "Кадзиура Сатору", "look": "около пятидесяти, седые виски",
            "facts": {"возраст": "около 50"}, "role": "старший врач реанимации",
            "body": "застёгнут не на ту пуговицу"}}})])
        page = self.page("npc/kadziura.md")
        self.assertIn("- внешность: около пятидесяти, седые виски [s09.text.001]", page)
        self.assertIn("- возраст: около 50 [s09.text.001]", page)
        self.assertIn("вид в последней сцене: застёгнут не на ту пуговицу", page)
        self.assertIn("впервые: s09.text.001", page)
        self.assertIn("`npc/kadziura` · в сцене", self.page("INDEX.md"))

    def test_current_look_never_overwrites_permanent_look(self):
        self.build([turn(1, {"cast": {"kadziura": {"name": "Кадзиура Сатору", "look": "седые виски"}}}),
                    turn(2, {"cast": {"kadziura": {"body": "пот на висках"}}})])
        page = self.page("npc/kadziura.md")
        self.assertIn("- внешность: седые виски [s09.text.001]", page)
        self.assertIn("вид в последней сцене: пот на висках [s09.text.002]", page)

    def test_changed_permanent_fact_is_kept_in_history_and_reported(self):
        first = turn(1, {"cast": {"r6": {"name": "Реципиент № 6", "facts": {"возраст": "около 16"}}}})
        self.build([first])
        report = self.build([turn(2, {"cast": {"r6": {"facts": {"возраст": "взрослый мужчина"}}}})])
        page = self.page("npc/r6.md")
        self.assertIn("- возраст: взрослый мужчина [s09.text.002]", page)
        self.assertIn("    - прежде: около 16 [s09.text.001]", page)
        notes = [n for n in report["checks"] if n["kind"] == "stable_change"]
        self.assertEqual(len(notes), 1)
        self.assertIn("Было «около 16»", notes[0]["text"])

    def test_unknown_speaker_is_reported_known_name_is_not(self):
        hot = hot_state()
        self.build([turn(1, {"cast": {"kadziura": {"name": "Кадзиура Сатору"}}})], hot)
        report = self.build([turn(2, {}, gm="**Кадзиура:** — Да.\n**Анестезиолог:** — Сатурация 93.")], hot)
        texts = [n["text"] for n in report["checks"] if n["kind"] == "new_character"]
        self.assertEqual(len(texts), 1)
        self.assertIn("«анестезиолог»", texts[0])

    def test_secrets_stay_in_gm_only_pages(self):
        self.build([turn(1, {"cast": {"kadziura": {"name": "Кадзиура Сатору", "hides": "подменил дозы"}}})])
        public = self.page("npc/kadziura.md")
        secret = self.page("npc/kadziura.GM_ONLY.md")
        self.assertNotIn("журнал доз", public)
        self.assertNotIn("подменил", public)
        self.assertIn("прячет журнал доз", secret)
        self.assertIn("`audit`", secret)
        self.assertIn("работает на Орочимару", self.page("npc/kabuto.GM_ONLY.md"))
        self.assertNotIn("Орочимару", self.page("npc/kabuto.md"))

    def test_all_knowledge_is_kept_beyond_page_limit(self):
        knows = [f"факт {i}" for i in range(wiki.KNOWS_ON_PAGE + 5)]
        self.build([turn(1, {"cast": {"kadziura": {"name": "Кадзиура Сатору", "knows+": knows}}})])
        full = self.page("npc/kadziura.knows.md")
        self.assertIn("- факт 0 [s09.text.001]", full)
        self.assertIn(f"факт {len(knows) - 1}", self.page("npc/kadziura.md"))
        self.assertNotIn("- факт 0 [", self.page("npc/kadziura.md"))

    def test_amended_turn_replaces_its_delta(self):
        self.build([turn(1, {"cast": {"kadziura": {"name": "Кадзиура Сатору", "look": "лысый"}}})])
        self.build([turn(1, {"cast": {"kadziura": {"name": "Кадзиура Сатору", "look": "седые виски"}}})])
        page = self.page("npc/kadziura.md")
        self.assertIn("- внешность: седые виски", page)
        self.assertNotIn("лысый", page)

    def test_roster_characters_from_early_sessions_have_pages(self):
        self.build([])
        page = self.page("npc/kabuto.md")
        self.assertIn("- внешность: очки", page)
        self.assertIn("Кабуто Якуши", self.page("INDEX.md"))


if __name__ == "__main__":
    unittest.main()
