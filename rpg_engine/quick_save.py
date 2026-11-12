"""Fast, source-bound checkpointing for an active NPR SQLite campaign.

Adapted conceptually from sifiroth-gif/Bleach-RPG-/docs/GITHUB_CHAT_CHECKPOINTS.md:
- append: confirm one already published draft, then batch already played turns;
- handoff: derive a compact continuation report without altering game state;
- checkpoint: verify a complete, consistent backup.

This module uses the NPR Campaign.prepare/confirm contract and state.json manifest.
It does not import Bleach RPG code or overwrite/reinitialize a campaign.
Use with a private Git branch compare-and-swap publication step.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import Campaign, file_sha256
from .git_state import FORMAT
from .preparation import packet as gm_packet

REQUEST_FORMAT = "NPR-QUICK-SAVE/1"
MAX_BATCH = 100


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_state(source: Path) -> tuple[Campaign, dict]:
    db = source / "campaign.sqlite"
    manifest = _read_json(source / "state.json")
    game = Campaign(db)
    verified = game.verify()
    expected = {"format": FORMAT, **verified, "sha256": file_sha256(db)}
    if manifest != expected:
        raise ValueError("Existing SQLite checkpoint and state.json do not match")
    return game, verified


def _identity(req: dict, info: dict):
    if req.get("format") != REQUEST_FORMAT:
        raise ValueError("Unknown quick-save request format")
    if req.get("campaign_id") != info["campaign_id"]:
        raise ValueError("Campaign identity mismatch")
    if req.get("instance") != info["instance"]:
        raise ValueError("Campaign instance mismatch; refusing to replace a campaign")
    if type(req.get("expected_revision")) is not int:
        raise ValueError("Expected revision is required")
    if req["expected_revision"] != info["revision"]:
        raise ValueError("Stale revision; read the live save before retrying")


def _observed_previous(game: Campaign, pending: dict | None, spec: dict | None):
    if pending is None:
        if spec is not None:
            raise ValueError("No pending draft to confirm")
        return None
    if not isinstance(spec, dict) or set(spec) != {"turn_id", "draft_sha", "observed_text"}:
        raise ValueError("Publication receipt and exact observed text are required")
    if pending["turn_id"] != spec["turn_id"] or pending.get("draft_sha") != spec["draft_sha"]:
        raise ValueError("Pending turn or receipt mismatch")
    if pending["packet"]["text"] != spec["observed_text"]:
        raise ValueError("Previous published response differs from saved draft")
    return game.confirm(spec["turn_id"], spec["draft_sha"])


def _snapshot(game: Campaign, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    db = output / "campaign.sqlite"
    if db.exists() or (output / "state.json").exists():
        raise FileExistsError("Refusing to overwrite a prior checkpoint output")
    game.checkpoint(db)
    result = Campaign(db).verify()
    manifest = {"format": FORMAT, **result, "sha256": file_sha256(db)}
    (output / "state.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def process_request(source: Path, request: dict, output: Path) -> dict:
    """Process a request on a copy; never edit the source database in place."""
    game, current = verify_state(source)
    _identity(request, current)
    mode = request.get("mode")
    if mode not in ("append", "handoff", "checkpoint"):
        raise ValueError("Unknown quick-save mode")

    if mode == "handoff":
        if request.get("observed_pending") or request.get("turns"):
            raise ValueError("Handoff cannot invent game turns or confirm publication")
        if output.exists():
            raise FileExistsError(output)
        output.mkdir(parents=True)
        preparation = gm_packet(game, mode="handoff")
        context = preparation["confirmed"]
        report = {
            "format": REQUEST_FORMAT, "mode": "handoff",
            "campaign_id": current["campaign_id"],
            "instance": current["instance"],
            "revision": current["revision"],
            "session": current["session"],
            "at": current["at"],
            "location": context["location"],
            "pending": context.get("pending"),
            "recent": game.context(audience="gm", recent=3)["recent"],
            "next_process": preparation["world"]["next"],
            "scene_context": preparation,
            "note": request.get("note", ""),
        }
        (output / "handoff.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"mode": "handoff", "revision": current["revision"],
                "pending": current["pending"], "path": str(output / "handoff.json")}

    if mode == "checkpoint" and (request.get("turns") or request.get("observed_pending")):
        raise ValueError("Use append for new played material")
    if mode == "append":
        if not isinstance(request.get("source_ref"), str) or not request["source_ref"].strip():
            raise ValueError("Append requires a source_ref to the played conversation")
        turns = request.get("turns")
        if not isinstance(turns, list) or not 1 <= len(turns) <= MAX_BATCH:
            raise ValueError("Append needs 1..100 already published turns")
        if any(not isinstance(turn, dict) for turn in turns):
            raise ValueError("Invalid turn packet")
    else:
        turns = []
        if current["pending"]:
            raise ValueError("Cannot make final checkpoint while a draft is pending")

    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    working = output / "_working.sqlite"
    snapshot = game.checkpoint(working)
    if any(snapshot[key] != current[key] for key in ("instance", "revision", "generation")):
        raise ValueError("source checkpoint changed during the copy")
    target = Campaign(working)
    initial = target.context(audience="gm", recent=0)
    if mode == "append":
        _observed_previous(target, initial["pending"], request.get("observed_pending"))
        current_rev = target.context(recent=0)["revision"]
        for packet in turns:
            if packet.get("input_mode", "in_character") == "ooc":
                raise ValueError("Meta instruction is not a played turn")
            if packet.get("expected_revision") != current_rev:
                raise ValueError(f"Nonsequential packet {packet.get('id')!r}")
            if not packet.get("player_input"):
                raise ValueError("No player statement/source to replay")
            # Legacy imports used the marker alone. Exact observed turns need
            # explicit status and retain the original bytes without decoration.
            if not packet.get("text", "").startswith("[Восстановление из сыгранного диалога]"):
                if packet.get("source_status") != "exact" or not packet.get("source_ref"):
                    raise ValueError("Catch-up turn must identify reconstructed narration or its exact source")
            receipt = target.prepare(packet)
            if receipt["status"] != "prepared" or receipt["text"] != packet["text"]:
                raise ValueError("Draft unexpectedly differs from recovered text")
            confirmed = target.confirm(packet["id"], receipt["draft_sha"])
            current_rev += 1
            if confirmed["revision"] != current_rev:
                raise ValueError("Unexpected revision after replay")

    final = target.verify()
    if final["campaign_id"] != current["campaign_id"] or final["instance"] != current["instance"]:
        raise ValueError("Campaign identity changed")
    if mode == "append":
        expected_count = len(turns) + (1 if request.get("observed_pending") else 0)
        if final["revision"] != current["revision"] + expected_count or final["pending"]:
            raise ValueError("Replayed revision/pending mismatch")
        entry = target.entry("turn", turns[-1]["id"], audience="gm")
        if entry["text"] != turns[-1]["text"] or entry["player_input"] != turns[-1]["player_input"]:
            raise ValueError("Read-back does not match final recorded turn")

    manifest = _snapshot(target, output)
    working.unlink()
    if manifest["sha256"] != file_sha256(output / "campaign.sqlite"):
        raise ValueError("Output SHA mismatch")
    return {
        "mode": mode, "before_revision": current["revision"],
        "after_revision": final["revision"], "session": final["session"],
        "pending": final["pending"], "at": final["at"],
        "instance": final["instance"], "sha256": manifest["sha256"],
        "turns_appended": len(turns),
        "previous_draft_confirmed": bool(request.get("observed_pending")),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("state", type=Path, help="Existing game-state directory")
    p.add_argument("request", type=Path, help="NPR-QUICK-SAVE/1 JSON packet")
    p.add_argument("output", type=Path, help="New, empty output directory")
    args = p.parse_args()
    print(json.dumps(process_request(args.state, _read_json(args.request), args.output),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
