import tempfile
import unittest
from pathlib import Path

from rpg_engine import continuity_guard as cg, fight, hot


def kinds(hot):
    return [c["kind"] for c in hot.get("_checks", [])]


class ThingsTest(unittest.TestCase):
    def test_new_then_sequential_transitions(self):
        hot = {}
        cg.apply_things(hot, {"things": {"amp": {"new": {"name": "ампулы", "holder": "shiki", "count": 2},
                                                 "why": "взял в процедурной"}}}, "t1", "a")
        cg.apply_things(hot, {"things": {"amp": {"was": {"count": 2}, "now": {"count": 1}}}}, "t2", "a")
        self.assertEqual(hot["things"]["amp"]["count"], 1)
        self.assertEqual(kinds(hot), [])
        # The same last ampoule cannot be spent twice from a stale "was".
        cg.apply_things(hot, {"things": {"amp": {"was": {"count": 2}, "now": {"count": 1}}}}, "t3", "a")
        self.assertIn("contradiction", kinds(hot))

    def test_new_without_why_and_negative_count(self):
        hot = {}
        cg.apply_things(hot, {"things": {"key": {"new": {"name": "ключ"}}}}, "t1", "a")
        self.assertIn("things", kinds(hot))
        cg.apply_things(hot, {"things": {"key": {"now": {"count": -1}}}}, "t2", "a")
        self.assertIn("contradiction", kinds(hot))

    def test_gone_moves_to_history(self):
        hot = {"things": {"note": {"name": "записка", "holder": "shiki"}}}
        cg.apply_things(hot, {"things": {"note": {"gone": "сожжена"}}}, "t1", "a")
        self.assertNotIn("note", hot["things"])
        self.assertEqual(hot["things_gone"][-1]["name"], "записка")

    def test_holder_mismatch_is_contradiction(self):
        hot = {"things": {"scalpel": {"name": "скальпель", "holder": "kadziura"}}}
        cg.apply_things(hot, {"things": {"scalpel": {"was": {"holder": "shiki"},
                                                     "now": {"holder": "nurse"}}}}, "t1", "a")
        self.assertIn("contradiction", kinds(hot))

    def test_bare_state_does_not_silently_advance_item(self):
        hot = {"things": {"kit": {"state": "грязный", "turn": "t0"}}}
        cg.apply_things(hot, {"things": {"kit": {"state": "стерильный"}}}, "t1", "a")
        self.assertEqual(hot["things"]["kit"], {"state": "грязный", "turn": "t0"})
        self.assertIn("things", kinds(hot))
        cg.apply_things(hot, {"things": {"kit": {"was": {"state": "грязный"}, "now": {"state": "стерильный"}}}}, "t2", "a")
        self.assertEqual(hot["things"]["kit"]["state"], "стерильный")
        self.assertEqual(hot["things"]["kit"]["turn"], "t2")

    def test_malformed_nested_patch_does_not_crash_fold(self):
        hot = {"things": {"kit": {"state": "грязный", "turn": "t0"}}}
        cg.apply_things(hot, {"things": {"kit": {"now": "стерильный"}}}, "t1", "a")
        self.assertIn("things", kinds(hot))
        self.assertEqual(hot["things"]["kit"]["turn"], "t0")

    def test_scene_things_selects_present_and_place(self):
        hot = {"cursor": {"place": "Кусагакуре, госпиталь, кабинет"},
               "things": {"a": {"holder": "shiki"}, "b": {"holder": "far"},
                          "c": {"where": "кабинет"}, "d": {"holder": "doc"}}}
        self.assertEqual(sorted(cg.scene_things(hot, ["doc"])), ["a", "c", "d"])


class SpeechCoverageTest(unittest.TestCase):
    def test_dropped_player_speech_is_flagged(self):
        hot = {}
        cg.check_turn(hot, {"id": "t1", "player": "Скажите прямо, кто подписал приказ о переводе",
                            "gm": "Врач молча отвернулся к окну."})
        self.assertIn("player", kinds(hot))

    def test_carried_speech_and_actions_only_pass(self):
        hot = {}
        cg.check_turn(hot, {"id": "t1", "player": "Скажите прямо, кто подписал приказ о переводе",
                            "gm": "— Скажите прямо, кто подписал приказ о переводе, — Шики не сел."})
        cg.check_turn(hot, {"id": "t2", "player": "#медленно открываю дверь кабинета и вхожу#",
                            "gm": "Дверь."})
        self.assertEqual(kinds(hot), [])



