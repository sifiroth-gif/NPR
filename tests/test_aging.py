import tempfile
import unittest
from pathlib import Path

from rpg_engine import aging


def _profile(n):
    return {"intent": {}, "intent_done": [{"key": f"p{i}", "status": "fulfilled", "outcome": f"исход {i}"}
                                          for i in range(n)],
            "dread": {"stage": "1", "log": [f"запись {i}" for i in range(n)]}}


class DormantTest(unittest.TestCase):
    def test_long_absent_npc_without_plan_keeps_only_the_last(self):
        state = Path(tempfile.mkdtemp())
        hot = {"cursor": {"id": "s10.text.059"}, "scene": {"present": ["shiki", "sage"]},
               "_psyche": {"old_patient": _profile(4), "sage": _profile(4), "plotter": _profile(4)},
               "_will": {"touched": {"old_patient": "s08.text.050", "sage": "s08.text.050",
                                     "plotter": "s08.text.050"}}}
        hot["_psyche"]["plotter"]["intent"] = {"next_move": {"goal": "сжечь склад", "due": "0010-03-09T00:00:00"}}
        aging.apply(state, hot)
        psyche = hot["_psyche"]
        self.assertEqual(len(psyche["old_patient"]["intent_done"]), 1)
        self.assertEqual(psyche["old_patient"]["dread"]["log"], ["запись 3"])
        self.assertEqual(len(psyche["sage"]["intent_done"]), min(4, aging.NORMAL["intent_done"]), "присутствующий не сжат")
        self.assertEqual(len(psyche["plotter"]["intent_done"]), min(4, aging.NORMAL["intent_done"]), "с живым планом не сжат")
        self.assertIn("p0", psyche["old_patient"]["done_keys"])
        self.assertTrue(aging.load(state, "npc-plans.GM_ONLY.jsonl"))


if __name__ == "__main__":
    unittest.main()


class SessionKeepTest(unittest.TestCase):
    def test_current_session_never_ages_and_restore_brings_it_back(self):
        # 23.11.2026: пять разгрузок за сессию 11 увели её же страхи, планы и линию В-7.
        state = Path(tempfile.mkdtemp())
        log = [{"turn": f"s10.text.{i:03d}", "saw": "x"} for i in range(1, 5)] + \
              [{"turn": f"s11.text.{i:03d}", "saw": "y"} for i in range(1, 9)]
        hot = {"cursor": {"id": "s11.text.074"}, "scene": {"present": ["shiki", "chiyo"]},
               "threads": {"v7": {"state": "В-7", "turn": "s11.text.020"}, "old": {"state": "x", "turn": "s09.text.010"}},
               "_psyche": {"chiyo": {"dread": {"log": list(log)}, "intent_done": []}}}
        aging._pass(state, hot, aging.TIGHT)
        self.assertIn("v7", hot["threads"])
        self.assertNotIn("old", hot["threads"])
        kept = hot["_psyche"]["chiyo"]["dread"]["log"]
        self.assertEqual([e for e in kept if e["turn"].startswith("s11")], log[4:])
        # Ушедшее во время сессии раньше — возвращается.
        aging._append(state, "threads.jsonl", [{"key": "lost", "value": {"state": "z", "turn": "s11.text.030"}, "aged_at": "s11.text.035"}])
        aging._append(state, "npc-dread.GM_ONLY.jsonl", [{"key": "chiyo:0", "npc": "chiyo",
                                                          "value": {"turn": "s11.text.064", "saw": "w"},
                                                          "aged_at": "s11.text.065"}])
        report = aging.restore(state, hot, 11)
        self.assertEqual(report, {"threads.jsonl": 1, "npc-dread.GM_ONLY.jsonl": 1})
        self.assertIn("lost", hot["threads"])
        self.assertEqual(aging.restore(state, hot, 11), {})   # повторно — ничего


class CanonJunkTest(unittest.TestCase):
    def test_single_letter_rules_are_not_corrections(self):
        import json
        from rpg_engine import canon
        state = Path(tempfile.mkdtemp())
        (state / "archive").mkdir()
        rows = [{"turn": "s11.text.073", "rule": ch} for ch in "Поправка"] + \
               [{"turn": "s11.text.073", "rule": "Ранний прорыв Шики сорвал извлечение: Гаара жив."}]
        (state / "archive/shiki-canon.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                                                         encoding="utf-8")
        got = canon.load(state)
        self.assertEqual([r["key"] for r in got], ["s11.text.073#1"])


class CanonPlusStringTest(unittest.TestCase):
    def test_string_canon_plus_is_one_rule(self):
        from rpg_engine import hot as hot_module
        state = {"cursor": {"id": "s11.text.072"}}
        try:
            hot_module._apply_delta(state, {"canon+": "Ранний прорыв Шики сорвал извлечение: Гаара жив."},
                                    {"id": "s11.text.073", "at": "0010-03-15T08:46:37"})
        except Exception:
            pass   # прочие поля дельты здесь не важны
        rules = [c["rule"] for c in state.get("canon", [])]
        self.assertEqual(rules, ["Ранний прорыв Шики сорвал извлечение: Гаара жив."])


class ResyncTest(unittest.TestCase):
    def test_keeper_resync_is_a_must_until_reread(self):
        import json
        from rpg_engine import hot as hot_module
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / hot_module.KEEPER_FILL
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"resync": {"id": "r1", "note": "Гаара жив."}}), encoding="utf-8")
            state = {"session": 11, "cursor": {"id": "s11.text.074"}}
            hot_module._keeper_fill(tmp, state)
            item = hot_module.resync_item(state)
            self.assertIn("archive/session-011-chronicle.md", item)
            self.assertIn("Гаара жив.", item)
            hot_module._keeper_fill(tmp, state)                       # тот же id — не заново
            hot_module._resync_seen(state, {"id": "s11.text.075", "read": ["campaigns/shiki/GM_CORE.md"]})
            self.assertIsNone(hot_module.resync_item(state))
            hot_module._keeper_fill(tmp, state)
            self.assertIsNone(hot_module.resync_item(state))
