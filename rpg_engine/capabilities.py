"""Source-bound compact capability index. No mechanics, no new activation.

The previous turn's topic cannot predict the next player message. Keep the
small permanent index in NEXT; route the actual new request at reading time.
"""
from __future__ import annotations
import json
from pathlib import Path


def load(root, campaign):
    # The live campaign id differs from its directory; unknown settings get no Shiki data.
    if campaign not in ("naruto-shiki", "shiki"):
        return None
    path = Path(root) / "campaigns/shiki/ref/CAPABILITIES.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "npr-capabilities/1" or data.get("campaign") != "naruto-shiki":
        raise ValueError("invalid capability index")
    entries = data.get("entries") or []
    routes = data.get("routes") or {}
    ids = set()
    for row in entries:
        if not row.get("id") or row["id"] in ids or not row.get("summary") or not row.get("sources"):
            raise ValueError("capabilities must have unique ids, summaries and sources")
        if not row.get("tags") or set(row["tags"]) - set(routes):
            raise ValueError("unknown capability route")
        ids.add(row["id"])
    return data


def for_request(data, player):
    """Match the current request, not the last archived turn. Multiple domains are valid."""
    text = str(player or "").casefold().replace("ё", "е")
    routes = [key for key, row in (data.get("routes") or {}).items()
              if any(str(term).casefold().replace("ё", "е") in text for term in row.get("terms") or [])]
    selected = [row for row in data.get("entries") or [] if set(row.get("tags") or []) & set(routes)]
    return {"routes": routes, "entries": selected}


def packet(root, campaign):
    data = load(root, campaign)
    if not data:
        return None
    return {"rule": data["rule"] + " Выбери тему по ТЕКУЩЕЙ заявке, не прошлой; если факта нет, адресно проверь источник.",
            "routes": {key: {"read": row["read"], "focus": row["focus"]} for key, row in data["routes"].items()},
            "items": [{"id": row["id"], "tags": row["tags"], "fact": row["summary"], "sources": row["sources"]} for row in data["entries"]]}
