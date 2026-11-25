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
