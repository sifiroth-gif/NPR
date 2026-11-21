import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import aging, echo


def _hot(turn="s09.text.083", situation="Караван у старого водосборника; Хирано и контракт №1184."):
    return {"cursor": {"id": turn, "place": "Страна Ветра, водосборник"},
            "scene": {"present": ["shiki"], "situation": situation}, "threads": {}, "things": {}}


class EchoTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        rows = [{"key": "hirano_contracts", "aged_at": "s08.text.100",
                 "value": {"state": "Хирано держал закрытые контракты на поставку С-17 через водосборник.",
                           "turn": "s08.text.050"}}]
        rows += [{"key": f"filler{i}", "aged_at": "s08.text.100",
                  "value": {"state": f"Обычная заметка номер {i} о погоде и дороге.", "turn": "s08.text.050"}}
                 for i in range(30)]
        path = self.root / aging.AGED_DIR / "threads.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    def test_picks_resonant_old_thread_and_rate_limits(self):
        hot = _hot()
        got = echo.pick(self.root, hot)
        self.assertIsNotNone(got)
        self.assertIn("Хирано", got["what"])
        self.assertEqual(echo.pick(self.root, hot), got, "та же свёртка — то же эхо")
        hot["cursor"]["id"] = "s09.text.085"
        self.assertIsNone(echo.pick(self.root, hot), "не чаще раза в GAP ходов")

    def test_no_echo_without_resonance(self):
        self.assertIsNone(echo.pick(self.root, _hot(situation="Тихая ночь, дождь стучит по крыше.")))


class AgingTest(unittest.TestCase):
    def test_old_thread_ages_but_live_one_stays_and_done_keys_kept(self):
        root = Path(tempfile.mkdtemp())
        hot = {"cursor": {"id": "s09.text.083"}, "scene": {"present": ["shiki"], "situation": "v7 рядом"},
               "threads": {"old": {"state": "x", "turn": "s08.text.010"}, "v7": {"state": "y", "turn": "s08.text.010"},
                           "fresh": {"state": "z", "turn": "s09.text.080"}},
               "_psyche": {"n": {"intent_done": [{"key": f"p{i}", "goal": "g", "status": "fulfilled"} for i in range(6)]}}}
        report = aging.apply(root, hot)
        self.assertEqual(set(hot["threads"]), {"v7", "fresh"})
        self.assertEqual(report["threads"], 1)
        self.assertIn("old", aging.load(root, "threads.jsonl"))
        self.assertEqual(len(hot["_psyche"]["n"]["intent_done"]), 3)
        self.assertIn("p0", hot["_psyche"]["n"]["done_keys"])
        self.assertNotIn("goal", hot["_psyche"]["n"]["intent_done"][0], "старые исходы — кратко")
