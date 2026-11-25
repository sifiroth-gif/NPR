"""Read-only local projection when the asynchronous fold lags behind.

This does not run the world's clock/AI or publish state. Only confirmed exact
TURN records may be projected. Missing receipts, gaps and stale derived
obligations never become permission to reuse dice or spend an old balance.
"""
from __future__ import annotations

import copy
import hashlib
import json

from . import directives, fight, hot


def obligation_ids(cursor, items):
    return [hashlib.sha256(json.dumps([cursor, item], ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest()[:16] for item in items]


def blob_sha(raw):
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()


def cas_decision(expected_sha, actual_raw, intended):
    """Never overwrite a concurrent edit with a freshly discovered SHA."""
    actual_sha = blob_sha(actual_raw)
    try:
        actual = json.loads(actual_raw)
    except (TypeError, ValueError):
        return {"status": "conflict", "sha": actual_sha}
    if actual == intended:
        return {"status": "already_written", "sha": actual_sha}
    if actual_sha == expected_sha:
        return {"status": "retry_same_sha", "sha": actual_sha}
    return {"status": "conflict", "sha": actual_sha, "actual_id": actual.get("id") if isinstance(actual, dict) else None}


def project(packet, records):
    """Project confirmed contiguous receipts after NEXT.cursor, never mutate it.

    records: [{"turn": exact submitted JSON, "sha": returned blob SHA}].
    For a cold chat without receipts use the actual TURN on a detected conflict;
    missing intermediate turns mean the projection cannot be trusted.
    """
    result = copy.deepcopy(packet)
    if packet.get("fold_errors") or packet.get("state_errors"):
        for field in ("dice", "chakra", "must", "must_ids", "turn_file", "scene_context", "state_sources", "medical", "must_pending", "news_id"):
            result.pop(field, None)
        if result.get("capabilities"):
            result["capabilities"].pop("scene_hint", None)
        result["lag_status"] = {"status": "blocked", "write_allowed": False, "reason": "fold/state errors"}
        return result
    anchor = packet.get("lag") or {}
    base = packet.get("cursor")
    cursor = base
    at = anchor.get("at")
    last = None
    have_last = False
    chakra = copy.deepcopy(packet.get("chakra") or {})
    balance = {k: chakra[k] for k in ("value", "state", "locked_until", "last") if k in chakra}
    if "value" in balance and anchor.get("chakra_at"):
        balance["at"] = anchor["chakra_at"]
        ledger = {"shiki": {"chakra": balance}}
    else:
        ledger = {"shiki": {}}
    pending = []
    receipts = set()
    declared_present = None
    declared_scene = None
    inventory_changes = []
    errors = []
    projected_directives = result.get("directives")
    for rec in records:
        if not isinstance(rec, dict) or not isinstance(rec.get("turn"), dict) or not isinstance(rec.get("sha"), str) or not rec["sha"]:
            errors.append("missing confirmed receipt")
            break
        turn = rec["turn"]
        try:
            hot.validate_turn(turn)
            seq = hot.turn_number(turn["id"])
            prior = hot.turn_number(cursor)
        except (hot.TurnError, TypeError, ValueError) as exc:
            errors.append(str(exc))
            break
        if seq <= hot.turn_number(base):
            if turn.get("amend") and seq == hot.turn_number(base):
                errors.append("accepted predecessor amended; refresh NEXT")
                break
            continue
        if pending and turn["id"] == cursor:
            if turn == pending[-1]["turn"] and rec["sha"] == pending[-1]["sha"]:
                continue
            errors.append("amended pending receipt; reproject from corrected chain")
            break
        expected = packet.get("next_id") if not pending else f"s{prior[0]:02d}.text.{prior[1]+1:03d}"
        if turn["id"] != expected or (turn.get("review") or {}).get("predecessor") != cursor:
            errors.append("receipt chain gap or predecessor mismatch")
            break
        if at and hot.parse_at(turn["at"]) < hot.parse_at(at):
            errors.append("time precedes predecessor")
            break
        try:
            projected_directives = directives.project_view(projected_directives, turn)
        except ValueError as exc:
            errors.append(str(exc))
            break
        pending.append(rec)
        delta = turn.get("d") or {}
        from . import news
        try:
            delivered_news = news.validate_receipts(turn)
        except ValueError as exc:
            errors.append(str(exc))
            break
        for row in delivered_news:
            receipts.add(row["id"])
        for ident in delta.get("must_done") or []:
            if isinstance(ident, str) and not ident.startswith("evt_"):
                receipts.add(ident)
        if turn.get("kind") != "ooc":
            at = turn["at"]
            last = hot._last_entry(turn)
            have_last = True
            result["place"] = turn["place"]
            result["header"] = " ".join(hot.heading(at).values())
            scene = delta.get("scene") or {}
            if scene:
                declared_scene = {"as_of": turn["id"], "source": "confirmed local TURN",
                                  **{k: scene[k] for k in ("situation", "positions", "pressure") if k in scene}}
            if "present" in scene:
                declared_present = {"turn": turn["id"], "ids": list(scene["present"])}
        if delta.get("things"):
            inventory_changes.append({"turn": turn["id"], "things": copy.deepcopy(delta["things"])})
        if at:
            fight._regen(ledger, turn["at"])
            bursts = (delta.get("fight") or {}).get("bursts", 0)
            bursts = bursts if type(bursts) is int and bursts >= 0 else 0
            fight._chakra(ledger, delta.get("chakra"), bursts, turn["id"], turn["at"], [])
        cursor = turn["id"]
    # A known stale packet never advertises its old dice/resources as current.
    if not pending and not errors and anchor.get("recorded_turn", base) == base:
        return result
    if "recorded_turn" in anchor and anchor["recorded_turn"] != base:
        match = next((rec for rec in pending if rec["turn"]["id"] == anchor["recorded_turn"]), None)
        if not match or match["sha"] != packet.get("turn_file"):
            errors.append("TURN ahead of NEXT; exact matching receipt unavailable")
    result.pop("dice", None)
    result.pop("chakra", None)
    for key in ("fight", "due", "checks", "prose", "echo", "recall", "must", "must_ids", "must_pending", "news_id", "fences", "source_review"):
        result.pop(key, None)
    # Keep offscreen world facts, not their presumed delivery. Actor summaries
    # are stale until the fold's knowledge and presence calculations complete.
    result.pop("present", None)
    # B2: never carry old mutable context or a scene-derived topic across a
    # pending receipt. Permanent registry entries are not mutable state.
    result.pop("scene_context", None)
    result.pop("state_sources", None)
    result.pop("medical", None)  # no claim about unresolved clinical consequences
    if result.get("capabilities"):
        result["capabilities"].pop("scene_hint", None)
    result["lag_status"] = {
        "status": "uncertain" if errors else "projected", "write_allowed": not errors,
        "base": base, "cursor": cursor,
        "unresolved_must": [{"id": ident, "text": text} for ident, text in
                            zip(packet.get("must_ids") or obligation_ids(base, packet.get("must") or []),
                                packet.get("must") or []) if ident not in receipts],
        "uncertain": ["NPC plans/knowledge", "world deadlines and delivery", "injuries and combat axes", "new must"],
        "errors": errors,
    }
    if errors:
        result.pop("turn_file", None)
        result.pop("next_id", None)
        result.pop("directives", None)
        result["lag_status"]["uncertain"].append("directives: exact accepted chain unavailable")
        return result
    if projected_directives:
        result["directives"] = projected_directives
    if declared_scene is not None:
        result["scene_context"] = declared_scene
    if declared_present is not None:
        result["lag_status"]["last_declared_present"] = declared_present
    if inventory_changes:
        result["lag_status"]["inventory_transitions"] = inventory_changes
    result["cursor"] = cursor
    s, q = hot.turn_number(cursor)
    result["next_id"] = f"s{s:02d}.text.{q+1:03d}"
    result["turn_file"] = pending[-1]["sha"]
    result["lag"] = {"at": at, "recorded_turn": cursor, "dice_for": result["next_id"]}
    if have_last and at:
        result["dice"] = fight.dice({"cursor": {"id": cursor, "at": at}, "last": last})
    else:
        result["lag_status"]["uncertain"].append("dice: exact last prose unavailable")
    if ledger["shiki"].get("chakra") and "value" in ledger["shiki"]["chakra"]:
        result["chakra"] = ledger["shiki"]["chakra"]
        result["lag"]["chakra_at"] = result["chakra"].get("at")
    else:
        result["lag_status"]["uncertain"].append("chakra: no verified starting balance")
    # Resource values are a projection, not a new confirmed world state.
    return result
