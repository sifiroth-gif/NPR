import json
import unittest

from rpg_engine import hot, lore, nextpack
from tests import test_hot
from tests.test_hot import turn

ENTRY = {"id": "byoton_shiton", "title": "Бётон и Шитон", "terms": ["бётон", "шитон", "ситон"],
         "wield": ["shiki", "nemera", "utsuro"], "world": "Миру неизвестны.",
         "heard": {"seina": "s05.text.030"}}


def state():
    return {"cast": {"seina": {"name": "Кирисава Сэйна"}, "nurse": {"name": "Помощница Сэйны"}},
            "_psyche": {"nemera": {"name": "Немера"}, "utsuro": {"name": "Уцуро"}}}


def play(gm, player="#жду#", number=36):
    return {"id": f"s08.text.{number:03}", "player": player, "gm": gm}


class LoreTest(unittest.TestCase):
    def test_terms_are_word_starts_and_concrete_is_not_byoton(self):
        self.assertTrue(lore.mentions(ENTRY, "Чакра Бётона вошла в иглу"))
        self.assertTrue(lore.mentions(ENTRY, "первый отклик Ситона"))
        self.assertFalse(lore.mentions(ENTRY, "кресло на бетонном основании"))

    def test_npc_who_heard_cannot_wield(self):
        gm = ("— Последствия последнего применения Бётона могут объяснить усиление.\n\n"
              "— Существование чёрной крови не даёт мне права испытывать на тебе Бётон.")
        notes = lore.review(state(), play(gm), [ENTRY], ["shiki", "seina", "nemera"], ["shiki", "seina"])
        self.assertEqual(len(notes), 1, "рассказ о применении Шики — не умение")
        self.assertIn("испытывать на тебе Бётон", notes[0]["text"])
        self.assertIn("владеют только Шики, Немера, Уцуро", notes[0]["text"])
        self.assertIn("Кирисава Сэйна знает лишь пересказ", notes[0]["text"])

    def test_npc_who_never_heard_is_a_leak(self):
        gm = "**Помощница:** — Это ведь Шитон у него, да?"
        notes = lore.review(state(), play(gm), [ENTRY], ["shiki", "seina", "nurse"], ["shiki", "seina", "nurse"])
        self.assertEqual(len(notes), 1)
        self.assertIn("Утечка знания", notes[0]["text"])
        self.assertIn("Помощница Сэйны", notes[0]["text"])

    def test_shiki_naming_it_in_this_turn_is_not_a_leak(self):
        gm = ("— Я считаю, что это воздействие встроенных кеккей генкай — Бётона и Шитона.\n\n"
              "**Помощница:** — Шитон? Никогда о таком не слышала.")
        player = "Я считаю что это воздействие встроенного кекей генкая бётон и Шитон"
        notes = lore.review(state(), play(gm, player), [ENTRY], ["shiki", "nurse"], ["shiki", "nurse"])
        self.assertEqual(notes, [])

    def test_wielders_and_mental_speech_are_skipped(self):
        gm = "**Немера, мысленно:** — Бётон не знает твоих названий.\n\n***— Шитон помнит.***"
        self.assertEqual(lore.review(state(), play(gm), [ENTRY], ["shiki", "nurse", "nemera"], ["shiki"]), [])

    def test_track_and_fences(self):
        h = state()
        t = play("— Бётон. Заражение.", "Бётон. Заражение: воспроизведение патогена")
        self.assertEqual(lore.track(h, t, [ENTRY], ["shiki", "seina", "nurse", "nemera"]),
                         [("byoton_shiton", "nurse")])
        lines = lore.fences(h, [ENTRY], ["shiki", "seina", "nurse"])
        self.assertIn("Кирисава Сэйна — слышал(а) с s05.text.030", lines[0])
        self.assertIn("Помощница Сэйны — слышал(а) с s08.text.036", lines[0])
        self.assertEqual(lore.fences(h, [ENTRY], ["shiki", "nemera"]), [])


class NextFoldTest(unittest.TestCase):
    setUp, tearDown = test_hot.HotFilesTest.setUp, test_hot.HotFilesTest.tearDown
    git, commit, put = test_hot.HotFilesTest.git, test_hot.HotFilesTest.commit, test_hot.HotFilesTest.put

    def test_fold_writes_small_next_pack_with_checks_and_sha(self):
        self.put(turn(45, "0006-10-01T07:14:00", review={"predecessor": "s04.text.044", "sources": ["x"]},
                      read=["npc/nori"], d={"scene": {"situation": "x" * 1000}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        pack = json.loads((self.root / nextpack.PATH).read_text(encoding="utf-8"))
        brief = json.loads((self.root / "recovery/GM_BRIEF.json").read_text(encoding="utf-8"))
        self.assertEqual(pack["cursor"], "s04.text.045")
        self.assertEqual(pack["next_id"], "s04.text.046")
        self.assertEqual(pack["turn_file"], brief["turn_file"]["sha"])
        write = [c for c in pack["checks"] if c.startswith("[write]")]
        self.assertEqual(len(write), 1)
        self.assertIn("sources не пиши", write[0])
        self.assertIn("situation 1000 знаков", write[0])
        self.assertLess(len((self.root / nextpack.PATH).read_bytes()), nextpack.HARD_BYTES)

    def test_repeated_page_read_is_noted(self):
        self.put(turn(45, "0006-10-01T07:14:00", read=["npc/nori"], d={"scene": {}}))
        hot.fold(self.root)
        self.put(turn(46, "0006-10-01T07:15:00", read=["npc/nori"], d={"scene": {}}))
        hot.fold(self.root)
        pack = json.loads((self.root / nextpack.PATH).read_text(encoding="utf-8"))
        self.assertTrue(any("повторно открыты npc/nori" in c for c in pack["checks"]))

    def test_rejected_fold_publishes_error_in_next(self):
        self.put(turn(47, "0006-10-01T07:14:00"))
        report = hot.fold(self.root)
        self.assertTrue(report["errors"])
        pack = json.loads((self.root / nextpack.PATH).read_text(encoding="utf-8"))
        self.assertTrue(pack["fold_errors"])
        self.assertIn("amend", pack["blocked_rule"])

    def test_lore_seed_reaches_next_and_checks(self):
        (self.root / "memory/_curated").mkdir(parents=True, exist_ok=True)
        entry = dict(ENTRY, heard={})
        (self.root / lore.SEED).write_text(json.dumps({"entries": [entry]}, ensure_ascii=False), encoding="utf-8")
        self.put(turn(45, "0006-10-01T07:14:00", gm="**Нори:** — Это был Бётон?",
                      d={"scene": {"present": ["shiki", "nori"]},
                         "cast": {"nori": {"name": "Хаясэ Нори"}}}))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        pack = json.loads((self.root / nextpack.PATH).read_text(encoding="utf-8"))
        self.assertTrue(any(c.startswith("[lore] Утечка знания") for c in pack["checks"]))
        self.assertIn("Хаясэ Нори — слышал(а) с s04.text.045", pack["fences"][0])
        public = json.loads((self.root / hot.HOT_PATH).read_text(encoding="utf-8"))
        self.assertEqual(public["lore"]["byoton_shiton"]["nori"], "s04.text.045")


if __name__ == "__main__":
    unittest.main()
