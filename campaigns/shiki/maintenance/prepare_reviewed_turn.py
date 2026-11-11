"""Publication path for a Shiki turn after NPC knowledge and P0 review.

Keeps the existing generic engine and the exact prepare/confirm protocol.
A reviewed envelope is checked against the live restored SQLite snapshot
before forwarding the original turn to stage_turn.main.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from rpg_engine import Campaign
from rpg_engine.knowledge_gate import KnowledgeLeak, validate_scene
from campaigns.shiki.maintenance.stage_turn import main as stage_turn_main

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / "campaigns" / "shiki" / "KNOWLEDGE_POLICY.json"


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
    if checked["campaign_id"] != "naruto-shiki" or turn.get("expected_revision") != checked["revision"]:
        raise KnowledgeLeak("Preflight not aligned with live Shiki checkpoint")
    if checked["pending"]:
        raise KnowledgeLeak("Pending response must be explicitly reconciled before new preflight")
    final_text = turn.get("text")
    if not isinstance(final_text, str):
        raise KnowledgeLeak("Missing exact final draft")
    ready = validate_scene(game, final_text, review, policy)
    _p0_review(final_text, review, has_dialogue=ready["lines_checked"] > 0)
    with tempfile.TemporaryDirectory(prefix="npr-reviewed-turn-") as tmp:
        packet = Path(tmp) / "turn.json"
        packet.write_text(json.dumps(turn, ensure_ascii=False, indent=2), encoding="utf-8")
        stage_turn_main(state_dir, packet, output_dir)
    print("SCENE_KNOWLEDGE_READY", ready["lines_checked"], "NPC dialogue lines")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("prepare_reviewed_turn.py STATE_DIR REVIEWED_ENVELOPE_JSON OUT_DIR")
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
