import copy
import json
import unittest

from rpg_engine import fight, hot, overlay, nextpack
from test_hot import base, turn


class LagProjectionTest(unittest.TestCase):
    def packet(self, state):
        brief = {"cursor": state["cursor"], "fight": fight.brief(state),
                 "turn_file": {"sha": "old", "turn": state["cursor"]["id"]}}
        return nextpack.build(state, brief, {}, [])

    def test_overlay_matches_folded_dice_and_chakra_without_mutating_packet(self):
        state = base()
        state["shiki"] = {"chakra": {"value": 40, "at": state["cursor"]["at"]}}
        pack = self.packet(state)
        old = copy.deepcopy(pack)
        t1 = turn(45, "0006-10-01T07:14:00", gm="Длинный текст " * 200,
                  review={"predecessor": "s04.text.044"},
                  d={"chakra": {"spend": [{"what": "x", "cost": 8}]}, "fight": {"bursts": 2}})
        t2 = turn(46, "0006-10-01T07:20:00", kind="ooc",
                  review={"predecessor": t1["id"]})
        rec = {"turn": t1, "sha": "one"}
        records = [rec, rec, {"turn": t2, "sha": "two"}]
        projected = overlay.project(pack, records)
        hot.apply_turn(state, t1)
        hot.apply_turn(state, t2)
        self.assertEqual(projected["dice"], fight.dice(state))
        self.assertEqual(projected["chakra"]["value"], state["shiki"]["chakra"]["value"])
        self.assertEqual(projected["next_id"], "s04.text.047")
        self.assertEqual(projected["turn_file"], "two")
        self.assertEqual(pack, old)

    def test_gap_disables_stale_dice_and_balance(self):
        pack = self.packet(base())
        t = turn(46, "0006-10-01T07:20:00", review={"predecessor": "s04.text.045"})
        result = overlay.project(pack, [{"turn": t, "sha": "one"}])
        self.assertEqual(result["lag_status"]["status"], "uncertain")
        self.assertNotIn("dice", result)
        self.assertNotIn("chakra", result)

    def test_must_receipts_do_not_replay_old_duty(self):
        pack = self.packet(base())
        pack["must"] = ["first", "second"]
        pack["must_ids"] = overlay.obligation_ids(pack["cursor"], pack["must"])
        t = turn(45, "0006-10-01T07:14:00", review={"predecessor": pack["cursor"]},
                 d={"must_done": [pack["must_ids"][0]]})
        result = overlay.project(pack, [{"turn": t, "sha": "one"}])
        self.assertNotIn("must", result)
        self.assertEqual([x["text"] for x in result["lag_status"]["unresolved_must"]], ["second"])

    def test_recorded_ahead_without_receipt_is_uncertain(self):
        pack = self.packet(base())
        pack["lag"]["recorded_turn"] = "s04.text.045"
        result = overlay.project(pack, [])
        self.assertEqual(result["lag_status"]["status"], "uncertain")
        self.assertNotIn("dice", result)

    def test_ahead_turn_file_requires_matching_receipt_sha(self):
        pack = self.packet(base())
        pack["lag"]["recorded_turn"] = "s04.text.045"
        pack["turn_file"] = "remote-amend"
        t = turn(45, "0006-10-01T07:14:00", review={"predecessor": pack["cursor"]})
        result = overlay.project(pack, [{"turn": t, "sha": "local-old"}])
        self.assertFalse(result["lag_status"]["write_allowed"])
        self.assertNotIn("turn_file", result)
        self.assertNotIn("next_id", result)
        result = overlay.project(pack, [{"turn": t, "sha": "remote-amend"}])
        self.assertTrue(result["lag_status"]["write_allowed"])

    def test_blocked_packet_cannot_supply_stale_resource_or_sha(self):
        pack = self.packet(base())
        pack["fold_errors"] = ["gap"]
        result = overlay.project(pack, [])
        self.assertFalse(result["lag_status"]["write_allowed"])
        for key in ("dice", "chakra", "must", "turn_file"):
            self.assertNotIn(key, result)

    def test_cas_never_overwrites_concurrent_edit(self):
        t = turn(45, "0006-10-01T07:14:00")
        raw = json.dumps(t).encode()
        self.assertEqual(overlay.cas_decision("old", raw, t)["status"], "already_written")
        changed = dict(t, gm="different")
        self.assertEqual(overlay.cas_decision("old", raw, changed)["status"], "conflict")
        self.assertEqual(overlay.cas_decision(overlay.blob_sha(raw), raw, changed)["status"], "retry_same_sha")

    def test_modern_wrong_predecessor_leaves_state_unchanged(self):
        state = base()
        state["session"] = 14
        state["cursor"].update({"id": "s14.text.023", "seq": 23})
        before = copy.deepcopy(state)
        t = turn(24, "0006-10-01T07:14:00", id="s14.text.024",
                 review={"predecessor": "s14.text.022"})
        with self.assertRaisesRegex(hot.TurnError, "predecessor"):
            hot.apply_turn(state, t)
        self.assertEqual(state, before)

    def test_stale_thematic_context_removed_on_lag(self):
        pack = self.packet(base())
        pack["capabilities"] = {"items": [{"id": "medical.sterilize"}],
                                "scene_hint": {"routes": ["medical"]}}
        pack["scene_context"] = {"as_of": pack["cursor"], "situation": "old"}
        pack["state_sources"] = {"medical_kit": {"state": "old"}}
        t = turn(45, "0006-10-01T07:14:00", review={"predecessor": pack["cursor"]},
                 d={"scene": {"situation": "new", "present": []}})
        result = overlay.project(pack, [{"turn": t, "sha": "one"}])
        self.assertEqual(result["scene_context"]["situation"], "new")
        self.assertNotIn("scene_hint", result["capabilities"])
        self.assertNotIn("state_sources", result)
        self.assertEqual(result["capabilities"]["items"], pack["capabilities"]["items"])
        self.assertIn("scene_hint", pack["capabilities"])

    def test_pending_directive_revoke_projects_once_without_stale_rule(self):
        from rpg_engine import directives
        pack = self.packet(base())
        old = "Впредь пиши коротко."
        ident = directives.stable_id(old)
        pack["directives"] = {"standing": [old], "standing_ids": [ident]}
        quote = "Отмени правило о коротких сценах."
        t = turn(45, "0006-10-01T07:14:00", kind="ooc", player=quote,
                 review={"predecessor": pack["cursor"]},
                 d={"directive_ops": [{"op": "revoke", "id": ident, "quote": quote}]})
        rec = {"turn": t, "sha": "one"}
        result = overlay.project(pack, [rec, rec])
        self.assertTrue(result["lag_status"]["write_allowed"])
        self.assertEqual(result["directives"]["standing"], [])
        self.assertEqual(pack["directives"]["standing"], [old])
