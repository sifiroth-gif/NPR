"""Regression: fast scene packets retain live plot, exact date and sourced NPC bounds."""
import json
import tempfile
import unittest
from pathlib import Path
from rpg_engine import Campaign, preparation

AT = "0006-09-27T22:05:34"
PACK = Path(__file__).resolve().parents[1] / "campaigns/shiki/SCENE_PACK.json"

class FourthSessionFocus(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "campaign.sqlite"
        self.game = Campaign.create(self.db, {
            "id":"naruto-shiki", "title":"Synthetic Shiki",
            "start_at":AT, "location":"Нижний госпиталь, Седьмая палата",
            "player":{"id":"shiki","name":"Мугами Шики"},
            "npcs":[
                {"id":"utsuro","name":"Уцуро","profile":{
                    "role":"запретное призывное существо",
                    "goal":"держать каменную угрозу за пределами ложа Саю",
                    "knowledge_limits":"образец Ш взят, но ещё не исследован"}},
                {"id":"sayu","name":"Саю","profile":{
                    "role":"пациентка",
                    "goal":"выжить, сохранив право на свои решения"}},
            ]
        })
        for revision in range(3):
            self.game.next_session(revision)
        self.assertEqual(self.game.verify()["session"],4)

    def test_focus_has_bounded_causal_spine_and_short_rules(self):
        p=preparation.packet(self.game,mode="focus",actors=("utsuro","sayu"),pack_path=PACK)
        self.assertEqual(p["confirmed"]["session"],4)
        self.assertIn("1283",p["heading"])
        self.assertTrue(p["heading"].startswith("среда,"), p["heading"])
        self.assertIn("Седьмая",p["heading"])
        self.assertEqual(p["scene_spine"]["source"],"sessions/SESSION_004.json")
        self.assertIn("ближайшему физическому конфликту",p["scene_spine"]["objective"])
        self.assertLessEqual(len(p["scene_spine"]["next_causes"]),2)
        self.assertLess(sum(len(x) for x in p["rules"]),2700)
        self.assertTrue(p["npc_psychological_presence"]["required"])
        self.assertEqual(len(p["npc_psychological_presence"]["actors"]),2)
        self.assertEqual(p["npc_psychological_presence"]["actors"][0]["independent_aim"],
                         "держать каменную угрозу за пределами ложа Саю")
        self.assertEqual(p["actors"][0]["profile"]["knowledge_limits"],
                         "образец Ш взят, но ещё не исследован")

    def test_scene_and_handoff_keep_full_gm_rules_and_plan(self):
        focus=preparation.packet(self.game,mode="focus",actors=("utsuro",),pack_path=PACK)
        scene=preparation.packet(self.game,mode="scene",actors=("utsuro",),pack_path=PACK)
        handoff=preparation.packet(self.game,mode="handoff",actors=("utsuro",),pack_path=PACK)
        self.assertGreater(sum(map(len,scene["rules"])),sum(map(len,focus["rules"])))
        self.assertEqual(scene["session_plan"]["title"],"Седьмая палата: цена сохранённой жизни")
        self.assertEqual(handoff["session_plan"]["source"],"sessions/SESSION_004.json")
        self.assertEqual(handoff["confirmed"]["at"],AT)
        self.assertNotIn("scene_spine",handoff)

    def test_era_calendar_preserves_pre_skip_weekday(self):
        pack=json.loads(PACK.read_text())
        self.assertTrue(preparation.heading(
            "0001-07-28T18:18:00","полигон",pack["calendar"]).startswith("воскресенье,"))
        self.assertTrue(preparation.heading(
            "0006-09-26T09:28:19","коллектор",pack["calendar"]).startswith("вторник,"))
        self.assertTrue(preparation.heading(
            "0006-09-27T22:05:34","палата",pack["calendar"]).startswith("среда,"))

    def test_sourced_presence_cannot_imply_secret_lab_analysis(self):
        cues=preparation.psychological_presence_contract("naruto-shiki", [
            {"id":"utsuro","name":"Уцуро","profile":{"goal":"защитить Саю"},
             "current_beliefs":[{"topic":"shiki.blood_sample","source_event":"s3.canon.073",
                                 "belief":"образец получен и не исследован"}]}
        ],"Уцуро ты исследовал мою кровь?")
        self.assertEqual(cues["actors"][0]["known_topics"],["shiki.blood_sample"])
        self.assertEqual(cues["actors"][0]["known_source_events"],["s3.canon.073"])
        self.assertTrue(cues["latest_player_input"].startswith("Уцуро"))

if __name__ == "__main__":
    unittest.main()
