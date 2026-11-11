"""End-to-end combat scene preparation: actor ownership, anatomy and stable RNG."""
import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import Campaign
from rpg_engine.engine import file_sha256
from rpg_engine.git_state import FORMAT
from rpg_engine.combat import (
    FRAME_FORMAT, ATTEMPT_FORMAT, OUTCOME_FORMAT,
    apply_outcome, outcome_world_event, contested_roll,
)
from rpg_engine.knowledge_gate import KnowledgeLeak
from campaigns.shiki.maintenance.prepare_reviewed_turn import main as reviewed_main

AT = "0001-07-02T07:30:00"


def fighter(pos, sees, skills, learned):
    return {"position_m":pos,"consciousness":"alert","mobility":"normal",
            "chakra":"ready","stamina":"ready","perceives":sees,
            "skills":skills,"learned":learned,"injuries":{},
            "ready_at_ms":0,"reaction_min_ms":180}


def frame():
    return {"format":FRAME_FORMAT,"id":"combat.s1","player_id":"shiki","at_ms":0,
            "actors":{
                "shiki":fighter([0,0],["enemy"],
                                {"taijutsu":1.5,"shunshin":3.5,"sensory":3,"medical_ninjutsu":3.5},
                                ["senbon","shunshin"]),
                "enemy":fighter([2,0],["shiki"],{"taijutsu":2},["kunai"])
            }}


def attempt():
    return {"format":ATTEMPT_FORMAT,"id":"fight.01","actor":"shiki",
            "source":{"kind":"player_declaration","ref":"#метаю сенбон#"},
            "intent":"Сбить хватку противника", "kind":"attack",
            "start_ms":0,"duration_ms":250,"cue_onset_ms":0,"target":"enemy",
            "technique":"senbon","ability":"sensory",
            "reach_m":6,"path_clear":True,"stamina_cost":"minor"}


PROSE = (
    "**Дата:** 2 июля 1278 года\n**Время:** 07:30\n**День:** вторник\n"
    "**Место:** Лес смерти, поляна\n\n"
    "Между Шики и противником ровно два метра, ближайшее дерево в трёх шагах. "
    "Противник перенёс вес на левую ногу, потянувшись к подсумку. "
    "Увидев Шики, юноша заметно напряг мышцы челюсти, хотя пытался улыбаться. "
    "Тонкий сенбон пересёк воздух, противник дёрнул предплечьем."
)
TACTICAL = "Между Шики и противником ровно два метра, ближайшее дерево в трёх шагах."
BODY = "Противник перенёс вес на левую ногу, потянувшись к подсумку."
PRESENCE = "Увидев Шики, юноша заметно напряг мышцы челюсти, хотя пытался улыбаться."


class CombatScenePipelineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.game = Campaign.create(self.state_dir/"campaign.sqlite",{
            "id":"naruto-shiki","title":"Combat synthetic",
            "start_at":AT,"location":"Лес смерти, поляна",
            "player":{"id":"shiki","name":"Шики"},
            "npcs":[{"id":"enemy","name":"Opponent"}]
        })
        self.manifest()

    def manifest(self):
        db=self.state_dir/"campaign.sqlite"
        m={"format":FORMAT,**self.game.verify(),"sha256":file_sha256(db)}
        (self.state_dir/"state.json").write_text(json.dumps(m,ensure_ascii=False),encoding="utf-8")

    def envelope(self):
        review={"format":"NPR-NPC-KNOWLEDGE-PREFLIGHT/1","lines":[],
                "p0":{"shiki_presence":PRESENCE,
                      "combat_present":True,"combat_body_language":BODY},
                "combat_review":{"format":"NPR-COMBAT-REVIEW/1","frame":frame(),
                    "tactical_excerpt":TACTICAL,"attempts":[attempt()],
                    "outcomes":[]}}
        turn={"id":"test.combat.01","expected_revision":0,"at":AT,
              "location":"Лес смерти, поляна","player_input":"#метаю сенбон#",
              "text":PROSE,"events":[]}
        # The first combat frame must be persisted even before any wound:
        # otherwise the next turn could silently shift positions.
        combat_review = review["combat_review"]
        combat_review["world_event_id"] = "fight.initial.frame"
        combat_review["event_summary"] = "Исходная геометрия и состояние участников"
        turn["events"].append(outcome_world_event(frame(),event_id="fight.initial.frame",
            at=AT,summary="Исходная геометрия и состояние участников"))
        return {"format":"NPR-SHIKI-REVIEWED-TURN/1","turn":turn,
                "knowledge_review":review}

    def run_review(self, envelope):
        p=self.root/"combat-review.json"
        p.write_text(json.dumps(envelope,ensure_ascii=False),encoding="utf-8")
        output=self.root/"output"
        reviewed_main(self.state_dir,p,output)
        return Campaign(output/"campaign.sqlite")

    def test_combat_preflight_prepares_exact_visible_draft(self):
        game=self.run_review(self.envelope())
        self.assertEqual(game.verify()["revision"],0)
        self.assertTrue(game.verify()["pending"])
        self.assertEqual(game.context(audience="gm")["pending"]["packet"]["text"],PROSE)

    def test_missing_combat_frame_blocks_turn(self):
        e=self.envelope()
        del e["knowledge_review"]["combat_review"]
        with self.assertRaisesRegex(KnowledgeLeak,"structured combat_review"):
            self.run_review(e)

    def test_player_attack_without_declaration_is_forbidden(self):
        e=self.envelope()
        e["turn"]["player_input"]="#молчу#"
        with self.assertRaisesRegex(KnowledgeLeak,"absent from actual player input"):
            self.run_review(e)

    def test_skill_inflation_cannot_erase_shikis_weak_taijutsu(self):
        e=self.envelope()
        e["knowledge_review"]["combat_review"]["frame"]["actors"]["shiki"]["skills"]["taijutsu"]=4.5
        with self.assertRaisesRegex(KnowledgeLeak,"exceeds verified lore"):
            self.run_review(e)

    def test_unbacked_claimed_hit_cannot_mutate_state(self):
        e=self.envelope()
        outcome={"format":OUTCOME_FORMAT,"id":"hit.01","attempt_id":"fight.01",
                 "resolution":"clear_causality","result":"partial","cause":"сенбон попал",
                 "at_ms":300,"changes":[{"actor":"enemy","injuries":{
                     "forearm":{"severity":"minor","mechanism":"укол сенбона",
                                "functions_lost":["ослаблен хват"]}}}]}
        e["knowledge_review"]["combat_review"]["outcomes"]=[outcome]
        # The existing first-frame event is not an outcome event.
        with self.assertRaisesRegex(KnowledgeLeak,"private persisted world event"):
            self.run_review(e)
        e["knowledge_review"]["combat_review"]["world_event_id"]="fight.world.1"
        e["knowledge_review"]["combat_review"]["event_summary"]="Нервный укол правого предплечья в ходе боя"
        projected=apply_outcome(frame(),outcome)
        e["turn"]["events"] = [outcome_world_event(projected,event_id="fight.world.1",
            at=AT,summary="Нервный укол правого предплечья в ходе боя")]
        result=self.run_review(e)
        self.assertEqual(result.verify()["revision"],0)
        self.assertTrue(result.verify()["pending"])
        self.assertEqual(self.game.world(audience="gm")["facts"],[])

    def test_registered_rng_must_match_narrated_result(self):
        e=self.envelope()
        bands=[{"low":1,"high":50,"result":"failure"},
               {"low":51,"high":100,"result":"success"}]
        rr=contested_roll(self.game,turn_id=e["turn"]["id"],expected_revision=0,
                          check_id="senbon.001",bands=bands,
                          rationale="Два метра, 250мс, видимая подготовка цели")
        self.manifest()
        actual=rr["result"]
        outcome={"format":OUTCOME_FORMAT,"id":"rng.1","attempt_id":"fight.01",
                 "resolution":"reserved_roll","check_id":"senbon.001",
                 "result":"success" if actual=="failure" else "failure",
                 "cause":"оспариваемое попадание","at_ms":300,
                 "changes":[{"actor":"enemy","stamina":"strained"}]}
        e["knowledge_review"]["combat_review"]["outcomes"]=[outcome]
        with self.assertRaisesRegex(KnowledgeLeak,"contradicts the reserved roll"):
            self.run_review(e)
        outcome["result"]=actual
        projected=apply_outcome(frame(),outcome)
        c=e["knowledge_review"]["combat_review"]
        c["world_event_id"]="rng.world"
        c["event_summary"]="Исполнение проверенной случайной развилки"
        e["turn"]["events"] = [outcome_world_event(projected,event_id="rng.world",
            at=AT,summary="Исполнение проверенной случайной развилки")]
        self.assertTrue(self.run_review(e).verify()["pending"])


if __name__ == "__main__":
    unittest.main()
