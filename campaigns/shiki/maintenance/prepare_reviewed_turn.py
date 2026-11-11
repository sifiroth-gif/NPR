"""Publication path for a Shiki turn after NPC knowledge and P0 review.

Keeps the existing generic engine and the exact prepare/confirm protocol.
A reviewed envelope is checked against the live restored SQLite snapshot
before forwarding the original turn to stage_turn.main.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from rpg_engine import Campaign
from rpg_engine.knowledge_gate import KnowledgeLeak, validate_scene
from rpg_engine.combat import validate_frame, assess_attempt, apply_outcome, outcome_world_event
from campaigns.shiki.maintenance.stage_turn import main as stage_turn_main

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / "campaigns" / "shiki" / "KNOWLEDGE_POLICY.json"
LORE = ROOT / "campaigns" / "shiki" / "lore.json"
COMBAT_REVIEW_FORMAT = "NPR-COMBAT-REVIEW/1"


def _grounded_excerpt(text: str, value, label: str):
    if not isinstance(value, str) or len(value) < 12 or value not in text:
        raise KnowledgeLeak(f"P0 {label}: exact, substantive excerpt absent from final draft")


def _p0_review(text: str, review: dict, *, has_dialogue: bool):
    p0 = review.get("p0")
    if not isinstance(p0, dict):
        raise KnowledgeLeak("Missing P0 scene preflight")
    _grounded_excerpt(text, p0.get("shiki_presence"), "Shiki psychological presence")
    if has_dialogue:
        _grounded_excerpt(text, p0.get("conversation_body_language"), "conversation body language")
    if p0.get("combat_present") is True:
        _grounded_excerpt(text, p0.get("combat_body_language"), "combat body language BEFORE resolution")
    elif p0.get("combat_present") is not False:
        raise KnowledgeLeak("P0 combat_present must be true/false")


def _check_new_acquisitions(turn: dict):
    for event in turn.get("events", []):
        if any(op.get("op") == "set_belief" for op in event.get("operations", [])):
            if "witnesses" not in event and "knowledge_routes" not in event:
                raise KnowledgeLeak(
                    "New NPC knowledge requires witnesses or explicit knowledge_routes "
                    "inside the source event (not GM context)"
                )



def _combat_review(game: Campaign, turn: dict, review: dict, final_text: str):
    """Causal, source- and ownership-bound gate for a live combat response."""
    spec = review.get("combat_review")
    if review["p0"]["combat_present"] is False:
        if spec is not None:
            raise KnowledgeLeak("Combat review for a scene marked noncombat")
        return
    if not isinstance(spec, dict) or spec.get("format") != COMBAT_REVIEW_FORMAT:
        raise KnowledgeLeak("P0 live combat requires a structured combat_review")
    frame = spec.get("frame")
    validate_frame(frame)
    if frame["player_id"] != "shiki":
        raise KnowledgeLeak("Combat frame must preserve Shiki as player-controlled")
    established = json.loads(LORE.read_text(encoding="utf-8"))["protagonist"]["stats_out_of_5"]
    aliases = {"shunshin": "shunshin_no_jutsu", "zero_genome": "zero_genome_mastery"}
    for name, value in frame["actors"]["shiki"]["skills"].items():
        canonical = established.get(aliases.get(name, name))
        if canonical is None or value > canonical:
            raise KnowledgeLeak(f"Shiki skill {name} exceeds verified lore")
    previous = game.world(audience="gm", subject="combat:"+frame["id"])["facts"]
    saved = next((f["value"] for f in previous if f["name"] == "frame"), None)
    if saved is not None and saved != frame:
        raise KnowledgeLeak("Combat frame changed from persisted state without outcome")
    _grounded_excerpt(final_text, spec.get("tactical_excerpt"), "combat geometry and observation")
    attempts = spec.get("attempts")
    if not isinstance(attempts, list) or len(attempts) > 30:
        raise KnowledgeLeak("Combat attempts must be a bounded ordered list")
    attempts_by_id = {}
    for action in attempts:
        assessed = assess_attempt(frame, action)
        if assessed["id"] in attempts_by_id:
            raise KnowledgeLeak("Duplicate combat attempt ID")
        attempts_by_id[assessed["id"]] = action
        if action["actor"] == "shiki":
            if action["source"]["ref"] not in turn.get("player_input", ""):
                raise KnowledgeLeak("Shiki combat action absent from actual player input")
    outcomes = spec.get("outcomes", [])
    if not isinstance(outcomes, list) or len(outcomes) > len(attempts):
        raise KnowledgeLeak("Combat outcomes need matching attempts, not invented attacks")
    projected = frame
    resolved = set()
    for result in outcomes:
        action_id = result.get("attempt_id")
        if action_id not in attempts_by_id or action_id in resolved:
            raise KnowledgeLeak("Outcome lacks unique declared combat action")
        resolved.add(action_id)
        if result.get("resolution") not in ("clear_causality", "reserved_roll"):
            raise KnowledgeLeak("Combat result needs causal explanation or reserved RNG")
        if result["resolution"] == "reserved_roll":
            pending = game.context(audience="gm", recent=0)["pending"]
            checks = [] if pending is None else pending.get("rolls", [])
            contract = next((c for c in checks if c["check_id"] == result.get("check_id")), None)
            if contract is None or pending["turn_id"] != turn["id"]:
                raise KnowledgeLeak("Unreserved combat RNG roll cannot be narrated")
            try:
                definition = json.loads(contract["spec"]["purpose"])
                if definition["mechanic"] != "combat-band-v1":
                    raise ValueError("noncombat roll")
                roll_value = contract["result"]["total"]
                resolved = next(
                    b["result"] for b in definition["bands"]
                    if b["low"] <= roll_value <= b["high"])
            except (ValueError, TypeError, KeyError, StopIteration):
                raise KnowledgeLeak("Combat RNG has no locked probability contract")
            if resolved != result["result"]:
                raise KnowledgeLeak("Combat result contradicts the reserved roll")
        projected = apply_outcome(projected, result)
    if outcomes:
        if not spec.get("world_event_id") or not spec.get("event_summary"):
            raise KnowledgeLeak("Combat outcome must be included as private persisted world event")
        expected = outcome_world_event(
            projected, event_id=spec.get("world_event_id"),
            at=turn["at"], summary=spec.get("event_summary"))
        if expected not in turn.get("events", []):
            raise KnowledgeLeak("Combat outcome must be included as private persisted world event")

def main(state_dir: Path, envelope_path: Path, output_dir: Path):
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    if envelope.get("format") != "NPR-SHIKI-REVIEWED-TURN/1":
        raise KnowledgeLeak("Wrong reviewed turn envelope")
    turn = envelope.get("turn")
    review = envelope.get("knowledge_review")
    if not isinstance(turn, dict) or not isinstance(review, dict):
        raise KnowledgeLeak("Turn and knowledge_review required")
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    game = Campaign(state_dir / "campaign.sqlite")
    checked = game.verify()
    pending_start = game.context(audience="gm", recent=0)["pending"]
    previous_draft = bool(pending_start and "packet" in pending_start)
    expected_revision = checked["revision"] + int(previous_draft)
    if checked["campaign_id"] != "naruto-shiki" or turn.get("expected_revision") != expected_revision:
        raise KnowledgeLeak("Preflight not aligned with live Shiki checkpoint")
    _check_new_acquisitions(turn)
    final_text = turn.get("text")
    if not isinstance(final_text, str):
        raise KnowledgeLeak("Missing exact final draft")
    with tempfile.TemporaryDirectory(prefix="npr-reviewed-turn-") as tmp:
        tmp = Path(tmp)
        if previous_draft:
            previous = turn.get("confirm_previous")
            pending = pending_start
            if (not isinstance(previous, dict) or previous.get("turn_id") != pending["turn_id"]
                    or previous.get("draft_sha") != pending["draft_sha"]):
                raise KnowledgeLeak("Previous prepared response must have been shown and confirmed")
            corrected = tmp / "knowledge-baseline.sqlite"
            shutil.copyfile(state_dir / "campaign.sqlite", corrected)
            game = Campaign(corrected)
            game.confirm(previous["turn_id"], previous["draft_sha"])
        elif pending_start and pending_start["turn_id"] != turn.get("id"):
            raise KnowledgeLeak("Reserved RNG belongs to a different combat turn")
        if turn.get("expected_revision") != game.verify()["revision"]:
            raise KnowledgeLeak("Reviewed scene is based on the wrong revision")
        ready = validate_scene(game, final_text, review, policy)
        _p0_review(final_text, review, has_dialogue=ready["lines_checked"] > 0)
        _combat_review(game, turn, review, final_text)
        packet = tmp / "turn.json"
        packet.write_text(json.dumps(turn, ensure_ascii=False, indent=2), encoding="utf-8")
        stage_turn_main(state_dir, packet, output_dir)
    print("SCENE_KNOWLEDGE_READY", ready["lines_checked"], "NPC dialogue lines")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("prepare_reviewed_turn.py STATE_DIR REVIEWED_ENVELOPE_JSON OUT_DIR")
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
