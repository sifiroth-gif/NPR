from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from rpg_engine import Campaign, autonomy, continuity, git_state, preparation, semantic_recall, sessions, setting, verification
from rpg_engine.history import recall

START = "0001-07-01T10:00:00"
DUE = "0001-07-01T11:00:00"
LATER = "0001-07-01T11:30:00"


def event(identifier, ops, *, at=START, summary=None, **extra):
    return {"id": identifier, "at": at, "summary": summary or identifier,
            "visibility": "gm_only", "operations": ops, **extra}


def note(key="a.rule", **kw):
    return {"op": "remember", "key": key, "category": "canon", "subject": "world",
            "text": "Действует уточнение игрока.", "authority": "player",
            "status": "open", "visibility": "gm_only"} | kw


class LongCampaign(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = Campaign.create(self.root / "campaign.sqlite", {
            "id": "example", "title": "Example", "player": {"id": "player", "name": "Player"},
            "start_at": START, "location": "Перевал",
            "npcs": [{"id": "guard", "name": "Дозорный"}, {"id": "herald", "name": "Гонец"}]
        })

    def turn(self, identifier, *, at=START, events=(), text="Игровой ответ.  ",
             raw="Смотрю на дорогу.", scene=None, confirm=True):
        packet = {"id": identifier, "expected_revision": self.game.context(recent=0)["revision"],
                  "at": at, "location": "Перевал", "player_input": raw, "text": text,
                  "events": list(events)}
        if scene:
            packet["scene"] = scene
        receipt = self.game.prepare(packet)
        if confirm:
            self.game.confirm(identifier, receipt["draft_sha"])
        return receipt

    def test_migrate_old_state_and_preserve_exact_history(self):
        self.turn("one", text="Точный ответ.  ")
        old_path = self.root / "old.sqlite"
        with sqlite3.connect(self.game.path) as c:
            c.executescript("""
                DROP TABLE session_exports; DROP TABLE messages; DROP TABLE process_plans;
                DROP TABLE memory; DROP TABLE journal_edges; DROP TABLE journal_entities;
                DROP TABLE journal_corrections; DROP INDEX journal_session_time;
                PRAGMA user_version=1;
            """)
        shutil.copyfile(self.game.path, old_path)
        self.assertTrue(verification.audit(self.game)["full_audit"])
        self.assertEqual(preparation.packet(self.game)["confirmed"]["revision"], 1)
        reviewed = event("review", [note()], source_ref="Игрок, подтверждённое уточнение")
        first = self.game.record_review(reviewed, 1)
        self.assertEqual(self.game.record_review(reviewed, 1), first)
        git_state.require_prefix(old_path, self.game.path)
        self.assertEqual(self.game.entry("turn", "one")["text"], "Точный ответ.  ")
        self.assertEqual(verification.audit(self.game)["revision"], 2)

    def test_review_supersession_and_search_visibility(self):
        old = event("old-account", [], summary="Первоначальный вариант о снежной дороге")
        self.turn("one", events=[old])
        changed = event("corrected", [note()], based_on=[old["id"]], supersedes=[old["id"]],
                        summary="Поправка игрока о снежной дороге", source_ref="Точный ввод игрока")
        self.game.record_review(changed, 1)
        with self.game.connection() as con:
            hidden = recall(con, query="снежная дорога", audience="player")
            valid = recall(con, query="снежная дорога")
            older = recall(con, query="снежная дорога", include_superseded=True)
            self.assertEqual(continuity.notes(con, key="a.rule")[0]["source_event"], "corrected")
        self.assertTrue({"old-account", "corrected"}.isdisjoint(
            item["id"] for item in hidden["matches"]))
        self.assertIn("corrected", [item["id"] for item in valid["matches"]])
        self.assertNotIn("old-account", [item["id"] for item in valid["matches"]])
        prior = next(item for item in older["matches"] if item["id"] == "old-account")
        self.assertEqual(prior["superseded_by"], "corrected")
        self.assertEqual(prior["consequences"][0]["id"], "corrected")
        overwrite = note(previous="corrected")
        overwrite["authority"] = "plan"
        with self.assertRaisesRegex(ValueError, "lower-authority"):
            self.game.record_review(event("weak", [overwrite], source_ref="Условный план"), 2)
        self.assertEqual(self.game.verify()["revision"], 2)

    def test_autonomy_checks_clock_guards_and_player_window(self):
        self.turn("start", events=[event("process", [
            {"op": "start_process", "id": "patrol", "owner": "guard", "goal": "Пройти перевал",
             "due_at": DUE, "visibility": "gm_only", "notes": ""},
            {"op": "set_fact", "subject": "road", "name": "open", "value": True,
             "visibility": "gm_only"},
            {"op": "configure_process", "id": "patrol", "plan": {
                "premise": "Дозорный знает путь", "options": [
                    {"id": "pass", "intent": "Пересечь проход", "status": "resolved", "player_window": True,
                     "outcome": "Патруль прошёл", "requires": [
                         {"kind": "fact", "subject": "road", "key": "open", "value": True}],
                     "effects": [{"op": "set_fact", "subject": "guard", "name": "crossed", "value": True,
                                  "visibility": "gm_only"}]}
                ]}}
        ])])
        with self.game.connection() as con:
            self.assertEqual(autonomy.queue(con, at=START, until=LATER)["due"][0]["eligible"][0]["id"], "pass")
        with self.assertRaisesRegex(ValueError, "review process"):
            self.turn("skip", at=LATER)
        window = event("window", [], at=DUE)
        window["visibility"] = "player_known"
        self.turn("approach", at=DUE, events=[window],
                  scene={"id": "pass", "actors": ["guard"], "beat": "decision"})
        invalid = {"op": "take_process_step", "process": "patrol", "option": "pass",
                   "plan_source": "process", "reason": "Путь доступен"}
        with self.assertRaisesRegex(ValueError, "intervention"):
            self.turn("invalid", at=LATER, events=[event("no-window", [invalid], at=DUE)])
        resolved = {**invalid, "window_resolved_by": "window"}
        self.turn("resolve", at=LATER, events=[event("done", [resolved], at=DUE, based_on=["window"])])
        self.assertTrue(self.game.world(audience="gm", subject="guard")["facts"][0]["value"])
        self.assertTrue(verification.audit(self.game)["ok"])

    def test_message_has_no_recipient_knowledge_until_delivered(self):
        self.turn("observe", events=[event("witness", [
            {"op": "set_belief", "actor": "guard", "topic": "sign", "belief": "белая лента",
             "evidence": "witnessed", "certainty": "certain", "visibility": "gm_only"}
        ], witnesses=["guard"])])
        self.turn("send", events=[event("dispatch", [
            {"op": "schedule_message", "id": "report", "sender": "guard", "recipient": "herald",
             "topic": "sign", "claim": "чёрная лента", "channel": "курьер",
             "knowledge_source": "witness", "deliver_at": DUE, "deception_reason": "Отвлечь охрану"}
        ])])
        with self.game.connection() as con:
            self.assertEqual(continuity.project_history(con, actor="herald", at=START, limit=5), [])
        delivery = event("arrival", [
            {"op": "deliver_message", "id": "report", "status": "delivered"},
            {"op": "set_belief", "actor": "herald", "topic": "sign", "belief": "чёрная лента",
             "evidence": "reported", "certainty": "probable", "visibility": "gm_only"}
        ], at=DUE, based_on=["dispatch"], knowledge_routes=[
            {"actor": "herald", "via": "reported", "source": "report", "source_actor": "guard"}])
        self.turn("received", at=DUE, events=[delivery])
        with self.game.connection() as con:
            self.assertEqual(continuity.project_history(con, actor="herald", at=START, limit=5), [])
            self.assertEqual(continuity.project_history(con, actor="herald", at=DUE, limit=5)[0]["route"]["source"], "report")
        self.assertEqual(self.game.world(audience="gm", subject="guard")["beliefs"][0]["belief"], "белая лента")
        self.assertTrue(verification.audit(self.game)["ok"])

    def test_handoff_rng_export_and_new_setting(self):
        self.game.roll(turn_id="one", expected_revision=0, check_id="listen", sides=6)
        receipt = self.turn("one", confirm=False, text="Подтверждаемый текст.  ")
        copied = self.root / "copied.sqlite"
        self.game.checkpoint(copied)
        restored = Campaign(copied)
        private = preparation.packet(restored, mode="handoff")["confirmed"]
        self.assertIsNone(private["last_confirmed_turn"])
        self.assertEqual(private["pending"]["draft_sha"], receipt["draft_sha"])
        self.assertEqual(len(private["pending"]["rolls"]), 1)
        with self.assertRaisesRegex(ValueError, "confirm"):
            sessions.close(restored, 0, self.root / "log.md")
        restored.confirm("one", receipt["draft_sha"])
        export = self.root / "log.md"
        self.assertEqual(sessions.close(restored, 1, export), sessions.close(restored, 1, export))
        self.assertIn("Подтверждаемый текст.  \n", export.read_text())
        self.assertTrue(verification.audit(restored)["ok"])
        destination = self.root / "separate"
        setting.scaffold(destination, portable=True)
        self.assertTrue((destination / "rpg_engine/engine.py").is_file())
        self.assertFalse((destination / "campaigns").exists())
        self.assertEqual(list(destination.glob("*.sqlite")), [])
        with self.assertRaises(FileExistsError):
            setting.scaffold(destination)
        self.assertEqual(self.game.verify()["revision"], 0)

    def test_embedding_cache_uses_exact_sources_and_reuses_old_passages(self):
        self.turn("one", text="Старая договорённость.")
        model = self.root / "model"
        model.mkdir()
        (model / "mock").write_text("pin")
        cache = self.root / "recall.sqlite"
        digest = semantic_recall.model_id(model)
        encoder = lambda texts: [[1.0, 0.0] if "договорённость" in text or "долг" in text
                                 else [0.0, 1.0] for text in texts]
        with patch.object(semantic_recall, "_encoder", return_value=encoder):
            semantic_recall.build(self.game, cache, model, digest)
            with self.game.connection() as con:
                seeds = semantic_recall.candidates(con, cache, model, digest, "долг",
                                                  audience="player", limit=3, session=None,
                                                  since=None, until=None, entities=(),
                                                  include_superseded=False)
                hits = recall(con, query="долг", audience="player", semantic=seeds)["matches"]
                self.assertEqual([item["id"] for item in hits], ["one"])
                self.assertIn("договорённость", hits[0]["semantic_passage"]["text"])
            self.turn("two", text="Другой ответ.")
            with self.game.connection() as con, self.assertRaisesRegex(ValueError, "out of date"):
                semantic_recall.candidates(con, cache, model, digest, "долг", audience="gm", limit=3,
                                           session=None, since=None, until=None, entities=(),
                                           include_superseded=False)
            self.assertGreater(semantic_recall.build(self.game, cache, model, digest)["reused"], 0)
