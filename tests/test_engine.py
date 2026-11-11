import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from rpg_engine import Campaign
from rpg_engine.engine import ConflictError

START = "2000-01-01T09:00:00"
NOON = "2000-01-01T12:00:00"
LATER = "2000-08-01T12:00:00"


def setup(**overrides):
    return {
        "id": "test-world", "title": "Test world", "start_at": START, "location": "Gate",
        "player": {"id": "pc", "name": "Player"}, **overrides,
    }


def packet(**overrides):
    return {
        "id": "t1", "expected_revision": 0, "at": START, "location": "Gate",
        "player_input": "#осматриваю ворота# Кто здесь?", "text": "Из-за ворот слышен голос.",
        **overrides,
    }


def event(identifier, *operations, at=START, visibility="gm_only", summary="Private event", **extra):
    return {
        "id": identifier, "at": at, "summary": summary, "visibility": visibility,
        "operations": list(operations), **extra,
    }


def fact(name, value, visibility="gm_only"):
    return {"op": "set_fact", "subject": "gate", "name": name, "value": value,
            "visibility": visibility}


def plan(visibility="gm_only", owner="watcher"):
    return {"op": "start_process", "id": "watch", "owner": owner, "goal": "Watch the gate",
            "due_at": NOON, "visibility": visibility, "notes": "Private motive"}


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "game.sqlite"
        self.game = Campaign.create(self.db, setup())

    def test_empty_bootstrap_and_never_overwrite_existing_game(self):
        self.assertEqual(self.game.context()["revision"], 0)
        self.assertEqual(self.game.context()["recent"], [])
        self.assertEqual(self.game.world(audience="gm"), {"facts": [], "beliefs": [], "processes": [], "npcs": []})
        with self.assertRaises(FileExistsError):
            Campaign.create(self.db, setup(title="Replacement"))
        self.assertEqual(self.game.context()["campaign"]["title"], "Test world")

        bad_path = self.db.with_name("bad.sqlite")
        with self.assertRaisesRegex(ValueError, "owner must be an NPC"):
            Campaign.create(bad_path, setup(operations=[plan(owner="pc")]))
        self.assertFalse(bad_path.exists())

    def test_prepare_confirm_and_search_preserve_exact_text(self):
        original = packet(events=[event("secret", fact("hidden", "Шифр"), summary="Криптограмма"),
                                  event("visible", fact("open", True, "player_known"),
                                        summary="Ворота открылись", visibility="player_known",
                                        based_on=["secret"])])
        prepared = self.game.prepare(original)
        self.assertEqual(prepared, Campaign(self.db).prepare(original))
        self.assertEqual(prepared["text"], original["text"])
        self.assertEqual(self.game.context()["revision"], 0)
        self.assertNotIn("pending", self.game.context())
        self.assertEqual(self.game.world()["facts"], [])
        self.assertEqual(self.game.search("ворота", audience="gm"), [])

        confirmed = Campaign(self.db).confirm("t1", prepared["draft_sha"])
        self.assertEqual(confirmed, self.game.confirm("t1", prepared["draft_sha"]))
        self.assertEqual(confirmed, self.game.prepare(original))
        with self.assertRaises(ConflictError):
            self.game.prepare({**original, "text": "A different response"})
        self.assertEqual(self.game.context()["recent"][0]["player_input"], original["player_input"])
        self.assertEqual(self.game.context()["recent"][0]["text"], original["text"])
        self.assertEqual([f["name"] for f in self.game.world()["facts"]], ["open"])
        self.assertEqual(self.game.search("Криптограмма"), [])
        self.assertEqual(self.game.search("Криптограмма", audience="gm")[0]["id"], "secret")
        self.assertEqual({hit["id"] for hit in self.game.search("ворота")}, {"t1", "visible"})
        self.assertEqual(self.game.entry("event", "visible", audience="gm")["based_on"], ["secret"])
        self.assertNotIn("based_on", self.game.entry("event", "visible"))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.game.entry("event", "secret")

    def test_rejected_candidate_leaves_world_and_fts_untouched(self):
        events = [event("one", fact("open", True, "player_known"), summary="Unique clue"),
                  event("two", {"op": "delete_fact", "subject": "gate", "name": "absent"})]
        with self.assertRaisesRegex(ValueError, "missing fact"):
            self.game.prepare(packet(events=events))
        self.assertEqual(self.game.search("unique", audience="gm"), [])
        self.assertIsNone(self.game.context(audience="gm")["pending"])
        self.assertEqual(self.game.world(audience="gm")["facts"], [])
        self.game.prepare(packet(events=events[:1]))

    def test_rolls_survive_retries_reopen_and_confirmation(self):
        request = {"turn_id": "t1", "expected_revision": 0, "check_id": "cross",
                   "count": 2, "sides": 6, "modifier": -1, "purpose": "Check footing"}
        roll = self.game.roll(**request)
        self.assertEqual(roll, Campaign(self.db).roll(**request))
        self.assertEqual(roll["result"]["total"], sum(roll["result"]["dice"]) - 1)
        self.assertTrue(all(1 <= die <= 6 for die in roll["result"]["dice"]))
        with self.assertRaisesRegex(ConflictError, "different contract"):
            self.game.roll(**{**request, "sides": 8})
        with self.assertRaisesRegex(ConflictError, "working turn is t1"):
            self.game.roll(**{**request, "turn_id": "other"})
        first = self.game.prepare(packet())
        with self.assertRaisesRegex(ConflictError, "reopen"):
            self.game.roll(**{**request, "check_id": "extra"})
        self.game.reopen("t1", first["draft_sha"])
        second = self.game.prepare(packet())
        self.assertNotEqual(first["draft_sha"], second["draft_sha"])
        with self.assertRaises(ConflictError):
            self.game.confirm("t1", first["draft_sha"])
        self.game.confirm("t1", second["draft_sha"])
        self.assertEqual(roll, self.game.roll(**request))
        with self.assertRaisesRegex(ConflictError, "already confirmed"):
            self.game.roll(**{**request, "check_id": "extra"})

    def test_event_boundary_blocks_jump_and_explicit_resolution_enables_it(self):
        npc = {"op": "upsert_npc", "npc": {"id": "watcher", "name": "Watcher",
                                          "profile": {"goals": ["Guard the gate"]}}}
        start = self.game.prepare(packet(events=[event("orders", npc, plan())]))
        self.game.confirm("t1", start["draft_sha"])
        self.assertEqual(self.game.context(audience="gm")["next_process"]["due_at"], NOON)
        stop = {"id": "t2", "expected_revision": 1, "at": NOON}
        jump = {**stop, "at": LATER}
        resolution = {"op": "finish_process", "id": "watch", "status": "resolved",
                      "outcome": {"report": "Gate checked"}}
        with self.assertRaisesRegex(ValueError, "review process watch"):
            self.game.prepare(packet(**jump, events=[event("too-late", resolution, at=LATER)]))

        at_boundary = self.game.prepare(packet(**stop))
        self.game.confirm("t2", at_boundary["draft_sha"])
        self.assertEqual(self.game.context(audience="gm")["next_process"]["id"], "watch")
        final = self.game.prepare(packet(id="t3", expected_revision=2, at=LATER, events=[
            event("report", resolution, at=NOON, based_on=["orders"]),
        ]))
        self.game.confirm("t3", final["draft_sha"])
        self.assertEqual(self.game.context()["at"], LATER)
        self.assertIsNone(self.game.context(audience="gm")["next_process"])
        state = self.game.world(audience="gm", subject="watcher")
        self.assertEqual(state["npc"]["profile"]["goals"], ["Guard the gate"])
        self.assertEqual(state["processes"][0]["status"], "resolved")
        self.assertEqual(state["processes"][0]["source_event"], "report")

    def test_hidden_process_outcome_and_npc_belief_stay_private(self):
        npc = {"op": "upsert_npc", "npc": {"id": "watcher", "name": "Watcher"}}
        belief = {"op": "set_belief", "actor": "watcher", "topic": "gate", "belief": "False lead",
                  "evidence": "reported", "certainty": "uncertain", "visibility": "player_known"}
        first = self.game.prepare(packet(events=[event("orders", npc, plan("player_known"), belief)]))
        self.game.confirm("t1", first["draft_sha"])
        self.assertEqual(self.game.world()["processes"][0]["status"], "active")
        self.assertNotIn("notes", self.game.world()["processes"][0])
        self.assertEqual(self.game.world()["beliefs"], [])

        resolution = {"op": "finish_process", "id": "watch", "status": "resolved", "outcome": "Secret result"}
        second = self.game.prepare(packet(id="t2", expected_revision=1, at=NOON,
                                          events=[event("report", resolution, at=NOON)]))
        self.game.confirm("t2", second["draft_sha"])
        self.assertEqual(self.game.world()["processes"], [])
        self.assertEqual(self.game.world(audience="gm")["processes"][0]["outcome"], "Secret result")

    def test_checkpoint_restores_working_draft_and_sessions(self):
        args = {"turn_id": "t1", "expected_revision": 0, "check_id": "roll"}
        roll = self.game.roll(**args)
        prepared = self.game.prepare(packet())
        checkpoint = self.db.with_name("copy.sqlite")
        backup = self.game.checkpoint(checkpoint)
        self.assertTrue(backup["pending"])
        restored = Campaign(checkpoint)
        self.assertEqual(restored.roll(**args), roll)
        self.assertEqual(restored.context(audience="gm")["pending"]["packet"], packet())
        with self.assertRaisesRegex(ConflictError, "working turn"):
            restored.next_session(0)
        restored.confirm("t1", prepared["draft_sha"])
        next_session = restored.next_session(1)
        self.assertEqual(next_session, restored.next_session(1))
        self.assertEqual(restored.context()["session"], 2)
        self.assertEqual(restored.context()["at"], START)
        self.assertEqual(restored.context()["recent"][0]["session"], 1)
        self.assertEqual(self.game.context()["revision"], 0)
        with self.assertRaises(FileExistsError):
            self.game.checkpoint(checkpoint)

    def test_concurrent_drafts_reserve_one_turn(self):
        barrier = threading.Barrier(2)

        def prepare(turn_id):
            barrier.wait(timeout=5)
            try:
                return Campaign(self.db).prepare(packet(id=turn_id))
            except ConflictError:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            candidates = list(pool.map(prepare, ("t1", "t2")))
        winners = [candidate for candidate in candidates if candidate is not None]
        self.assertEqual(len(winners), 1)
        winner, = winners
        self.game.confirm(winner["turn_id"], winner["draft_sha"])
        self.assertEqual(len(self.game.context()["recent"]), 1)

    def test_process_death_during_confirmation_rolls_back_and_allows_retry(self):
        prepared = self.game.prepare(packet(events=[event("one", fact("open", True, "player_known"))]))
        child = """
import os
import sys
from rpg_engine import Campaign, world
original = world.apply_operations
def crash(con, event, player):
    original(con, event, player)
    os._exit(91)
world.apply_operations = crash
Campaign(sys.argv[1]).confirm('t1', sys.argv[2])
"""
        result = subprocess.run([sys.executable, "-c", child, str(self.db), prepared["draft_sha"]],
                                timeout=10, capture_output=True)
        self.assertEqual(result.returncode, 91, result.stderr)
        self.assertEqual(self.game.context()["revision"], 0)
        self.assertEqual(self.game.world()["facts"], [])
        self.assertEqual(self.game.context(audience="gm")["pending"]["draft_sha"], prepared["draft_sha"])
        self.game.confirm("t1", prepared["draft_sha"])
        self.assertEqual(self.game.world()["facts"][0]["name"], "open")

    def test_ooc_does_not_change_campaign(self):
        with self.assertRaisesRegex(ValueError, "out-of-character"):
            self.game.prepare(packet(player_input="Мастеру: сколько времени прошло?"))
        self.assertEqual(self.game.context()["revision"], 0)
        self.assertIsNone(self.game.context(audience="gm")["pending"])

    def test_cli_round_trip(self):
        root = self.db.parent

        def cli(*args):
            result = subprocess.run([sys.executable, "-m", "rpg_engine", "--db", str(root / "cli.sqlite"), *args],
                                    timeout=10, check=True, capture_output=True, text=True)
            return json.loads(result.stdout)

        configuration = root / "campaign.json"
        configuration.write_text(json.dumps(cli("template")), encoding="utf-8")
        self.assertEqual(cli("init", str(configuration))["revision"], 0)
        turn = root / "turn.json"
        turn.write_text(json.dumps(packet(at="0001-01-01T09:00:00")), encoding="utf-8")
        prepared = cli("prepare", str(turn))
        cli("confirm", "--turn", "t1", "--draft-sha", prepared["draft_sha"])
        self.assertEqual(cli("verify")["revision"], 1)
        self.assertEqual(cli("search", "ворота")[0]["text"], packet()["text"])


if __name__ == "__main__":
    unittest.main()
