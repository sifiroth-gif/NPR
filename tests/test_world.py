import json
import unittest

from rpg_engine import hot, inquiry, nextpack, npc_memory
from tests import test_hot
from tests.test_hot import turn


def profile():
    return {"intent": {"report": {"goal": "доложить главе", "next": "написать рапорт", "due": "0006-10-01T09:00:00",
                                  "flaw": "торопится", "if_unopposed": "глава знает о Шики", "status": "committed"}}}


class IntentStatusTest(unittest.TestCase):
    def apply(self, prof, patch):
        h = {"_checks": []}
        npc_memory._intents(h, prof, patch, "s08.text.063", "0006-10-03T23:47:00")
        return [c["text"] for c in h["_checks"]]

    def test_done_closes_the_plan_instead_of_reviving_it(self):
        prof = profile()
        notes = self.apply(prof, {"report": {"status": "done", "outcome": "рапорт лёг на стол главы"}})
        self.assertEqual(prof["intent"], {})
        self.assertEqual(prof["intent_done"][-1]["status"], "fulfilled")
        self.assertEqual(prof["intent_done"][-1]["outcome"], "рапорт лёг на стол главы")
        self.assertEqual(notes, [])

    def test_in_progress_stays_active_and_partial_success_closes(self):
        prof = profile()
        self.apply(prof, {"report": {"status": "in_progress"}})
        self.assertEqual(prof["intent"]["report"]["status"], "committed")
        notes = self.apply(prof, {"report": {"status": "partial_success"}})
        self.assertEqual(prof["intent_done"][-1]["status"], "partial")
        self.assertIn("наполовину: глава знает о Шики", prof["intent_done"][-1]["outcome"])
        self.assertTrue(any("закрыт без outcome" in n for n in notes))

    def test_new_next_on_close_becomes_the_next_plan(self):
        prof = profile()
        self.apply(prof, {"report": {"status": "done", "outcome": "доложил",
                                     "next": "выставить пост у дома Шики"}})
        self.assertEqual(prof["intent_done"][-1]["next"], "написать рапорт")
        successor = prof["intent"]["report_2"]
        self.assertEqual(successor["next"], "выставить пост у дома Шики")
        self.assertEqual(successor["status"], "committed")
        self.assertEqual(successor["due"], "0006-10-04T11:47:00")
        self.assertEqual(successor["after"], "report")

    def test_waiting_is_not_a_step(self):
        prof = profile()
        notes = self.apply(prof, {"report": {"status": "done", "outcome": "доложил",
                                             "next": "Дождаться ответа главы"}})
        self.assertTrue(any("ожидание, а не шаг" in n for n in notes))

    def test_unknown_status_is_explained(self):
        prof = profile()
        notes = self.apply(prof, {"report": {"status": "maybe"}})
        self.assertEqual(prof["intent"]["report"]["status"], "committed")
        self.assertTrue(any("fulfilled, partial, failed, abandoned" in n for n in notes))


class InquiryTest(unittest.TestCase):
    def test_two_research_turns_ending_in_a_missing_sheet_are_a_stall(self):
        h = {}
        filler = "Шики читал протоколы. " * 40
        first = {"id": "s08.text.060", "player": "#хочу просмотреть архив сам#",
                 "gm": filler + "Рядом стояла ссылка: двадцатый лист изъят и передан в другое ведомство."}
        second = {"id": "s08.text.061", "player": "#дотошное изучение документов#",
                  "gm": filler + "Перед Шики лежала карточка с номером приложения недостающего документа."}
        self.assertEqual(inquiry.track(h, first), [])
        notes = inquiry.track(h, second)
        self.assertEqual(len(notes), 1)
        self.assertIn("s08.text.060, s08.text.061", notes[0]["text"])
        self.assertTrue(inquiry.must(h))
        full = {"id": "s08.text.062", "player": "#изучаю дальше#", "gm": filler + "Он выписал всё."}
        self.assertEqual(inquiry.track(h, full), [])
        self.assertEqual(inquiry.must(h), [])

    def test_ordinary_scene_does_not_count(self):
        h = {}
        talk = {"id": "s08.text.050", "player": "Здравствуйте", "gm": "Лист изъят. " * 30}
        self.assertEqual(inquiry.track(h, talk), [])
        self.assertEqual(inquiry.deferrals("Медик отметил отсутствие рефлекса."), [])


class WorldFoldTest(unittest.TestCase):
    setUp, tearDown = test_hot.HotFilesTest.setUp, test_hot.HotFilesTest.tearDown
    git, commit, put = test_hot.HotFilesTest.git, test_hot.HotFilesTest.commit, test_hot.HotFilesTest.put

    def test_fold_advances_absent_npc_plan_and_reports_it_in_next(self):
        self.put(turn(45, "0006-10-01T07:14:00", d={"scene": {"present": ["shiki"]}, "cast": {
            "nori": {"name": "Хаясэ Нори", "intent": {"registry": {
                "goal": "найти след выемки журнала", "next": "поднять реестр", "due": "0006-10-01T08:00:00",
                "flaw": "доверяет архивариусу", "if_unopposed": "реестр у Нори", "against_shiki": "улика против Шики"}}}}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        pack = json.loads((self.root / nextpack.PATH).read_text(encoding="utf-8"))
        self.assertNotIn("offscreen", pack, "срок не наступил")
        self.put(turn(46, "0006-10-01T09:30:00", d={"scene": {}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        pack = json.loads((self.root / nextpack.PATH).read_text(encoding="utf-8"))
        self.assertIn("Хаясэ Нори", pack["offscreen"]["events"][0])
        self.assertIn("Против Шики: улика против Шики", pack["offscreen"]["events"][0])
        self.assertTrue(any("Хаясэ Нори" in m and "нового нет" in m for m in pack["must"]))
        public = (self.root / hot.HOT_PATH).read_text(encoding="utf-8")
        self.assertNotIn("реестр у Нори", public, "исход за кадром — только в GM_ONLY")
        secret = json.loads((self.root / "recovery/GM_ONLY.json").read_text(encoding="utf-8"))
        self.assertEqual(secret["psyche"]["nori"]["intent_done"][-1]["by"], "world")


if __name__ == "__main__":
    unittest.main()
