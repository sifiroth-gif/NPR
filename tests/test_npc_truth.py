"""NPC знает, помнит и умеет только своё; мир не подыгрывает (поправка игрока 23.11.2026)."""
import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import acquaint, expertise, favor, hot, lore, nextpack, presence, scene_packet

FACT = {"id": "fact_orochimaru", "kind": "fact", "title": "Год у Орочимару", "short": "год у Орочимару",
        "patterns": [r"(?:жил|прожил)\w*[^.!?\n]{0,40}(?:у|с|при)\s+орочимару"], "wield": ["shiki", "orochimaru"]}


def names_for(*rows):
    return {key: presence._entry([name]) for key, name in rows}


NAMES = names_for(("sakura", "Сакура Харуно"), ("kakashi", "Какаши Хатакэ"), ("kankuro", "Канкуро"),
                  ("baki", "Баки"), ("nemera", "Немера"))


def turn(tid, gm, player=""):
    return {"id": tid, "gm": gm, "player": player}


class FactsTest(unittest.TestCase):
    def setUp(self):
        self.hot = {"cast": {"baki": {"name": "Баки"}, "sakura": {"name": "Сакура Харуно"}}}

    def test_fact_without_source_is_a_leak(self):
        t = turn("s11.text.040", "**Баки:** — Вы целый год жили у Орочимару.")
        notes = lore.review(self.hot, t, [FACT], ["baki"], ["baki"])
        self.assertEqual(len(notes), 1)
        self.assertIn("Знание о Шики без источника", notes[0]["text"])

    def test_question_and_guess_are_not_knowledge(self):
        for line in ("Вы жили у Орочимару?", "Возможно, вы жили у Орочимару."):
            t = turn("s11.text.040", f"**Баки:** — {line}")
            self.assertEqual(lore.review(self.hot, t, [FACT], ["baki"], ["baki"]), [], line)

    def test_channel_in_knows_counts(self):
        self.hot["cast"]["baki"]["knows"] = ["Савара донесла: Мугами прожил год у Орочимару"]
        t = turn("s11.text.040", "**Баки:** — Вы целый год жили у Орочимару.")
        self.assertEqual(lore.review(self.hot, t, [FACT], ["baki"], ["baki"]), [])

    def test_fact_is_heard_only_aloud(self):
        quiet = turn("s11.text.030", "Шики вспомнил, как жил у Орочимару.\n\n**Баки:** — Работайте.")
        self.assertEqual(lore.track(self.hot, quiet, [FACT], ["baki"]), [])
        loud = turn("s11.text.031", "**Баки:** — Работайте.", player="Я жил у Орочимару год.")
        self.assertEqual(lore.track(self.hot, loud, [FACT], ["baki"]), [("fact_orochimaru", "baki")])

    def test_fact_heard_only_by_those_in_the_scene_text(self):
        names = names_for(("baki", "Баки"), ("sakura", "Сакура Харуно"))
        t = {"id": "s11.text.040", "player": "Я жил у Орочимару год.", "gm": "Сакура слушала.", "_spoke": []}
        self.assertEqual(lore.track(self.hot, t, [FACT], ["baki", "sakura"], names), [("fact_orochimaru", "sakura")])

    def test_facts_stay_out_of_fences_and_first_sight(self):
        self.assertEqual(lore.fences(self.hot, [FACT], ["baki"]), [])
        self.hot["fold"] = {"lore_new": [{"entry": "fact_orochimaru", "key": "baki", "turn": "s11.text.031"}]}
        self.assertEqual(lore.first_sight(self.hot, [FACT]), [])

    def test_backfill_marks_speakers_who_heard(self):
        rows = [turn("s11.text.030", "**Баки:** — Понимаю.\n**Немера:** — Он жил у Орочимару.",
                     player="Ну я жил у него. У Орочимару, если точнее: жил у Орочимару год.")]
        added = acquaint.backfill(None, self.hot, [FACT], NAMES, rows)
        self.assertEqual(added, {"fact_orochimaru": ["baki"]})
        self.assertEqual(acquaint.backfill(None, self.hot, [FACT], NAMES, rows), {})   # разово


