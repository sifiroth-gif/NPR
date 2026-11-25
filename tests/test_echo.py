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
        self.assertEqual(echo.pick(self.root, hot, texts=["Тихо."]), got, "не вплетено — держится HOLD ходов")
        hot["cursor"]["id"] = "s09.text.086"
        self.assertEqual(echo.pick(self.root, hot, texts=["Хирано, водосборник, контракты."]), got,
                         "общие со сценой слова — не доказательство, что эхо вплетено")
        hot["cursor"]["id"] = "s09.text.087"
        woven = ["Шики вспомнил о закрытых поставках через старый узел."]
        self.assertIsNone(echo.pick(self.root, hot, texts=woven), "вплетено — снято, новое не раньше GAP")
        self.assertEqual(hot["fold"]["echo"]["used_count"], 1)
        hot["cursor"]["id"] = "s09.text.089"
        self.assertIsNone(echo.pick(self.root, hot), "не чаще раза в GAP ходов")

    def test_echo_counts_only_as_event(self):
        """Эхо — событие, не слово (s13, 24.11.2026): память и речь Шики — упоминание; весть — событие."""
        hot = _hot()
        got = echo.pick(self.root, hot)
        self.assertIsNotNone(got)
        hot["cursor"]["id"] = "s09.text.085"
        recall = [{"id": "s09.text.085", "gm": "Шики вспомнил о закрытых поставках через старый узел.\n"
                                          "**Шики:** Закрытые поставки. Помню."}]
        self.assertEqual(echo.pick(self.root, hot, turns=recall), got, "воспоминание и речь Шики — не исполнение")
        self.assertEqual(hot["fold"]["echo"].get("used_count", 0), 0)
        self.assertEqual(hot["fold"]["echo"]["mentioned_count"], 1)
        hot["cursor"]["id"] = "s09.text.086"
        event = [{"id": "s09.text.086", "gm": "К вечеру гонец принёс записку: закрытые поставки снова идут."}]
        self.assertIsNone(echo.pick(self.root, hot, turns=event), "весть в сцене — эхо исполнено")
        self.assertEqual(hot["fold"]["echo"]["used_count"], 1)

    def test_echo_event_kinds(self):
        from rpg_engine.heuristic_history import stem
        state = {"fresh": [stem("сэйдзи"), stem("задолжал")]}
        hot = {"cast": {"seiji": {"name": "Сэйдзи"}}}
        use = lambda gm, d=None: echo._event_use(state, hot, [{"gm": gm, "d": d or {}}])
        self.assertEqual(use("**Сэйдзи:** Долг за мной."), (True, True), "говорит — событие")
        self.assertEqual(use("Тихо.", {"scene": {"present": ["shiki", "seiji"]}}), (True, True), "в составе — событие")
        self.assertEqual(use("**Шики:** Сэйдзи задолжал."), (False, True), "речь Шики — упоминание")
        self.assertEqual(use("Шики вспомнил, как Сэйдзи задолжал."), (False, True), "память — упоминание")
        self.assertEqual(use("У ворот ждал Сэйдзи."), (True, True), "появился — событие")
        self.assertEqual(use("Дождь."), (False, False))

    def test_thread_of_present_npc_is_not_an_echo(self):
        hot = _hot()
        hot["scene"]["present"] = ["shiki", "hirano_seiji"]
        hot["cast"] = {"hirano_seiji": {"name": "Хирано Сэйдзи"}}
        self.assertIsNone(echo.pick(self.root, hot), "Хирано в сцене — его нить не давнее")

    def test_no_echo_without_resonance(self):
        self.assertIsNone(echo.pick(self.root, _hot(situation="Тихая ночь, дождь стучит по крыше.")))


class AgingTest(unittest.TestCase):
    def test_old_thread_ages_but_live_one_stays_and_done_keys_kept(self):
        root = Path(tempfile.mkdtemp())
        hot = {"cursor": {"id": "s09.text.083"}, "scene": {"present": ["shiki"], "situation": "v7 рядом"},
               "threads": {"old": {"state": "x", "turn": "s08.text.010"}, "v7": {"state": "y", "turn": "s08.text.010"},
                           "fresh": {"state": "z", "turn": "s09.text.080"}},
               "_psyche": {"n": {"intent_done": [{"key": f"p{i}", "goal": "g", "status": "fulfilled"} for i in range(10)]}}}
        report = aging.apply(root, hot)
        self.assertEqual(set(hot["threads"]), {"v7", "fresh"})
        self.assertEqual(report["threads"], 1)
        self.assertIn("old", aging.load(root, "threads.jsonl"))
        self.assertEqual(len(hot["_psyche"]["n"]["intent_done"]), aging.NORMAL["intent_done"])
        self.assertIn("p0", hot["_psyche"]["n"]["done_keys"])
        self.assertNotIn("goal", hot["_psyche"]["n"]["intent_done"][0], "старые исходы — кратко")
