import tempfile
from pathlib import Path
import unittest

from rpg_engine import Campaign
from rpg_engine.knowledge_gate import FORMAT, validate_scene

AT = "2001-01-01T12:00:00"
POLICY = {"speakers":{"Рэн":"ren","Аки":"aki"},
          "topics":{"street.beam":{"triggers":["балк"]}}}


class SourceAcquisitionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.game = Campaign.create(Path(tmp.name) / "campaign.sqlite", {
            "id":"provenance-test","title":"Provenance test",
            "start_at":AT,"location":"Room",
            "player":{"id":"shiki","name":"Shiki"},
            "npcs":[{"id":"ren","name":"Ren"},{"id":"aki","name":"Aki"}],
        })

    def run_event(self, *, actor="ren", evidence="witnessed", witnesses=None, routes=None):
        ev = {"id":"source.1","at":AT,"summary":"Source of knowledge",
              "visibility":"gm_only", "operations":[{
                  "op":"set_belief","actor":actor,"topic":"street.beam",
                  "belief":{"text":"Beam fell on Shiki"},"evidence":evidence,
                  "certainty":"certain","visibility":"gm_only",
              }]}
        if witnesses is not None:
            ev["witnesses"] = witnesses
        if routes is not None:
            ev["knowledge_routes"] = routes
        packet = {"id":"t1","expected_revision":0,"at":AT,
                  "location":"Room","player_input":"#говорю#",
                  "text":"Время прошло.","events":[ev]}
        prepared = self.game.prepare(packet)
        self.game.confirm("t1", prepared["draft_sha"])
        return self.game.entry("event","source.1",audience="gm")

    def test_other_person_witness_does_not_grant_ren_memory(self):
        with self.assertRaisesRegex(ValueError, "direct NPC belief requires named witness"):
            self.run_event(witnesses=["aki"])
        self.assertEqual(self.game.verify()["revision"], 0)

    def test_personal_witness_is_preserved_in_source_and_allows_dialogue(self):
        ev = self.run_event(witnesses=["ren"])
        self.assertEqual(ev["witnesses"], ["ren"])
        line = "Шики получил балкой по плечу."
        output = validate_scene(self.game, "**Рэн:** — "+line,
            {"format":FORMAT,"lines":[{
                "actor":"ren","text":line,"claims":[
                    {"topic":"street.beam","stance":"assert"}]
            }]}, POLICY)
        self.assertEqual(output["status"], "ready")
        with self.assertRaisesRegex(ValueError, "no personal belief"):
            validate_scene(self.game, "**Аки:** — "+line,
                {"format":FORMAT,"lines":[{
                    "actor":"aki","text":line,"claims":[
                        {"topic":"street.beam","stance":"assert"}]
                }]}, POLICY)

    def test_report_needs_personal_information_route(self):
        with self.assertRaisesRegex(ValueError, "requires an explicit information route"):
            self.run_event(actor="aki",evidence="reported",witnesses=["ren"])
        self.assertEqual(self.game.verify()["revision"], 0)
        ev = self.run_event(actor="aki",evidence="reported",witnesses=["ren"],
            routes=[{"actor":"aki","via":"reported","source":"Ren told Aki directly."}])
        self.assertEqual(ev["knowledge_routes"][0]["actor"], "aki")

    def test_reported_knowledge_cannot_be_just_a_witness_without_message_route(self):
        with self.assertRaisesRegex(ValueError, "requires an explicit information route"):
            self.run_event(actor="aki", evidence="reported", witnesses=["aki"])

    def test_wrong_route_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "disagrees with belief evidence"):
            self.run_event(actor="aki",evidence="document",witnesses=["ren"],
                routes=[{"actor":"aki","via":"reported","source":"Ren told Aki directly."}])


if __name__ == "__main__":
    unittest.main()
