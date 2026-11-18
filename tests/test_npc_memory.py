import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from rpg_engine import hot, npc_memory
from tests.test_hot import base, turn


class NpcMemoryFoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "recovery").mkdir()
        hot._write_state(self.root, base())
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

    def read(self, path):
        return json.loads((self.root / path).read_text(encoding="utf-8"))

    def test_present_npc_gets_episode_card_and_archive(self):
        self.put(turn(45, "0006-10-01T07:14:00", line="Шики впустил Нори; Тэцу смотрел из угла."))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        public = self.read(hot.HOT_PATH)
        for key in ("nori", "tetsu"):
            card = public["cast"][key]
            self.assertEqual(card["memory"][-1]["turn"], "s04.text.045")
            self.assertEqual(card["memory"][-1]["ch"], "present")
            self.assertEqual(card["first"], "s04.text.045")
        archive = self.read(f"{npc_memory.MEMORY_DIR}/nori.json")
        self.assertEqual([e["turn"] for e in archive["episodes"]], ["s04.text.045"])
        self.assertNotIn("_mem_log", public)

    def test_private_fields_never_reach_player_files(self):
        self.put(turn(45, "0006-10-01T07:14:00", d={"cast": {"nori": {
            "mem": "мальчишка открыл сам, значит ждал кого-то другого",
            "rel": {"trust": -2, "fear": 1, "why": "открыл без вопроса"},
            "affect": {"feel": "холодное подозрение", "hours": 2},
            "intent": {"take_case": {"goal": "забрать дело Хиросэ", "status": "committed"}},
            "psyche": {"mask": "вежливая усталость", "decides": "по выгоде дела"}}}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        public_text = (self.root / hot.HOT_PATH).read_text(encoding="utf-8")
        brief_text = (self.root / "recovery/CHECKS.json").read_text(encoding="utf-8")
        cast_text = (self.root / "recovery/cast/nori.json").read_text(encoding="utf-8")
        for text in (public_text, brief_text, cast_text):
            self.assertNotIn("холодное подозрение", text)
            self.assertNotIn("вежливая усталость", text)
            self.assertNotIn("ждал кого-то другого", text)
        private = self.read(hot.SECRET_PATH)["psyche"]["nori"]
        self.assertEqual(private["rel"]["shiki"]["trust"], -2)
        self.assertEqual(private["affect"]["feel"], "холодное подозрение")
        self.assertEqual(private["intent"]["take_case"]["status"], "committed")
        self.assertEqual(private["psyche"]["decides"], "по выгоде дела")
        self.assertEqual(private["memories"][-1]["turn"], "s04.text.045")

    def test_affect_expires_by_game_hours_not_by_turns(self):
        self.put(turn(45, "0006-10-01T07:14:00", d={"cast": {"nori": {
            "affect": {"feel": "злость", "hours": 1}}}}))
        hot.fold(self.root)
        self.commit("f45")
        self.put(turn(46, "0006-10-01T07:40:00"))
        hot.fold(self.root)
        self.commit("f46")
        self.assertEqual(self.read(hot.SECRET_PATH)["psyche"]["nori"]["affect"]["feel"], "злость")
        self.put(turn(47, "0006-10-01T08:30:00"))
        hot.fold(self.root)
        private = self.read(hot.SECRET_PATH)["psyche"]["nori"]
        self.assertNotIn("affect", private)
        self.assertEqual(private["affect_past"][-1]["feel"], "злость")

    def test_relation_is_clamped_and_silent_character_change_is_flagged(self):
        self.put(turn(45, "0006-10-01T07:14:00", d={"cast": {"nori": {
            "rel": {"trust": -9}, "wants": "уйти домой"}}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        private = self.read(hot.SECRET_PATH)["psyche"]["nori"]
        self.assertEqual(private["rel"]["shiki"]["trust"], -5)
        kinds = [c["kind"] for c in self.read("recovery/CHECKS.json")["checks"]]
        self.assertIn("drift", kinds)

    def test_name_collision_is_reported_not_blocking(self):
        self.put(turn(45, "0006-10-01T07:14:00", d={"cast": {"nori_2": {
            "name": "Хаясэ Кэн", "wants": "денег"}}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        checks = self.read("recovery/CHECKS.json")["checks"]
        self.assertTrue(any(c["kind"] == "name" and "Хаясэ" in c["text"] for c in checks))

    def test_amendment_of_current_turn_replaces_episode_and_line(self):
        self.put(turn(45, "0006-10-01T07:14:00", line="Черновая строка."))
        hot.fold(self.root)
        self.commit("f45")
        self.put(turn(45, "0006-10-01T07:14:00", amend=True, line="Исправленная строка.",
                      review={"predecessor": "s04.text.044"}))
        report = hot.fold(self.root)
        self.assertEqual(report["errors"], [])
        archive = self.read(f"{npc_memory.MEMORY_DIR}/nori.json")
        self.assertEqual([e["what"] for e in archive["episodes"]], ["Исправленная строка."])
        card = self.read(hot.HOT_PATH)["cast"]["nori"]
        self.assertEqual(card["memory_total"], 1)
        self.assertEqual(self.read(hot.HOT_PATH)["chronicle"][-1]["line"], "Исправленная строка.")

    def test_dread_is_private_and_amend_does_not_double_count(self):
        d = {"scene": {}, "cast": {"nori": {"dread": {"saw": "зашил рану без нити", "reads": "он режет живое как бумагу",
                                                     "stage": 1}}}}
        self.put(turn(45, "0006-10-01T07:14:00", d=d))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        self.commit("f45")
        d2 = {"scene": {}, "cast": {"nori": {"dread": {"saw": "прочитал запечатанный пакет", "reads": "он знает"}}}}
        self.put(turn(46, "0006-10-01T07:20:00", d=d2))
        hot.fold(self.root)
        self.commit("f46")
        self.put(turn(46, "0006-10-01T07:20:00", d=d2, amend=True, review={"predecessor": "s04.text.045"}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        private = json.loads((self.root / hot.SECRET_PATH).read_text(encoding="utf-8"))
        self.assertEqual(private["psyche"]["nori"]["dread"]["stage"], 2)
        self.assertIn("horror", private)
        public = (self.root / hot.HOT_PATH).read_text(encoding="utf-8")
        self.assertNotIn("режет живое", public)
        brief = json.loads((self.root / "recovery/GM_BRIEF.json").read_text(encoding="utf-8"))
        self.assertEqual(brief["horror"]["npcs"]["nori"]["stage"], 2)


class BackfillTest(unittest.TestCase):
    def test_backfill_from_verbatim_log_is_idempotent_and_hydrates_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "recovery/cast").mkdir(parents=True)
            (root / "archive").mkdir()
            (root / "recovery/cast/kadziura.json").write_text(json.dumps(
                {"schema": "npr-cast-card/2", "key": "kadziura", "name": "Кадзиура Сатору",
                 "seen": "s05.text.003"}, ensure_ascii=False), encoding="utf-8")
            rows = [{"id": f"s05.text.{n:03}", "at": "0006-10-02T01:00:00", "line": line,
                     "player": "", "gm": gm} for n, line, gm in (
                (1, "Шики пришёл в госпиталь.", "Дежурная не подняла глаз."),
                (2, "Кадзиуру вызвали в операционную.", ""),
                (3, "Шики ждал в коридоре.", "Тишина."),
                (4, "Сатору закрыл дверь кабинета.", "Кадзиура Сатору сел."))]
            (root / "archive/session-005-turns.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            first = npc_memory.backfill(root)
            second = npc_memory.backfill(root)
            self.assertEqual(first, second)
            episodes = npc_memory.load_episodes(root, "kadziura")
            self.assertEqual([e["turn"][-3:] for e in episodes], ["002", "003", "004"])
            self.assertEqual({e["ch"] for e in episodes}, {"mentioned", "present"})
            state = {"cast": {}}
            hot.ensure_npc_memory(root, state)
            card = json.loads((root / "recovery/cast/kadziura.json").read_text(encoding="utf-8"))
            self.assertEqual((card["memory_total"], card["first"], card["tier"]),
                             (3, "s05.text.002", "archetypal"))


if __name__ == "__main__":
    unittest.main()
