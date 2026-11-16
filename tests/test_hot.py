import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from rpg_engine import hot


def base():
    return {
        "schema": hot.SCHEMA, "campaign": "naruto-shiki", "session": 4,
        "calendar": {"year_offset": 1277},
        "cursor": {"id": "s04.text.044", "seq": 44, "at": "0006-10-01T07:10:00",
                   "place": "Кусагакуре, дом Мугами Шики", "awaiting": "player",
                   **hot.heading("0006-10-01T07:10:00")},
        "scene": {"situation": "Нори у ворот", "present": ["nori", "tetsu"]},
        "cast": {"nori": {"name": "Хаясэ Нори", "wants": "удержать дело у себя", "knows": ["Тэцу ранен"]},
                 "tetsu": {"name": "Тэцу", "wants": "выжить"}},
        "threads": {"v7": {"state": "контейнер под двумя запорами"}},
        "offscreen": {"truth": {}, "clocks": {
            "cleaner": {"owner": "неизвестный", "what": "добить свидетеля", "due": "0006-10-01T09:00:00",
                        "if_unopposed": "Тэцу мёртв"}}, "resolved": []},
        "pact": {"bound_at": "0006-09-27T06:30:19", "rate_per_day": 0.75,
                 "events": [{"id": "s04.text.008:bridge", "points": 6.5},
                            {"id": "s04.text.009:shiton_attempt", "points": 0.6},
                            {"id": "s04.text.009:byoton_attempt", "points": 0.9}]},
        "canon": [], "chronicle": [{"id": "s04.text.044", "at": "0006-10-01T07:10:00", "line": "Нори пришла"}],
        "last": {"id": "s04.text.044", "player": "стойте там", "gm": "…"},
        "fold": {"commit": "", "turns": 44},
    }


def turn(number, at, **extra):
    data = {"schema": hot.TURN_SCHEMA, "id": f"s04.text.{number:03}", "at": at,
            "place": "Кусагакуре, дом Мугами Шики, ворота", "player": "#открываю калитку#",
            "gm": "Нори вошла первой и сразу присела у кровяного следа.", "line": "Шики впустил Нори."}
    data.update(extra)
    return data


