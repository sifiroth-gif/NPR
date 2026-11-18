"""Инструкции ведущего не должны снова разрастись и разойтись."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rpg_engine import hot, prose_lint  # noqa: E402

SKILL = ROOT / "skills/dark-literary-gm"
BUDGET = {
    "AGENTS.md": 6000,
    "campaigns/shiki/GM_CORE.md": 16000,
    "skills/dark-literary-gm/SKILL.md": 8000,
    "campaigns/shiki/ref/MEDICINE.md": 6000,
    "campaigns/shiki/ref/COMBAT.md": 6000,
    "campaigns/shiki/ref/KNOWLEDGE.md": 6000,
    "campaigns/shiki/ref/MEMORY.md": 6000,
    "campaigns/shiki/ref/HORROR.md": 6000,
    "campaigns/shiki/BOOT.md": 2000,
}


class InstructionBudgetTest(unittest.TestCase):
    def test_instruction_files_stay_within_budget(self):
        for name, limit in BUDGET.items():
            size = len((ROOT / name).read_text(encoding="utf-8"))
            self.assertLessEqual(size, limit, f"{name}: {size} знаков, предел {limit}. Замени правило, не добавляй.")

    def test_router_has_no_priority_patches(self):
        for name in ("AGENTS.md", "campaigns/shiki/GM_CORE.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?m)^#+ .*\bP0\b", f"{name}: разделы-заплатки «P0» запрещены")

    def test_router_points_to_existing_files(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for path in ("campaigns/shiki/GM_CORE.md", "skills/dark-literary-gm/SKILL.md",
                     "docs/ARCHITECTURE.md", "tests/test_instruction_budget.py"):
            self.assertIn(path, text)
            self.assertTrue((ROOT / path).is_file(), path)
        for path in (hot.HOT_PATH, hot.SECRET_PATH, hot.TURN_PATH):
            self.assertIn(path, text)

    def test_core_documents_the_turn_schema(self):
        text = (ROOT / "campaigns/shiki/GM_CORE.md").read_text(encoding="utf-8")
        self.assertIn(hot.TURN_SCHEMA, text)
        for kind in hot.PACT_EVENT_POINTS:
            self.assertIn(f"`{kind}`", text)
        example = re.search(r"```json\n(.*?)\n```", text, re.S).group(1)
        import json
        turn = json.loads(example)
        hot.validate_turn(turn)
        state = {"session": 4, "cursor": {"id": "s04.text.044", "seq": 44, "at": "0006-10-01T07:10:00"},
                 "cast": {"nori": {"knows": []}, "sayu": {}}, "threads": {"tetsu": {"state": "x"}},
                 "offscreen": {"clocks": {"gate": {"what": "x"}}}, "pact": {"bound_at": "0006-09-27T06:30:19"}}
        self.assertTrue(hot.apply_turn(state, turn))
        self.assertNotIn("sayu", state["cast"])
        self.assertIn("new_clock", state["offscreen"]["clocks"])

    def test_skill_linter_is_the_engine_linter(self):
        self.assertEqual((SKILL / "scripts/prose_lint.py").read_bytes(),
                         (ROOT / "rpg_engine/prose_lint.py").read_bytes())

    def test_skill_examples_teach_what_the_linter_checks(self):
        text = (SKILL / "references/before-after.md").read_text(encoding="utf-8")
        before = re.findall(r"\*\*До\.\*\* (.+)", text)
        after = re.findall(r"\*\*После\.\*\* («.+)", text)
        self.assertGreaterEqual(len(before), 8)
        self.assertGreaterEqual(len(after), 7)
        def plain(sample):
            sample = sample[sample.index("«"):]
            return sample.replace("» «", "\n\n").strip("«»")

        missed = [sample for sample in before if not prose_lint.lint(plain(sample))["hits"]]
        self.assertEqual(missed, [], "линтер перестал ловить реальные провалы")
        for sample in after:
            self.assertEqual(prose_lint.lint(plain(sample))["hits"], [], sample)

    def test_core_and_skill_do_not_fail_their_own_rules(self):
        for name in ("campaigns/shiki/GM_CORE.md", "campaigns/shiki/ref/MEDICINE.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for banned in ("обучени", "понятным русским названием", "границы согласия"):
                self.assertNotIn(banned, text, f"{name}: вернулось отменённое правило «{banned}»")


if __name__ == "__main__":
    unittest.main()
