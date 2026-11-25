"""Source-bound capability registry: learned status is not active state."""
from __future__ import annotations

import json
import re
from pathlib import Path

PATH = "campaigns/shiki/ref/CAPABILITIES.json"
STATUSES = {"mastered", "experimental", "learning", "retired", "unlearned"}
ACTIVATIONS = {"passive", "declared", "conditional", "autonomous", "unavailable"}
TURN_ID = re.compile(r"s\d+\.(?:text|played)\.\d+")
STABLE_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


def normalize(value):
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _strings(values):
    return isinstance(values, list) and bool(values) and all(
        isinstance(v, str) and bool(v.strip()) for v in values)


def validate(data, root):
    if data.get("schema") != "npr-capabilities/1" or data.get("campaign") != "naruto-shiki":
        raise ValueError("invalid capability index")
    entries, routes = data.get("entries"), data.get("routes")
    if not isinstance(entries, list) or not entries or not isinstance(routes, dict) or not routes:
        raise ValueError("empty capability entries/routes")
    ids, aliases = set(), {}
    root = Path(root).resolve()
    def source_ok(value):
        if TURN_ID.fullmatch(value):
            return True  # archived in game-state, verified separately
        path = (root / value).resolve()
        return value.startswith("campaigns/shiki/") and path.is_relative_to(root) and path.is_file()
    for key, route in routes.items():
        if not _strings(route.get("terms")) or not _strings(route.get("read")) or not route.get("focus"):
            raise ValueError(f"invalid route {key}")
        if not all(source_ok(x) for x in route["read"]):
            raise ValueError(f"missing route source {key}")
    for row in entries:
        ident = row.get("id", "")
        if not isinstance(ident, str) or not STABLE_ID.fullmatch(ident) or ident in ids:
            raise ValueError("capabilities must have unique stable ids")
        ids.add(ident)
        if any(not isinstance(row.get(k), str) or not row[k].strip()
               for k in ("title", "summary", "requires", "limits")):
            raise ValueError(f"missing description/condition/limit: {ident}")
        if row.get("status") not in STATUSES or row.get("activation") not in ACTIVATIONS:
            raise ValueError(f"invalid capability state: {ident}")
        if (row["status"] == "mastered") == (row["activation"] == "unavailable"):
            raise ValueError(f"availability contradicts status: {ident}")
        if not _strings(row.get("tags")) or set(row["tags"]) - set(routes):
            raise ValueError(f"unknown capability route: {ident}")
        if not _strings(row.get("sources")) or not all(source_ok(x) for x in row["sources"]):
            raise ValueError(f"missing capability source: {ident}")
        if not any(x.startswith("campaigns/shiki/") for x in row["sources"]):
            raise ValueError(f"no accessible canonical reference: {ident}")
        if type(row.get("core")) is not bool or not _strings(row.get("aliases")):
            raise ValueError(f"missing core/aliases: {ident}")
        for alias in row["aliases"]:
            norm = normalize(alias)
            if norm in aliases and aliases[norm] != ident:
                raise ValueError(f"ambiguous capability alias: {alias}")
            aliases[norm] = ident
    return data


def load(root, campaign):
    if campaign not in ("naruto-shiki", "shiki"):
        return None
    path = Path(root) / PATH
    if not path.is_file():
        return None
    return validate(json.loads(path.read_text(encoding="utf-8")), root)


def resolve(data, name):
    """Exact stable ID, title or normalized alias. No substring identity guess."""
    norm = normalize(name)
    matches = [row for row in (data or {}).get("entries", [])
               if norm in {normalize(row["id"]), normalize(row["title"]),
                           *map(normalize, row["aliases"])}]
    return matches[0] if len(matches) == 1 else None


def for_request(data, player):
    """Current request, never last turn; B2 will refine multi-domain selection."""
    text = normalize(player)
    routes = [key for key, row in (data.get("routes") or {}).items()
              if any(normalize(term) in text for term in row.get("terms") or [])]
    selected = [row for row in data.get("entries") or [] if set(row.get("tags") or []) & set(routes)]
    return {"routes": routes, "entries": selected}


def packet(root, campaign):
    data = load(root, campaign)
    if not data:
        return None
    return {"rule": data["rule"] + " Выбери тему по ТЕКУЩЕЙ заявке; полный реестр " + PATH,
            "routes": {key: {"read": row["read"], "focus": row["focus"]} for key, row in data["routes"].items()},
            "items": [{"id": row["id"], "tags": row["tags"], "fact": row["summary"], "sources": row["sources"],
                       "status": row["status"], "activation": row["activation"]} for row in data["entries"] if row["core"]],
            "catalog": [{"id": row["id"], "title": row["title"], "tags": row["tags"],
                         "status": row["status"], "activation": row["activation"]}
                        for row in data["entries"] if not row["core"]]}
