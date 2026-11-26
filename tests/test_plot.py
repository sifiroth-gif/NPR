import json
import unittest

from rpg_engine import hot, plot
from tests import test_hot
from tests.test_hot import turn


def side(**extra):
    event = {"kind": "side", "parent": "ch1", "title": "Кража из В-7", "question": "Кто вынес контейнер?",
             "endings": ["вор найден", "след потерян", "ничего не вышло"], "limit": {"scenes": 3}}
    event.update(extra)
    return event


def texts(notes):
    return [n["text"] for n in notes]


class PlotTest(unittest.TestCase):
    def setUp(self):
        self.hot = {"session": 4, "cursor": {"id": "s04.text.044", "at": "0006-10-01T07:10:00"}}
        plot.apply(self.hot, {"main": {"kind": "main", "question": "…", "endings": ["…", "…"]},
                              "ch1": {"kind": "chapter", "question": "…", "endings": ["…", "…"],
                                      "limit": {"scenes": 50}}}, "s04.text.044", "0006-10-01T07:10:00")

    def play(self, number, patch, threads=None):
        turn_id = f"s04.text.{number:03}"
        self.hot["cursor"] = {"id": turn_id, "at": f"0006-10-01T{7 + number % 10:02}:00:00"}
        plot.apply(self.hot, patch, turn_id, self.hot["cursor"]["at"])
        plot.touch_threads(self.hot, threads or {}, turn_id)
        return plot.review(self.hot, [{"id": turn_id}])

    def test_side_event_counts_scenes_and_demands_ending_at_limit(self):
        self.assertEqual(self.play(45, {"theft": side()}), [])
        self.play(46, {"theft": {"touch": True}})
        self.assertEqual(self.hot["_plot"]["events"]["theft"]["scenes"], 2)
        notes = texts(self.play(47, {"theft": {"touch": True}}))
        self.assertEqual(len(notes), 1)
        self.assertIn("на пределе (3 из 3 сцен)", notes[0])
        self.assertIn("вор найден / след потерян / ничего не вышло", notes[0])
        self.assertIn("Новых преград не вводи", notes[0])
        self.play(48, {"theft": {"closed": "След потерян в канализации."}})
        self.assertNotIn("theft", self.hot["_plot"]["events"])
        closed = self.hot["_plot"]["closed"][-1]
        self.assertEqual((closed["id"], closed["outcome"], closed["scenes"]),
                         ("theft", "След потерян в канализации.", 3))

    def test_one_turn_counts_once_even_if_written_twice(self):
        self.play(45, {"theft": side()})
        plot.apply(self.hot, {"theft": {"touch": True}}, "s04.text.045", "0006-10-01T07:10:00")
        self.assertEqual(self.hot["_plot"]["events"]["theft"]["scenes"], 1)

    def test_event_without_end_is_flagged_but_random_scene_is_not(self):
        notes = texts(self.play(45, {"theft": {"kind": "side", "title": "Кража"},
                                     "street": {"kind": "scene", "title": "торговец на мосту"}}))
        self.assertEqual(len(notes), 1)
        self.assertIn("«Кража» (`theft`) без конца", notes[0])
        self.assertIn("question", notes[0])
        self.assertIn("limit", notes[0])
        self.assertEqual(self.hot["_plot"]["events"]["street"]["limit"], {"scenes": 2})

    def test_random_scene_ends_after_two_exchanges(self):
        self.play(45, {"street": {"kind": "scene", "title": "торговец на мосту"}})
        notes = texts(self.play(46, {"street": {"touch": True}}))
        self.assertIn("на пределе (2 из 2 сцен)", notes[0])

    def test_time_limit_counts_too(self):
        self.play(45, {"theft": side(limit={"until": "0006-10-01T20:00:00"})})
        self.assertEqual(self.play(46, {}), [])
        self.hot["cursor"]["at"] = "0006-10-01T20:30:00"
        notes = texts(plot.review(self.hot, [{"id": "s04.text.047"}]))
        self.assertIn("срок 0006-10-01T20:00:00 наступил", notes[0])

    def test_iso_deadline_shorthand_is_normalized(self):
        deadline = "0006-10-01T20:00:00"
        self.assertEqual(self.play(45, {"theft": side(limit=deadline)}), [])
        self.assertEqual(self.hot["_plot"]["events"]["theft"]["limit"], {"until": deadline})
        self.hot["cursor"]["at"] = "0006-10-01T20:30:00"
        self.assertTrue(any("срок " + deadline in n for n in texts(plot.review(self.hot, []))))
        self.assertTrue(plot.brief(self.hot)["active"][1]["at_limit"])

    def test_legacy_and_invalid_limits_do_not_crash(self):
        self.play(45, {"theft": side()})
        event = self.hot["_plot"]["events"]["theft"]
        event["limit"] = "0006-10-01T12:00:00"
        self.assertEqual(plot.brief(self.hot)["active"][1]["limit"], {"until": event["limit"]})
        event["limit"] = {"scenes": "bad", "until": "bad"}
        self.assertTrue(any("добавь в d.plot limit" in n for n in texts(plot.review(self.hot, [{"id": "s04.text.045"}]))))
        self.assertNotIn("limit", plot.brief(self.hot)["active"][1])

    def test_linked_thread_change_counts_as_scene(self):
        self.play(45, {"theft": side(threads=["v7"])})
        self.play(46, {}, threads={"v7": {"state": "запор сорван"}})
        self.play(47, {}, threads={"order": {"state": "другое"}})
        self.assertEqual(self.hot["_plot"]["events"]["theft"]["scenes"], 2)

    def test_too_many_side_events_and_queue(self):
        patch = {f"s{i}": {"kind": "scene", "title": f"сцена {i}"} for i in range(6)}
        patch["later"] = side(status="queued", trigger="когда Нори уйдёт")
        notes = texts(self.play(45, patch))
        self.assertTrue(any("больше 5" in n for n in notes))
        self.assertEqual(self.hot["_plot"]["events"]["later"]["scenes"], 0)
        self.play(46, {"later": {"status": "active"}})
        later = self.hot["_plot"]["events"]["later"]
        self.assertEqual((later["scenes"], later["opened"]), (1, "s04.text.046"))

    def test_main_story_needs_an_open_chapter(self):
        self.play(45, {"main": {"kind": "main", "title": "Нулевой Геном", "question": "Кто охотится за Шики?",
                                "endings": ["…", "…"]},
                       "ch1": {"kind": "chapter", "parent": "main", "question": "Что в В-7?",
                               "endings": ["…", "…"], "limit": {"scenes": 20}},
                       "ch2": {"kind": "chapter", "parent": "main", "status": "queued", "title": "Экзамен"}})
        notes = texts(self.play(46, {"ch1": {"closed": "В-7 вскрыт."}}))
        self.assertEqual(len(notes), 1)
        self.assertIn("нет активной главы", notes[0])
        self.assertIn("«Экзамен» (`ch2`)", notes[0])
        self.assertEqual(self.hot["_plot"]["events"]["main"].get("scenes"), 0)

    def test_missing_main_story_and_stale_threads_are_flagged(self):
        self.hot = {"session": 8, "cursor": {"id": "s08.text.003", "at": "0006-10-03T03:00:00"},
                    "threads": {"old": {"state": "…", "turn": "s05.text.010"},
                                "fresh": {"state": "…", "turn": "s07.text.002"},
                                "goal": {"state": "цель игрока"},
                                "linked": {"state": "…", "turn": "s04.text.001"}}}
        plot.apply(self.hot, {"x": side(threads=["linked"])}, "s08.text.003", "0006-10-03T03:00:00")
        notes = texts(plot.review(self.hot, [{"id": "s08.text.003"}]))
        self.assertTrue(any("Сквозной сюжет не задан" in n for n in notes))
        stale = [n for n in notes if n.startswith("Линии без движения")]
        self.assertEqual(len(stale), 1)
        self.assertIn(": old.", stale[0])

    def test_brief_and_page_show_what_is_running(self):
        self.play(45, {"theft": side(), "street": {"kind": "scene", "title": "торговец"}})
        data = plot.brief(self.hot)
        self.assertEqual([e["id"] for e in data["active"]], ["ch1", "theft", "street"])
        self.assertEqual(data["active"][1]["scenes_left"], 2)
        self.assertIn("1–2 обмена", data["rules"])
        page = plot.page(self.hot)
        self.assertIn("концовка: след потерян", page)
        self.assertIn("ТОЛЬКО ВЕДУЩИЙ", page)