class AcquaintTest(unittest.TestCase):
    ROWS = [turn("s01.text.011", "**Сакура Харуно:** — Наруто! Не ори!"),
            turn("s01.text.060", "**Канкуро:** — Эй."), turn("s01.text.061", "**Канкуро:** — Ну?"),
            turn("s01.text.062", "**Канкуро:** — Пошли."), dict(turn("s02.text.052", "Какаши перехватил атаку."),
                 d={"scene": {"present": ["shiki", "kakashi"]}})]

    def setUp(self):
        self.met = acquaint.index(None, NAMES, self.ROWS)

    def test_levels(self):
        self.assertEqual(acquaint.level(self.met, "kankuro", 11)[0], 2)
        self.assertEqual(acquaint.level(self.met, "sakura", 11)[0], 1)
        self.assertEqual(acquaint.level(self.met, "kakashi", 11), (1, ["s02.text.052"]))
        self.assertEqual(acquaint.level(self.met, "baki", 11), (0, []))

    def test_invented_past_is_flagged(self):
        gm = ("**Какаши Хатакэ:** — Мугами Шики. Давно не виделись.\n"
              "**Канкуро:** — Помню тебя, Мугами.\n**Баки:** — Работайте.")
        notes = acquaint.review({}, turn("s11.text.037", gm), self.met, NAMES)
        self.assertEqual(len(notes), 1)
        self.assertIn("Какаши Хатакэ", notes[0]["text"])
        self.assertIn("лишь мельком", notes[0]["text"])

    def test_view_line(self):
        hot_state = {"lore": {"fact_orochimaru": {"sakura": "s11.text.036"}}}
        line = acquaint.view(hot_state, "sakura", self.met, [FACT], 11, "медик мира")
        self.assertEqual(line, "до s11 мельком (s01.011), без имени и прошлого; слышал: год у Орочимару 036; медик мира")
        self.assertEqual(acquaint.view({}, "baki", self.met, [FACT], 11), "до s11 встреча не подтверждена; тайн Шики не слышал")

    def test_village_is_not_a_name(self):
        entry = presence._entry(["Связной Песка"])
        self.assertNotIn("песка", entry["parts"])
        self.assertIn("песков", presence._entry(["Песков Ёсио"])["parts"])

    def test_generic_cards_count_only_speech(self):
        names = names_for(("svyaznoy", "Связной Шумного")) 
        rows = [turn(f"s01.text.{i:03d}", "Связной Шумного принёс письмо.") for i in range(1, 8)]
        met = acquaint.index(None, names, rows)
        self.assertEqual(acquaint.level(met, "svyaznoy", 11)[0], 0)
        self.assertEqual(len(met["svyaznoy"]["mentioned"]), 7)
        self.assertEqual(acquaint.index(None, names, rows, {"svyaznoy"}), {})
        rows = [dict(turn("s01.text.001", "**Связной Шумного:** — Письмо."),
                     d={"scene": {"present": ["shiki", "svyaznoy"]}})]
        self.assertEqual(acquaint.level(acquaint.index(None, names, rows, {"svyaznoy"}), "svyaznoy", 11)[0], 1)

    def test_shared_surname_does_not_go_to_main_card(self):
        names = names_for(("arakawa", "Аракава Дзюн"), ("arakawa_mei", "Аракава Мэй"), ("arakawa_guard", "Охранник Аракавы"))
        stems = acquaint._stems(names)
        self.assertNotIn(("аракав", "arakawa"), stems.get("арак", []))


class ExpertiseTest(unittest.TestCase):
    def setUp(self):
        self.hot = {"cast": {"sakura": {"name": "Сакура Харуно", "facts": ["Шиноби-медик Конохагакуре"]},
                             "baki": {"name": "Баки", "state": "Ждёт, пока врачи закончат"}}}

    def test_medic_speaking_shiki_science(self):
        gm = "**Сакура Харуно:** — Угнетение комплекса I и блок натриевых каналов — вот причина."
        notes = expertise.review(self.hot, turn("s11.text.033", gm), NAMES, {})
        self.assertEqual(len(notes), 1)
        self.assertIn("комплекса i", notes[0]["text"])

    def test_short_question_and_clinic_are_fine(self):
        gm = ("**Сакура Харуно:** — Комплекс I — это что?\n"
              "**Сакура Харуно:** — Его вентиляция недостаточна, проверю нервно-мышечную проводимость.\n"
              "**Врач Песка:** — Вентиляцию держим.")
        self.assertEqual(expertise.review(self.hot, turn("s11.text.033", gm), NAMES, {}), [])

    def test_non_medic_and_seed(self):
        gm = "**Баки:** — Значит, интоксикация и угнетение проводимости."
        self.assertEqual(len(expertise.review(self.hot, turn("s11.text.033", gm), NAMES, {})), 1)
        self.assertEqual(expertise.review(self.hot, turn("s11.text.033", gm), NAMES, {"baki": {"medicine": "clinical"}}), [])
        self.assertIsNone(expertise.tier_line(self.hot, {}, "baki"))   # «врачи» в state не делают медиком
        self.assertEqual(expertise.tier_line(self.hot, {}, "sakura"), "медик мира, без химии Шики")


