"""Adaptive source-aware context router for every NPR scene.

Adapts Context Engineering's dynamic assembly: select only what the immediate
action needs, preserve mandatory facts and source pointers, and expose overflow.
No LLM reranking, external requests, additional canonical store or GM action.
"""
from __future__ import annotations

import json
import re

from . import general_memory

SCHEMA = "npr-context-routing/1"
DEFAULT_FOCUS = 10
MAX_FOCUS = 20
DEFAULT_BYTES = 6600

HISTORY = re.compile(
    r"\b(?:раньше|прежде|прошл(?:ый|ое|ой)|истори[яию]|вспом(?:ни|ина)|"
    r"помни(?:шь|т)|говорил(?:а|и)?|обещал(?:а)?|знал(?:а|и)?|"
    r"скрывал(?:а|и)?|когда\s+(?:впервые|раньше)|"
    r"previously|remember|earlier|promised)\b", re.I)
TIME_SKIP = re.compile(r"\b(?:перемот|прошл[ои]\s+\d|жду\s+(?:день|дней|недел)|"
                       r"следующ(?:ий|ей)\s+(?:день|утро|недел)|"
                       r"через\s+\d+|time\s+skip)\b", re.I)
COMBAT = re.compile(r"\b(?:удар|атак|сраж|бой|прыж|уклон|кунай|ниндзюцу|"
                    r"тайдзюцу|враг|стрел|броса[юет]|захват|"
                    r"attack|fight|dodge)\w*", re.I)
MEDICINE = re.compile(r"\b(?:леч|кров|рана|патолог|диагност|врач|пациент|"
                      r"дыхани|ткан|операци|медицин|реанима|бронх|"
                      r"heal|medical|patient)\w*", re.I)


def _size(x):
    return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def classify(action):
    if not action.strip():
        return "scene"
    if TIME_SKIP.search(action):
        return "time_advance"
    if HISTORY.search(action):
        return "historical_claim"
    if COMBAT.search(action):
        return "combat"
    if MEDICINE.search(action):
        return "medical"
    return "routine"


def route(hot, index, *, action="", limit=DEFAULT_FOCUS, max_bytes=DEFAULT_BYTES):
    """Build a bounded L0 with a traceable plan, not a new game decision."""
    scene, cursor = hot.get("scene") or {}, hot.get("cursor") or {}
    mode = classify(action)
    present = [p for p in scene.get("present", []) if p not in ("shiki", "player")]
    # Retrieve enough initial candidates to discover omissions and avoid a
    # "top-7 is the whole world" fallacy. The eventual user context stays small.
    candidates = general_memory.retrieve(
        index, scene=scene, cursor=cursor, action=action, level=0,
        limit=max(MAX_FOCUS, limit))
    pool = list(candidates.get("activated", []))
    selected = []
    selected_keys = set()
    action_priority = [x for x in pool if "exact_action" in x.get("channels", [])]
    mandatory = [x for x in pool if
                 (x["id"] in present or
                  (x.get("facts") and "scene_anchor" in x.get("channels", [])))]
    by_scene = [x for x in pool if "scene_anchor" in x.get("channels", [])]
    for bucket in (action_priority, mandatory, by_scene, pool):
        for item in bucket:
            if item["id"] not in selected_keys:
                selected.append(item)
                selected_keys.add(item["id"])
    focus = selected[:min(MAX_FOCUS, max(1, int(limit)))]
    available = {v["id"] for v in pool}
    missing_cast = [actor for actor in present if actor not in available]
    omitted = [x["id"] for x in selected[len(focus):]
               if (x["id"] in present or x.get("facts") and
                   "scene_anchor" in x.get("channels", []) or
                   "exact_action" in x.get("channels", []))]
    omitted.extend(x for x in candidates.get("omitted_priority_entities", [])
                   if x not in omitted)
    packet = {
        **candidates,
        "activated": focus, "omitted_priority_entities": omitted,
        "omitted": candidates.get("omitted", 0) + len(pool) - len(focus),
    }
    packet = general_memory.compact(packet, max_bytes=max_bytes)
    kept = {item["id"] for item in packet["activated"]}
    overflow = sorted(set(omitted + [k for k in selected_keys if k not in kept and
                                    (k in present or
                                     any(f["id"] == k for f in action_priority +
                                         mandatory))]))
    omitted_cast = [p for p in present if p not in kept]
    if any(k not in packet.get("omitted_priority_entities", []) for k in overflow):
        packet.setdefault("omitted_priority_entities", []).extend(
            k for k in overflow if k not in packet["omitted_priority_entities"])
    packet["omitted_priority_entities"] = list(dict.fromkeys(
        packet.get("omitted_priority_entities", [])))
    result = {
        "schema": SCHEMA,
        "mode": mode, "cursor": cursor.get("id"),
        "focus": [x["id"] for x in packet["activated"]],
        "unindexed_present_actors": missing_cast,
        "omitted_present_actors": omitted_cast,
        "omitted_critical": packet.get("omitted_priority_entities", []),
        "needs_primary_source_review": bool(mode == "historical_claim" or
                                            missing_cast or omitted_cast or
                                            packet.get("over_budget")),
        "time_advance_requires_world_review": mode == "time_advance",
        "read_levels": {
            "L0": "source-bound entity facts and current scene identities",
            "L1": "open relevant cast, equipment, process and psyche projections on demand",
            "L2": "open exact archived turn and testimony before historical truth claims",
        },
        "epistemic_rules": {
            "truth": "verified fact requires source and scope",
            "npc_knowledge": "separate from world truth and from player disclosure",
            "source_candidate": "retrieve original before treating as canon",
            "shiki": "player alone owns Shiki's voluntary decisions",
        },
        "budget_bytes": max_bytes,
        "focus_bytes": _size(packet),
        "status": ("review_required" if missing_cast or omitted_cast or
                   packet.get("omitted_priority_entities") or
                   packet.get("over_budget") else "focused"),
    }
    return packet, result
