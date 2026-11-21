import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import session_close


def _log(root, rows):
    path = Path(root) / "archive/session-010-turns.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _row(n, at, **extra):
    return {"id": f"s10.text.{n:03}", "at": at, "place": "Трава", "player": "p", "gm": "сцена", **extra}


class SessionCloseTest(unittest.TestCase):
    def test_rows_accept_ordered_log_with_ooc(self):
        root = tempfile.mkdtemp()
        _log(root, [_row(1, "0010-03-01T08:00:00"), _row(2, "0010-03-01T07:00:00", kind="ooc", gm="", place=""),
                    _row(3, "0010-03-01T09:00:00")])
        source, rows, digest = session_close.rows(root, 10)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(digest), 64)

    def test_rows_reject_gap_and_time_regression(self):
        root = tempfile.mkdtemp()
        _log(root, [_row(1, "0010-03-01T08:00:00"), _row(3, "0010-03-01T09:00:00")])
        with self.assertRaises(ValueError):
            session_close.rows(root, 10)
        _log(root, [_row(1, "0010-03-01T08:00:00"), _row(2, "0010-03-01T07:00:00")])
        with self.assertRaisesRegex(ValueError, "время назад"):
            session_close.rows(root, 10)

    def test_handle_idle_without_request_and_reports_failure(self):
        root = Path(tempfile.mkdtemp())
        self.assertIsNone(session_close.handle(root))
        (root / "recovery").mkdir()
        (root / session_close.REQUEST).write_text(json.dumps({"close": True}), encoding="utf-8")
        result = session_close.handle(root)
        self.assertEqual(result["result"], "failed")
        self.assertIn("не закрыта", (root / session_close.REPORT).read_text(encoding="utf-8"))
        self.assertIsNone(json.loads((root / session_close.REQUEST).read_text(encoding="utf-8"))["close"])