class FavorTest(unittest.TestCase):
    def test_measure(self):
        gm = ("**Баки:** — Мугами. Вы спасли его. Я этого не забуду.\n"
              "**Баки:** — Вы свободны покинуть деревню.\n"
              "**Какаши Хатакэ:** — Добро пожаловать. Но решения отряда — мои.\n"
              "**Сакура Харуно:** — Нет. Сначала ты скажешь, откуда яд.")
        row = favor.measure(turn("s11.text.036", gm), NAMES)
        self.assertEqual((row["praise"], row["grant"], row["free"], row["friction"]), (1, 2, 1, 1))

    def test_window_must_and_cooldown(self):
        state = {}
        praise = "**Баки:** — Великолепно. Спасибо."
        for i in range(1, 6):
            notes = favor.track(state, turn(f"s11.text.{i:03d}", praise), NAMES)
            self.assertEqual(notes, [])
        item = favor.must(state)
        self.assertIn("похвал и благодарностей Шики — 5", item)
        self.assertEqual(favor.must(state), item)              # повторная свёртка того же хода
        favor.track(state, turn("s11.text.006", praise), NAMES)
        self.assertIsNone(favor.must(state))                   # пауза между напоминаниями
        for i in range(7, 10):
            favor.track(state, turn(f"s11.text.{i:03d}", praise), NAMES)
        self.assertIsNotNone(favor.must(state))

    def test_seed_from_archive_once(self):
        state = {}
        rows = [turn(f"s11.text.{i:03d}", "**Баки:** — Великолепно.") for i in range(1, 12)]
        favor.seed(state, rows, NAMES)
        self.assertEqual(favor.totals(state)["praise"], favor.WINDOW)
        state["fold"]["favor"]["recent"] = []
        favor.seed(state, rows, NAMES)
        self.assertEqual(favor.totals(state)["turns"], 0)

    def test_turn_note_for_free_grant(self):
        notes = favor.track({}, turn("s11.text.037", "**Баки:** — Вы свободны."), NAMES)
        self.assertEqual(notes[0]["kind"], "favor")


class PresenceAndCacheTest(unittest.TestCase):
    def test_silent_people_leave_the_scene(self):
        state = {"scene": {"present": ["shiki", "baki", "sakura", "nemera"]}}
        gone = []
        for seq in range(1, 9):
            gm = "**Сакура Харуно:** — Идём." if seq != 3 else "Баки остался у ворот."
            gone += presence.prune_silent(state, {"id": f"s11.text.{seq:03d}", "gm": gm, "_spoke": ["sakura"]}, seq,
                                          NAMES, ["nemera"])
        self.assertEqual(state["scene"]["present"], ["shiki", "sakura", "nemera"])
        self.assertIn("Баки", gone[0]["text"])

    def test_disclosure_cache_is_bounded(self):
        cues = {f"npc{i}": [{"turn": f"s11.text.{i:03d}", "line": "х" * 300, "source": "a", "status": "s"}] * 4
                for i in range(1, 12)}
        cues["nemera"] = cues["npc1"]
        out = hot._disclosures(cues, ["nemera"])
        self.assertNotIn("nemera", out)
        self.assertLessEqual(len(json.dumps(out, ensure_ascii=False).encode("utf-8")), hot.DISCLOSURE_BYTES)
        self.assertIn("npc11", out)                      # уходят давние, свежие остаются
        self.assertNotIn("npc1", out)

    def test_slim_actor_keeps_the_core(self):
        actor = {"profile": {"name": "Баки", "state": "ждёт" * 100, "memory": ["x"] * 9, "inner": "y"},
                 "dynamics": {"a": 1}, "prior_disclosures": [1, 2], "alignment": {"a": 1}, "alignment_ref": "ref"}
        scene_packet._slim(actor, "baki")
        self.assertEqual(sorted(actor["profile"]), ["name", "state"])
        self.assertLessEqual(len(actor["profile"]["state"]), 200)
        self.assertEqual(actor["profile_ref"], "memory/npc/baki.md")
        self.assertNotIn("dynamics", actor)
        self.assertNotIn("alignment", actor)

    def test_keeper_fix(self):
        state = {"cast": {"sakura": {"facts": ["медик", "Встречала Шики на экзамене"]}}}
        self.assertTrue(hot._apply_fix(state, {"path": ["cast", "sakura", "facts"], "match": "Встречала", "value": "не встречала"}))
        self.assertEqual(state["cast"]["sakura"]["facts"], ["медик", "не встречала"])
        self.assertFalse(hot._apply_fix(state, {"path": ["cast", "kakashi", "facts"], "match": "x", "value": "y"}))

    def test_keeper_fix_runs_once_per_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / hot.KEEPER_FILL
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"fix": [{"path": ["cast", "sakura", "state"], "value": "A"}]}), encoding="utf-8")
            state = {"cast": {"sakura": {"state": "old"}}}
            hot._keeper_fill(tmp, state)
            state["cast"]["sakura"]["state"] = "ведущий"
            hot._keeper_fill(tmp, state)
            self.assertEqual(state["cast"]["sakura"]["state"], "ведущий")


