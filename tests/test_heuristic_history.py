"""Whole-campaign heuristic recall: every session, status gates and live integration."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from rpg_engine import heuristic_history, scene_memory


class HeuristicHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        archive = self.root / "archive"
        archive.mkdir()
        (self.root / "campaigns/shiki/continuity").mkdir(parents=True)
        (self.root / "recovery").mkdir()

        # Session 1 has no curated index: all lines must remain findable,
        # but no raw historical recollection may be treated as approved canon.
        first = ["Тихие улицы, город ждал рассвета."] * 100
        first[59] = "На площади впервые заговорила Лиора, носившая серебряное кольцо."
        (archive / "session-001.md").write_text("\n".join(first), encoding="utf-8")

        source2 = archive / "session-002-source.md"
        source2.write_text("\n".join([
            "Ночь. Сэндо установил древний маяк.",
            "Сэндо оставил ключ в старой башне.",
            "Отменённая сцена: Матрица Лжи обнаружена.",
            "Эта сцена позже отменена игроком.",
        ]), encoding="utf-8")
        (archive / "session-002-index.json").write_text(json.dumps({
            "source": "archive/session-002-source.md",
            "source_sha256": hashlib.sha256(source2.read_bytes()).hexdigest(),
            "entries": [
                {"id": "s2.001", "source_lines": [1, 2], "status": "played",
                 "scene_heading": "18 сентября 1278 года"},
                {"id": "s2.002", "source_lines": [3, 4], "status": "void"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        source3 = archive / "session-003-source.md"
        source3.write_text("\n".join([
            "В госпитале Дорин врачевал безымянных пациентов.",
            "Внутренний коридор сообщил о неисправном ключе.",
            "Текст отменён: Призрачный ковчег сгинул.",
        ]), encoding="utf-8")
        (archive / "session-003-index.json").write_text(json.dumps({
            "primary_source": "archive/session-003-source.md",
            "source_sha256": hashlib.sha256(source3.read_bytes()).hexdigest(),
            "index": [
                {"n": 3, "status": "played", "source_lines": [1, 2],
                 "at": "0006-09-26T10:00:00"},
                {"n": 4, "status": "void", "source_lines": [3, 3]},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        (archive / "session-004-index.json").write_text(json.dumps({
            "source": "archive/session-004-source.md", "ooc_in_live": [2],
        }), encoding="utf-8")
        (archive / "session-004-turns.jsonl").write_text("\n".join(json.dumps({
            "id": "s04.text.%03d" % n,
            "at": "0006-09-28T22:05:00",
            "player": message,
            "gm": message,
            "line": message
        }, ensure_ascii=False) for n, message in [
            (1, "Наблюдатель Варвен сверил список и вышел."),
            (2, "Мастеру: Неверная сцена, откати её.")
        ]) + "\n", encoding="utf-8")
        (archive / "session-005-turns.jsonl").write_text(
            json.dumps({"id": "s05.text.001", "at": "0006-10-01T22:00:00",
                        "player": "Где хранится палладиум?", "gm": "Палладиум был в сундуке.",
                        "line": "Палладиум в сундуке."}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        (archive / "shiki-canon.jsonl").write_text(
            json.dumps({"turn": "s04.text.001",
                        "rule": "Поздняя поправка: Варвен не вскрывал сундук."},
                       ensure_ascii=False) + "\n", encoding="utf-8")
        (self.root / "campaigns/shiki/continuity/RULE.json").write_text(
            json.dumps({"summary": "Хранитель Селестин охраняет маяк.",
                        "source": "s02.text.001"}, ensure_ascii=False),
            encoding="utf-8")
        (self.root / "recovery/HOT.json").write_text(
            json.dumps({"cursor": {"id": "s05.text.001"},
                        "last": {"id": "s05.text.001", "gm": "Палладиум в сундуке."}}),
            encoding="utf-8")

    def test_whole_corpus_coverage_and_void_exclusion(self):
        index = heuristic_history.build(self.root, self.root)
        sources = {row["source"] for row in index["records"]}
        for number in range(1, 6):
            self.assertTrue(any("session-%03d" % number in s for s in sources),
                            "Missing session " + str(number))
        indexed = " ".join(r["terms"] for r in index["records"])
        for word in ("лиор", "сэнд", "дорин", "варвен", "паллади"):
            self.assertIn(word, indexed)
        self.assertNotIn("матрица", indexed)
        self.assertNotIn("ковчег", indexed)
        self.assertFalse(any("s04.text.002" == row["id"] for row in index["records"]))
        first = [r for r in index["records"]
                 if r["source"] == "archive/session-001.md"]
        self.assertTrue(first)
        self.assertTrue(all(r["kind"] == "archived_source_review_required" for r in first))

    def test_corrected_canon_separate_from_event(self):
        index = heuristic_history.build(self.root, self.root)
        matches = heuristic_history.query(index, "Варвен сундук поправка", limit=12)
        self.assertTrue(any(m["kind"] == "correction" for m in matches["matches"]))
        self.assertTrue(all(m["status"] == "candidate_only" for m in matches["matches"]))
        self.assertTrue(any(m["source_sha256"] for m in matches["matches"]))

    def test_persisted_shards_and_live_preflight(self):
        saved = heuristic_history.save(self.root, self.root)
        self.assertGreater(saved["records"], 8)
        cached = heuristic_history.load(self.root / "archive/heuristic-recall")
        self.assertEqual(cached["documents"], saved["records"])
        self.assertEqual(heuristic_history.save(self.root, self.root)["status"], "current")
        packet = scene_memory.fast_packet_from_files(self.root, self.root,
                              action="Кто встречал Лиору на площади?")
        self.assertTrue(packet["historical_memory"]["matches"])
        self.assertEqual(packet["historical_memory"]["status"], "persistent_derived_cache")

    def test_generic_router_covers_each_session_without_subject_shortcuts(self):
        heuristic_history.save(self.root, self.root)
        folder = self.root / "archive/heuristic-recall"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        groups = {item["group"]: item for item in manifest["routes"]}
        for session in range(1, 6):
            self.assertIn("s%03d" % session, groups)
        self.assertIn("canon", groups)
        for group, token in (("s001", "лиора"), ("s002", "сэндо"),
                             ("s003", "дорин"), ("s004", "варвен"),
                             ("s005", "палладиум"), ("canon", "селестин")):
            self.assertTrue(heuristic_history.might_contain(
                groups[group]["bloom"], token), (group, token))
            route = json.loads((folder / groups[group]["route"]).read_text(encoding="utf-8"))
            self.assertEqual(route["group"], group)
            self.assertTrue(any(heuristic_history.might_contain(
                shard["bloom"], token) for shard in route["shards"]))

    def test_bloom_repeated_terms_are_idempotent(self):
        rows = [{"terms": "стерилизация стерилизация ключ"},
                {"terms": "стерилизация маяк ключ"}]
        union = [{"terms": "стерилизация ключ маяк"}]
        self.assertEqual(heuristic_history._bloom(rows), heuristic_history._bloom(union))
        self.assertEqual(heuristic_history._bloom(rows * 50), heuristic_history._bloom(union))

    def test_query_is_repeatable_and_does_not_mutate_index(self):
        index = heuristic_history.build(self.root, self.root)
        before = json.dumps(index, sort_keys=True)
        for question in ("Палладиум сундук", "стерилизация", "Варвен", "и в на"):
            first = heuristic_history.query(index, question)
            self.assertEqual(first, heuristic_history.query(index, question))
        self.assertEqual(before, json.dumps(index, sort_keys=True))

    def test_stale_shard_is_rejected_not_silently_trusted(self):
        heuristic_history.save(self.root, self.root)
        shard = self.root / "archive/heuristic-recall/shard-000.json"
        shard.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            heuristic_history.load(self.root / "archive/heuristic-recall")

    def test_new_turn_is_sourced_before_fold(self):
        heuristic_history.save(self.root, self.root)
        (self.root / "recovery/TURN.json").write_text(json.dumps({
            "id": "s05.text.002", "at": "0006-10-01T22:01:00",
            "player": "Извлекаю аквамарин.", "gm": "Аквамарин вынут.",
            "line": "Аквамарин обнаружен.", "d": {"scene": {}}
        }, ensure_ascii=False), encoding="utf-8")
        packet = scene_memory.fast_packet_from_files(self.root, self.root,
                              action="Как обнаружили аквамарин?")
        self.assertEqual(packet["status"], "text_ahead_pending_fold")
        self.assertEqual(packet["pending_turn"]["id"], "s05.text.002")
        self.assertTrue(any(m["source"] == "recovery/TURN.json"
                            for m in packet["historical_memory"]["matches"]))


if __name__ == "__main__":
    unittest.main()
