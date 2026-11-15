import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import Campaign, handoff_guard, git_state
from rpg_engine.engine import file_sha256

AT="0006-09-27T22:05:34"

class HandoffGuardTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        self.db=self.root/"campaign.sqlite"
        self.game=Campaign.create(self.db,{
            "id":"naruto-shiki","title":"Handoff",
            "player":{"id":"shiki","name":"Шики"},
            "start_at":AT,"location":"Седьмая палата"})
        m={"format":git_state.FORMAT,**self.game.verify(),
           "sha256":file_sha256(self.db)}
        (self.root/"state.json").write_text(json.dumps(m))
        (self.root/"recovery").mkdir()
        self.hot={"format":"npr-shiki-fast-scene/1","session":1,
                  "cursor":{"at":AT,"last_turn_id":None},
                  "runtime":{"sqlite_revision":0,"sqlite_canonical":True},
                  "latest_turn":None}
        self.save()

    def save(self):
        (self.root/"recovery/FAST_SCENE.json").write_text(
            json.dumps(self.hot,ensure_ascii=False))

    def test_synchronized_binary_is_accepted(self):
        r=handoff_guard.audit(self.game,self.root)
        self.assertEqual(r["fallback"],"synchronized")
        self.assertTrue(r["full_audit"])
        self.assertEqual(r["at"],AT)

    def test_stale_hot_cannot_supersede_verified_sqlite(self):
        self.hot["cursor"]["at"]="0006-09-27T22:03:00"
        self.save()
        with self.assertRaisesRegex(ValueError,"older than canonical"):
            handoff_guard.audit(self.game,self.root,full=False)

    def test_new_fallback_turn_without_exact_text_is_rejected(self):
        self.hot["cursor"].update({"at":"0006-09-27T22:07:00",
                                    "last_turn_id":"s4.text.001"})
        self.hot["runtime"]["sqlite_canonical"]=False
        self.save()
        with self.assertRaisesRegex(ValueError,"no full last exact response"):
            handoff_guard.audit(self.game,self.root,full=False)
        self.hot["latest_turn"]={"id":"s4.text.001","player_input":"#смотрю#",
                                  "exact_visible_gm_response":"Саю попыталась отойти, камень треснул."}
        self.save()
        result=handoff_guard.audit(self.game,self.root,full=False)
        self.assertEqual(result["fallback"],"text_ahead_unreplayed")
        self.assertEqual(result["revision"],0)

    def test_live_memory_index_must_agree(self):
        (self.root/"recovery/ACTIVE_MEMORY_INDEX_1283_09_27.json").write_text(
            json.dumps({"sqlite_authoritative":True,"session":1,"sqlite_revision":99}))
        with self.assertRaisesRegex(ValueError,"memory index"):
            handoff_guard.audit(self.game,self.root,full=False)

if __name__=="__main__":
    unittest.main()