class NextPackTest(unittest.TestCase):
    def test_knows_and_favor_reach_next(self):
        state = {"cursor": {"id": "s11.text.040"}, "scene": {"present": ["shiki", "baki"]},
                 "cast": {"baki": {"name": "Баки"}}}
        pack = nextpack.build(state, {"cursor": {"id": "s11.text.040"}}, {"checks": []}, [],
                              extra={"knows": {"baki": "до s11 с Шики не встречались"}, "favor": "Мир подыгрывает Шики"})
        self.assertEqual(pack["present"]["baki"]["knows"], "до s11 с Шики не встречались")
        self.assertEqual(pack["must"][0], "Мир подыгрывает Шики")
        self.assertIn("present.<id>.knows", pack["form"])


if __name__ == "__main__":
    unittest.main()


TRAIT = {"id": "trait_black_chakra", "kind": "trait", "title": "Чёрная чакра", "short": "чёрную чакру",
         "patterns": ["чёрн\\w* чакр"], "wield": ["shiki"]}


class SeenAndWhoTest(unittest.TestCase):
    def test_trait_seen_by_those_in_scene(self):
        names = names_for(("sakura", "Сакура Харуно"), ("baki", "Баки"))
        state = {"cast": {}}
        t = {"id": "s11.text.034", "player": "", "_spoke": [],
             "gm": "Чёрная чакра Шики легла на ткани. Сакура держала сердце."}
        self.assertEqual(lore.track(state, t, [TRAIT], ["sakura", "baki"], names), [("trait_black_chakra", "sakura")])
        met = acquaint.index(None, NAMES, [dict(turn("s11.text.033", "**Сакура Харуно:** — Да."), d={"scene": {"present": ["shiki", "sakura"]}}),
                                           dict(turn("s11.text.034", "Сакура держала сердце."), d={}),
                                           dict(turn("s11.text.054", "**Сакура Харуно:** — Яд!"), d={"scene": {"present": ["shiki", "sakura"]}}),
                                           dict(turn("s11.text.055", "Сакура уклонилась."), d={})])
        line = acquaint.view(state, "sakura", met, [FACT, TRAIT], 11)
        self.assertIn("видел: чёрную чакру 034", line)
        self.assertIn("в сессии вместе: 033–034, 054–055", line)

    def test_canon_person_now_and_character(self):
        ref, card = __import__("rpg_engine.fight", fromlist=["x"]).canon_card("naruto", {"name": "Наруто Узумаки"})
        self.assertEqual(ref, "naruto")
        self.assertIn("вмешивается первым", card["character"])

    def test_wiki_marks_exam_cards_and_applies_keeper_fixes(self):
        from rpg_engine import wiki
        store = {}
        wiki._merge_roster(store, {"cards": [{"id": "sakura", "name": "Сакура", "role": "генин, участница",
                                              "capabilities": "начальные техники"}]})
        self.assertIn("(на 1283, экзамен)", store["sakura"]["character"]["role"][0]["v"])
        root = Path(__file__).resolve().parents[1]
        wiki._merge_canon_people(store, root)
        self.assertEqual(list(store["sakura"]["stable"])[0], "сейчас (канон 1287)")
        self.assertIn("канон", store["sakura"]["character"])
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "memory/_curated").mkdir(parents=True)
            (Path(tmp) / "memory/_curated/state_fill.json").write_text(json.dumps({"fix": [
                {"path": ["cast", "sakura", "facts"], "match": "Встречала", "value": "Не встречала."}]}), encoding="utf-8")
            store["sakura"]["stable"]["сведения"] = [{"v": "Медик. Встречала Шики. Пришла.", "src": "s"}]
            wiki._apply_keeper_fixes(store, tmp)
            self.assertEqual(store["sakura"]["stable"]["сведения"][0]["v"], "Медик. Не встречала. Пришла.")
