"""The D&D moral compass is GM-only and never assigned to background extras."""
import json
import tempfile
import unittest
from pathlib import Path
from rpg_engine import live_scene

def state():
    return {
        "cursor": {"id": "s08.text.001", "at": "0006-10-03T01:35:00",
                   "place": "doctor's office", "time": "01:35", "date": "Вторник"},
        "scene": {"present": ["shiki", "doctor", "sentry"]},
        "cast": {
            "doctor": {"name": "Doctor", "role": "physician", "wants": "treat the patient"},
            "sentry": {"name": "Guard", "role": "ordinary sentry", "wants": "stay at post"}},
        "last": {"player": "Shiki says nothing", "gm": "The doctor closes the vial."},
        "offscreen": {"truth": {}, "clocks": {}},
        "threads": {}, "shiki": {},
    }

def character(code):
    return {
        "name": "Doctor", "scope": "active",
        "alignment": {"code": code, "label": "example", "status": "provisional"},
        "order": "patients over office regulations",
        "morality": "protect the patient",
        "line": "no blind injection",
        "breaking_point": "imminent respiratory failure",
    }

class StoryAlignmentTests(unittest.TestCase):
    def test_only_curated_plot_npc_receives_gm_alignment(self):
        brief = live_scene.build(state(), [], alignments={"doctor": character("NG")})
        self.assertEqual(brief["present_npcs"]["doctor"]["alignment"]["code"], "NG")
        self.assertEqual(brief["present_npcs"]["doctor"]["alignment"]["line"], "no blind injection")
        self.assertNotIn("alignment", brief["present_npcs"]["sentry"])
        self.assertNotIn("shiki", brief["present_npcs"])

    def test_no_automatic_inference_from_rank_or_profession(self):
        value = state()
        value["cast"]["sentry"]["role"] = "Senior elite commander"
        brief = live_scene.build(value, [], alignments={"doctor": character("LN")})
        self.assertNotIn("alignment", brief["present_npcs"]["sentry"])

    def test_private_registry_loaded_without_modifying_hot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p = root / live_scene.ALIGNMENT_PATH
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({
                "schema": live_scene.ALIGNMENT_SCHEMA,
                "characters": {"doctor": character("NG")}
            }), encoding="utf-8")
            hot = state()
            brief = live_scene.build_for_state(hot, [], root)
            self.assertEqual(brief["present_npcs"]["doctor"]["alignment"]["code"], "NG")
            self.assertNotIn("alignment", hot["cast"]["doctor"])
            self.assertNotIn("alignment", hot["cast"]["sentry"])

    def test_wrong_code_or_player_alignment_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / live_scene.ALIGNMENT_PATH
            p.parent.mkdir(parents=True)
            for ident, code in (("doctor", "LG_plus"), ("shiki", "NG")):
                p.write_text(json.dumps({
                    "schema": live_scene.ALIGNMENT_SCHEMA,
                    "characters": {ident: character(code)}
                }), encoding="utf-8")
                with self.assertRaises(ValueError):
                    live_scene.load_alignments(temp)

    def test_legacy_campaign_without_registry_still_works(self):
        with tempfile.TemporaryDirectory() as temp:
            brief=live_scene.build_for_state(state(), [], temp)
            self.assertNotIn("alignment", brief["present_npcs"]["doctor"])

if __name__ == "__main__":
    unittest.main()
