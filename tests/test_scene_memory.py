"""Regression: scene preparation always recalls established capabilities and provenance."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from rpg_engine import scene_memory


class SceneMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "campaigns/shiki"
        for name in ("locations", "characters", "continuity", "sessions"):
            (self.base / name).mkdir(parents=True)

        self.lab = "campaigns/shiki/locations/SECRET_LAB.json"
        lab_data = {
            "status": "established in play",
            "name": "Тайная лаборатория Шики",
            "capabilities": "Личный генетический исследовательский комплекс: лаборатория, оборудование, ДНК, микроскоп, секвенатор и анализ образцов",
            "privacy": "Ни один чужой лаборант не знает адреса, материалы генетики и биопсии остаются секретными",
            "source": "campaigns/shiki/continuity/ASSIMILATION.json",
        }
        (self.root / self.lab).write_text(json.dumps(lab_data, ensure_ascii=False), encoding="utf-8")
        (self.base / "continuity/ASSIMILATION.json").write_text(json.dumps({
            "summary": "Шики проводил генетические исследования, ассимиляцию и перестройку своего генома в собственной тайной лаборатории",
            "effective": "0006-09-25",
            "priority": "played",
        }, ensure_ascii=False), encoding="utf-8")
        (self.base / "sessions/SESSION_004.json").write_text(json.dumps({
            "status": "plan", "objective": "проверка генетики Саю"}, ensure_ascii=False), encoding="utf-8")

    def test_genetic_action_auto_recalls_private_lab_from_unstated_location(self):
        index = scene_memory.build_sources(self.root)
        result = scene_memory.preflight(
            action="Секвенирование тканей с руки Саю и руки существа",
            location="Нижний госпиталь, Седьмая палата",
            source_index=index)
        sources = [e["source"] for e in result["file_memory"]["evidence"]]
        self.assertIn(self.lab, sources)
        self.assertTrue(result["retrieval_used"])
        self.assertTrue(any(e["channel"] == "research_capability" for e in result["file_memory"]["evidence"]))

    def test_source_sha_and_explicit_graph_links(self):
        idx = scene_memory.build_sources(self.root)
        result = scene_memory.source_candidates(idx, action="генетика лаборатория", limit=12)
        lab = next(e for e in result["evidence"] if e["source"] == self.lab)
        source_bytes = (self.root / self.lab).read_bytes()
        self.assertEqual(lab["sha256"], hashlib.sha256(source_bytes).hexdigest())
        self.assertIn("campaigns/shiki/continuity/ASSIMILATION.json", lab["links"])

    def test_old_plan_is_not_promoted_as_canon(self):
        idx = scene_memory.build_sources(self.root)
        answer = scene_memory.source_candidates(idx, action="генетика Саю", limit=12)
        e = [e for e in answer["evidence"] if e["source"].endswith("SESSION_004.json")]
        if e:
            self.assertLess(e[0]["score"], 6.0)

    def test_hot_text_ahead_keeps_location_and_live_action(self):
        hot = self.root / "recovery/FAST_SCENE.json"
        hot.parent.mkdir()
        hot.write_text(json.dumps({
            "cursor": {"last_turn_id": "s04.text.004",
                       "location": "Лаборатория Шики", "at": "0006-09-28T06:48:00"},
            "latest_turn": {"id": "s04.text.004", "player_input": "секвенирование тканей"},
            "runtime": {"text_ahead_unreplayed": True}
        }, ensure_ascii=False), encoding="utf-8")
        p = scene_memory.fast_packet_from_files(self.root, self.root,
            action="исследование генетики и ДНК")
        self.assertEqual(p["status"], "text_ahead_unreplayed")
        self.assertEqual(p["cursor"]["last_turn_id"], "s04.text.004")
        self.assertTrue(p["file_memory"]["evidence"])

    def test_compact_index_tracks_hot_version_and_is_idempotent(self):
        hot = self.root / "recovery/FAST_SCENE.json"
        hot.parent.mkdir()
        hot.write_text(json.dumps({
            "cursor": {"last_turn_id": "s04.text.004", "at": "0006-09-28T06:48:00",
                       "location": "Тайная лаборатория"},
            "latest_turn": {"id": "s04.text.004", "facts": {"consent": "two skin biopsies"}},
            "story_compass": {"immediate": "Шики исследует клетки Саю"},
            "history": [{"turn_id": "s04.text.004", "summary": "ДНК анализ"}]
        }, ensure_ascii=False), encoding="utf-8")
        out = self.root / "recovery/SCENE_MEMORY_INDEX.json"
        first = scene_memory.write_live_index(self.root, self.root, out)
        self.assertEqual(first["status"], "updated")
        self.assertEqual(scene_memory.write_live_index(self.root, self.root, out)["status"], "current")
        p = json.loads(out.read_text())
        self.assertEqual(p["cursor"]["turn"], "s04.text.004")
        self.assertIn(self.lab, [d["source"] for d in p["documents"]])
        self.assertEqual(p["hot_sha256"], hashlib.sha256(hot.read_bytes()).hexdigest())
        data = json.loads(hot.read_text())
        data["cursor"]["last_turn_id"] = "s04.text.005"
        data["latest_turn"]["id"] = "s04.text.005"
        data["latest_turn"]["turn_kind"] = "player_action"
        data["latest_turn"]["player_input"] = "Продолжаю анализ"
        data["latest_turn"]["memory_gate"] = {
            "schema": "npr-memory-gate/1", "previous_turn_id": "s04.text.004",
            "index_turn": "s04.text.004", "index_blob_sha": "a" * 40,
            "player_input": "Продолжаю анализ",
            "source_refs": [{"source": self.lab,
                             "sha256": hashlib.sha256((self.root / self.lab).read_bytes()).hexdigest()}],
            "unretrieved": [], "mode": "indexed_hybrid"}
        data["history"].append({"turn_id": "s04.text.005", "summary": "Продолжение"})
        hot.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(scene_memory.write_live_index(self.root, self.root, out)["status"], "updated")

    def test_current_hot_facts_are_retrievable_without_sqlite(self):
        hot = self.root / "recovery/FAST_SCENE.json"
        hot.parent.mkdir()
        hot.write_text(json.dumps({
            "cursor": {"last_turn_id": "s04.text.004", "at": "0006-09-28T06:50:00",
                       "location": "Тайная лаборатория"},
            "latest_turn": {"id": "s04.text.004",
                            "facts": {"blood_sample": "Уцуро не исследовал образец Ш"}},
            "story_compass": {"immediate": "Саю жива, образцы кожи получены"}
        }, ensure_ascii=False), encoding="utf-8")
        result = scene_memory.fast_packet_from_files(self.root, self.root,
            action="Что Уцуро сделал с образцом крови?")
        sources = [r["source"] for r in result["file_memory"]["evidence"]]
        self.assertIn("recovery/FAST_SCENE.json", sources)

    def test_future_text_turn_requires_real_source_receipt(self):
        hot = self.root / "recovery/FAST_SCENE.json"
        hot.parent.mkdir()
        payload = {
            "cursor": {"last_turn_id": "s04.text.005", "at": "0006-09-28T06:50:00",
                       "location": "Тайная лаборатория"},
            "history": [
                {"turn_id": "s04.text.004", "summary": "Предыдущий ход"},
                {"turn_id": "s04.text.005", "summary": "Следующий ход"}],
            "latest_turn": {"id": "s04.text.005", "turn_kind": "player_action",
                            "player_input": "Изучаю ДНК"}
        }
        hot.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Missing mandatory"):
            scene_memory.write_live_index(self.root, self.root,
                self.root / "recovery/SCENE_MEMORY_INDEX.json")
        payload["latest_turn"]["memory_gate"] = {
            "schema": "npr-memory-gate/1", "previous_turn_id": "s04.text.004",
            "index_turn": "s04.text.004", "index_blob_sha": "a" * 40,
            "player_input": "Изучаю ДНК",
            "source_refs": [{"source": self.lab,
                             "sha256": hashlib.sha256((self.root / self.lab).read_bytes()).hexdigest()}],
            "unretrieved": [], "mode": "indexed_hybrid"}
        hot.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(scene_memory.write_live_index(self.root, self.root,
            self.root / "recovery/SCENE_MEMORY_INDEX.json")["status"], "updated")
        payload["latest_turn"]["memory_gate"]["player_input"] = "Чужая фраза"
        hot.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match"):
            scene_memory.write_live_index(self.root, self.root,
                self.root / "recovery/SCENE_MEMORY_INDEX.json")

    def test_engine_turn_contract_accepts_valid_memory_gate(self):
        from rpg_engine.engine import turn_value
        player = "Провожу исследование образцов"
        turn = {
            "id": "s04.text.005", "expected_revision": 304,
            "at": "0006-09-28T06:50:00", "location": "Тайная лаборатория",
            "player_input": player, "text": "Следующая сцена.",
            "memory_gate": {
                "schema": "npr-memory-gate/1",
                "previous_turn_id": "s04.text.004",
                "index_turn": "s04.text.004",
                "index_blob_sha": "a"*40,
                "player_input": player,
                "source_refs": [{"source": self.lab,
                                 "sha256": hashlib.sha256((self.root / self.lab).read_bytes()).hexdigest()}],
                "unretrieved": [], "mode": "indexed_hybrid",
            }
        }
        turn_value(turn)
        turn["memory_gate"]["index_blob_sha"] = "not-a-git-sha"
        with self.assertRaisesRegex(ValueError, "index Git blob"):
            turn_value(turn)

    def test_broken_cursor_refuses_to_narrate(self):
        hot = self.root / "recovery/FAST_SCENE.json"
        hot.parent.mkdir()
        hot.write_text(json.dumps({"cursor": {"last_turn_id": "now"},
                "latest_turn": {"id": "stale"}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cursor"):
            scene_memory.fast_packet_from_files(self.root, self.root)


if __name__ == "__main__":
    unittest.main()
