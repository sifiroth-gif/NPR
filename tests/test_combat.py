import json
import tempfile
import unittest
from pathlib import Path

from rpg_engine import Campaign
from rpg_engine.engine import ConflictError
from rpg_engine.combat import (
    FRAME_FORMAT, ATTEMPT_FORMAT, OUTCOME_FORMAT,
    CombatContractError, validate_frame, distance_m, assess_attempt,
    apply_outcome, outcome_world_event, contested_roll, validate_bands,
)

TIME = "0001-07-02T07:30:00"


def actor(x, y, *, perceives=(), consciousness="alert", mobility="normal",
          chakra="ready", stamina="ready", skills=None, learned=None,
          injury=None, reaction_min_ms=180, ready_at_ms=0):
    return {
        "position_m":[x,y],"consciousness":consciousness,"mobility":mobility,
        "chakra":chakra,"stamina":stamina,"perceives":list(perceives),
        "skills": skills if skills is not None else {},
        "learned":learned if learned is not None else [],
        "injuries":injury or {},"reaction_min_ms":reaction_min_ms,
        "ready_at_ms":ready_at_ms,
    }


def scene():
    return {
        "format":FRAME_FORMAT,"id":"s01.forest_skirmish","at_ms":0,
        "player_id":"shiki",
        "actors":{
            "shiki":actor(-1,0,perceives=["enemy"],skills={
                "taijutsu":1.5,"shunshin":3.5,"medical_ninjutsu":3.5,
                "genjutsu":3,"raiton":0.5,"sensory":3},
                learned=["shunshin","chakra_scalpel","genjutsu_blur","senbon"]),
            "enemy":actor(2,0,perceives=["shiki"],skills={"taijutsu":2},
                          learned=["kunai"]),
        },
    }


def attempt(*, actor_id="shiki", kind="attack", technique="senbon",
            ability="sensory", reach=8, distance_target="enemy", duration_ms=250,
            **overrides):
    ans={
        "format":ATTEMPT_FORMAT,"id":"s01.action.1","actor":actor_id,
        "kind":kind,"intent":"Попытаться нарушить хватку противника",
        "start_ms":0,"duration_ms":duration_ms,"cue_onset_ms":0,
        "source":{"kind":"player_declaration" if actor_id=="shiki" else "npc_intention",
                  "ref":"player #бросаю сенбон#" if actor_id=="shiki" else "confirmed enemy goal"},
        "target":distance_target,"ability":ability,"technique":technique,
        "reach_m":reach,"path_clear":True,
        "chakra_cost":"minor" if technique not in (None,"senbon") else None,
        "stamina_cost":"minor",
    }
    ans.update(overrides)
    return ans