class PlotFoldTest(unittest.TestCase):
    setUp, tearDown = test_hot.HotFilesTest.setUp, test_hot.HotFilesTest.tearDown
    git, commit, put = test_hot.HotFilesTest.git, test_hot.HotFilesTest.commit, test_hot.HotFilesTest.put

    def test_plot_lives_only_in_gm_only_and_reaches_brief_checks_and_wiki(self):
        self.put(turn(45, "0006-10-01T07:14:00",
                      d={"plot": {"theft": side(limit={"scenes": 1}, endings=["вор найден", "ничего"])}}))
        report = hot.fold(self.root)
        self.assertEqual(report["errors"], [])
        public = (self.root / hot.HOT_PATH).read_text(encoding="utf-8")
        self.assertNotIn("вор найден", public)
        self.assertNotIn("_plot", public)
        secret = json.loads((self.root / hot.SECRET_PATH).read_text(encoding="utf-8"))
        self.assertEqual(secret["plot"]["events"]["theft"]["scenes"], 1)
        checks = json.loads((self.root / hot.CHECKS_PATH).read_text(encoding="utf-8"))["checks"]
        self.assertTrue(any(c["kind"] == "plot" and "на пределе" in c["text"] for c in checks))
        brief = json.loads((self.root / "recovery/GM_BRIEF.json").read_text(encoding="utf-8"))
        self.assertEqual(brief["plot"]["active"][0]["id"], "theft")
        self.assertIn("вор найден", (self.root / "memory/PLOT.GM_ONLY.md").read_text(encoding="utf-8"))
        scene = (self.root / "memory/SCENE.md").read_text(encoding="utf-8")
        self.assertIn("События: идут 1", scene)
        self.assertNotIn("вор найден", scene)
        self.assertTrue(hot.check(self.root)["ok"], hot.check(self.root))

    def test_fold_replays_iso_deadline_without_rewriting_turn(self):
        deadline = "0006-10-01T20:00:00"
        self.put(turn(45, "0006-10-01T07:14:00",
                      d={"plot": {"theft": side(limit=deadline)}}))
        self.put(turn(46, "0006-10-01T07:20:00"))
        report = hot.fold(self.root)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["cursor"], "s04.text.046")
        events = json.loads((self.root / hot.SECRET_PATH).read_text(encoding="utf-8"))["plot"]["events"]
        self.assertEqual(events["theft"]["limit"], {"until": deadline})
        self.assertTrue(hot.check(self.root)["ok"], hot.check(self.root))

    def test_plot_notes_are_replaced_not_piled_up(self):
        self.put(turn(45, "0006-10-01T07:14:00", d={"plot": {"theft": {"kind": "side"}}}))
        hot.fold(self.root)
        self.commit("fold")
        self.put(turn(46, "0006-10-01T07:20:00", d={"plot": {"theft": {"touch": True}}}))
        hot.fold(self.root)
        checks = json.loads((self.root / hot.CHECKS_PATH).read_text(encoding="utf-8"))["checks"]
        plot_notes = [c for c in checks if c["kind"] == "plot"]
        self.assertEqual({c["turn"] for c in plot_notes}, {"s04.text.046"})
        self.assertEqual(sum("без конца" in c["text"] for c in plot_notes), 1)

    def test_amend_rolls_back_plot_counts(self):
        self.put(turn(45, "0006-10-01T07:14:00", d={"plot": {"theft": side()}}))
        hot.fold(self.root)
        self.commit("fold")
        self.put(turn(45, "0006-10-01T07:14:00", amend=True, review={"predecessor": "s04.text.044"},
                      d={"plot": {"theft": side(), "street": {"kind": "scene"}}}))
        hot.fold(self.root)
        events = json.loads((self.root / hot.SECRET_PATH).read_text(encoding="utf-8"))["plot"]["events"]
        self.assertEqual((events["theft"]["scenes"], events["street"]["scenes"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
