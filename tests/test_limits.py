import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import limits


class LimitsTest(unittest.TestCase):
    def test_warns_once_then_reminds_after_gap(self):
        root = Path(tempfile.mkdtemp())
        hot = {"cursor": {"id": "s09.text.050"}}
        got = limits.check(root, hot, hot_bytes=int(limits.HOT_LIMIT * 0.95), secret_bytes=0, engine=root)
        self.assertTrue(got["tell"])
        self.assertIn("HOT", got["items"][0]["what"])
        self.assertTrue(any("закрыть сессию" in a for a in got["items"][0]["acts"]))
        hot["cursor"]["id"] = "s09.text.055"
        self.assertFalse(limits.check(root, hot, hot_bytes=int(limits.HOT_LIMIT * 0.95), secret_bytes=0,
                                      engine=root)["tell"], "не повторять каждый ход")
        hot["cursor"]["id"] = "s09.text.066"
        self.assertTrue(limits.check(root, hot, hot_bytes=int(limits.HOT_LIMIT * 0.95), secret_bytes=0,
                                     engine=root)["tell"])

    def test_quiet_when_within_limits_and_long_session_suggests_close(self):
        root = Path(tempfile.mkdtemp())
        self.assertIsNone(limits.check(root, {"cursor": {"id": "s09.text.010"}}, hot_bytes=1000, secret_bytes=0,
                                       engine=root))
        got = limits.check(root, {"cursor": {"id": "s09.text.115"}}, hot_bytes=1000, secret_bytes=0, engine=root)
        self.assertEqual(got["items"][0]["key"], "session")

    def test_rule_file_only_when_over_budget(self):
        # Близость правил к пределу держат тесты; игроку — только перерасход (23.11.2026).
        root = Path(tempfile.mkdtemp())
        (root / "campaigns/shiki").mkdir(parents=True)
        limit = limits.RULES["campaigns/shiki/SHIKI.md"]
        (root / "campaigns/shiki/SHIKI.md").write_text("ш" * (limit - 1), encoding="utf-8")
        self.assertIsNone(limits.check(root, {"cursor": {"id": "s09.text.010"}}, hot_bytes=0, secret_bytes=0,
                                       engine=root))
        (root / "campaigns/shiki/SHIKI.md").write_text("ш" * (limit + 1), encoding="utf-8")
        got = limits.check(root, {"cursor": {"id": "s09.text.010"}}, hot_bytes=0, secret_bytes=0, engine=root)
        self.assertIn("SHIKI.md", got["items"][0]["what"])

    def test_trim_request_is_consumed(self):
        root = Path(tempfile.mkdtemp())
        (root / "recovery").mkdir()
        (root / limits.TRIM_REQUEST).write_text(json.dumps({"trim": True}), encoding="utf-8")
        self.assertTrue(limits.trim_requested(root))
        self.assertFalse(limits.trim_requested(root))
