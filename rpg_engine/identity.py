"""Source-scoped identity corrections; never merge a generic role globally.

The campaign supplies an exact played anchor. Without that anchor the rule
is inert (including a reset campaign). Primary transcripts and raw episode
archives are not modified: consumers see a corrected projection.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CONFIG = "campaigns/shiki/ref/IDENTITY_FIXES.json"


@lru_cache(maxsize=8)
def _configured(root):
    try:
        data = json.loads((Path(root) / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(data.get("rules") or ()) if data.get("schema") == "npr-source-identity/1" else ()


def active(state_dir, source_root=None, actor=None):
    root = Path(source_root) if source_root else Path(__file__).resolve().parents[1]
    result = []
    for rule in _configured(str(root)):
        if actor is not None and rule["from"] != actor:
            continue
        try:
            anchor = (Path(state_dir) / rule["anchor"]).read_text(encoding="utf-8")
        except OSError:
            continue
        if rule["proof"] in anchor:
            result.append(rule)
    return result


def speech_actor(rules, turn, label, store):
    """A role label belongs to an exact source turn, never to a whole session."""
    for rule in rules:
        if (turn in rule["speech_turns"] and label.casefold().strip() == rule["label"].casefold()
                and rule["to"] in store):
            return rule["to"]
    return None


def excluded_episodes(state_dir, actor, rules=None):
    """Exclude only duplicated episodes already present under the stable ID."""
    rules = active(state_dir, actor=actor) if rules is None else rules
    excluded = set()
    for rule in rules:
        if rule["from"] != actor:
            continue
        path = Path(state_dir) / "archive/npc-memory" / f"{rule['to']}.json"
        try:
            rows = json.loads(path.read_text(encoding="utf-8")).get("episodes") or []
        except (OSError, ValueError):
            continue
        canonical = {row.get("turn") for row in rows if row.get("ch") in ("present", "witness", "witnessed")}
        excluded.update(canonical.intersection(rule["duplicate_turns"]))
    return excluded


def project_card(state_dir, actor, card, rules=None):
    """Remove source-proven duplicate metadata, not unrelated card facts."""
    rules = active(state_dir, actor=actor) if rules is None else rules
    excluded = excluded_episodes(state_dir, actor, rules)
    if not excluded:
        return card
    out = dict(card)
    old = card.get("memory") or []
    kept = [row for row in old if row.get("turn") not in excluded]
    if len(kept) != len(old):
        out["memory"] = kept
        out["memory_total"] = max(len(kept), int(card.get("memory_total") or len(old)) - len(excluded))
        if not out["memory_total"]:
            out.pop("tier", None)
    if out.get("first") in excluded:
        out.pop("first", None)
        if kept:
            out["first"] = kept[0].get("turn")
    stale_seen = {turn for rule in rules if rule["from"] == actor for turn in rule.get("stale_seen", [])}
    if out.get("seen") in excluded | stale_seen:
        out.pop("seen", None)
        if kept:
            out["seen"] = kept[-1].get("turn")
    return out


def duplicate_cast(rules, turn, actor, patch, cast):
    """Only redundant reaction patches may be suppressed in the wiki projection."""
    for rule in rules:
        if (actor == rule["from"] and turn in rule["duplicate_turns"]
                and isinstance(patch, dict) and set(patch) == {"dread"}
                and isinstance(cast.get(rule["to"]), dict) and "dread" in cast[rule["to"]]):
            return True
    return False
