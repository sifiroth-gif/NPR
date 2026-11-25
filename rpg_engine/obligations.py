"""Bounded display, durable unresolved obligations. Acknowledgment is explicit."""
from __future__ import annotations

import hashlib
import re

ID = re.compile(r"duty_[0-9a-f]{20}")


def key(kind, text):
    return "duty_" + hashlib.sha256((kind + "\0" + text).encode()).hexdigest()[:20]


def validate(turn):
    rows = (turn.get("d") or {}).get("must_done")
    if rows is None:
        return
    if not isinstance(rows, list) or any(not isinstance(x, str) for x in rows):
        raise ValueError("must_done: expected list of IDs")


def accept(hot, turn):
    validate(turn)
    store = (hot.get("_will") or {}).get("obligations") or {}
    for ident in (turn.get("d") or {}).get("must_done") or []:
        row = store.get(ident)
        if row and row.get("kind") not in ("news", "due", "arc"):
            row["done"] = turn["id"]


def select(hot, candidates, limit=3):
    """Keep the full queue privately; only the first N are due in NEXT.

    Situational cues are recalculated, not permanent commands. Previously
    hidden cues remain recorded until they can be offered, or their source
    condition ceases to apply. Never force stale clinical/scene facts.
    """
    store = hot.setdefault("_will", {}).setdefault("obligations", {})
    cursor = (hot.get("cursor") or {}).get("id")
    active = []
    seen = set()
    for kind, text, ident in candidates:
        if not text:
            continue
        ident = ident or key(kind, text)
        if ident in seen:
            continue
        seen.add(ident)
        old = store.get(ident)
        if old and old.get("done"):
            continue
        row = store.setdefault(ident, {"kind": kind, "text": text, "since": cursor})
        row["text"], row["last"] = text, cursor
        row.pop("inactive", None)
        active.append((ident, row))
    for ident, row in store.items():
        if ident not in seen and not row.get("inactive"):
            row["inactive"] = cursor
    # No loss by display cap. Re-evaluate inactive cues next time rather
    # than carrying an obsolete command into a different scene.
    active.sort(key=lambda item: (0 if item[1]["kind"] in ("body", "arc", "resync", "news", "due") else 1,
                                  0 if item[1].get("offered_at") == cursor else 1,
                                  item[1].get("offered", 0), item[1].get("since") or "", item[0]))
    chosen = active[:limit]
    for ident, row in chosen:
        if row.get("offered_at") != cursor:
            row["offered"] = row.get("offered", 0) + 1
            row["offered_at"] = cursor
    return [row["text"] for ident, row in chosen], [ident for ident, row in chosen], [
        {"id": ident, "kind": row["kind"]} for ident, row in active[limit:]]
