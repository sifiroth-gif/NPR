"""Verified historical-search sources after closing a long RPG session."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import heuristic_history

class ClosedReviewIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.archive=self.root/"archive"
        self.archive.mkdir()
        self.body="""# Naruto RPG — session 5

## 0006-10-02T10:11:00 · Северный пост · s5.played.057

**Игрок**

#смотрю#

**Ведущий**

Двойник Кагэ удерживает заложника за дверью.
Он требует ключ и ничего не открыл.

**Ход Шики.**
"""
        p=self.archive/"session-005.md"
        p.write_text(self.body,encoding="utf-8")
        self.export_sha=hashlib.sha256(p.read_bytes()).hexdigest()
        self.idx={"format":"test","export_sha256":self.export_sha,
                  "index":[
                      {"id":"s05.text.057","canonical_id":"s5.played.057",
                       "status":"played","at":"0006-10-02T10:11:00"},
                      {"id":"s05.text.058","status":"ooc_canon_or_amendment"}
                  ]}
        (self.archive/"session-005-index.json").write_text(json.dumps(self.idx))
        (self.archive/"session-005-turns.jsonl").write_text(
            json.dumps({"id":"s05.text.057","kind":"game",
                        "player":"#смотрю#",
                        "gm":"ВОЗМОЖНОЕ НЕПОДТВЕРЖДЁННОЕ СООБЩЕНИЕ",
                        "at":"0006-10-02T10:11:00","line":"старый вариант"})+"\n"+
            json.dumps({"id":"s05.text.058","kind":"ooc",
                        "player":"Отменяй эту сцену","gm":"отменена"})+"\n")

    def test_canonical_export_preferred_over_superseded_hot(self):
        r=heuristic_history._closed_reviewed_export(self.root,5)
        self.assertTrue(r)
        self.assertTrue(all(item["source"]=="archive/session-005.md" for item in r))
        ids=" ".join(x["id"] for x in r)
        self.assertIn("s5.played.057",ids)
        self.assertNotIn("s05.text.058",ids)
        combined=heuristic_history._turn_sessions(self.root)
        self.assertEqual(combined,[])
        self.assertTrue(any("двойник" in x["terms"] for x in r))

    def test_tampered_canonical_export_rejected(self):
        (self.archive/"session-005.md").write_text(self.body+"ТАЙНА ДОПИСАНА\n")
        with self.assertRaisesRegex(ValueError,"SHA mismatch"):
            heuristic_history._closed_reviewed_export(self.root,5)

    def test_orphan_canonical_receipt_rejected(self):
        self.idx["index"][0]["canonical_id"]="s5.played.056"
        (self.archive/"session-005-index.json").write_text(json.dumps(self.idx))
        with self.assertRaisesRegex(ValueError,"disagree with"):
            heuristic_history._closed_reviewed_export(self.root,5)

    def test_source_renaming_does_not_silently_fallback(self):
        (self.archive/"session-005.md").unlink()
        with self.assertRaisesRegex(ValueError,"missing canonical export"):
            heuristic_history._turn_sessions(self.root)

if __name__=="__main__":
    unittest.main()
