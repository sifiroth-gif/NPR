import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import drama


def _hot(present=("shiki", "keiji", "yamato"), at="0010-03-25T20:00:00"):
    return {"cursor": {"id": "s13.text.010", "at": at},
            "scene": {"present": list(present)},
            "cast": {"keiji": {"name": "Кэйдзи", "tier": "developed"}, "yamato": {"name": "Ямато"},
                     "nemera": {"name": "Немера (Невыписанная)"}},
            "_will": {"streak": {}}, "_psyche": {}}


class RhythmTest(unittest.TestCase):
    def test_even_rhythm_is_flagged_and_varied_is_not(self):
        hot = _hot()
        for n in range(7):
            drama.track(hot, {"id": f"s13.text.{n:03}", "gm": "х" * 2400, "at": f"0010-03-25T20:{n * 5:02}:00"})
        self.assertIn("Ритм ровный", drama.rhythm(hot))
        drama.track(hot, {"id": "s13.text.007", "gm": "х" * 600, "at": "0010-03-26T08:00:00"})
        self.assertIsNone(drama.rhythm(hot), "короткий удар и скачок времени — ритм сменился")


class TalkTest(unittest.TestCase):
    def test_npcs_only_talking_to_shiki(self):
        hot = _hot()
        for n in range(3):
            drama.track(hot, {"id": f"s13.text.{n:03}", "gm": "**Кэйдзи:** Да.\n**Шики:** Нет.\n**Ямато:** Ясно."})
        self.assertIn("говорят только с Шики", drama.talk(hot))
        drama.track(hot, {"id": "s13.text.004", "gm": "**Кэйдзи:** Капитан, это он.\n**Ямато:** Тише."})
        self.assertIsNone(drama.talk(hot), "две реплики NPC подряд — говорят друг с другом")

    def test_named_address_in_narration(self):
        hot = _hot()
        self.assertTrue(drama.talked("Кэйдзи повернулся к Ямато и что-то сказал.", hot, ["keiji", "yamato"]))
        self.assertFalse(drama.talked("Кэйдзи и Ямато молчали.", hot, ["keiji", "yamato"]))


class InitiativeTest(unittest.TestCase):
    def test_memory_note_is_not_a_move_but_initiative_is(self):
        hot = _hot(present=("shiki", "nemera"))
        hot["_will"]["streak"] = {"nemera": 9}
        drama.track(hot, {"id": "s13.text.020", "gm": "Немера ответила коротко."}, always={"nemera"})
        self.assertEqual(hot["_will"]["streak"]["nemera"], 9)
        self.assertIn("9 ходов без своего шага", drama.passive(hot, always={"nemera"}))
        drama.track(hot, {"id": "s13.text.021", "gm": "Немера сама потянула отросток к воде."}, always={"nemera"})
        self.assertEqual(hot["_will"]["streak"]["nemera"], 0)


class ChapterGapTest(unittest.TestCase):
    def test_far_world_chapter_asks_for_own(self):
        hot = _hot(at="0010-03-29T16:00:00")
        hot["_plot"] = {"events": {"main": {"kind": "main", "status": "active", "title": "След Шики"},
                                   "ch15": {"kind": "chapter", "status": "queued", "starts": "0010-08-10T22:00:00"}}}
        item = drama.chapter_gap(hot)
        self.assertIn("нет открытой главы", item)
        self.assertIn("134 дн.", item)
        hot["_plot"]["events"]["mine"] = {"kind": "chapter", "status": "active"}
        self.assertIsNone(drama.chapter_gap(hot))

    def test_must_cooldown(self):
        hot = _hot(at="0010-03-29T16:00:00")
        hot["_plot"] = {"events": {"main": {"kind": "main", "status": "active"}}}
        self.assertIsNotNone(drama.must(hot))
        hot["cursor"]["id"] = "s13.text.011"
        self.assertIsNone(drama.must(hot), "не чаще COOLDOWN ходов")
        hot["cursor"]["id"] = "s13.text.015"
        self.assertIsNotNone(drama.must(hot))


class GapTest(unittest.TestCase):
    def test_old_acquaintance_after_years(self):
        root = Path(tempfile.mkdtemp())
        (root / "recovery/cast").mkdir(parents=True)
        memory = [{"turn": "s08.text.030", "at": "0006-10-03T06:49:00"},
                  {"turn": "s13.text.010", "at": "0010-03-25T19:58:00"}]
        (root / "recovery/cast/keiji.json").write_text(json.dumps({"memory": memory}), encoding="utf-8")
        got = drama.gaps(root, _hot())
        self.assertIn("keiji", got)
        self.assertIn("03.10.1283", got["keiji"])
        self.assertIn("3 г. 5 мес.", got["keiji"])
        self.assertNotIn("yamato", got)


if __name__ == "__main__":
    unittest.main()


class FlatIntentTest(unittest.TestCase):
    def test_flat_plan_is_wrapped_and_debris_removed(self):
        from rpg_engine import npc_memory
        hot = {"_checks": []}
        profile = {"intent": {"goal": {"goal": "старое", "status": "committed", "turn": "s13.text.017"}}}
        npc_memory._intents(hot, profile, {"plan": "trace_watcher", "goal": "узнать цель наблюдателя",
                                           "next": "проследить за змеёй", "due": "0010-03-29T16:35:00"},
                            "s13.text.018", "0010-03-29T16:20:00")
        self.assertEqual(list(profile["intent"]), ["trace_watcher"])
        self.assertEqual(profile["intent"]["trace_watcher"]["next"], "проследить за змеёй")
        self.assertTrue(any("без ключа" in c["text"] for c in hot["_checks"]))


class NewsChannelTest(unittest.TestCase):
    def test_news_needs_a_channel(self):
        from rpg_engine import news
        from rpg_engine.heuristic_history import stem
        markers = [stem("кадзекаге")]
        said = lambda gm: news._delivered([{"gm": gm}], markers)
        self.assertFalse(said("**Шики:** Кадзекаге жив?"), "вопрос Шики — не весть")
        self.assertFalse(said("Шики подумал о Кадзекаге."), "мысль Шики — не весть")
        self.assertTrue(said("**Торговец:** Говорят, Кадзекаге вернулся в Суну."))
        self.assertTrue(said("На заставе болтают: Кадзекаге вернули живым."))