class CombatMechanicsTests(unittest.TestCase):
    def test_scene_distance_signed_coordinates_and_perception(self):
        frame = scene()
        self.assertEqual(validate_frame(frame), frame)
        self.assertEqual(distance_m(frame,"shiki","enemy"),3)
        frame["actors"]["enemy"]["position_m"]=[-4,0]
        self.assertAlmostEqual(distance_m(frame,"shiki","enemy"),3)

    def test_attack_preflight_only_never_applies_hit_or_death(self):
        frame=scene()
        output=assess_attempt(frame,attempt())
        self.assertEqual(output["outcome"][:10],"UNRESOLVED")
        self.assertEqual(output["measured_distance_m"],3)
        self.assertEqual(output["reaction_window"],"possible_not_guaranteed")
        self.assertEqual(frame["actors"]["enemy"]["injuries"],{})

    def test_player_control_cannot_be_replaced_by_gm_intention(self):
        a=attempt()
        a["source"]={"kind":"npc_intention","ref":"GM decides"}
        with self.assertRaisesRegex(CombatContractError,"GM cannot invent"):
            assess_attempt(scene(),a)

    def test_npc_action_requires_its_own_intention(self):
        a=attempt(actor_id="enemy",ability="taijutsu",technique="kunai",
                  reach=4,distance_target="shiki")
        a["source"]={"kind":"player_declaration","ref":"player's choice"}
        with self.assertRaisesRegex(CombatContractError,"NPC action needs"):
            assess_attempt(scene(),a)

    def test_healthy_enemy_does_not_wait_for_multiattack(self):
        frame=scene()
        frame["actors"]["enemy"]["ready_at_ms"]=300
        result=assess_attempt(frame,attempt())
        self.assertEqual(result["reaction_window"],"already_committed")
        invalid=attempt(start_ms=0,duration_ms=250)
        frame["actors"]["shiki"]["ready_at_ms"]=1000
        with self.assertRaisesRegex(CombatContractError,"prior action"):
            assess_attempt(frame,invalid)

    def test_unseen_attack_does_not_grant_omniscient_defense(self):
        frame=scene()
        frame["actors"]["enemy"]["perceives"]=[]
        result=assess_attempt(frame,attempt())
        self.assertEqual(result["reaction_window"],"not_yet_detected")
        frame["actors"]["enemy"]["consciousness"]="unconscious"
        self.assertEqual(assess_attempt(frame,attempt())["reaction_window"],
                         "not_possible_unconscious")

    def test_no_impossible_instant_reaction(self):
        frame=scene()
        result=assess_attempt(frame,attempt(duration_ms=25))
        self.assertEqual(result["reaction_window"],
                         "too_late_given_observed_window")

    def test_reaction_uses_actual_cue_not_entire_action_duration(self):
        a=attempt(duration_ms=250,cue_onset_ms=175)
        self.assertEqual(assess_attempt(scene(),a)["reaction_window"],
                         "too_late_given_observed_window")
        a.pop("cue_onset_ms")
        self.assertEqual(assess_attempt(scene(),a)["reaction_window"],
                         "uncertain_no_grounded_timing")

    def test_no_automatic_perception_of_hidden_target(self):
        frame=scene()
        frame["actors"]["shiki"]["perceives"]=[]
        with self.assertRaisesRegex(CombatContractError,"not perceived"):
            assess_attempt(frame,attempt())
        a=attempt(blind_target_basis="Слышал движение на последней известной позиции")
        self.assertIn("UNRESOLVED",assess_attempt(frame,a)["outcome"])

    def test_contact_attack_must_close_distance_separately(self):
        with self.assertRaisesRegex(CombatContractError,"outside current reach"):
            assess_attempt(scene(), attempt(technique="chakra_scalpel",
                ability="taijutsu",reach=0.75,chakra_cost="minor"))
        frame=scene()
        frame["actors"]["shiki"]["position_m"]=[1.7,0]
        self.assertEqual(assess_attempt(frame,attempt(
            technique="chakra_scalpel",ability="taijutsu",reach=0.75,
            chakra_cost="minor"))["measured_distance_m"],0.3)

    def test_shunshin_movement_not_teleportation_or_contact_skill(self):
        a=attempt(kind="move",technique="shunshin",ability="shunshin",
                  destination_m=[1,0],target=None,reach_m=None,
                  chakra_cost="minor",stamina_cost="minor")
        self.assertAlmostEqual(assess_attempt(scene(),a)["measured_distance_m"],2)
        a["path_clear"]=False
        with self.assertRaisesRegex(CombatContractError,"not teleportation"):
            assess_attempt(scene(),a)
        b=attempt(technique="shunshin",ability="shunshin")
        with self.assertRaisesRegex(CombatContractError,"not taijutsu"):
            assess_attempt(scene(),b)

    def test_no_free_chakra_technique_or_student_raiton_nuke(self):
        a=attempt(kind="technique",technique="genjutsu_blur",ability="genjutsu",
                  stamina_cost=None)
        a.pop("chakra_cost")
        with self.assertRaisesRegex(CombatContractError,"explicit qualitative cost"):
            assess_attempt(scene(),a)
        a["chakra_cost"]="minor"
        self.assertEqual(assess_attempt(scene(),a)["kind"],"technique")
        frame=scene()
        frame["actors"]["shiki"]["learned"].append("raiton_pulse")
        b=attempt(kind="technique",technique="raiton_pulse",ability="raiton",
                  chakra_cost="minor")
        with self.assertRaisesRegex(CombatContractError,"cannot be assumed battle-reliable"):
            assess_attempt(frame,b)

    def test_impaired_player_requires_decision_not_forced_action(self):
        frame=scene()
        frame["actors"]["shiki"]["consciousness"]="unconscious"
        with self.assertRaisesRegex(CombatContractError,"unconscious"):
            assess_attempt(frame,attempt())

    def test_injury_persists_and_healing_requires_cause(self):
        frame=scene()
        injury={"severity":"moderate","mechanism":"Ушиб правого плеча",
                "functions_lost":["вынужденное снижение силы отведения плеча"]}
        out={"format":OUTCOME_FORMAT,"id":"out.1","result":"partial",
             "cause":"удар краем балки","at_ms":700,
             "changes":[{"actor":"shiki","injuries":{"right_shoulder":injury},
                         "stamina":"strained"}]}
        next_frame=apply_outcome(frame,out)
        self.assertEqual(frame["actors"]["shiki"]["injuries"],{})
        self.assertEqual(next_frame["actors"]["shiki"]["injuries"]["right_shoulder"],injury)
        out2={"format":OUTCOME_FORMAT,"id":"out.2","result":"success",
              "cause":"Освоенное локальное лечение", "at_ms":1500,
              "changes":[{"actor":"shiki",
                          "injuries":{"right_shoulder":{
                              **injury,"severity":"minor","functions_lost":[]}}}]}
        with self.assertRaisesRegex(CombatContractError,"cannot heal"):
            apply_outcome(next_frame,out2)
        out2["changes"][0]["treatments"]=["медицинское восстановление, с расходом чакры"]
        healed=apply_outcome(next_frame,out2)
        self.assertEqual(healed["actors"]["shiki"]["injuries"]["right_shoulder"]["severity"],"minor")

    def test_no_free_energy_reset(self):
        frame=scene()
        frame["actors"]["shiki"]["chakra"]="strained"
        out={"format":OUTCOME_FORMAT,"id":"out.3","result":"success","cause":"Выдох",
             "at_ms":500,"changes":[{"actor":"shiki","chakra":"ready"}]}
        with self.assertRaisesRegex(CombatContractError,"cannot recover"):
            apply_outcome(frame,out)
        out["changes"][0]["treatments"]=["восстановление после долгого отдыха и еды"]
        self.assertEqual(apply_outcome(frame,out)["actors"]["shiki"]["chakra"],"ready")

    def test_no_arbitrary_or_overlapping_probability_bands(self):
        with self.assertRaisesRegex(CombatContractError,"partition"):
            validate_bands([{"low":1,"high":49,"result":"failure"},
                            {"low":51,"high":100,"result":"success"}])
        with self.assertRaisesRegex(CombatContractError,"cover"):
            validate_bands([{"low":1,"high":99,"result":"failure"}])

    def test_stable_rng_for_contested_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            game=Campaign.create(Path(tmp)/"f.sqlite",{
                "id":"test-fight","title":"Fight","start_at":TIME,
                "location":"Forest","player":{"id":"shiki","name":"Shiki"}
            })
            bands=[{"low":1,"high":35,"result":"failure"},
                   {"low":36,"high":69,"result":"partial"},
                   {"low":70,"high":100,"result":"success"}]
            basis="Дистанция 3м, время 250мс, противник предупреждён, рука движется"
            first=contested_roll(game,turn_id="fight.01",expected_revision=0,
                      check_id="senbon.01",bands=bands,rationale=basis)
            second=contested_roll(game,turn_id="fight.01",expected_revision=0,
                      check_id="senbon.01",bands=bands,rationale=basis)
            self.assertEqual(first,second)
            with self.assertRaises(ConflictError):
                contested_roll(game,turn_id="fight.01",expected_revision=0,
                      check_id="senbon.01",bands=bands,rationale="Изменённые шансы задним числом")

    def test_private_frame_can_be_persisted_in_existing_world_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            game=Campaign.create(Path(tmp)/"f.sqlite",{
                "id":"test-fight","title":"Fight","start_at":TIME,
                "location":"Forest","player":{"id":"shiki","name":"Shiki"}
            })
            event=outcome_world_event(scene(),event_id="forest.combat.1",at=TIME,
                summary="Ведущий сохранил истинное скрытое расположение бойцов")
            self.assertEqual(event["visibility"],"gm_only")
            packet={"id":"t1","expected_revision":0,"at":TIME,
                    "location":"Forest","player_input":"#занимаю позицию#",
                    "text":"Противник приготовился.","events":[event]}
            r=game.prepare(packet)
            game.confirm("t1",r["draft_sha"])
            hidden=game.world(audience="gm",subject="combat:s01.forest_skirmish")
            self.assertEqual(hidden["facts"][0]["value"]["actors"]["enemy"]["position_m"],[2,0])
            self.assertEqual(game.world(audience="player",subject="combat:s01.forest_skirmish")["facts"],[])


if __name__=="__main__":
    unittest.main()
