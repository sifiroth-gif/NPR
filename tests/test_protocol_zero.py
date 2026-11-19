import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from rpg_engine import protocol_zero as zero


def _state():
    root = Path(tempfile.mkdtemp())
    (root / "recovery").mkdir()
    (root / "memory/_curated").mkdir(parents=True)
    (root / "recovery/HOT.json").write_text(json.dumps({"cursor": {"id": "s09.text.001", "at": "0006-10-05T08:00:00"}}),
                                            encoding="utf-8")
    (root / "AGENTS.md").write_text("x", encoding="utf-8")
    (root / zero.DIRECTIVES).write_text(json.dumps({"standing": [
        {"text": "Общая манера", "scope": "general"}, {"text": "Про Шики", "scope": "shiki"},
        {"text": "Без разметки"}]}, ensure_ascii=False), encoding="utf-8")
    return root


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


PHRASE = hashlib.sha256("testphrase".encode()).hexdigest()


@mock.patch.object(zero, "PHRASE_SHA256", PHRASE)
class ProtocolZeroTest(unittest.TestCase):
    NOW = datetime(2026, 11, 19, 22, 40, tzinfo=zero.MSK)

    def test_idle_without_request_or_command(self):
        root = _state()
        self.assertIsNone(zero.handle(root, now=self.NOW))
        (root / zero.REQUEST).write_text(json.dumps({"command": ""}), encoding="utf-8")
        self.assertIsNone(zero.handle(root, now=self.NOW))
        self.assertFalse((root / zero.REPORT).exists())

    def test_phrase_normalized(self):
        self.assertTrue(zero.verify("  TestPhrase. "))
        self.assertFalse(zero.verify("test phrase"))

    def test_wrong_phrase_rejected_and_cleared(self):
        root = _state()
        (root / zero.REQUEST).write_text(json.dumps({"command": "nope123"}), encoding="utf-8")
        self.assertEqual(zero.handle(root, now=self.NOW)["result"], "rejected")
        self.assertIn("не распознана", (root / zero.REPORT).read_text(encoding="utf-8"))
        self.assertEqual(json.loads((root / zero.REQUEST).read_text(encoding="utf-8"))["command"], "")

    def test_dry_run_changes_nothing_but_its_own_files(self):
        root = _state()
        (root / zero.REQUEST).write_text(json.dumps({"command": "testphrase", "mode": "live"}), encoding="utf-8")
        before = {k: v for k, v in _files(root).items() if not k.startswith("recovery/ZERO")}
        self.assertEqual(zero.handle(root, now=self.NOW)["result"], "dry-run")
        after = {k: v for k, v in _files(root).items() if not k.startswith("recovery/ZERO")}
        self.assertEqual(before, after, "пробный прогон не трогает игру")
        text = (root / zero.REPORT).read_text(encoding="utf-8")
        self.assertIn("archive/shiki-20261119", text)
        self.assertIn("Ничего не изменено", text)
        self.assertIn("- Общая манера", text.split("Уйдут в архив")[0])
        self.assertIn("Без разметки *(не размечено)*", text)
        self.assertNotIn("testphrase", (root / zero.REQUEST).read_text(encoding="utf-8"))
        last = json.loads((root / zero.REQUEST).read_text(encoding="utf-8"))["last"]
        self.assertTrue(last["live_requested"])
        self.assertFalse(last["live_enabled"])

    def test_live_stays_disabled(self):
        self.assertFalse(zero.LIVE)
