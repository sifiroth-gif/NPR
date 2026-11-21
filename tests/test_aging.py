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
        self.assertEqual(len(psyche["sage"]["intent_done"]), aging.NORMAL["intent_done"], "присутствующий не сжат")
        self.assertEqual(len(psyche["plotter"]["intent_done"]), aging.NORMAL["intent_done"], "с живым планом не сжат")
        self.assertIn("p0", psyche["old_patient"]["done_keys"])
        self.assertTrue(aging.load(state, "npc-plans.GM_ONLY.jsonl"))


if __name__ == "__main__":
    unittest.main()
