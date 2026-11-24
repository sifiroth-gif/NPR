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


if __name__ == "__main__":
    unittest.main()
