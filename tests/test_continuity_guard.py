import unittest

from rpg_engine import continuity_guard as cg


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


if __name__ == "__main__":
    unittest.main()
