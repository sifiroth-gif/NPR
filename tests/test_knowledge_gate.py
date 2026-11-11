import json
from pathlib import Path
import tempfile
import unittest

from rpg_engine import Campaign
from rpg_engine.knowledge_gate import (
    FORMAT, KnowledgeLeak, actor_ledger, dialogue_lines, validate_scene,
)

START = "2001-07-01T18:00:00"
POLICY = {
    "speakers": {"Рэн": "ren", "Аки": "aki"},
    "topics": {
        "exam.scrolls": {"triggers": ["свит"]},
        "shiki.beam": {"triggers": ["балк"]},
    },
}


def text_review(actor, line, claims=None):
    return {"format":FORMAT, "lines":[
        {"actor":actor, "text":line, "claims":list(claims or [])}
    ]}


class ActorKnowledgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = Path(temp.name) / "campaign.sqlite"
        self.game = Campaign.create(self.db, {
            "id":"knowledge-test","title":"Knowledge test","start_at":START,
            "location":"Hotel", "player":{"id":"shiki","name":"Shiki"},
            "npcs":[{"id":"ren","name":"Ren"}, {"id":"aki","name":"Aki"}],
        })

    def acquired(self, actor, topic, *, evidence="reported", certainty="certain"):
        packet = {
            "id":"test.001","expected_revision":0,"at":START,
            "location":"Hotel","player_input":"#рассказываю#",
            "text":"[Восстановление из сыгранного диалога] Игрок поделился сведением.",
            "events":[{
                "id":"test.source","at":START,"visibility":"player_known",
                "summary":"Герой сообщил сведения при свидетелях.",
                "operations":[{
                    "op":"set_belief","actor":actor,"topic":topic,
                    "belief":"Сведения получены личным разговором",
                    "evidence":evidence,"certainty":certainty,
                    "visibility":"gm_only",
                }]
            }]
        }
        receipt = self.game.prepare(packet)
        self.game.confirm(packet["id"], receipt["draft_sha"])

    def test_unsourced_scroll_rule_cannot_be_spoken_by_aki(self):
        line = "Пока не выясним, у какой команды нужный нам свиток."
        draft = "**Аки:** — " + line
        with self.assertRaisesRegex(KnowledgeLeak, "mentions protected"):
            validate_scene(self.game, draft, text_review("aki", line), POLICY)
        with self.assertRaisesRegex(KnowledgeLeak, "no personal belief"):
            validate_scene(self.game, draft, text_review("aki", line, [
                {"topic":"exam.scrolls","stance":"assert"}
            ]), POLICY)

    def test_unreported_rescue_cannot_be_spoken_by_ren(self):
        line = "Наш медик снова получил балкой по плечу."
        draft = "**Рэн:** — " + line
        with self.assertRaisesRegex(KnowledgeLeak, "no personal belief"):
            validate_scene(self.game, draft, text_review("ren", line, [
                {"topic":"shiki.beam","stance":"assert"}
            ]), POLICY)

    def test_actor_scoped_report_allows_only_its_real_recipient(self):
        self.acquired("ren", "shiki.beam")
        line = "Ты получил балкой по плечу."
        claims = [{"topic":"shiki.beam","stance":"assert"}]
        good = validate_scene(self.game, "**Рэн:** — "+line,
                              text_review("ren", line, claims), POLICY)
        self.assertEqual(good["status"], "ready")
        with self.assertRaisesRegex(KnowledgeLeak, "no personal belief"):
            validate_scene(self.game, "**Аки:** — "+line,
                           text_review("aki", line, claims), POLICY)
        self.assertEqual(len(actor_ledger(self.game, "ren")), 1)
        self.assertEqual(len(actor_ledger(self.game, "aki")), 0)

    def test_reliable_source_required_for_claim(self):
        self.acquired("ren", "exam.scrolls", evidence="inferred", certainty="uncertain")
        line = "Нам нужен свиток."
        with self.assertRaisesRegex(KnowledgeLeak, "presented as certain"):
            validate_scene(self.game, "**Рэн:** — "+line,
                           text_review("ren", line, [
                               {"topic":"exam.scrolls","stance":"assert"}]), POLICY)
        question = "А если там нужны свитки?"
        ready = validate_scene(self.game, "**Рэн:** — "+question,
                               text_review("ren", question, [
                                   {"topic":"exam.scrolls","stance":"suspect"}]), POLICY)
        self.assertEqual(ready["lines_checked"], 1)

    def test_all_dialogue_must_be_reviewed_and_matches_actual_text(self):
        d = "**Рэн:** — Пойдём.\n**Аки:** — Согласна."
        with self.assertRaisesRegex(KnowledgeLeak, "actual dialogue has"):
            validate_scene(self.game, d, text_review("ren", "Пойдём."), POLICY)
        with self.assertRaisesRegex(KnowledgeLeak, "does not match"):
            validate_scene(self.game, "**Аки:** — Согласна.",
                           text_review("ren", "Согласна."), POLICY)
        self.assertEqual(len(dialogue_lines(d)), 2)

    def test_ordinary_dialogue_and_scene_without_dialogue_are_accepted(self):
        line = "Пойдём в гостиницу."
        self.assertEqual(validate_scene(self.game, "**Рэн:** — "+line,
                         text_review("ren", line), POLICY)["status"], "ready")
        empty = validate_scene(self.game, "Вечерело.",
                               {"format":FORMAT,"lines":[]}, POLICY)
        self.assertEqual(empty["lines_checked"], 0)

    def test_unsupported_claim_does_not_use_global_fact(self):
        packet = {
            "id":"test.001","expected_revision":0,"at":START,
            "location":"Hotel","player_input":"#говорю#",
            "text":"[Восстановление из сыгранного диалога] Истина наблюдаема герою.",
            "events":[{
                "id":"test.fact","at":START,"visibility":"player_known",
                "summary":"Только Шики видел балку.",
                "operations":[{
                    "op":"set_fact","subject":"shiki","name":"beam_rescue",
                    "value":{"truth":True},"visibility":"player_known",
                }]
            }]
        }
        r = self.game.prepare(packet)
        self.game.confirm(packet["id"], r["draft_sha"])
        line = "Шики получил балкой по плечу."
        with self.assertRaisesRegex(KnowledgeLeak, "no personal belief"):
            validate_scene(self.game, "**Аки:** — "+line,
                           text_review("aki", line,[{"topic":"shiki.beam","stance":"assert"}]), POLICY)


if __name__ == "__main__":
    unittest.main()
