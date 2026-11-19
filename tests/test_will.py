import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import hot, will
from tests import test_hot
from tests.test_hot import turn

SEED = {
    "kadziura": {
        "psyche": {"decides": "по протоколу, пока протокол прикрывает его", "deep_fear": "суд"},
        "intent": {"cover": {"goal": "закрыть историю с инфузией", "next": "переписать лист назначений",
                             "due": "0006-10-01T09:00:00", "flaw": "доверяет подписи начальства",
                             "against_shiki": "вина ляжет на Шики"}},
    },
}


def state():
    return {"cursor": {"id": "s04.text.044", "at": "0006-10-01T07:10:00"},
            "scene": {"present": ["shiki", "kadziura"]},
            "cast": {"kadziura": {"name": "Кадзиура", "tier": "deep", "memory_total": 20}},
            "_psyche": {"kadziura": {"psyche": {"deep_fear": "написано ведущим"}}}}


class WillTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "memory/_curated").mkdir(parents=True)
        (self.root / will.SEED).write_text(json.dumps(SEED, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_fills_only_what_the_gm_has_not_written_and_runs_once(self):
        h = state()
        self.assertEqual(will.seed(self.root, h), ["kadziura"])
        profile = h["_psyche"]["kadziura"]
        self.assertEqual(profile["psyche"]["deep_fear"], "написано ведущим")
        self.assertEqual(profile["psyche"]["decides"], "по протоколу, пока протокол прикрывает его")
        self.assertEqual(profile["intent"]["cover"]["next"], "переписать лист назначений")
        profile["intent"]["cover"]["next"] = "ведущий сменил шаг"
        self.assertEqual(will.seed(self.root, h), [])
        self.assertEqual(profile["intent"]["cover"]["next"], "ведущий сменил шаг")

    def test_overdue_plan_of_absent_npc_happens_by_itself(self):
        h = state()
        will.seed(self.root, h)
        h["_psyche"]["kadziura"]["intent"]["cover"]["if_unopposed"] = "лист переписан, виноват Шики"
        h["cursor"]["at"] = "0006-10-01T09:30:00"
        self.assertEqual(will.advance(h), [], "присутствующий: его ход играется в сцене")
        self.assertEqual([k for k, _, _ in will.due_here(h)], ["kadziura"])
        h["scene"]["present"] = ["shiki"]
        self.assertEqual(will.soon(h)[0]["overdue"], True)
        self.assertEqual(will.advance(h), [], "час на решение ведущего ещё идёт")
        self.assertEqual(will.advance(h, always_present=["kadziura"]), [])
        h["cursor"]["at"] = "0006-10-01T10:30:00"
        events = will.advance(h)
        self.assertEqual(len(events), 1)
        profile = h["_psyche"]["kadziura"]
        self.assertNotIn("cover", profile["intent"])
        closed = profile["intent_done"][-1]
        self.assertEqual(closed["by"], "world")
        self.assertIn(closed["status"], ("fulfilled", "partial", "failed"))
        self.assertTrue(closed["outcome"])
        self.assertEqual(will.advance(h), [], "решённое не решается дважды")
        shown = will.offscreen(h)
        self.assertIn("Кадзиура", shown[0])
        self.assertIn("Против Шики: вина ляжет на Шики", shown[0])
        for _ in range(will.SHOW_FOLDS - 1):
            will.offscreen(h)
        self.assertEqual(will.offscreen(h), [])
        self.assertEqual([k for k, _ in will.without_plan(h)], ["kadziura"])

    def test_keeper_closes_a_played_plan_once(self):
        h = state()
        will.seed(self.root, h)
        fix = dict(SEED)
        fix["kadziura"] = dict(SEED["kadziura"], close={"cover": {"status": "done", "outcome": "лист переписан"}})
        (self.root / will.SEED).write_text(json.dumps(fix, ensure_ascii=False), encoding="utf-8")
        will.seed(self.root, h)
        profile = h["_psyche"]["kadziura"]
        self.assertNotIn("cover", profile["intent"])
        self.assertEqual(profile["intent_done"][-1]["status"], "fulfilled")
        self.assertEqual(profile["intent_done"][-1]["by"], "keeper")

    def test_roll_is_deterministic(self):
        self.assertEqual(will._roll("a", "b", "c"), will._roll("a", "b", "c"))
        self.assertTrue(1 <= will._roll("a", "b", "c") <= 100)

    def test_passive_significant_npc_is_asked_about_his_own_move(self):
        h = state()
        will.seed(self.root, h)
        for n in range(4):
            will.track(h, {"id": f"s04.text.{45 + n:03}"}, {})
        notes = [n["text"] for n in will.review(h, [{"id": "s04.text.048"}])]
        self.assertTrue(any("4 ход(ов) рядом с Шики только отвечает" in n for n in notes))
        will.track(h, {"id": "s04.text.049"}, {"kadziura": {"rel": {"trust": -1}}})
        notes = [n["text"] for n in will.review(h, [{"id": "s04.text.049"}])]
        self.assertFalse(any("только отвечает" in n for n in notes))

    def test_significant_npc_without_will_is_flagged(self):
        h = state()
        h["_psyche"] = {}
        notes = [n["text"] for n in will.review(h, [{"id": "s04.text.045"}])]
        self.assertTrue(any("а воли у него нет" in n for n in notes))

    def test_view_for_the_brief(self):
        h = state()
        will.seed(self.root, h)
        view = will.view(h, "kadziura")
        self.assertEqual(view["plans"]["cover"]["against_shiki"], "вина ляжет на Шики")
        self.assertIn("decides", view)


class WillFoldTest(unittest.TestCase):
    setUp, tearDown = test_hot.HotFilesTest.setUp, test_hot.HotFilesTest.tearDown
    git, commit, put = test_hot.HotFilesTest.git, test_hot.HotFilesTest.commit, test_hot.HotFilesTest.put

    def test_seed_reaches_gm_only_and_brief(self):
        seed = {"kabuto": {"psyche": {"decides": "по приказу"},
                          "intent": {"report": {"goal": "доложить", "next": "отнести рапорт",
                                                "due": "0006-10-01T08:00:00"}}}}
        (self.root / "memory/_curated").mkdir(parents=True, exist_ok=True)
        (self.root / will.SEED).write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
        self.put(turn(45, "0006-10-01T07:14:00"))
        self.assertEqual(hot.fold(self.root)["errors"], [])
        secret = json.loads((self.root / hot.SECRET_PATH).read_text(encoding="utf-8"))
        self.assertEqual(secret["psyche"]["kabuto"]["intent"]["report"]["next"], "отнести рапорт")
        self.assertIn("seed", secret["will"])
        public = (self.root / hot.HOT_PATH).read_text(encoding="utf-8")
        self.assertNotIn("отнести рапорт", public)
        brief = json.loads((self.root / "recovery/GM_BRIEF.json").read_text(encoding="utf-8"))
        self.assertEqual(brief["agendas"]["soon"][0]["next"], "отнести рапорт")


if __name__ == "__main__":
    unittest.main()


class KeeperFillTest(unittest.TestCase):
    def test_fill_only_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory/_curated").mkdir(parents=True)
            (root / hot.KEEPER_FILL).write_text(json.dumps(
                {"_note": "x", "shiki": {"chakra": {"value": 0}, "body": "не затирать"}}), encoding="utf-8")
            h = {"shiki": {"body": "записано ведущим"}}
            hot._keeper_fill(root, h)
            self.assertEqual(h["shiki"], {"body": "записано ведущим", "chakra": {"value": 0}})
