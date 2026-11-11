import json
from pathlib import Path
import tempfile
import unittest

from rpg_engine import Campaign
from rpg_engine.engine import file_sha256
from rpg_engine.git_state import FORMAT
from rpg_engine.knowledge_gate import KnowledgeLeak
from campaigns.shiki.maintenance.prepare_reviewed_turn import main as prepare_reviewed

START = "2000-01-01T11:00:00"


class ReviewedPreparationTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.state = self.root / "game-state"
        self.state.mkdir()
        db = self.state / "campaign.sqlite"
        self.game = Campaign.create(db, {
            "id":"naruto-shiki","title":"Synthetic Naruto test",
            "start_at":START,"location":"Inn",
            "player":{"id":"shiki","name":"Shiki"},
            "npcs":[{"id":"ren","name":"Ren"},{"id":"aki","name":"Aki"}],
        })
        manifest = {"format":FORMAT, **self.game.verify(), "sha256":file_sha256(db)}
        (self.state / "state.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def envelope(self, *, line="Пойдём.", speaker="Рэн", presence=None, body=None):
        presence = presence or "При появлении Шики часовой отвёл взгляд и расправил плечи."
        body = body or "В пальцах Рэна появилась короткая нервная дрожь."
        text = ("**Дата:** 1 июля 1278 года\n**Время:** 11:00\n**День:** понедельник\n"
                "**Место:** гостиница\n\n"+presence+"\n"+body+
                "\n\n**"+speaker+":** — "+line)
        return {
            "format":"NPR-SHIKI-REVIEWED-TURN/1",
            "turn":{
                "id":"t1","expected_revision":0,"at":START,"location":"Inn",
                "player_input":"#смотрю на собеседника#","text":text,"events":[]
            },
            "knowledge_review":{
                "format":"NPR-NPC-KNOWLEDGE-PREFLIGHT/1",
                "lines":[{"actor":"ren" if speaker=="Рэн" else "aki", "text":line, "claims":[]}],
                "p0":{"shiki_presence":presence,"conversation_body_language":body,
                      "combat_present":False}
            }
        }

    def run_envelope(self, obj):
        file = self.root / "reviewed.json"
        file.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        target = self.root / "output"
        prepare_reviewed(self.state, file, target)
        return Campaign(target / "campaign.sqlite")

    def test_exact_turn_passes_both_guards_and_remains_pending(self):
        prepared = self.run_envelope(self.envelope())
        state = prepared.verify()
        self.assertTrue(state["pending"])
        self.assertEqual(state["revision"], 0)
        self.assertEqual(prepared.context(audience="gm")["pending"]["packet"]["id"], "t1")
        self.assertEqual(self.game.verify()["revision"], 0)

    def test_real_unsourced_scroll_line_blocks_before_sqlite_prepare(self):
        x = self.envelope(line="У какой команды нужный нам свиток?",speaker="Аки")
        with self.assertRaisesRegex(KnowledgeLeak, "mentions protected"):
            self.run_envelope(x)
        self.assertFalse((self.root / "output").exists())

    def test_shiki_presence_cannot_be_omitted(self):
        x = self.envelope()
        x["knowledge_review"]["p0"]["shiki_presence"] = "Несуществующая психологическая реакция."
        with self.assertRaisesRegex(KnowledgeLeak, "Shiki psychological presence"):
            self.run_envelope(x)

    def test_body_language_must_have_grounded_excerpt(self):
        x = self.envelope()
        x["knowledge_review"]["p0"]["conversation_body_language"] = "Нет никаких наблюдений за телом Рэна."
        with self.assertRaisesRegex(KnowledgeLeak, "conversation body language"):
            self.run_envelope(x)


if __name__ == "__main__":
    unittest.main()
