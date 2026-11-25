import copy
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

    def test_generic_masked_label_is_scoped_not_globally_merged(self):
        names = {"old_mask": presence._entry(["Человек в маске"]),
                 "quay": presence._entry(["Сэйдзи Минасэ", "Человек в маске"]),
                 "rei": presence._entry(["Минасэ Рэи"])}
        self.assertIsNone(presence.resolve("Человек в маске", names))
        self.assertEqual(presence.resolve("Человек в маске", names, scope={"quay"}), "quay")
        self.assertEqual(presence.resolve("Минасэ Рэи", names, scope={"quay"}), "rei")
        self.assertIsNone(presence.resolve("Другой Минасэ", names))

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


class CanonPlanTest(unittest.TestCase):
    def test_canon_plan_happens_without_roll(self):
        from rpg_engine import will
        h = {"cursor": {"id": "s09.text.010", "at": "0006-10-20T12:00:00"}, "scene": {"present": []},
             "_psyche": {"sasuke": {"name": "Учиха Саске", "intent": {
                 "end_snake": {"status": "committed", "goal": "g", "due": "0006-10-17T22:00:00",
                               "if_unopposed": "Орочимару мёртв.", "flaw": "f", "canon": "да"}}}}}
        events = will.advance(h)
        self.assertEqual(events[0]["status"], "fulfilled")
        self.assertIn("Орочимару мёртв", events[0]["outcome"])
        self.assertIsNone(h["_psyche"]["sasuke"]["intent_done"][-1]["roll"])

    def test_keeper_truth_goes_to_hidden_offscreen(self):
        from rpg_engine import keeper
        root = Path(tempfile.mkdtemp())
        (root / "memory/_curated").mkdir(parents=True)
        (root / keeper.SEED).write_text(json.dumps({"truth": {"leak": "источник"}}, ensure_ascii=False), encoding="utf-8")
        h = {"cursor": {"id": "x", "at": "0006-10-04T07:13:00"}}
        self.assertTrue(keeper.apply(root, h))
        self.assertEqual(h["offscreen"]["truth"]["leak"], "источник")


class ReplanTest(unittest.TestCase):
    def test_keeper_drops_only_unstarted_events(self):
        from rpg_engine import keeper
        root = Path(tempfile.mkdtemp())
        (root / "memory/_curated").mkdir(parents=True)
        h = {"cursor": {"id": "x", "at": "0006-10-04T07:13:00"},
             "_plot": {"events": {"later": {"id": "later", "status": "queued", "scenes": 0},
                                  "live": {"id": "live", "status": "active", "scenes": 2}}}}
        (root / keeper.SEED).write_text(json.dumps({"plot": {"later": {"drop": True}, "live": {"drop": True}}}),
                                        encoding="utf-8")
        keeper.apply(root, h)
        self.assertNotIn("later", h["_plot"]["events"])
        self.assertIn("live", h["_plot"]["events"], "начатое событие не снимается")

    def test_will_seed_drops_plan_silently(self):
        from rpg_engine import will
        root = Path(tempfile.mkdtemp())
        (root / "memory/_curated").mkdir(parents=True)
        h = {"cursor": {"id": "x", "at": "0006-10-04T07:13:00"},
             "_psyche": {"sasuke": {"name": "Учиха Саске", "intent": {"old": {"due": "0006-10-17T22:00:00"}}}}}
        (root / will.SEED).write_text(json.dumps({"sasuke": {"drop": ["old"], "intent": {
            "new": {"goal": "g", "due": "0010-03-10T22:00:00", "canon": "да"}}}}), encoding="utf-8")
        will.seed(root, h)
        intent = h["_psyche"]["sasuke"]["intent"]
        self.assertNotIn("old", intent)
        self.assertEqual(intent["new"]["due"], "0010-03-10T22:00:00")
        self.assertFalse(will._state(h).get("happened"), "снятый план не становится новостью мира")

