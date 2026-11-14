"""Regression tests: archived turns remain source-exact and fully auditable."""
import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import Campaign, sessions, verification, compaction

AT = "0001-07-01T10:00:00"

class JournalCompactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.game=Campaign.create(self.root/"campaign.sqlite",{
            "id":"compaction-test","title":"Compaction regression",
            "start_at":AT,"location":"Тоннель",
            "player":{"id":"hero","name":"Герой"},
        })

    def play(self,turn,text,raw="#наблюдаю#"):
        p={"id":turn,"expected_revision":self.game.verify()["revision"],
           "at":AT,"location":"Тоннель","player_input":raw,"text":text}
        r=self.game.prepare(p);self.game.confirm(turn,r["draft_sha"])

    def test_closed_session_exact_hydration_and_audit(self):
        long=("Здесь важная история. "+("Обычная фраза и реакция NPC. "*200)
              +"Причинная развязка с редким словом ИНДИКАТОР.")
        self.play("one",long,"#посмотреть на печать#")
        out=self.root/"archive"/"session-001.md"
        before=self.game.entry("turn","one")
        result=sessions.close(self.game,1,out)
        self.assertEqual(result["next_session"],2)
        self.assertEqual(self.game.entry("turn","one"),before)
        self.assertTrue(self.game.search("история"))
        self.assertTrue(verification.audit(self.game)["full_audit"])
        with self.game.connection() as con:
            self.assertLess(len(con.execute("SELECT body FROM journal WHERE id='one'").fetchone()[0]),500)
            self.assertEqual(len(compaction.audit(con)),1)
            self.assertEqual(compaction._archive(con,1)[1][1]["body"],long)
        self.play("two","Новая сессия. "+("Повтор. "*100))
        with self.game.connection() as con:
            body=con.execute("SELECT body FROM journal WHERE id='two'").fetchone()[0]
            self.assertGreater(len(body),360)
        self.assertEqual(verification.audit(self.game)["session"],2)
        self.assertEqual(self.game.entry("turn","one")["text"],long)
        self.assertEqual(sessions.close(self.game,1,out),result)

    def test_verbatim_transcript_and_private_checkpoint(self):
        long="Голос капитана. "+("Непостижимые тени возвращаются. "*180)+"Последняя фраза."
        self.play("step",long)
        t={"format":"npr-session-transcript/1","session":1,
           "source":"example:source.md","source_sha256":"1"*64,
           "passages":[{"role":"gm","at":AT,"text":long,
                        "ranges":[[1,1,12,1]],"entities":["hero"]}]}
        path=self.root/"archive"/"session-001.md"
        sessions.close(self.game,1,path,t)
        self.assertEqual(self.game.entry("event","transcript.s1.1")["text"],long)
        self.assertEqual(self.game.entry("turn","step")["text"],long)
        self.assertTrue(verification.audit(self.game)["full_audit"])
        copy=self.root/"copy.sqlite"
        self.game.checkpoint(copy)
        self.assertTrue(verification.audit(Campaign(copy))["full_audit"])
        self.assertTrue(compaction.compact_game(self.game)[0]["already_compacted"])
        with self.game.connection(write=True) as con:
            con.execute("UPDATE archived_journals SET payload=? WHERE session=1",(b'not-a-valid-zlib',))
        with self.assertRaises(ValueError):
            verification.audit(self.game)

if __name__ == "__main__":
    unittest.main()
