import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from rpg_engine import Campaign, git_state
from rpg_engine.engine import ConflictError


@unittest.skipUnless(shutil.which("git"), "Git is needed for the state transport")
class GitStateTests(unittest.TestCase):
    def test_publish_restore_recover_pending_and_reject_stale_writer(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
            "GIT_AUTHOR_NAME": "RPG Test", "GIT_AUTHOR_EMAIL": "rpg-test@example.invalid",
            "GIT_COMMITTER_NAME": "RPG Test", "GIT_COMMITTER_EMAIL": "rpg-test@example.invalid",
        }):
            root = Path(temp)
            remote = root / "remote.git"
            subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
            branch = "game-state/test"
            at = "2026-01-01T12:00:00"
            game = Campaign.create(root / "local.sqlite", {
                "id": "test", "title": "Test", "start_at": at, "location": "Gate",
                "player": {"id": "pc", "name": "Player"},
            })
            base = git_state.save(game, str(remote), branch, "new")
            self.assertEqual(base, git_state.save(game, str(remote), branch, "new"))
            stale = root / "stale.sqlite"
            self.assertEqual(git_state.load(str(remote), branch, stale), base)

            request = {"turn_id": "turn", "expected_revision": 0, "check_id": "balance"}
            rolled = game.roll(**request)
            packet = {"id": "turn", "expected_revision": 0, "at": at, "location": "Gate",
                      "player_input": "Wait.", "text": "The gate opens."}
            prepared = game.prepare(packet)
            pushed = git_state.save(game, str(remote), branch, base["head"])
            self.assertEqual(pushed["revision"], base["revision"])
            self.assertGreater(pushed["generation"], base["generation"])
            self.assertEqual(pushed, git_state.save(game, str(remote), branch, base["head"]))
            working = root / "resumed.sqlite"
            self.assertEqual(pushed, git_state.load(str(remote), branch, working))
            resumed = Campaign(working)
            self.assertEqual(rolled, resumed.roll(**request))
            self.assertEqual(prepared, resumed.prepare(packet))
            self.assertEqual(resumed.search("gate"), [])

            Campaign(stale).prepare({**packet, "id": "other-turn"})
            with self.assertRaisesRegex(ConflictError, "state branch changed"):
                git_state.save(Campaign(stale), str(remote), branch, base["head"])
            resumed.confirm("turn", prepared["draft_sha"])
            latest = git_state.save(resumed, str(remote), branch, pushed["head"])
            final = root / "accepted.sqlite"
            self.assertEqual(latest, git_state.load(str(remote), branch, final))
            self.assertEqual(Campaign(final).search("gate")[0]["text"], packet["text"])
            self.assertEqual(Campaign(final).roll(**request), rolled)
            with self.assertRaisesRegex(ConflictError, "older than remote"):
                git_state.save(Campaign(stale), str(remote), branch, latest["head"])
            with self.assertRaisesRegex(ValueError, "state branch"):
                git_state.save(resumed, str(remote), "main", "new")


if __name__ == "__main__":
    unittest.main()