class HotFoldTest(unittest.TestCase):
    def test_heading_matches_campaign_calendar(self):
        self.assertEqual(hot.heading("0006-10-01T07:10:00"),
                         {"date": "Воскресенье, 1 октября 1283 года", "time": "07:10"})
        self.assertEqual(hot.heading("0006-09-27T22:05:34")["date"], "Среда, 27 сентября 1283 года")

    def test_pact_is_projected_from_game_time(self):
        state = hot.pact_state(base()["pact"], "0006-10-01T06:41:00")
        self.assertAlmostEqual(state["value"], 11.0056, places=3)
        self.assertEqual((state["stage"], state["contact_hazard"]), (1, False))
        later = hot.pact_state(base()["pact"], "0006-10-20T06:30:19")
        self.assertEqual(later["stage"], 2)

    def test_turn_advances_cursor_and_merges_delta(self):
        state = base()
        changed = hot.apply_turn(state, turn(45, "0006-10-01T07:14:00", d={
            "scene": {"situation": "Нори в доме", "present": ["nori", "tetsu"]},
            "cast": {"nori": {"stance": "давит", "knows+": ["Шики читал пакет не вскрывая"]},
                     "kase": {"name": "Касэ", "wants": "выслужиться"}},
            "threads": {"v7": {"state": "Нори требует свидетеля"}, "attack": "место нападения осматривают"},
            "clocks": {"cleaner": {"due": "0006-10-01T07:12:00"}},
            "truth": {"v7": "подпись подделал писарь"},
            "canon+": ["Немера не человек"]}))
        self.assertTrue(changed)
        self.assertEqual(state["cursor"]["id"], "s04.text.045")
        self.assertEqual(state["cursor"]["time"], "07:14")
        self.assertEqual(state["cast"]["nori"]["knows"], ["Тэцу ранен", "Шики читал пакет не вскрывая"])
        self.assertEqual(state["cast"]["kase"]["seen"], "s04.text.045")
        self.assertEqual(state["threads"]["attack"]["state"], "место нападения осматривают")
        self.assertEqual(state["offscreen"]["due_now"], ["cleaner"])
        self.assertEqual(state["offscreen"]["truth"]["v7"], "подпись подделал писарь")
        self.assertEqual(state["canon"][-1], {"turn": "s04.text.045", "rule": "Немера не человек"})
        self.assertEqual(state["last"]["player"], "#открываю калитку#")
        self.assertEqual(len(state["chronicle"]), 2)

    def test_replay_is_idempotent_and_amend_replaces_last(self):
        state = base()
        first = turn(45, "0006-10-01T07:14:00")
        self.assertTrue(hot.apply_turn(state, first))
        self.assertFalse(hot.apply_turn(state, first))
        fixed = turn(45, "0006-10-01T07:15:00", line="Исправлено: Нори осталась у ворот.", amend=True)
        self.assertTrue(hot.apply_turn(state, fixed))
        self.assertEqual(len(state["chronicle"]), 2)
        self.assertEqual(state["chronicle"][-1]["line"], "Исправлено: Нори осталась у ворот.")

    def test_clock_resolution_and_thread_closure_are_recorded(self):
        state = base()
        hot.apply_turn(state, turn(45, "0006-10-01T09:30:00", d={
            "clocks": {"cleaner": {"done": "Тэцу зарезан в лазарете управления"}},
            "threads": {"v7": {"closed": "контейнер ушёл в Центральное управление"}}}))
        self.assertNotIn("cleaner", state["offscreen"]["clocks"])
        self.assertNotIn("v7", state["threads"])
        outcomes = [r["outcome"] for r in state["offscreen"]["resolved"]]
        self.assertIn("Тэцу зарезан в лазарете управления", outcomes)
        self.assertEqual(state["offscreen"]["due_now"], [])

    def test_pact_event_is_counted_once(self):
        state = base()
        event = {"id": "s04.text.045:cast", "type": "cast_B", "school": "byoton"}
        hot.apply_turn(state, turn(45, "0006-10-01T07:14:00", d={"pact_events": [event]}))
        hot.apply_turn(state, turn(46, "0006-10-01T07:15:00", d={"pact_events": [event]}))
        self.assertEqual(sum(1 for e in state["pact"]["events"] if e["id"] == event["id"]), 1)
        self.assertAlmostEqual(state["pact"]["value"], 0.75 * 4.0310 + 8 + 3, places=1)

    def test_ooc_turn_keeps_clock_and_scene_text(self):
        state = base()
        ooc = {"schema": hot.TURN_SCHEMA, "id": "s04.text.045", "at": "0006-10-01T07:10:00", "kind": "ooc",
               "player": "Мастеру: меньше канцелярии", "line": "Правка игрока: меньше канцелярии.",
               "d": {"canon+": ["Меньше канцелярии, больше действия"]}}
        self.assertTrue(hot.apply_turn(state, ooc))
        self.assertEqual(state["cursor"]["at"], "0006-10-01T07:10:00")
        self.assertEqual(state["last"]["id"], "s04.text.044")
        self.assertTrue(state["chronicle"][-1]["ooc"])

    def test_invalid_turns_are_rejected(self):
        for broken in ({"schema": "x"}, turn(45, "вчера"), turn(45, "0006-10-01T07:14:00", gm=""),
                       turn(45, "0006-10-01T07:14:00", line="я" * 400),
                       {**turn(45, "0006-10-01T07:14:00"), "id": "turn-45"}):
            with self.assertRaises(hot.TurnError):
                hot.validate_turn(broken)

    def test_lint_report_reaches_hot(self):
        state = base()
        hot.apply_turn(state, turn(45, "0006-10-01T07:14:00", gm=(
            "— Без вашего согласия никто не войдёт. Я пришла не спорить.\n\n"
            "Это не прозвучало упрёком. Кровь не вернулась чудесным образом.")))
        recent = state["lint"]["recent"][-1]
        self.assertEqual(recent["id"], "s04.text.045")
        self.assertEqual(recent["verdict"], "переписать")
        self.assertGreaterEqual(state["lint"]["session_totals"]["deference"], 2)

    def test_legacy_fast_scene_is_adapted(self):
        legacy = {"format": "npr-shiki-fast-scene/1",
                  "cursor": {"last_turn_id": "s04.text.045", "at": "0006-10-01T07:14:00"},
                  "history": [{"turn_id": "s04.text.045", "summary": "Шики впустил Нори. " * 40}],
                  "latest_turn": {"id": "s04.text.045", "at": "0006-10-01T07:14:00", "location": "Дом",
                                  "player_input": "#открываю#", "exact_visible_gm_response": "Нори вошла."}}
        adapted = hot.turn_from_legacy(legacy)
        hot.validate_turn(adapted)
        self.assertLessEqual(len(adapted["line"]), hot.LINE_LIMIT)
        state = base()
        self.assertTrue(hot.apply_turn(state, adapted))
        self.assertEqual(state["last"]["gm"], "Нори вошла.")


    def test_calendar_ahead_is_computed_for_the_gm(self):
        state = hot.refresh({"cursor": {"at": "0006-10-01T07:10:00"}})
        ahead = state["calendar"]["ahead"]
        self.assertEqual(len(ahead), hot.CALENDAR_AHEAD)
        self.assertEqual(ahead[0], "Понедельник, 2 октября 1283 года")
        self.assertEqual(ahead[6], "Воскресенье, 8 октября 1283 года")

    def test_malformed_legacy_files_are_rejected_not_crashing(self):
        for broken in ([], {"latest_turn": "текст"}, {"history": ["строка"], "latest_turn": {"id": "s04.text.045"}},
                       {"cursor": "s04", "latest_turn": {}}):
            with self.assertRaises(hot.TurnError):
                hot.validate_turn(hot.turn_from_legacy(broken))


class HotFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "recovery").mkdir()
        state = base()
        state["cast"]["nori"]["hides"] = "ей нужен виновный за Хиросэ, любой"
        hot._write_state(self.root, state)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.commit("base")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def put(self, data):
        (self.root / hot.TURN_PATH).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.commit(data["id"])

    def test_fold_recovers_every_turn_from_git_history_of_single_slot(self):
        self.put(turn(45, "0006-10-01T07:14:00"))
        self.put(turn(46, "0006-10-01T07:20:00", line="Нори осмотрела след."))
        self.put(turn(47, "0006-10-01T07:31:00", line="Тэцу назвал имя.",
                      d={"cast": {"tetsu": {"knows+": ["имя писаря"]}}}))
        report = hot.fold(self.root)
        self.assertEqual(report["applied"], ["s04.text.045", "s04.text.046", "s04.text.047"])
        self.assertEqual(report["errors"], [])
        log = (self.root / "archive/session-004-turns.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(row)["id"][-3:] for row in log], ["045", "046", "047"])
        chronicle = (self.root / "archive/session-004-chronicle.md").read_text(encoding="utf-8")
        self.assertIn("`s04.text.046` 0006-10-01T07:20:00 — Нори осмотрела след.", chronicle)
        card = json.loads((self.root / "recovery/cast/tetsu.json").read_text(encoding="utf-8"))
        self.assertEqual(card["knows"], ["имя писаря"])
        self.commit("fold")
        self.assertEqual(hot.fold(self.root)["status"], "current")
        self.assertTrue(hot.check(self.root)["ok"], hot.check(self.root))

    def test_fold_accepts_old_protocol_writes_after_migration(self):
        legacy = {"format": "npr-shiki-fast-scene/1",
                  "cursor": {"last_turn_id": "s04.text.045", "at": "0006-10-01T07:14:00"},
                  "history": [{"turn_id": "s04.text.045", "summary": "Старый протокол: Шики впустил Нори."}],
                  "latest_turn": {"id": "s04.text.045", "at": "0006-10-01T07:14:00", "location": "Дом",
                                  "player_input": "#открываю#", "exact_visible_gm_response": "Нори вошла."}}
        (self.root / hot.LEGACY_PATH).write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        self.commit("legacy 45")
        report = hot.fold(self.root)
        self.assertEqual(report["applied"], ["s04.text.045"])
        state = json.loads((self.root / hot.HOT_PATH).read_text(encoding="utf-8"))
        self.assertEqual(state["chronicle"][-1]["line"], "Старый протокол: Шики впустил Нори.")

    def test_hidden_truth_never_reaches_player_readable_files(self):
        self.put(turn(45, "0006-10-01T09:05:00", d={
            "truth": {"knife": "бил свой"},
            "cast": {"tetsu": {"hides": "подписывал пустой лист", "stance": "врёт"},
                     "nori": None},
            "clocks": {"raid": {"owner": "покупатель", "what": "налёт на хранилище",
                                "due": "0006-10-02T23:00:00", "if_unopposed": "В-7 унесён, караул вырезан"}}}))
        report = hot.fold(self.root)
        self.assertEqual(report["due"], 1)
        public = (self.root / hot.HOT_PATH).read_text(encoding="utf-8")
        cards = "".join(p.read_text(encoding="utf-8") for p in (self.root / hot.CAST_DIR).glob("*.json"))
        for secret in ("бил свой", "подписывал пустой лист", "любой", "вырезан", "добить свидетеля"):
            self.assertNotIn(secret, public + cards)
        self.assertEqual(json.loads(public)["offscreen"], {"see": hot.SECRET_PATH, "due": 1, "clocks": 2})
        hidden = json.loads((self.root / hot.SECRET_PATH).read_text(encoding="utf-8"))
        self.assertEqual(hidden["offscreen"]["due_now"], ["cleaner"])
        self.assertEqual(hidden["offscreen"]["truth"]["knife"], "бил свой")
        self.assertEqual(hidden["cast"]["nori"], "ей нужен виновный за Хиросэ, любой")
        self.assertNotIn("nori", json.loads(public)["cast"])
        # Отложенная карточка возвращается вместе со своей тайной, когда NPC снова в сцене.
        self.put(turn(46, "0006-10-01T09:20:00", d={"cast": {"nori": {"stance": "вернулась с приказом"}}}))
        hot.fold(self.root)
        merged, _raw = hot._read_state(self.root)
        self.assertEqual(merged["cast"]["nori"]["hides"], "ей нужен виновный за Хиросэ, любой")
        self.assertEqual(merged["cast"]["nori"]["wants"], "удержать дело у себя")
        self.assertTrue(hot.check(self.root)["ok"], hot.check(self.root))

    def test_bootstrap_migrates_legacy_history_losslessly(self):
        (self.root / hot.HOT_PATH).unlink()
        (self.root / hot.SECRET_PATH).unlink()
        for number, at in ((43, "0006-10-01T07:06:00"), (44, "0006-10-01T07:10:00")):
            legacy = {"format": "npr-shiki-fast-scene/1",
                      "cursor": {"last_turn_id": f"s04.text.{number:03}", "at": at},
                      "history": [{"turn_id": f"s04.text.{number:03}", "summary": f"Сводка {number}"}],
                      "latest_turn": {"id": f"s04.text.{number:03}", "at": at, "location": "Дом",
                                      "player_input": f"заявка {number}",
                                      "exact_visible_gm_response": f"Ответ {number}."}}
            (self.root / hot.LEGACY_PATH).write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            self.commit(f"legacy {number}")
        seed = {k: v for k, v in base().items() if k not in ("cursor", "last", "fold", "schema")}
        seed["chronicle"] = [{"id": "s04.text.001", "at": "0006-09-27T22:05:36", "line": "Начало"},
                             {"id": "s04.text.044", "at": "0006-10-01T07:10:00", "line": "Нори у ворот"}]
        report = hot.bootstrap(self.root, seed)
        self.assertEqual((report["cursor"], report["verbatim_turns"], report["chronicle"]),
                         ("s04.text.044", 2, 3))
        log = [json.loads(r) for r in
               (self.root / "archive/session-004-turns.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(r["id"][-3:], r["gm"]) for r in log], [("043", "Ответ 43."), ("044", "Ответ 44.")])
        state, _raw = hot._read_state(self.root)
        self.assertEqual(state["last"], {"id": "s04.text.044", "player": "заявка 44", "gm": "Ответ 44."})
        self.assertEqual([e["line"] for e in state["chronicle"]], ["Начало", "Сводка 43", "Нори у ворот"])
        self.assertEqual(state["pact"]["stage"], 1)
        self.commit("bootstrap")
        self.assertEqual(hot.fold(self.root)["status"], "current")
        self.assertTrue(hot.check(self.root)["ok"])

    def test_broken_turn_file_is_reported_not_fatal(self):
        (self.root / hot.TURN_PATH).write_text("{не json", encoding="utf-8")
        report = hot.fold(self.root)
        self.assertEqual(report["applied"], [])
        self.assertTrue(report["errors"])


if __name__ == "__main__":
    unittest.main()
