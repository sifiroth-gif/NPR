import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import talk


def entity(key, name, aliases=(), first=None, hint=None, sources=()):
    e = {"id": key, "name": name, "aliases": list(aliases), "first": first, "last": None,
         "sources": list(sources), "history": []}
    if hint:
        e["sessions_hint"] = hint
    return e


STORE = {
    "ren": entity("ren", "Хаясэ Рэн", hint="сессии 1–3 (экзамен на тюнина)"),
    "takagi_ren": entity("takagi_ren", "Такаги Рэн", first="s05.text.025", sources=["s06.text.001"]),
    "kuroda_konoha": entity("kuroda_konoha", "Курода", sources=["s01.text.029"]),
    "kuroda_mayu": entity("kuroda_mayu", "Курода Маю", first="s06.text.040", sources=["s07.text.002"]),
    "nori": entity("nori", "Хаясэ Нори", first="s04.text.050"),
    "messenger": entity("messenger", "Посыльный", first="s05.text.010"),
}


class SourceIdentityRegressionTest(unittest.TestCase):
    def test_source_scoped_masked_dialogue_and_episode_projection(self):
        from rpg_engine import identity, npc_memory, presence
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            anchor = state / "archive/turns/s14/s14.text.015.md"
            anchor.parent.mkdir(parents=True)
            anchor.write_text("**Человек в маске:** — Минасэ. Сэйдзи Минасэ.", encoding="utf-8")
            memory = state / "archive/npc-memory"
            memory.mkdir()
            old, new = "chelovek_v_maske", "quay_masked_hunter"
            historical = {"turn": "s08.text.098", "ch": "present", "what": "other"}
            duplicate = {"turn": "s14.text.007", "ch": "present", "what": "capture"}
            (memory / f"{old}.json").write_text(json.dumps({"episodes": [historical, duplicate]}), encoding="utf-8")
            (memory / f"{new}.json").write_text(json.dumps({"episodes": [duplicate]}), encoding="utf-8")
            for session, rows in ((8, [{"id": "s08.text.098", "gm": "**Человек в маске:** — Другая сцена."}]),
                                  (14, [{"id": "s14.text.007", "gm": "**Человек в маске:** — Да."},
                                        {"id": "s14.text.015", "gm": "**Человек в маске:** — Минасэ. Сэйдзи Минасэ."}])):
                path = state / "archive" / f"session-{session:03}-turns.jsonl"
                path.write_text("\\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
            store = {old: entity(old, "Человек в маске", first="s08.text.098", sources=["s14.text.007"]),
                     new: entity(new, "Сэйдзи Минасэ", first="s13.text.099")}
            conversations, _ = talk.collect(state, store)
            self.assertEqual([row["turn"] for row in conversations[old]], ["s08.text.098"])
            self.assertEqual([row["turn"] for row in conversations[new]], ["s14.text.007", "s14.text.015"])
            self.assertEqual(npc_memory.load_episodes(state, old), [historical])
            self.assertEqual(npc_memory.load_episodes(state, new), [duplicate])
            self.assertEqual(json.loads((memory / f"{old}.json").read_text())["episodes"], [historical, duplicate])
            card = {"first": "s08.text.098", "seen": "s14.text.018", "memory": [historical, duplicate], "memory_total": 2}
            projected = identity.project_card(state, old, card)
            self.assertEqual(projected["memory"], [historical])
            self.assertEqual(projected["seen"], "s08.text.098")
            self.assertEqual(projected["memory_total"], 1)
            # Reset or different campaign: absence of the exact anchor disables the correction.
            anchor.unlink()
            self.assertEqual(npc_memory.load_episodes(state, old), [historical, duplicate])
            self.assertEqual(identity.active(state), [])
            names = {old: presence._entry(["Человек в маске"]), new: presence._entry(["Сэйдзи Минасэ"])}
            self.assertIsNone(presence.resolve("Человек в маске", names, scope={new}))
            self.assertEqual(presence.resolve("Человек в маске", names, scope={old}), old)

    def test_no_episode_removed_without_canonical_counterpart(self):
        from rpg_engine import identity
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            anchor = state / "archive/turns/s14/s14.text.015.md"
            anchor.parent.mkdir(parents=True)
            anchor.write_text("**Человек в маске:** — Минасэ. Сэйдзи Минасэ.", encoding="utf-8")
            self.assertEqual(identity.excluded_episodes(state, "chelovek_v_maske"), set())


class TalkTest(unittest.TestCase):
    def test_speech_drops_actions_and_ooc(self):
        player = "#сюнсин к двери# Не подходите. #достаю сенбон# Я врач. мастеру: опиши мимику"
        self.assertEqual(talk.speech(player), "Не подходите. Я врач.")

    def test_utterances_take_the_paragraph_after_the_label(self):
        gm = ("**Кабуто Якуши\\:** — Вы умеете удивлять.\n\nТишина.\n\n"
              "— **Кадзиура:** В документах пересадки нет. **Анестезиолог:** Давление падает.")
        self.assertEqual(talk.utterances(gm), [("Кабуто Якуши", "Вы умеете удивлять."),
                                               ("Кадзиура", "В документах пересадки нет."),
                                               ("Анестезиолог", "Давление падает.")])

    def test_resolver_uses_the_session_for_shared_names(self):
        resolve = talk.Resolver(STORE)
        self.assertEqual(resolve("Рэн", 1), "ren")
        self.assertEqual(resolve("Рэн", 6), "takagi_ren")
        self.assertEqual(resolve("Курода", 1), "kuroda_konoha")
        self.assertEqual(resolve("Курода", 7), "kuroda_mayu")
        self.assertEqual(resolve("Хаясэ Нори, по рации", 3), "nori")
        self.assertEqual(resolve("11:57. Хаясэ Нори", 5), "nori")
        self.assertIsNone(resolve("Шиноби Нори", 5))
        self.assertIsNone(resolve("Посыльный", 2), "роль в чужой сессии — другой человек")
        self.assertEqual(resolve("Посыльный", 5), "messenger")
        self.assertIsNone(resolve("Мугами Шики", 5))

    def test_large_logs_split_by_session(self):
        person = STORE["nori"]
        entries = [{"turn": f"s0{s}.text.{i:03}", "at": "", "heard": "х" * 300, "said": ["у" * 240]}
                   for s in (4, 5) for i in range(1, 40)]
        files = talk.pages(person, entries, "memory/npc")
        self.assertIn("memory/npc/nori.talk.s04.md", files)
        self.assertIn("`memory/npc/nori.talk.s05.md`", files["memory/npc/nori.talk.md"])
        small = talk.pages(person, entries[:2], "memory/npc")
        self.assertEqual(list(small), ["memory/npc/nori.talk.md"])


if __name__ == "__main__":
    unittest.main()


class ReadCheckTest(unittest.TestCase):
    def test_returning_npc_must_be_read(self):
        from rpg_engine import wiki
        store = {"nori": dict(entity("nori", "Хаясэ Нори", first="s04.text.001"),
                              talk=[{"turn": "s04.text.001"}])}
        gm = "**Хаясэ Нори:** — Вы опоздали."
        later = {"id": "s05.text.010", "gm": gm, "read": []}
        soon = {"id": "s04.text.005", "gm": gm}
        opened = {"id": "s05.text.011", "gm": gm, "read": ["memory/npc/nori.md"]}
        notes = wiki._read_checks(store, [later, soon, opened])
        self.assertEqual([n["turn"] for n in notes], ["s05.text.010"])
        self.assertIn("`npc/nori`", notes[0]["text"])


class RepeatCheckTest(unittest.TestCase):
    def test_npc_repeating_an_old_line_is_flagged(self):
        from rpg_engine import wiki
        old = "В документах пересадки нет, и я не подпишу ничего, пока не увижу протокол инфузии целиком."
        store = {"kadziura": dict(entity("kadziura", "Кадзиура", first="s08.text.001"),
                                  talk=[{"turn": "s08.text.009", "said": [old]}])}
        repeat = {"id": "s08.text.020", "gm": f"**Кадзиура:** — {old}"}
        fresh = {"id": "s08.text.021", "gm": "**Кадзиура:** — Протокол я видел, и в нём стоит чужая подпись. Кто её поставил?"}
        notes = wiki._repeat_checks(store, [repeat, fresh])
        self.assertEqual([n["turn"] for n in notes], ["s08.text.020"])
        self.assertIn("s08.text.009", notes[0]["text"])
