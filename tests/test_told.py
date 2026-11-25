import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import npc_memory


def _hot():
    return {"scene": {"present": ["morita"]}, "cast": {"morita": {"name": "Морита Кай"},
                                                      "morioka": {"name": "Мориока Хидэаки"},
                                                      "nori": {"name": "Нори"}}, "_psyche": {}}


TURN = {"id": "s10.text.084", "at": "0010-03-06T04:19:00", "line": "Шики допрашивает Мориту о заказчице."}


class ToldTest(unittest.TestCase):
    def test_plan_for_absent_npc_is_not_an_episode(self):
        hot = _hot()
        inner = {"morioka": {"intent": {"audit": {"goal": "сохранить акты", "due": "0010-03-07T17:00:00"}}},
                 "nori": {"mem": "услышала от гонца о допросе"}}
        npc_memory.apply(hot, TURN, inner)
        self.assertEqual([e["ch"] for e in hot["cast"]["morita"]["memory"]], ["present"])
        self.assertNotIn("memory", hot["cast"]["morioka"], "план за кадром — не опыт NPC")
        self.assertNotIn("memory", hot["cast"]["nori"], "private memo is not a verified delivery")
        incoming = dict(TURN, d={"knowledge_events": [{"actor": "nori", "source": "s10.text.083",
                        "channel": "message", "claim": "Гонец сообщил о допросе."}]})
        npc_memory.apply(hot, incoming, inner)
        self.assertEqual(hot["cast"]["nori"]["memory"][0]["ch"], "told")
        self.assertEqual(hot["cast"]["nori"]["memory"][0]["what"], "Гонец сообщил о допросе.")

    def test_repair_removes_false_told_everywhere(self):
        state = Path(tempfile.mkdtemp())
        for sub in ("archive/deltas", "archive/npc-memory", "recovery/cast"):
            (state / sub).mkdir(parents=True)
        deltas = [{"id": "s10.text.084", "d": {"cast": {"morioka": {"intent": {"audit": {}}}}}},
                  {"id": "s08.text.090", "d": {"cast": {"morioka": {"knows+": ["имя Хирано"], "mem": "назвал Хирано"}}}}]
        (state / "archive/deltas/session-010.jsonl").write_text(
            "\n".join(json.dumps(d, ensure_ascii=False) for d in deltas) + "\n", encoding="utf-8")
        episodes = [{"turn": "s08.text.090", "ch": "told", "what": "назвал Хирано"},
                    {"turn": "s10.text.084", "ch": "told", "what": "допрос Мориты"}]
        (state / "archive/npc-memory/morioka.json").write_text(
            json.dumps({"key": "morioka", "episodes": episodes}, ensure_ascii=False), encoding="utf-8")
        (state / "recovery/cast/morioka.json").write_text(
            json.dumps({"name": "Мориока", "memory": episodes, "memory_total": 9}, ensure_ascii=False), encoding="utf-8")
        (state / "recovery/HOT.json").write_text(json.dumps({"cast": {}}), encoding="utf-8")
        report = npc_memory.repair_told(state)
        self.assertEqual(report["removed"], 1)
        kept = json.loads((state / "archive/npc-memory/morioka.json").read_text(encoding="utf-8"))["episodes"]
        self.assertEqual([e["turn"] for e in kept], ["s08.text.090"])
        card = json.loads((state / "recovery/cast/morioka.json").read_text(encoding="utf-8"))
        self.assertEqual((len(card["memory"]), card["memory_total"]), (1, 8))


if __name__ == "__main__":
    unittest.main()
