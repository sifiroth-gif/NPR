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

    def test_broken_cursor_refuses_to_narrate(self):
        hot = self.root / "recovery/FAST_SCENE.json"
        hot.parent.mkdir()
        hot.write_text(json.dumps({"cursor": {"last_turn_id": "now"},
                "latest_turn": {"id": "stale"}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cursor"):
            scene_memory.fast_packet_from_files(self.root, self.root)


if __name__ == "__main__":
    unittest.main()
