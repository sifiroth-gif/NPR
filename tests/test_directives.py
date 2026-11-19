import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import claims, directives, hot, lore, merge, nextpack, presence
from tests import test_hot
from tests.test_hot import turn


class DirectivesTest(unittest.TestCase):
    def test_extracts_words_to_the_master_not_the_game(self):
        player = ("Мастеру: усиль влияние Шики обязательно. Это супер как важно мне.\n"
                  "По игре: #убираю влияние на контейнер# подходите ближе\n"
                  "Шики сложил печати. Печати есть, нужны конкретные. Перепиши сцену. Это важно")
        got = directives.extract(player)
        self.assertEqual(got[0], "усиль влияние Шики обязательно. Это супер как важно мне.")
        self.assertIn("Перепиши сцену", got[1])
        self.assertEqual(directives.extract("#мастеру, прямо нужно и достаточно ускорено#"),
                         ["прямо нужно и достаточно ускорено"])
        self.assertEqual(directives.extract("#одеваюсь в полное снаряжение и к нему#"), [])

    def test_unpaired_hash_keeps_only_the_request(self):
        player = ("#направляю хьетон чакру# Уяснили. #пристально глядя на мариоку# а ещё я могу лечить "
                  "#прошу тебя не потеряй напряжение и эмоции#")
        self.assertEqual(directives.extract(player), ["прошу тебя не потеряй напряжение и эмоции"])

    def test_capture_and_view_with_standing_list(self):
        root = Path(tempfile.mkdtemp())
        (root / "memory/_curated").mkdir(parents=True)
        (root / directives.SEED).write_text(json.dumps({"standing": [
            {"text": "Влияние Шики — главное", "since": "s08.text.090", "from": "усиль влияние Шики обязательно."}]},
            ensure_ascii=False), encoding="utf-8")
        h = {"cursor": {"id": "s08.text.091"}}
        directives.capture(h, {"id": "s08.text.090", "player": "Мастеру: усиль влияние Шики обязательно."})
        directives.capture(h, {"id": "s08.text.091", "player": "#мастеру: документы короче#"})
        directives.capture(h, {"id": "s08.text.091", "player": "#мастеру: документы короче#"})
        view = directives.view(root, h)
        self.assertEqual(view["standing"], ["Влияние Шики — главное (s08.text.090)"])
        self.assertEqual(view["recent"], ["документы короче (s08.text.091)"])
        h["cursor"]["id"] = "s08.text.120"
        self.assertNotIn("recent", directives.view(root, h))


class PresenceKeyTest(unittest.TestCase):
    def test_nameless_card_is_found_by_its_key(self):
        names = {"morioka_hideaki": presence._entry([]), "morioka_escort_leader": presence._entry([]),
                 "takagi_ren": presence._entry(["Такаги Рэн"])}
        self.assertEqual(presence.resolve("Мориока Хидэаки", names), "morioka_hideaki")
        self.assertIsNone(presence.resolve("Мориока", names), "две карточки с этим словом — не угадывать")

    def test_stub_card_keeps_the_case_of_the_label(self):
        h = {}
        names = {}
        self.assertEqual(presence.auto_cards(h, {"id": "a"}, ["Служащая архива"], names), {})
        made = presence.auto_cards(h, {"id": "b"}, ["Служащая архива"], names)
        self.assertEqual(list(made.values())[0]["name"], "Служащая архива")


class ClaimsTest(unittest.TestCase):
    def test_memory_of_a_place_shiki_never_visited_is_flagged(self):
        visited = {"кусагакуре, центральное управление, кабинет инспектора аракавы"}
        bad = {"id": "s08.text.090", "gm": "Он вспомнил Шики в доме Аракавы, ночные отчёты."}
        good = {"id": "s08.text.090", "gm": "Он вспомнил Шики в кабинете Аракавы."}
        here = {"id": "s08.text.090", "gm": "Шики находится в доме Мориоки."}
        self.assertEqual(len(claims.review(bad, visited)), 1)
        self.assertIn("кабинет инспектора аракавы", claims.review(bad, visited)[0]["text"])
        self.assertEqual(claims.review(good, visited), [])
        self.assertEqual(claims.review(here, visited), [])


