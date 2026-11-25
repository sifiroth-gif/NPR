import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import historical_regression as h


class CorpusRegressionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "archive/deltas").mkdir(parents=True)

    def write(self, path, rows):
        (self.root / path).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def test_join_and_missing_delta_are_distinct(self):
        first = {"id": "s04.text.001", "at": "0006-10-01T07:10:00",
                 "place": "place", "player": "", "gm": "Opening"}
        second = dict(first, id="s04.text.002", player="hello")
        self.write("archive/session-004-turns.jsonl", [first, second])
        self.write("archive/deltas/session-004.jsonl", [{"id": first["id"], "at": first["at"], "d": {}}])
        report = h.scan(self.root)
        self.assertEqual(report, {"prose": 2, "deltas": 1, "validated": 1, "prose_only": 1, "delta_only": 0, "errors": []})

    def test_invalid_delta_and_timestamp_are_reported(self):
        row = {"id": "s04.text.001", "at": "0006-10-01T07:10:00",
               "place": "place", "player": "x", "gm": "y"}
        self.write("archive/session-004-turns.jsonl", [row])
        self.write("archive/deltas/session-004.jsonl", [{"id": row["id"], "at": "0006-10-01T07:11:00", "d": {"heal": "bad"}}])
        self.assertEqual(len(h.scan(self.root)["errors"]), 2)

    def test_duplicate_and_protected_hash(self):
        row = {"id": "s04.text.001"}
        path = "archive/session-004-turns.jsonl"
        self.write(path, [row, row])
        self.assertTrue(h.scan(self.root)["errors"])
        before = h.hashes(self.root)
        self.write(path, [row])
        with self.assertRaises(AssertionError):
            h.check_hashes(self.root, before)
