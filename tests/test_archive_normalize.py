import json
import sqlite3
import tempfile
import unittest
import zlib
from pathlib import Path

from rpg_engine import archive_normalize as an

TRANSCRIPT = """# Naruto RPG — Мугами Шики

Сессия 1 · ревизии 0–84

**Ведущий**

**Дата\\:** 1 июля
**Время\\:** 14:10
**Место\\:** Конохагакуре, Академия шиноби, коридор

Жара.

**Игрок**

#встаю#

**Ведущий**

**Вторник, 2 июля 1278 года. 15:08.**
**Конохагакуре. Центральная башня, комната 214.**

Шики лёг спать.

**Ведущий**

### Понедельник, 8 июля — воскресенье, 14 июля 1278 года

Неделя тренировок.

**Игрок**

первая часть

**Игрок**

вторая часть

**Ведущий**

Ответ без шапки.
"""


def journal_db(path, session, turns):
    db = sqlite3.connect(path)
    db.execute("create table journal (seq, id, kind, session, at, body, player_input, details)")
    db.execute("create table archived_journals (session, payload)")
    full = []
    for seq, (legacy, at, body, player, details) in enumerate(turns, 1):
        db.execute("insert into journal values (?,?,?,?,?,?,?,?)",
                   (seq, legacy, "turn", session, at, body[:10], player[:5], json.dumps(details)))
        full.append({"id": legacy, "kind": "turn", "body": body, "player_input": player, "seq": seq})
    full.append({"id": "transcript.x", "kind": "event", "body": "не ход", "seq": 99})
    db.execute("insert into archived_journals values (?,?)",
               (session, zlib.compress(json.dumps(full, ensure_ascii=False).encode())))
    db.commit()
    db.close()


class ArchiveNormalizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "archive").mkdir()
        (self.root / "archive/session-001.md").write_text(TRANSCRIPT, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_one_turns_follow_transcript_answers(self):
        rows = an.session_one(self.root)
        self.assertEqual([r["id"] for r in rows], ["s01.text.001", "s01.text.002", "s01.text.003",
                                                     "s01.text.004"])
        self.assertEqual(rows[0]["player"], "")
        self.assertEqual(rows[0]["at"], "0001-07-01T14:10:00")
        self.assertEqual(rows[0]["place"], "Конохагакуре, Академия шиноби, коридор")
        self.assertEqual(rows[1]["player"], "#встаю#")
        self.assertEqual(rows[1]["at"], "0001-07-02T15:08:00")
        self.assertEqual(rows[1]["place"], "Конохагакуре. Центральная башня, комната 214")
        self.assertEqual(rows[2]["player"], "")
        self.assertEqual(rows[2]["at"], "0001-07-08T00:00:00")
        self.assertEqual(rows[3]["player"], "первая часть\n\nвторая часть")
        self.assertEqual(rows[3]["gm"], "Ответ без шапки.")
        self.assertEqual(rows[3]["at"], rows[2]["at"])
        joined = "\n".join(r["player"] + "\n" + r["gm"] for r in rows)
        for line in ("Жара.", "Шики лёг спать.", "Неделя тренировок."):
            self.assertIn(line, joined)

    def test_played_session_uses_full_text_and_merges_parts(self):
        journal_db(self.root / "campaign.sqlite", 3, [
            ("s3.played.002", "0006-09-26T09:29:06", "Полный ответ два.", "заявка два",
             {"location": "Коллектор", "source_ref": "archive/session-003-source.md#L1-L9",
              "source_status": "exact"}),
            ("s3.played.007.p1", "0006-09-26T09:41:53", "Часть первая.", "",
             {"location": "Коллектор", "source_ref": "archive/session-003-source.md#L10-L20",
              "source_status": "exact"}),
            ("s3.played.007.p2", "0006-09-26T09:41:53", "Часть вторая.", "",
             {"location": "Коллектор", "source_ref": "archive/session-003-source.md#L21-L30",
              "source_status": "exact"}),
            ("s3.played.009", "0006-09-26T10:00:00", "[Восстановление из сыгранного диалога] нет ответа",
             "заявка", {"location": "Коллектор"}),
            ("s3.played.010", "0006-09-26T10:05:00", "Исправленный ответ.", "вопрос",
             {"location": "Палата", "source_ref": "archive/session-003-source.md#L40-L50; "
                                                  "recovery/corrections/fix.json"}),
        ])
        rows = an.played_session(self.root, 3)
        self.assertEqual([r["id"] for r in rows], ["s03.text.002", "s03.text.007", "s03.text.009",
                                                     "s03.text.010"])
        self.assertEqual(rows[0]["gm"], "Полный ответ два.")
        self.assertEqual(rows[0]["player"], "заявка два")
        self.assertEqual(rows[0]["place"], "Коллектор")
        self.assertEqual(rows[1]["gm"], "Часть первая.\n\nЧасть вторая.")
        self.assertEqual(rows[1]["legacy_id"], ["s3.played.007.p1", "s3.played.007.p2"])
        self.assertEqual(rows[2]["status"], "reconstructed")
        self.assertEqual(rows[3]["status"], "corrected")
        self.assertEqual(rows[3]["source"], ["archive/session-003-source.md#L40-L50",
                                             "recovery/corrections/fix.json"])
        self.assertEqual(an.id_map(self.root)["s3.played.007.p2"], "s03.text.007")


if __name__ == "__main__":
    unittest.main()