class MergeFoldTest(unittest.TestCase):
    setUp, tearDown = test_hot.HotFilesTest.setUp, test_hot.HotFilesTest.tearDown
    git, commit, put = test_hot.HotFilesTest.git, test_hot.HotFilesTest.commit, test_hot.HotFilesTest.put

    def test_alias_folds_into_the_main_key_and_redirects_new_writes(self):
        self.put(turn(45, "0006-10-01T07:14:00", d={"scene": {"present": ["shiki", "nori", "nori_twin"]},
                                                     "cast": {"nori_twin": {"name": "Хаясэ нори", "state": "дубль"}}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        (self.root / "memory/_curated").mkdir(parents=True, exist_ok=True)
        (self.root / merge.SEED).write_text(json.dumps({"aliases": {"nori_twin": "nori"}}), encoding="utf-8")
        self.put(turn(46, "0006-10-01T07:15:00", d={"scene": {}, "cast": {"nori_twin": {"state": "после слияния"}}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        state = json.loads((self.root / hot.HOT_PATH).read_text(encoding="utf-8"))
        self.assertNotIn("nori_twin", state["cast"])
        self.assertNotIn("nori_twin", state["scene"]["present"])
        self.assertEqual(state["cast"]["nori"]["state"], "после слияния")
        self.assertFalse((self.root / "recovery/cast/nori_twin.json").exists())
        self.assertTrue(hot.check(self.root)["ok"])


class FirstSightTest(unittest.TestCase):
    setUp, tearDown = test_hot.HotFilesTest.setUp, test_hot.HotFilesTest.tearDown
    git, commit, put = test_hot.HotFilesTest.git, test_hot.HotFilesTest.commit, test_hot.HotFilesTest.put

    def test_first_sight_of_rare_lore_becomes_a_lasting_duty(self):
        (self.root / "memory/_curated").mkdir(parents=True, exist_ok=True)
        entry = {"id": "hyoton", "title": "Хётон", "terms": ["хётон", "чакру льда"], "wield": ["shiki"],
                 "world": "В Траве не видели.", "first_sight": "чужая стихия ломает картину мира"}
        (self.root / lore.SEED).write_text(json.dumps({"entries": [entry]}, ensure_ascii=False), encoding="utf-8")
        self.put(turn(45, "0006-10-01T07:14:00", player="#направляю чакру льда#",
                      gm="**Хаясэ Нори:** — Это… лёд?", d={"scene": {"present": ["shiki", "nori"]}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        pack = json.loads((self.root / nextpack.PATH).read_text(encoding="utf-8"))
        self.assertTrue(pack["must"][0].startswith("След увиденного: Хаясэ Нори"))
        self.assertIn("чужая стихия ломает картину мира", pack["must"][0])


if __name__ == "__main__":
    unittest.main()


class KeeperReplaceTest(unittest.TestCase):
    def test_replace_applies_once_per_version_then_gm_wins(self):
        root = Path(tempfile.mkdtemp())
        (root / "memory/_curated").mkdir(parents=True)
        path = root / hot.KEEPER_FILL
        path.write_text(json.dumps({"replace": {"shiki": {"presence": "новый канон"}}}, ensure_ascii=False),
                        encoding="utf-8")
        h = {"shiki": {"presence": "старое"}}
        hot._keeper_fill(root, h)
        self.assertEqual(h["shiki"]["presence"], "новый канон")
        h["shiki"]["presence"] = "ведущий уточнил ходом"
        hot._keeper_fill(root, h)
        self.assertEqual(h["shiki"]["presence"], "ведущий уточнил ходом")


class KeeperDeltaTest(unittest.TestCase):
    def test_closes_played_events_and_threads_once(self):
        from rpg_engine import keeper, plot
        root = Path(tempfile.mkdtemp())
        (root / "memory/_curated").mkdir(parents=True)
        h = {"cursor": {"id": "s08.text.090", "at": "0006-10-04T06:43:00"},
             "threads": {"old": {"state": "давно"}, "live": {"state": "идёт"}},
             "_plot": {"events": {"side_a": {"id": "side_a", "kind": "side", "status": "queued", "scenes": 0},
                                  "ch": {"id": "ch", "kind": "chapter", "status": "active", "scenes": 7}}}}
        (root / keeper.SEED).write_text(json.dumps({
            "threads": {"old": {"closed": "сыграно"}, "live": {"state": "обновлено"}},
            "plot": {"side_a": {"closed": "исход"}, "ch": {"notes": ["заметка"]},
                     "side_b": {"kind": "side", "question": "?", "endings": ["да", "нет"]}}}, ensure_ascii=False),
            encoding="utf-8")
        self.assertTrue(keeper.apply(root, h))
        self.assertNotIn("old", h["threads"])
        self.assertEqual(h["threads"]["live"]["state"], "обновлено")
        self.assertEqual(h["_plot"]["closed"][-1]["outcome"], "исход")
        self.assertEqual(h["_plot"]["events"]["ch"]["scenes"], 7, "сцену не засчитывает")
        self.assertEqual(h["_plot"]["events"]["side_b"]["status"], "queued")
        self.assertFalse(keeper.apply(root, h), "второй раз та же версия не применяется")


class WikiFactsListTest(unittest.TestCase):
    def test_list_facts_render_and_bad_patch_is_skipped(self):
        from rpg_engine import wiki
        store, changes = {}, []
        wiki._apply_cast(store, "porter", {"name": "Носильщик", "facts": ["ночной служащий", "ранен"]},
                         "s08.text.082", changes)
        self.assertIn("ночной служащий; ранен", store["porter"]["stable"]["сведения"][-1]["v"])
        wiki._apply_cast(store, "porter", {"facts": ["другое"]}, "s08.text.083", changes)
        self.assertEqual(changes, [], "смена списка сведений — не смена постоянной черты")
