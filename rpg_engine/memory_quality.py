"""Deterministic coverage checks for the entire campaign memory, not one case.

Inspired by Context Engineering evaluation: test *retrieval into scene focus*,
not just whether source data exists. These are transparent self-retrieval probes,
not a claim of human-level semantic or narrative quality.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from . import context_dispatcher, general_memory

SCHEMA = "npr-memory-quality/1"
PATH = "recovery/MEMORY_QUALITY_GM_ONLY.json"
MAX_PER_KIND = 8
MAX_PROBES = 72


def _pick_alias(card):
    names = [card.get("label", "")] + list(card.get("aliases", []))
    # Prefer a distinctive displayed name, rather than generic form such as
    # "doctor" or "patient"; fail visibly when no usable name exists.
    for v in names:
        if isinstance(v, str) and len(v.strip()) >= 5 and v not in ("unknown", "unresolved"):
            return v.strip()
    return ""


def audit(index, hot, *, expected_max_bytes=context_dispatcher.DEFAULT_BYTES):
    """Return source-bound checks; never change canon or psychological state."""
    if not index or index.get("schema") != general_memory.FORMAT:
        return {"schema": SCHEMA, "status": "unindexed", "probes": [], "ok": False}
    cursor = (hot.get("cursor") or {}).get("id")
    focus, route = context_dispatcher.route(hot, index, max_bytes=expected_max_bytes)
    cards = index.get("entities") or {}
    kinds = Counter(card.get("kind", "unknown") for card in cards.values())
    source_missing = [key for key, card in cards.items()
                      if card.get("facts") and
                      any(not fact.get("source") for fact in card["facts"])]
    # Independently select stable, source-backed entities per category and
    # verify each can be found by its visible name. This is intentionally not
    # semantic paraphrase retrieval or a hidden test of model narration.
    probes = []
    quotas = Counter()
    for key, card in sorted(cards.items(), key=lambda pair: (pair[1].get("kind", ""), pair[0])):
        kind = card.get("kind", "unknown")
        if quotas[kind] >= MAX_PER_KIND or len(probes) >= MAX_PROBES:
            continue
        label = _pick_alias(card)
        if not label:
            continue
        # Source references are a prerequisite to probe eligibility.
        if not card.get("sources") and not any(f.get("source") for f in card.get("facts", [])):
            continue
        hits = general_memory.retrieve(
            index, action=label, scene={}, cursor={"at": (hot.get("cursor") or {}).get("at", "")},
            limit=5)
        ids = [row["id"] for row in hits.get("activated", [])]
        checked = {"id": key, "kind": kind, "at_5": key in ids,
                   "at_1": bool(ids and ids[0] == key)}
        probes.append(checked)
        quotas[kind] += 1
    bad_probes = [v["id"] for v in probes if not v["at_5"]]
    missed_critical = list(dict.fromkeys(
        route.get("unindexed_present_actors", []) +
        route.get("omitted_critical", []) +
        route.get("omitted_present_actors", [])))
    focus_bytes = route.get("focus_bytes") or 0
    status = ("review_required" if
              (bad_probes or missed_critical or source_missing or
               focus_bytes > expected_max_bytes or
               index.get("cursor") != cursor)
              else "coverage_checks_passed")
    return {
        "schema": SCHEMA, "status": status,
        "cursor": cursor,
        "indexed_cursor": index.get("cursor"),
        "index_entities": len(cards),
        "index_source_records": index.get("source_turns_indexed", 0),
        "kinds": dict(sorted(kinds.items())),
        "self_query_at_5": sum(p["at_5"] for p in probes),
        "self_query_at_1": sum(p["at_1"] for p in probes),
        "self_query_count": len(probes),
        "unretrieved_probe_ids": bad_probes,
        "unattributed_fact_entity_ids": source_missing,
        "current_focus": [r["id"] for r in focus.get("activated", [])],
        "missing_critical": missed_critical,
        "focus_bytes": focus_bytes,
        "focus_byte_limit": expected_max_bytes,
        "probes": probes,
        "notes": [
            "Self-query tests exact aliases and source pointers, not free-form semantic accuracy.",
            "Coverage scores cannot certify complete extraction from unstructured narrative.",
            "Use played-source regression cases for historical disambiguation and NPC epistemics.",
        ],
    }


def save(state_dir, hot, index):
    value = audit(index, hot)
    dest = Path(state_dir) / PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not dest.is_file() or dest.read_text(encoding="utf-8") != body:
        dest.write_text(body, encoding="utf-8")
    return value
