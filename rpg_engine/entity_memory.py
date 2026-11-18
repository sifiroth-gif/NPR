"""Source-bound, rebuildable memory of in-world entities.

Facts enter through reviewed seed cards or explicit turn deltas; natural-language
search is never allowed to invent a canonical attribute. Only a derived GM-only
index is written. This module has no third-party dependencies.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

SCHEMA = "npr-entity-ledger/1"
SEED_PATH = "recovery/ENTITY_SEED_GM_ONLY.json"
LEDGER_PATH = "recovery/ENTITY_LEDGER_GM_ONLY.json"
MAX_ENTITY_FACTS = 8
MAX_ACTIVATED = 8


def _json(path):
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _clean(value):
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _match(text, alias):
    alias = _clean(alias)
    return bool(alias and re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", _clean(text)))


def _normalise(seed):
    if not isinstance(seed, dict) or seed.get("schema") != SCHEMA or not isinstance(seed.get("entities"), dict):
        raise ValueError("entity seed/ledger has an invalid schema")
    items = copy.deepcopy(seed["entities"])
    for key, card in items.items():
        if not isinstance(card, dict) or not isinstance(card.get("label"), str):
            raise ValueError(f"entity {key}: expected label")
        if not isinstance(card.get("aliases", []), list) or not isinstance(card.get("facts", []), list):
            raise ValueError(f"entity {key}: invalid aliases/facts")
        for fact in card["facts"]:
            if (not isinstance(fact, dict) or not fact.get("id") or not fact.get("field") or
                    not fact.get("source") or "value" not in fact):
                raise ValueError(f"entity {key}: unattributed fact")
    return {"schema": SCHEMA, "entities": items, "conflicts": []}


def load(state_dir):
    """Read derived ledger, falling back to a reviewed source seed."""
    root = Path(state_dir)
    data = _json(root / LEDGER_PATH) or _json(root / SEED_PATH)
    if data is None:
        return {"schema": SCHEMA, "entities": {}, "conflicts": []}
    normal = _normalise(data)
    normal["conflicts"] = data.get("conflicts", [])
    # The submitted TURN may be newer than GitHub Actions' asynchronous fold.
    # Include its explicit facts without treating generated excerpts as canon.
    latest = _json(root / "recovery/TURN.json")
    if isinstance(latest, dict) and latest.get("id") and (latest.get("d") or {}).get("entity_facts"):
        present = {f["id"] for card in normal["entities"].values() for f in card.get("facts", [])}
        introduced = {raw.get("id") or f"{latest['id']}:{key}:{i}"
                      for key, patch in latest["d"]["entity_facts"].items()
                      for i, raw in enumerate(patch.get("facts+", []))}
        if not introduced.issubset(present):
            _update(normal, latest)
    return normal


def _update(ledger, turn):
    patches = (turn.get("d") or {}).get("entity_facts") or {}
    if not isinstance(patches, dict):
        raise ValueError("d.entity_facts must be a mapping")
    for key, patch in patches.items():
        if not isinstance(patch, dict) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{2,90}", key):
            raise ValueError("entity key must be a stable identifier")
        card = ledger["entities"].setdefault(key, {"label": patch.get("label", key), "aliases": [], "facts": []})
        if patch.get("label"):
            card["label"] = patch["label"]
        for alias in patch.get("aliases+", []):
            if isinstance(alias, str) and alias and alias not in card["aliases"]:
                card["aliases"].append(alias)
        for i, raw in enumerate(patch.get("facts+", [])):
            if (not isinstance(raw, dict) or not isinstance(raw.get("field"), str)
                    or "value" not in raw or not raw.get("source")):
                raise ValueError(f"entity {key}: fact must have field/value/source")
            fact = {"id": raw.get("id") or f"{turn['id']}:{key}:{i}",
                    "field": raw["field"], "value": raw["value"],
                    "source": raw["source"], "recorded_at": turn.get("at", ""),
                    "authority": raw.get("authority", "played"),
                    "visibility": raw.get("visibility", "gm_only")}
            if raw.get("supersedes"):
                fact["supersedes"] = list(raw["supersedes"])
            current = next((old for old in card["facts"] if old["id"] == fact["id"]), None)
            if current:
                if any(current.get(k) != fact.get(k) for k in ("field", "value", "source")):
                    raise ValueError(f"entity {key}: conflicting duplicate fact id")
                continue
            active = [old for old in card["facts"] if old["field"] == fact["field"] and not old.get("superseded_by")]
            conflicting = [old for old in active if old["value"] != fact["value"]]
            if conflicting and not set(x["id"] for x in conflicting).issubset(set(fact.get("supersedes", []))):
                ledger["conflicts"].append({"entity": key, "field": fact["field"],
                                           "candidate": fact["id"], "source": fact["source"],
                                           "active": [x["id"] for x in conflicting]})
                continue
            for old in conflicting:
                old["superseded_by"] = fact["id"]
            card["facts"].append(fact)


def rebuild(state_dir):
    """Deterministically rebuild from reviewed seed and archived explicit deltas."""
    root = Path(state_dir)
    seed = _json(root / SEED_PATH) or {"schema": SCHEMA, "entities": {}}
    ledger = _normalise(seed)
    archive = root / "archive"
    for file in sorted(archive.glob("session-*-turns.jsonl")) if archive.is_dir() else []:
        for line in file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                _update(ledger, json.loads(line))
    target = root / LEDGER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not target.is_file() or target.read_text(encoding="utf-8") != body:
        target.write_text(body, encoding="utf-8")
    return ledger


def activate(ledger, *, scene=None, cursor=None, action="", limit=MAX_ACTIVATED):
    """Exact alias activation; never boosts a semantic candidate to canon."""
    scene = scene or {}
    cursor = cursor or {}
    scene_text = " ".join(str(scene.get(k, "")) for k in
                          ("situation", "positions", "pressure", "present")) + " " + str(cursor.get("place", ""))
    results = []
    for key, card in ledger.get("entities", {}).items():
        aliases = [key, card.get("label", "")] + list(card.get("aliases", []))
        in_scene = any(_match(scene_text, alias) for alias in aliases if alias)
        in_action = any(_match(action, alias) for alias in aliases if alias)
        if not (in_scene or in_action):
            continue
        facts = [fact for fact in card.get("facts", []) if not fact.get("superseded_by")]
        # Keep the core identity invariants in the bounded hot context.
        essential = ("identity", "sex", "age_at_discovery", "species", "origin",
                     "birth_date", "discovery", "relationship")
        facts.sort(key=lambda f: (f.get("field") not in essential, f.get("field", ""), f.get("id", "")))
        results.append({"id": key, "label": card["label"],
                        "activated_by": "action" if in_action else "scene",
                        "facts": facts[:MAX_ENTITY_FACTS],
                        "status": "source_bound" if facts else "unknown_requires_source",
                        "rule": "No absent demographic or biographical attribute may be guessed."})
    results.sort(key=lambda r: (r["activated_by"] != "action", r["id"]))
    return {"schema": SCHEMA, "activated": results[:limit],
            "total_activated": len(results), "conflicts": ledger.get("conflicts", []),
            "rule": "Entity attributes are claims with sources, not prose suggestions."}


def claim_errors(ledger, turn):
    """Pre-publication rejection of explicit conflicting structured facts."""
    candidate = copy.deepcopy(ledger)
    _update(candidate, turn)
    return [f"{r['entity']}.{r['field']}: missing supersession for {', '.join(r['active'])}"
            for r in candidate.get("conflicts", []) if r not in ledger.get("conflicts", [])]
