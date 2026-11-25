"""Source-scoped historical access. A mention is not presence or knowledge."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import identity, presence

WITNESS = frozenset(("present", "witness", "witnessed"))
PLAYED = re.compile(r"s\d+\.text\.\d+")
REMOTE = re.compile(r"по рации|по связи|за кадром|в другом месте|издалека|голос в записи", re.I)


def _ordinal(value):
    if not PLAYED.fullmatch(str(value or "")):
        return None
    s, _, n = value.split(".")
    return int(s[1:]), int(n)


def deltas(state_dir):
    out = {}
    if state_dir is None:
        return out
    for path in sorted((Path(state_dir) / "archive/deltas").glob("session-*.jsonl")):
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                if raw.strip():
                    row = json.loads(raw)
                    if PLAYED.fullmatch(str(row.get("id", ""))) and isinstance(row.get("d"), dict):
                        out[row["id"]] = row["d"]
        except (OSError, ValueError):
            continue
    return out


def scopes(rows, patches):
    """Only primary turn deltas; absent/gapped session evidence is unknown."""
    result, scope, last = {}, None, None
    for row in sorted(rows, key=lambda r: _ordinal(r.get("id")) or (0, 0)):
        ident = row.get("id")
        order = _ordinal(ident)
        if not order or row.get("kind") in ("ooc", "technical", "void"):
            continue
        if last is None or order[0] != last[0] or order[1] != last[1] + 1:
            scope = None
        delta = row.get("d") if isinstance(row.get("d"), dict) else patches.get(ident)
        if not isinstance(delta, dict):
            scope = None
        else:
            listed = (delta.get("scene") or {}).get("present")
            if isinstance(listed, list):
                scope = {k for k in listed if isinstance(k, str)}
        if scope is not None:
            result[ident] = set(scope)
        last = order
    return result


def corrected_scope(rules, turn, scope):
    out = set(scope)
    for rule in rules:
        if turn in rule["duplicate_turns"] and rule["to"] in out:
            out.discard(rule["from"])
    return out


def speaker(rules, turn, label, names, scope=None):
    """Source-scoped correction, then unique full label or scoped exact name."""
    if REMOTE.search(label):
        return None
    canonical = identity.speech_actor(rules, turn, label, names)
    if canonical:
        return canonical
    clean = presence._clean(label)
    if not clean or "шики" in clean:
        return None
    exact = [k for k, entry in names.items() if clean in entry.get("forms", ())]
    if scope is not None:
        scoped = [k for k in exact if k in scope]
        if len(scoped) == 1:
            return scoped[0]
        # A unique partial name is useful only within the verified cast.
        if not exact:
            found = presence.resolve(label, {k: names[k] for k in scope if k in names}, scope=scope)
            if found in scope:
                return found
        return None
    # Without scene evidence, a generic role/one-word surname is not identity.
    if len(exact) == 1 and not presence._is_background(clean):
        entry = names[exact[0]]
        # Canonical mononyms (Баки, Канкуро) are names, not surname shortcuts.
        # Require the one-word label to be the only substantive name form.
        if len(clean.split()) >= 2 or all(len(form.split()) == 1 for form in entry.get("forms", ())):
            return exact[0]
    return None


def own_speech(text, actor, turn, names, rules, scope=None):
    from . import acquaint
    return [said for label, said in acquaint.lines(text)
            if said and speaker(rules, turn, label, names, scope) == actor]
