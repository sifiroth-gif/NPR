"""Regression: a GM must not sell earlier NPC disclosures as new revelations."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rpg_engine import scene_gate  # noqa: E402


def turn(index, player, line, gm):
    return {
        "schema": "npr-turn/2",
        "id": "s07.text.%03d" % index,
        "at": "0006-10-02T22:%02d:00" % (index % 60),
        "player": player, "line": line, "gm": gm,
        "place": "central office",
    }


class SceneGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "recovery").mkdir()
        (self.root / "archive").mkdir()
        self.a = turn(12, "зачем послали Тэцу?",
                      "Аракава признал мотив изъятия В-7: зависимость от С-17, отверг заказ убийства, сообщил о Тэцу.",
                      "Аракава: Я направил Тэцу, потому что мне был нужен С-17. Приказ об убийстве прошёл через снабжение.")
        self.b = turn(13, "зачем, Аракава?",
                      "Аракава подтвердил, что послал Тэцу, и предъявил папку с маршрутом согласований по В-7.",
                      "Аракава: Я предъявляю запрос на выдачу В-7 с подписями и маршрутом согласования.")
        self.c = turn(29, "придут ли за вами?",
                      "Шики оставил пробирку себе; Аракава опасается последствий очередной пропущенной дозы С-17.",
                      "Аракава: Они придут, если я не приму следующую дозу.")
        self.d = turn(30, "о чём же вы прежде молчали?",
                      "Аракава не повторяет старые сведения; различает сказанное Шики и официальные показания.",
                      "Аракава: Вы это уже знаете, но я не давал официальных показаний.")
        (self.root / "archive/session-007-turns.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in
                      [self.a, self.b, self.c, self.d]) + "\n", encoding="utf-8")
        (self.root / "recovery/HOT.json").write_text(json.dumps({
            "session": 7,
            "cursor": {"id": "s07.text.030", "seq": 30},
            "cast": {"arakawa": {"name": "Аракава Дзюн", "knows": [
                "Шики говорил, что может восстановить С-17."
            ]}},
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "recovery/TURN.json").write_text(
            json.dumps(self.d, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_old_disclosures_are_recalled_for_vague_question(self):
        packet = scene_gate.prepare(
            self.root, player_action="О чём же вы прежде молчали?",
            actors=["arakawa"])
        seen = {r["turn"] for r in packet["actors"]["arakawa"]["played_disclosures"]}
        self.assertIn("s07.text.012", seen)
        self.assertIn("s07.text.013", seen)
        self.assertIn("s07.text.012", packet["review_required"])
        self.assertIn("s07.text.013", packet["review_required"])
        self.assertEqual(packet["actors"]["arakawa"]["known_vs_disclosed"],
                         "An actor's knowledge does not mean the actor told the player.")

    def test_repeated_confession_is_not_allowed_without_review(self):
        packet = scene_gate.prepare(
            self.root, player_action="О чём вы прежде молчали?",
            actors=["arakawa"])
        flawed = ("Аракава впервые признал, что добивался В-7 ради С-17, "
                  "отправлял Тэцу и предъявил папку с маршрутом согласований.")
        report = scene_gate.review_draft(packet, flawed, new_revelation=True)
        self.assertFalse(report["ready"])
        self.assertIn("s07.text.012", report["errors"][0])
        self.assertIn("s07.text.013", report["errors"][0])
        acknowledged = scene_gate.review_draft(
            packet, flawed, new_revelation=True,
            reviewed=packet["review_required"])
        self.assertTrue(acknowledged["ready"])
        # A pass proves the sources were acknowledged, not that the GM
        # actually interpreted them correctly. That is a human GM decision.
        self.assertIn("overlap_candidates_not_proof", acknowledged)

    def test_turn_override_wins_over_stale_archive_and_no_duplicate(self):
        revised = dict(self.d)
        revised["gm"] = "Аракава: Я уже отвечал. Эта версия новая."
        (self.root / "recovery/TURN.json").write_text(
            json.dumps(revised, ensure_ascii=False), encoding="utf-8")
        hot, rows = scene_gate.played_turns(self.root)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1]["gm"], revised["gm"])

    def test_graph_edges_are_order_not_a_claim_of_causality(self):
        packet = scene_gate.prepare(
            self.root, player_action="Что сказал Аракава прежде?",
            actors=["arakawa"])
        graph = packet["graph"]
        self.assertTrue(graph["nodes"])
        self.assertTrue(graph["edges"])
        self.assertTrue(all(edge["status"] == "source_order_only" for edge in graph["edges"]))

    def test_unrelated_actors_do_not_inherit_arakawa_disclosures(self):
        packet = scene_gate.prepare(
            self.root, player_action="Что известно о В-7?",
            actors=["takagi"])
        self.assertFalse(packet["actors"]["takagi"]["played_disclosures"])

    def test_plain_dialogue_not_forced_into_history_review(self):
        packet = scene_gate.prepare(
            self.root, player_action="Повернитесь к двери.",
            actors=["arakawa"])
        self.assertEqual(packet["review_required"], [])
        self.assertTrue(scene_gate.review_draft(packet,
                         "Он повернулся к двери.")["ready"])


if __name__ == "__main__":
    unittest.main()
