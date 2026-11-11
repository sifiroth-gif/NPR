import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rpg_engine import Campaign
from rpg_engine.engine import file_sha256
from rpg_engine.git_state import FORMAT
from rpg_engine.quick_save import process_request, REQUEST_FORMAT


def packet(turn_id, revision, at, *, events=None):
    return {"id": turn_id, "expected_revision": revision, "at": at, "location": "Room",
            "player_input": "#осматриваю комнату#", "input_mode": "in_character",
            "text": "[Восстановление из сыгранного диалога] Осмотрена комната.",
            "events": events or []}


class QuickSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "live"
        self.state_dir.mkdir()
        self.game = Campaign.create(self.state_dir / "campaign.sqlite", {
            "id": "test-campaign", "title": "Test", "player": {"id": "pc", "name": "Player"},
            "location": "Room", "start_at": "2000-01-01T09:00:00",
        })
        self.initial = self.game.verify()
        self._refresh_manifest()

    def _refresh_manifest(self):
        d = self.game.verify()
        state = {"format": FORMAT, **d,
                 "sha256": file_sha256(self.state_dir / "campaign.sqlite")}
        (self.state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def request(self, mode, **extra):
        return {"format": REQUEST_FORMAT, "mode": mode,
                "campaign_id": self.initial["campaign_id"],
                "instance": self.initial["instance"],
                "expected_revision": self.game.verify()["revision"],
                **extra}

    def test_append_confirms_seen_draft_then_records_batch(self):
        pending = packet("t0", 0, "2000-01-01T09:00:00")
        prepared = self.game.prepare(pending)
        self._refresh_manifest()
        events = [{
            "id": "e1", "at": "2000-01-01T09:01:00", "summary": "The gate opens",
            "visibility": "player_known", "operations": [{
                "op": "set_fact", "subject": "gate", "name": "open",
                "value": True, "visibility": "player_known"
            }]
        }]
        req = self.request("append", source_ref="chat:test-visible", observed_pending={
            "turn_id": "t0", "draft_sha": prepared["draft_sha"],
            "observed_text": pending["text"],
        }, turns=[
            packet("t1", 1, "2000-01-01T09:01:00", events=events),
            packet("t2", 2, "2000-01-01T09:02:00"),
        ])
        result = process_request(self.state_dir, req, self.root / "output")
        self.assertEqual(result["after_revision"], 3)
        self.assertFalse(result["pending"])
        saved = Campaign(self.root / "output" / "campaign.sqlite")
        self.assertEqual(saved.verify()["instance"], self.initial["instance"])
        self.assertEqual(saved.context()["recent"][-1]["id"], "t2")
        self.assertTrue(saved.world()["facts"][0]["value"])
        self.assertEqual(self.game.verify()["revision"], 0)

    def test_wrong_observed_response_refuses_confirmation(self):
        pending = packet("t0", 0, "2000-01-01T09:00:00")
        prepared = self.game.prepare(pending)
        self._refresh_manifest()
        req = self.request("append", source_ref="chat:test", observed_pending={
            "turn_id": "t0", "draft_sha": prepared["draft_sha"], "observed_text": "WRONG",
        }, turns=[packet("t1", 1, "2000-01-01T09:01:00")])
        with self.assertRaisesRegex(ValueError, "differs"):
            process_request(self.state_dir, req, self.root / "invalid")
        self.assertEqual(self.game.verify()["revision"], 0)

    def test_handoff_is_readonly_and_concise(self):
        result = process_request(self.state_dir, self.request("handoff", note="scene ongoing"),
                                 self.root / "handoff")
        self.assertEqual(result["revision"], 0)
        content = json.loads((self.root / "handoff" / "handoff.json").read_text())
        self.assertEqual(content["recent"], [])
        self.assertEqual(content["note"], "scene ongoing")
        self.assertEqual(self.game.verify()["revision"], 0)

    def test_checkpoint_refuses_pending(self):
        pending = packet("t0", 0, "2000-01-01T09:00:00")
        self.game.prepare(pending)
        self._refresh_manifest()
        with self.assertRaisesRegex(ValueError, "pending"):
            process_request(self.state_dir, self.request("checkpoint"), self.root / "checkpoint")


if __name__ == "__main__":
    unittest.main()
