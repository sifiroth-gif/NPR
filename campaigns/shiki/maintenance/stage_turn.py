"""Prepare a single actual, visible RPG turn in the live game-state SQLite.

The previous prepared turn, when specified, must have been shown to the
player before the following player request. The stage script never fabricates
or confirms a turn without an explicit matching previous receipt.
"""

import json
import shutil
import sys
from pathlib import Path
from rpg_engine import Campaign
from rpg_engine.engine import file_sha256
from rpg_engine.git_state import FORMAT

def main(state_dir: Path, packet_path: Path, out_dir: Path):
    state_file = state_dir / "campaign.sqlite"
    manifest = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    src = Campaign(state_file)
    state_before = src.verify()
    if manifest != {"format": FORMAT, **state_before, "sha256": file_sha256(state_file)}:
        raise RuntimeError("Original state SHA/verify manifest mismatch")
    if state_before["campaign_id"] != "naruto-shiki":
        raise RuntimeError("Unexpected campaign identity")
    next_packet = json.loads(packet_path.read_text(encoding="utf-8"))
    previous = next_packet.pop("confirm_previous", None)
    if next_packet.get("input_mode") == "ooc":
        raise RuntimeError("Out-of-character instruction cannot be applied as game turn")
    if not isinstance(next_packet.get("player_input"), str) or not next_packet["player_input"]:
        raise RuntimeError("Missing player input")
    out_dir.mkdir(parents=True, exist_ok=True)
    working_path = out_dir / "working.sqlite"
    shutil.copyfile(state_file, working_path)
    game = Campaign(working_path)
    initial = game.context(audience="gm")
    if previous:
        if (initial["pending"] is None or
            initial["pending"]["turn_id"] != previous.get("turn_id") or
            initial["pending"].get("draft_sha") != previous.get("draft_sha")):
            raise RuntimeError("Prior prepared text or hash changed unexpectedly")
        game.confirm(previous["turn_id"], previous["draft_sha"])
    elif initial["pending"] is not None:
        # An already-reserved RNG roll is not a published draft. It belongs
        # to the exact same turn, and prepare must reuse it without rerolling.
        pending = initial["pending"]
        if ("packet" in pending or pending["turn_id"] != next_packet["id"]
                or pending["base_revision"] != next_packet["expected_revision"]):
            raise RuntimeError("Prior prepared draft exists; publication receipt required")
    current = game.verify()
    if next_packet["expected_revision"] != current["revision"]:
        raise RuntimeError("Revision conflict with target packet")
    receipt = game.prepare(next_packet)
    if receipt["status"] != "prepared" or receipt["text"] != next_packet["text"]:
        raise RuntimeError("Prepared answer does not exactly match player-visible text")
    checked = game.verify()
    if (checked["revision"] != current["revision"] or not checked["pending"] or
        checked["instance"] != state_before["instance"]):
        raise RuntimeError("Unexpected prepared state")
    prepared_ctx = game.context(audience="gm")
    if (prepared_ctx["pending"]["turn_id"] != next_packet["id"] or
        prepared_ctx["pending"]["draft_sha"] != receipt["draft_sha"] or
        prepared_ctx["pending"]["packet"] != next_packet):
        raise RuntimeError("Readback differs from input turn")
    destination = out_dir / "campaign.sqlite"
    game.checkpoint(destination)
    state_after = Campaign(destination).verify()
    output = {"format": FORMAT, **state_after, "sha256": file_sha256(destination)}
    (out_dir / "state.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("PREPARED_TURN", next_packet["id"])
    print("PREPARED_DRAFT_SHA", receipt["draft_sha"])
    print("EXPECTED_REVISION", state_after["revision"])
    print("PENDING_VERIFIED", output["pending"])
    print("STATE_SHA256", output["sha256"])

if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("stage_turn.py STATE_DIR PACKET_JSON OUT_DIR")
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
