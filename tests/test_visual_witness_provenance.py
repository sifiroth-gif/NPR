"""Eyewitness provenance: a shared party roster is not a shared line of sight."""
import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import acquaint, lore, presence


VISUAL = {
    "id": "trait_susanoo", "kind": "trait", "title": "Сусаноо Шики",
    "short": "Сусаноо", "terms": ["сусаноо"], "wield": ["shiki"],
    "_visual_witness": {
        "mode": "explicit",
        "episodes": {"s11.text.063": {
            "witnessed_by": ["sakura", "chiyo", "sasori"],
            "known_non_witnesses": ["naruto", "kakashi", "neji", "gaara"],
        }},
    },
}


def state():
    return {
        "cast": {
            "sakura": {"name": "Сакура Харуно"},
            "chiyo": {"name": "Тиё"},
            "naruto": {"name": "Наруто Узумаки"},
            "kakashi": {"name": "Какаши Хатакэ"},
        },
        "lore": {"trait_susanoo": {
            "sakura": "s11.text.063", "chiyo": "s11.text.063",
            "kakashi": "s11.text.063", "naruto": "s11.text.065",
        }},
    }


class SightlineTests(unittest.TestCase):
    def test_real_lore_entry_uses_main_branch_source_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory/_curated").mkdir(parents=True)
            (root / lore.SEED).write_text(
                json.dumps({"entries": [{k: v for k, v in VISUAL.items() if k != "_visual_witness"}]}),
                encoding="utf-8")
            entry = lore.load(root)[0]
            self.assertIn("_visual_witness", entry)
            self.assertIn("s11.text.063", entry["_visual_witness"]["episodes"])

    def test_stale_cache_does_not_turn_remote_teammates_into_witnesses(self):
        h = state()
        known = lore.heard(h, VISUAL)
        self.assertEqual(set(known), {"sakura", "chiyo"})
        naruto_view = acquaint.view(h, "naruto", {}, [VISUAL], 12)
        self.assertIn("не видел в данном эпизоде: Сусаноо в s11.text.063", naruto_view)
        self.assertNotIn("видел: Сусаноо 065", naruto_view)
        self.assertIn("видел: Сусаноо 063", acquaint.view(h, "sakura", {}, [VISUAL], 11))
        fence = lore.fences(h, [VISUAL], ["naruto", "kakashi", "sakura"])[0]
        self.assertIn("Сакура Харуно", fence)
        self.assertIn("Наруто Узумаки", fence)
        self.assertIn("нет подтверждения", fence)

    def test_reconcile_removes_only_false_derivative_cache(self):
        h = state()
        removed = lore.reconcile_visual_memory(h, [VISUAL])
        self.assertEqual(removed["trait_susanoo"],
                         {"kakashi": "s11.text.063", "naruto": "s11.text.065"})
        self.assertEqual(set(h["lore"]["trait_susanoo"]), {"sakura", "chiyo"})
        self.assertEqual(lore.reconcile_visual_memory(h, [VISUAL]), {})

    def test_mentioning_naruto_elsewhere_does_not_grant_sight(self):
        h = {"cast": state()["cast"], "lore": {}}
        names = {key: presence._entry([card["name"]]) for key, card in h["cast"].items()}
        row = {
            "id": "s11.text.063", "player": "",
            "gm": ("Шики развернул Сусаноо рядом с Сакурой и Тиё. "
                   "Наруто преследовал Дейдару далеко от убежища. "
                   "Какаши тоже был далеко."),
            "_spoke": [],
        }
        added = lore.track(h, row, [VISUAL],
                           ["sakura", "chiyo", "naruto", "kakashi"], names)
        self.assertEqual(set(added),
                         {("trait_susanoo", "sakura"), ("trait_susanoo", "chiyo")})
        self.assertEqual(set(h["lore"]["trait_susanoo"]), {"sakura", "chiyo"})

    def test_an_actor_cannot_become_witness_from_unreviewed_scene(self):
        h = {"cast": state()["cast"], "lore": {}}
        row = {"id": "s12.text.010", "player": "",
               "gm": "Какаши наблюдал, как Шики развернул Сусаноо."}
        names = {key: presence._entry([card["name"]]) for key, card in h["cast"].items()}
        self.assertEqual(lore.track(h, row, [VISUAL], ["kakashi"], names), [])
        h["cast"]["kakashi"]["knowledge_provenance"] = [{
            "claim": "trait_susanoo", "source": "s12.text.010",
            "channel": "witness", "turn": "s12.text.010",
        }]
        self.assertEqual(lore.heard(h, VISUAL)["kakashi"], "s12.text.010")

    def test_witness_claims_audit_target_not_just_speaker(self):
        h = state()
        row = {"id": "s12.text.006", "player": "",
               "gm": "**Сакура Харуно:** — Наруто видел Сусаноо."}
        notes = lore.review(h, row, [VISUAL], ["sakura", "naruto"], ["sakura", "naruto"])
        self.assertTrue(any("Недоказанное личное свидетельство" in n["text"] and
                            "Наруто" in n["text"] for n in notes), notes)

    def test_unverified_eyewitness_claim_of_kakashi_is_flagged(self):
        h = state()
        row = {"id": "s12.text.006", "player": "",
               "gm": "**Какаши Хатакэ:** — Я видел твоё Сусаноо."}
        notes = lore.review(h, row, [VISUAL], ["kakashi"], ["kakashi"])
        self.assertTrue(any("Недоказанное личное свидетельство" in n["text"] and
                            "Какаши" in n["text"] for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
