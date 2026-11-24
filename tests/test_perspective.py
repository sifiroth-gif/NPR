"""Перспектива Шики и историческая память присутствующих (поправка игрока 24.11.2026, s12.text.043)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import acquaint, echo, heuristic_history, presence, prose_lint

TRAIT = {"id": "trait_sharingan", "kind": "trait", "title": "Шаринган Шики",
         "patterns": [r"(?:шаринган|мангек)\w* (?:шики|мугами)"], "wield": ["shiki"]}
NAMES = {k: presence._entry([n]) for k, n in (("orochimaru", "Орочимару"), ("naruto", "Наруто Узумаки"),
                                              ("kabuto", "Кабуто Якуши"))}


class InteriorTest(unittest.TestCase):
    NPC = ["Орочимару", "Наруто Узумаки", "Кабуто Якуши", "Ямато"]

    def test_npc_head_is_caught(self):
        gm = ("Орочимару медленно поднял руку.\n\nНе повторил.\n\nОн знал эти глаза.\n\n"
              "**Наруто Узумаки:** — Шаринган…\n\nВоспоминание оказалось почти болезненным.\n\n"
              "Поэтому Ямато предпочёл воспользоваться окном.")
        hit = prose_lint.interior(gm, self.NPC)
        self.assertIsNotNone(hit)
        for who in ("Орочимару", "Наруто Узумаки", "Ямато"):
            self.assertIn(who, hit["quote"])

    def test_shiki_hedges_and_speech_are_fine(self):
        gm = ("Шики знал эти печати.\n\nОн понимал, что времени мало.\n\n"
              "Кабуто, похоже, понял.\n\nПо лицу Орочимару было видно, что он вспомнил.\n\n"
              "**Кабуто Якуши:** — Я знал, что ты придёшь.\n\nНемера знала его тело.")
        self.assertIsNone(prose_lint.interior(gm, self.NPC))

    def test_twin_scripts_are_identical(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "rpg_engine/prose_lint.py").read_bytes(),
                         (root / "skills/dark-literary-gm/scripts/prose_lint.py").read_bytes())


class SmoothingAndMenuTest(unittest.TestCase):
    """Игрок, 24.11.2026: «мастер подыгрывает, сглаживает конфликты, проза стерильная» (s12.text.054–062)."""

    def test_conflict_that_dies_by_itself(self):
        from rpg_engine import favor
        gm = ("Ямато медленно опустил руку, так и не завершив печать.\n\nОн отступил на полшага, освобождая тропу.\n\n"
              "**Ямато:** — Хорошо.\n\nНа этот раз Кабуто последовал без возражений.")
        self.assertEqual(len(favor.smoothing({"gm": gm})), 3)
        kept = ("Ямато опустил барьер. Кровь из пореза на его ладони капала на мох, и он запомнил, чья это игла.\n\n"
                "Наруто не отступил.\n\nНить Немеры отступила, освобождая тропу.")
        self.assertEqual(favor.smoothing({"gm": kept}), [], "с ценой, «не отступил», своя Немера — не сглаживание")

    def test_window_must_on_smoothing(self):
        from rpg_engine import favor
        hot = {"fold": {"favor": {"recent": [{"id": f"s12.text.{i:03d}", "praise": 0, "grant": 0, "free": 0,
                                               "friction": 1, "smooth": 1} for i in range(55, 63)]}}}
        self.assertIn("конфликт гаснет сам", favor.must(hot))

    def test_menu_ending(self):
        gm = ("Ямато указал на склон.\n\nВпереди оставались два доступных пути: верхняя тропа и низина.\n\n**Ход Шики.**")
        self.assertEqual(prose_lint.menu(gm)["rule"], "menu")
        fine = "Змея повернула голову к склону. Язык коснулся камня там, где минуту назад лежала рука Сакуры.\n\n**Ход Шики.**"
        self.assertIsNone(prose_lint.menu(fine))


class UneaseTest(unittest.TestCase):
    """Игрок, 24.11.2026: больше реакций на Шики по-лавкрафтовски и тревоги, без описаний внешности."""

    def test_calm_streak_asks_for_unease_with_rotating_ways(self):
        from rpg_engine import favor
        hot = {"scene": {"present": ["shiki", "yamato", "nemera"]}, "cast": {"yamato": {"name": "Ямато"}}}
        calm = "Ямато указал на склон. Отряд двинулся по верхней тропе.\n\n**Ход Шики.**"
        for i in (63, 64):
            favor.track(hot, {"id": f"s12.text.0{i}", "gm": calm}, {}, ("nemera",))
        item = favor.unease_must(hot)
        self.assertIn("2 хода подряд", item)
        self.assertIn("без описания его облика", item)
        self.assertEqual(item.count(";") >= 2, True)
        favor.track(hot, {"id": "s12.text.065", "gm": "Ямато осёкся на полуслове и сказал о погоде."}, {}, ("nemera",))
        self.assertIsNone(favor.unease_must(hot))

    def test_alone_with_nemera_is_not_calm(self):
        from rpg_engine import favor
        hot = {"scene": {"present": ["shiki", "nemera"]}}
        for i in (63, 64, 65):
            favor.track(hot, {"id": f"s12.text.0{i}", "gm": "Лес молчал."}, {}, ("nemera",))
        self.assertIsNone(favor.unease_must(hot))


class TraitWitnessTest(unittest.TestCase):
    def test_named_is_not_a_witness_present_is(self):
        rows = [{"id": "s09.text.024", "gm": "— Смотрю на Шаринган Шики, — сказал Саске. Орочимару захочет его.",
                 "d": {"scene": {"present": ["shiki", "sasuke"]}}},
                {"id": "s11.text.046", "gm": "Вспыхнул Шаринган Шики.\n\n**Наруто Узумаки:** — У НЕГО ТОЖЕ ШАРИНГАН?!"}]
        hot = {"lore": {"trait_sharingan": {"orochimaru": "s09.text.024"}}, "fold": {"lore_backfill": ["trait_sharingan"]}}
        names = dict(NAMES, sasuke=presence._entry(["Саске Учиха"]))
        acquaint.backfill(None, hot, [TRAIT], names, rows)
        seen = hot["lore"]["trait_sharingan"]
        self.assertNotIn("orochimaru", seen, "назван в реплике — не видел")
        self.assertEqual(seen.get("sasuke"), "s09.text.024")
        self.assertEqual(seen.get("naruto"), "s11.text.046")

    def test_surprise_at_what_was_seen_before(self):
        hot = {"lore": {"trait_sharingan": {"naruto": "s11.text.046"}}, "cast": {"naruto": {"name": "Наруто Узумаки"}},
               "scene": {"present": ["shiki", "naruto"]}}
        gm = "Зажёгся Шаринган Шики.\n\n**Наруто Узумаки:** — Немыслимо… Шаринган!"
        notes = acquaint.again(hot, {"id": "s12.text.043", "gm": gm}, NAMES, [TRAIT])
        self.assertEqual(len(notes), 1)
        self.assertIn("s11.text.046", notes[0]["text"])
        hot["lore"]["trait_sharingan"]["naruto"] = "s12.text.040"   # та же сцена — не «раньше»
        self.assertEqual(acquaint.again(hot, {"id": "s12.text.043", "gm": gm}, NAMES, [TRAIT]), [])


class PresentPastTest(unittest.TestCase):
    def _index(self, state, records):
        folder = state / "archive/heuristic-recall"
        folder.mkdir(parents=True)
        raw = json.dumps(records, ensure_ascii=False).encode("utf-8")
        (folder / "shard-0.json").write_bytes(raw)
        (folder / "manifest.json").write_text(json.dumps({
            "schema": heuristic_history.SCHEMA, "documents": len(records),
            "shards": [{"file": "shard-0.json", "sha256": hashlib.sha256(raw).hexdigest(), "records": len(records)}]}),
            encoding="utf-8")

    def test_past_sessions_on_the_scene_topic(self):
        state = Path(tempfile.mkdtemp())
        (state / "archive").mkdir()
        rows = [{"id": "s11.text.046", "gm": "Шаринган Шики вспыхнул над змеёй.\n\n**Наруто Узумаки:** — У НЕГО ТОЖЕ ШАРИНГАН?!"},
                {"id": "s11.text.010", "gm": "**Наруто Узумаки:** — Где рамен?"}]
        (state / "archive/session-011-turns.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        terms = lambda t: " ".join(sorted({heuristic_history.stem(w) for w in heuristic_history.tokens(t) if len(w) >= 3}))
        self._index(state, [
            {"id": r["id"], "kind": "played", "source": "archive/session-011-turns.jsonl", "start": 0, "end": 0,
             "terms": terms(r["gm"]), "headline": "", "preview": ""} for r in rows] + [
            {"id": "s12.text.040", "kind": "played", "source": "archive/session-012-turns.jsonl", "start": 0, "end": 0,
             "terms": terms("Наруто Шаринган змея"), "headline": "", "preview": ""}])
        hot = {"session": 12, "cursor": {"id": "s12.text.043"}, "cast": {"naruto": {"name": "Наруто Узумаки"}},
               "scene": {"present": ["shiki", "naruto", "nemera"],
                         "situation": "Шики Шаринганом остановил призванную змею; Наруто держит Девятихвостого."}}
        floors = echo.PAST_MIN, echo.PAST_SENTENCE    # три записи в указателе: редкость слов мала
        echo.PAST_MIN, echo.PAST_SENTENCE = 0.1, 0.1
        try:
            past = echo.present_past(state, hot, skip={"nemera"})
        finally:
            echo.PAST_MIN, echo.PAST_SENTENCE = floors
        self.assertEqual(list(past), ["naruto"])
        self.assertEqual(len(past["naruto"]), 1, "нынешняя сессия — в HOT, рамен — не тема сцены")
        self.assertTrue(past["naruto"][0].startswith("s11.text.046 — «"))
        self.assertIn("ШАРИНГАН", past["naruto"][0])


class PresentPastMeaningTest(PresentPastTest):
    def test_meaning_brings_an_episode_without_shared_words(self):
        # Смысловой слой (e5-small в свёртке): близкое по смыслу проходит и без общих редких слов.
        from rpg_engine import meaning
        state = Path(tempfile.mkdtemp())
        (state / "archive").mkdir()
        rows = [{"id": "s09.text.034", "gm": "Орочимару долго смотрел на подпись договора и улыбался одними губами."},
                {"id": "s09.text.010", "gm": "Орочимару сказал: тела, глаза и гении — всё в коллекцию."}]
        (state / "archive/session-009-turns.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        terms = lambda t: " ".join(sorted({heuristic_history.stem(w) for w in heuristic_history.tokens(t) if len(w) >= 3}))
        self._index(state, [{"id": r["id"], "kind": "played", "source": "archive/session-009-turns.jsonl",
                             "start": 0, "end": 0, "terms": terms(r["gm"]), "headline": "", "preview": ""} for r in rows])
        hot = {"session": 12, "cursor": {"id": "s12.text.043"}, "cast": {"orochimaru": {"name": "Орочимару"}},
               "scene": {"present": ["shiki", "orochimaru"], "situation": "Шики остановил белую змею взглядом."}}
        real = meaning.search
        meaning.search = lambda state_dir, q, limit=12, allow=None: [("s09.text.010", 0.91), ("s09.text.034", 0.80)]
        try:
            past = echo.present_past(state, hot)
        finally:
            meaning.search = real
        self.assertEqual([x.split(" — ")[0] for x in past["orochimaru"]], ["s09.text.010", "s09.text.034"])
        self.assertTrue(hot["fold"]["past"]["meaning"])
        self.assertEqual(echo.PAST_TURNS, 4)


if __name__ == "__main__":
    unittest.main()