class DurableDirectiveTest(unittest.TestCase):
    def test_permanent_survives_expiry_and_session_boundary(self):
        h = {"cursor": {"id": "s15.text.001"}}
        directives.capture(h, {"id": "s14.text.024", "kind": "ooc",
                               "player": "Мастеру: впредь показывай движения подробно."})
        for i in range(2, 40):
            directives.capture(h, {"id": f"s15.text.{i:03}", "kind": "ooc",
                                   "player": f"Почему сцена {i} такая?"})
        self.assertEqual(len(directives.active(None, h)), 1)
        self.assertIn("впредь", directives.active(None, h)[0]["text"])
        self.assertLessEqual(len(h["directives"]), directives.KEEP)

    def test_question_and_once_are_not_binding(self):
        h = {"cursor": {"id": "s14.text.024"}}
        for player in ("Мастеру: как ты всегда выбираешь длину?", "Мастеру: перепиши сцену заново.",
                       "Мастеру: отмени непонятное правило."):
            directives.capture(h, {"id": "s14.text.024", "kind": "ooc", "player": player})
        self.assertEqual(directives.active(None, h), [])
        self.assertEqual([x["kind"] for x in h["directives"]], ["question", "once", "review"])

    def test_explicit_revoke_and_replace_keep_audit_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / directives.SEED
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"standing": [{"text": "Старое правило", "scope": "general"}]}))
            h = {}
            ident = directives.standing(root)[0]["id"]
            quote = "Замени старое правило: впредь пиши короче."
            t = {"id": "s14.text.024", "kind": "ooc", "player": quote,
                 "d": {"directive_ops": [{"op": "replace", "id": ident, "quote": quote,
                                         "text": "впредь пиши короче."}]}}
            directives.capture(h, t, root)
            directives.capture(h, t, root)
            rows = directives.active(root, h)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scope"], "general")
            quote = "Отмени правило о коротких сценах."
            directives.capture(h, {"id": "s14.text.025", "kind": "ooc", "player": quote,
                                   "d": {"directive_ops": [{"op": "revoke", "id": rows[0]["id"], "quote": quote}]}}, root)
            self.assertEqual(directives.active(root, h), [])
            self.assertEqual(len(h["directive_events"]), 2)
            self.assertEqual(directives.standing(root)[0]["text"], "Старое правило")

    def test_fabricated_quote_and_question_cannot_create_rule(self):
        for quote, player in (("Впредь пиши кратко.", "Пиши кратко сейчас."),
                              ("Как всегда писать?", "Как всегда писать?")):
            with self.assertRaises(ValueError):
                directives.validate_ops({"id": "s14.text.024", "kind": "ooc", "player": player,
                                         "d": {"directive_ops": [{"op": "add", "quote": quote, "text": quote}]}})

    def test_migration_keeps_seed_and_needs_played_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / directives.SEED
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"standing": [{"text": "Старое", "scope": "general"}]}))
            self.assertEqual(len(directives.active(root, {})), 1)
            anchor = root / "archive/turns/s14/s14.text.006.md"
            anchor.parent.mkdir(parents=True)
            anchor.write_text("## Игрок\nи вообще если можешь делать быстро визуализацию в игре делай ее\n\n## Ведущий\n")
            self.assertEqual(len(directives.active(root, {})), 2)
            self.assertEqual(directives.active(root, {})[0]["text"], "Старое")

    def test_atomic_invalid_target_and_game_address_exclusion(self):
        h = {}
        source = "Отмени правило о темпе."
        with self.assertRaises(ValueError):
            directives.capture(h, {"id": "s14.text.024", "kind": "ooc", "player": source,
                                   "d": {"directive_ops": [{"op": "revoke", "id": "dir_0000000000000000", "quote": source}]}})
        self.assertEqual(h, {})
        directives.capture(h, {"id": "s14.text.024", "kind": "ooc",
                               "player": "Мастеру: покажи заново. По игре: я всегда буду ждать."})
        self.assertEqual(directives.active(None, h), [])

    def test_empty_view_can_accept_first_confirmed_directive(self):
        player = "Впредь описывай движения подробно."
        incoming = {"id": "s14.text.024", "kind": "ooc", "player": player}
        out = directives.project_view({}, incoming)
        self.assertEqual(out["standing_ids"], [directives.stable_id(player)])

class DirectiveFoldTest(unittest.TestCase):
    setUp, tearDown = test_hot.HotFilesTest.setUp, test_hot.HotFilesTest.tearDown
    git, commit, put = test_hot.HotFilesTest.git, test_hot.HotFilesTest.commit, test_hot.HotFilesTest.put

    def test_amend_rolls_back_directive_event(self):
        self.put(turn(45, "0006-10-01T07:14:00", kind="ooc",
                      player="Мастеру: впредь описывай движения подробно."))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        state, _ = hot._read_state(self.root)
        self.assertEqual(len(directives.active(self.root, state)), 1)
        self.commit("accepted")
        self.put(turn(45, "0006-10-01T07:14:00", kind="ooc", amend=True,
                      player="Мастеру: как устроены броски?"))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        state, _ = hot._read_state(self.root)
        self.assertEqual(directives.active(self.root, state), [])
        self.assertNotIn("standing", directives.view(self.root, state))
