#!/usr/bin/env python3
"""Deterministic, standalone Seven Locks pact burden tracker for Shiki.

Examples:
 python campaigns/shiki/contract/progression.py status --at 0007-09-28T12:00:00
 python campaigns/shiki/contract/progression.py advance --at 0007-09-28T12:00:00
 python campaigns/shiki/contract/progression.py event --at 0007-09-28T13:00:00 --id s03.training.01 --kind practice --school shiton
 python campaigns/shiki/contract/progression.py event --at 0007-09-30T12:00:00 --id s03.learn.01 --kind mastery --school shiton --technique-id shito_example

Mutating calls change ONLY the adjacent live JSON, not the SQLite engine.
A Game Master must first validate that the player actually undertook the training,
mastery or casting; never count mere intent, an OOC message or a different school.
"""
import argparse
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent
RULES = ROOT / "PROGRESSION_RULES.json"
STATE = ROOT / "PACT_BURDEN_LIVE.json"
Q = Decimal("0.0001")


def stamp(s):
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError) as e:
        raise ValueError("Invalid in-world YYYY-MM-DDTHH:MM:SS timestamp") from e


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def round4(n):
    return float(Decimal(str(n)).quantize(Q, rounding=ROUND_HALF_UP))


def stage_for(points, rules):
    return max(x["stage"] for x in rules["scale"]["thresholds"] if points >= x["at"])


def advance(state, rules, at):
    from_at = stamp(state["last_at"])
    until = stamp(at)
    if until < from_at:
        raise ValueError("Cannot move game time backwards")
    # Calculate from the immutable binding timestamp, so small scene hops
    # cannot discard fractions by repeatedly rounding the current delta.
    elapsed = Decimal(str((until - stamp(state["bound_at"])).total_seconds()))
    if elapsed < 0:
        raise ValueError("Cannot calculate pact burden before blood binding")
    baseline = elapsed * Decimal(str(rules["trigger"]["base_points_per_in_game_day"])) / Decimal("86400")
    state["time_growth_total"] = round4(baseline)
    state["value"] = min(100, round4(
        Decimal(str(state["time_growth_total"])) + Decimal(str(state["technique_growth_total"]))))
    state["last_at"] = at
    state["stage"] = stage_for(state["value"], rules)
    state["passive_direct_bare_skin_transmission_active"] = state["stage"] >= 5
    return state


def apply(state, rules, at, eid, kind, school, technique_id):
    if not eid or len(eid) > 128:
        raise ValueError("Stable unique event ID required")
    # An event replay is harmless and does not double-charge.
    if eid in state["event_ids"]:
        return state
    if kind not in rules["trigger"]["event_types"]:
        raise ValueError("Unknown forbidden-event kind")
    if school not in ("shiton", "byoton"):
        raise ValueError("Must specify shiton or byoton")
    mastered_key = "mastered_" + school
    if kind == "mastery":
        if not technique_id or technique_id in state[mastered_key]:
            raise ValueError("Mastery requires unique newly confirmed technique ID")
    if kind.startswith("cast_"):
        if not technique_id or technique_id not in state[mastered_key]:
            raise ValueError("Only a confirmed learned technique can be cast")
    advance(state, rules, at)
    points = Decimal(str(rules["trigger"]["event_types"][kind]["points"][school]))
    state["technique_growth_total"] = round4(
        Decimal(str(state["technique_growth_total"])) + points)
    state["value"] = min(100, round4(
        Decimal(str(state["time_growth_total"])) + Decimal(str(state["technique_growth_total"]))))
    state["stage"] = stage_for(state["value"], rules)
    state["passive_direct_bare_skin_transmission_active"] = state["stage"] >= 5
    if kind == "mastery":
        state[mastered_key].append(technique_id)
    state["event_ids"].append(eid)
    state["events"].append({"event_id": eid, "at": at, "kind": kind,
                            "school": school, "technique_id": technique_id,
                            "added": float(points), "value_after": state["value"]})
    return state


def save_atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=("status", "advance", "event"))
    p.add_argument("--at", help="in-world timestamp")
    p.add_argument("--id", dest="event_id")
    p.add_argument("--kind")
    p.add_argument("--school")
    p.add_argument("--technique-id")
    p.add_argument("--state", default=str(STATE))
    p.add_argument("--rules", default=str(RULES))
    a = p.parse_args()
    rules, st = load(Path(a.rules)), load(Path(a.state))
    if a.operation == "status":
        if a.at:
            advance(st, rules, a.at)
    elif a.operation == "advance":
        if not a.at:
            p.error("--at required")
        advance(st, rules, a.at)
    else:
        if not a.at or not a.event_id or not a.kind or not a.school:
            p.error("--at, --id, --kind, --school required for event")
        apply(st, rules, a.at, a.event_id, a.kind, a.school, a.technique_id)
    if a.operation != "status":
        save_atomic(Path(a.state), st)
    print(json.dumps({"at": st["last_at"], "value": st["value"],
                      "stage": st["stage"], "contact_active":
                      st["passive_direct_bare_skin_transmission_active"],
                      "logged_events": len(st["event_ids"])},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