class SourceStateRegressionTest(unittest.TestCase):
    def test_eye_repair_requires_exact_cursor_fields_and_both_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = state / "archive/turns/s13"
            source.mkdir(parents=True)
            original = {"body": "Активирован обычный Шаринган с тремя томоэ, змеиные вертикальные зрачки сохранены.",
                        "eyes": "Вечный Мангекё отключён 10.03 17:20."}
            hot_state = {"cursor": {"id": "s14.text.023"}, "shiki": dict(original)}
            (source / "s13.text.077.md").write_text("#убираю шаринган# Алый цвет исчез из глаз.", encoding="utf-8")
            self.assertEqual(cg.reconcile_shiki(state, hot_state), [])
            (source / "s13.text.100.md").write_text(
                "обычный можешь вообще убрать из файлов. В действующем боевом листе оставлена только активируемая игроком форма глаз",
                encoding="utf-8")
            self.assertEqual(cg.reconcile_shiki(state, hot_state), ["shiki-eyes-s14-023"])
            self.assertIn("выключен", hot_state["shiki"]["eyes"])
            self.assertEqual(cg.reconcile_shiki(state, hot_state), [])
            later = {"cursor": {"id": "s14.text.024"}, "shiki": dict(original)}
            self.assertEqual(cg.reconcile_shiki(state, later), [])
            self.assertEqual(later["shiki"], original)
            changed = {"cursor": {"id": "s14.text.023"}, "shiki": {**original, "eyes": "новый факт"}}
            self.assertEqual(cg.reconcile_shiki(state, changed), [])


class DeltaResourceTest(unittest.TestCase):
    def test_invalid_inventory_patch_is_atomic(self):
        for patch in ({"now": {"count": -1, "state": "стерильный"}},
                      {"now": {"count": 1.5}}, {"now": {"count": True}},
                      {"now": {"holder": ["shiki"]}}, {"gone": ""},
                      {"new": {"count": 5}, "why": "дубликат"},
                      {"was": {"count": 4}, "now": {"count": 0}}):
            with self.subTest(patch=patch):
                original = {"state": "грязный", "count": 2, "turn": "t0"}
                state = {"things": {"kit": dict(original)}}
                cg.apply_things(state, {"things": {"kit": patch}}, "t1", "a")
                self.assertEqual(state["things"]["kit"], original)
                self.assertTrue(kinds(state))

    def test_invalid_things_container_is_diagnostic(self):
        state = {}
        cg.apply_things(state, {"things": ["kit"]}, "t1", "a")
        self.assertIn("things", kinds(state))

    def test_negative_and_nonfinite_costs_do_not_create_chakra(self):
        at = "0006-10-01T07:10:00"
        for value in (-10, float("nan"), float("inf"), True, "10"):
            with self.subTest(value=value):
                state = {"shiki": {"chakra": {"value": 40, "at": at}}}
                notes = []
                fight._chakra(state, {"spend": [{"what": "test", "cost": value}]},
                              0, "t1", at, notes)
                self.assertEqual(state["shiki"]["chakra"]["value"], 40)
                self.assertTrue(notes)

    def test_invalid_bursts_and_set_do_not_change_chakra(self):
        at = "0006-10-01T07:10:00"
        for value in (-2, True, 2.5, "2"):
            with self.subTest(value=value):
                state = {"shiki": {"chakra": {"value": 40, "at": at}}}
                notes = fight.apply(state, {"fight": {"bursts": value},
                                           "chakra": {"set": -5}},
                                    {"id": "s04.text.045", "at": at}, [])
                self.assertEqual(state["shiki"]["chakra"]["value"], 40)
                self.assertGreaterEqual(len(notes), 2)

    def test_malformed_nested_structures_fail_before_cursor_change(self):
        for delta in ({"cast": {"npc": "bad"}}, {"clocks": {"clock": []}},
                      {"scene": {"present": "npc"}}, {"fight": {"wounds": ["bad"]}},
                      {"heal": {}}, {"chakra": {"spend": [1]}},
                      {"fight": {"foes": {"npc": "bad"}}}, {"shiki": []}):
            with self.subTest(delta=delta):
                state = {"session": 4, "cursor": {"id": "s04.text.044", "seq": 44}}
                original = {"session": 4, "cursor": dict(state["cursor"])}
                turn = {"schema": hot.TURN_SCHEMA, "id": "s04.text.045",
                        "at": "0006-10-01T07:10:00", "place": "test",
                        "player": "test", "gm": "test", "d": delta}
                with self.assertRaises(hot.TurnError):
                    hot.apply_turn(state, turn)
                self.assertEqual(state, original)

if __name__ == "__main__":
    unittest.main()
