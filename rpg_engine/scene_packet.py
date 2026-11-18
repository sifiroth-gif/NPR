"""Bounded, source-preserving L0 projection for a live GM scene.

This is a derived read model, never a second canon. The full character cards,
story events, testimony, and hidden processes remain in HOT/GM_ONLY and
immutable played archives. Do not shrink irreversible clinical state or clocks
just to meet a byte target.
"""
from __future__ import annotations

from copy import deepcopy
import json


TARGET_BYTES = 32_000
HARD_BYTES = 48_000
MAX_PASSAGES = 4
MAX_THREAD_DETAILS = 6


def _bytes(packet):
    return len(json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _short(value, length):
    text = str(value or "")
    return text if len(text) <= length else text[:length - 1] + "…"


def _plot(packet):
    """Only active resolutions are actionable; queued endings stay in GM_ONLY."""
    source = packet.get("plot") or {}
    active = []
    for event in source.get("active") or []:
        record = {k: event[k] for k in (
            "id", "kind", "title", "parent", "question", "limit",
            "scenes", "scenes_left", "threads", "at_limit",
        ) if k in event}
        # The current chapter and any near-deadline scene need their outcomes.
        resolve_soon = (event.get("kind") == "chapter" or
                        event.get("at_limit") or
                        event.get("scenes_left", 100) <= 2)
        if resolve_soon:
            record["endings"] = event.get("endings", [])
        elif event.get("endings"):
            record["endings_ref"] = "recovery/GM_ONLY.json:plot.events." + str(event["id"])
        if event.get("notes"):
            record["notes"] = [_short(x, 210) for x in event["notes"][:3]]
        active.append(record)
    packet["plot"] = {
        "main": {k: v for k, v in (source.get("main") or {}).items()
                 if k in ("title", "question")},
        "active": active,
        "queued": [{k: (_short(v, 135) if k == "trigger" else v)
                    for k, v in event.items() if k in ("id", "kind", "title", "trigger")}
                   for event in source.get("queued") or []],
        "source": "recovery/GM_ONLY.json:plot",
        "rules": "campaigns/shiki/GM_CORE.md#темп-и-события",
    }


def _actors(packet):
    """Retain identity, actions, knowledge and sources; de-duplicate dynamics."""
    present = packet.get("present_npcs") or {}
    for actor, node in present.items():
        profile = node.get("profile") or {}
        knows = profile.get("knows")
        if isinstance(knows, list):
            if len(knows) > 6:
                profile["knows_total"] = len(knows)
            profile["knows"] = [_short(v, 265) for v in knows[-6:]]
            profile["knows_ref"] = "memory/npc/" + actor + ".md"
        if isinstance(profile.get("memory"), list):
            profile["memory"] = profile["memory"][-2:]
            profile.setdefault("memory_file", "archive/npc-memory/" + actor + ".json")
        provenance = profile.get("knowledge_provenance")
        if isinstance(provenance, list) and len(provenance) > 6:
            profile["knowledge_provenance"] = provenance[-6:]
            profile["provenance_ref"] = "memory/npc/" + actor + ".md"
        if isinstance(profile.get("inner"), dict):
            inner = profile["inner"]
            if isinstance(inner.get("remembers"), list):
                inner["remembers"] = inner["remembers"][-2:]
        if profile.get("hides") and len(str(profile["hides"])) > 450:
            profile["hides"] = _short(profile["hides"], 450)
            profile["hides_ref"] = "recovery/GM_ONLY.json:cast." + actor
        alignment = node.get("alignment")
        if isinstance(alignment, dict):
            node["alignment"] = {k: _short(v, 190) for k, v in alignment.items()
                                 if k in ("code", "order", "line", "breaking_point")}
            node["alignment_ref"] = "campaigns/shiki/plans/NPC_ALIGNMENTS_GM_ONLY.json"
        prior = node.get("prior_disclosures") or []
        for ref in prior:
            if "line" in ref:
                ref["line"] = _short(ref["line"], 165)
        if prior:
            node["source_ref"] = "archive/session-*-turns.jsonl"
    dynamics = packet.get("npc_dynamics") or {}
    background = []
    for entry in dynamics.get("actors") or []:
        actor = entry.get("id")
        detail = {
            "current_affect": entry.get("current_affect"),
            "committed_or_paused_intents": entry.get("committed_or_paused_intents"),
            "psyche_facets": entry.get("psyche_facets"),
            "world_clocks": entry.get("world_clocks"),
            "unclocked_intents": entry.get("unclocked_intents"),
            "pressure_pairs": [p for p in entry.get("pressure_pairs") or []
                               if p.get("kind") != "goal_at_risk"],
        }
        detail = {k: v for k, v in detail.items() if v}
        if actor in present:
            if detail:
                present[actor]["dynamics"] = detail
        else:
            background.append({
                "id": actor, "identity": entry.get("identity_projection") or {},
                **detail,
            })
    packet["npc_dynamics"] = {
        "actors": [{**entry, "present": False} for entry in background],
        "present_inline": list(present),
        "omitted_actor_ids": dynamics.get("omitted_actor_ids") or [],
        "overdue_owner_ids": dynamics.get("overdue_owner_ids") or [],
        "unresolved_clock_owners": dynamics.get("unresolved_clock_owners") or [],
        "source": "recovery/GM_ONLY.json:psyche",
    }


def _threads(packet, hot):
    """Prefer current chapter, then active opposition and most recently touched."""
    world = packet.get("world") or {}
    all_threads = hot.get("threads") or {}
    if not all_threads:
        return
    active = packet.get("plot", {}).get("active") or []
    chapter = [e for e in active if e.get("kind") == "chapter"]
    side = [e for e in active if e.get("kind") != "chapter"]
    priority = []
    for event in chapter + side:
        priority.extend(event.get("threads") or [])
    # A genuinely recent update can be relevant even if not yet plotted.
    recent = sorted(all_threads, key=lambda key:
                    str(all_threads[key].get("turn", "")) if isinstance(all_threads[key], dict)
                    else "", reverse=True)
    priority = list(dict.fromkeys(priority + recent))
    focus = [key for key in priority if key in all_threads][:MAX_THREAD_DETAILS]
    detail = {}
    for key in focus:
        value = all_threads[key]
        state = value.get("state", value) if isinstance(value, dict) else value
        detail[key] = {
            "state": _short(state, 380),
            "source": "memory/threads/" + key + ".md",
        }
        if isinstance(value, dict) and value.get("turn"):
            detail[key]["turn"] = value["turn"]
    world["threads"] = detail
    world["omitted_thread_ids"] = [key for key in all_threads if key not in detail]
    world["thread_sources"] = "memory/INDEX.md"
    # All active/overdue clocks remain intact, regardless of packet size.


def _passages(packet):
    passages = packet.get("source_passages") or {}
    if not passages:
        return
    chosen = []
    for actor in (packet.get("present_npcs") or {}).values():
        prior = [x["turn"] for x in actor.get("prior_disclosures") or []
                 if x.get("turn") in passages]
        if prior:
            chosen.extend((prior[0], prior[-1]))
    chosen = list(dict.fromkeys(chosen))
    if not chosen:
        chosen = list(passages)[-MAX_PASSAGES:]
    chosen = chosen[:MAX_PASSAGES]
    old_ids = set(passages)
    retained = {}
    trimmed = bool(old_ids - set(chosen))
    for key in chosen:
        value = passages[key]
        excerpt = value.get("gm_excerpt", "")
        short = _short(excerpt, 780)
        trimmed = trimmed or short != excerpt
        retained[key] = {**value, "gm_excerpt": short}
    packet["source_passages"] = retained
    if trimmed:
        packet["source_passages_truncated"] = True
    packet["source_passages_ref"] = "archive/session-*-turns.jsonl"


def compact(raw_packet, hot, *, target_bytes=TARGET_BYTES, hard_bytes=HARD_BYTES):
    """Return independently allocated L0 without modifying HOT or input packet.

    An oversize mandatory core is explicitly flagged instead of erasing clocks,
    active chapter endings or the only remaining played-source passage.
    """
    if not 0 < target_bytes <= hard_bytes:
        raise ValueError("invalid packet byte budgets")
    packet = deepcopy(raw_packet)
    _plot(packet)
    _actors(packet)
    _threads(packet, hot)
    _passages(packet)
    packet["checks"] = (packet.get("checks") or [])[-4:]
    packet["checks_ref"] = "recovery/CHECKS.json"
    packet["recent_turns"] = (packet.get("recent_turns") or [])[-2:]
    packet["recent_turns_ref"] = "recovery/TURN.json"
    last = packet.get("last") or {}
    if last:
        packet["last"] = {
            "player": _short(last.get("player"), 450),
            "gm_tail": _short(last.get("gm_tail"), 350),
            "source": "recovery/TURN.json",
        }
    # No cutting of arbitrary player/narrator text is required at first:
    # clinical state and physical positions are more important than an aesthetic target.
    if _bytes(packet) > target_bytes:
        for actor in (packet.get("present_npcs") or {}).values():
            profile = actor.get("profile") or {}
            if _bytes(packet) <= target_bytes:
                break
            if isinstance(profile.get("memory"), list) and len(profile["memory"]) > 1:
                profile["memory"] = profile["memory"][-1:]
    if _bytes(packet) > target_bytes:
        graph = packet.get("evidence_graph") or {}
        graph["nodes"] = (graph.get("nodes") or [])[:20]
        graph["edges"] = (graph.get("edges") or [])[:20]
        graph["source"] = "archive/session-*-turns.jsonl"
    if _bytes(packet) > target_bytes:
        for actor in (packet.get("present_npcs") or {}).values():
            if _bytes(packet) <= target_bytes:
                break
            prior = actor.get("prior_disclosures") or []
            for record in prior:
                record.pop("line", None)  # keep exact turn, date, SHA and source
    if _bytes(packet) > target_bytes:
        for event in packet.get("plot", {}).get("active") or []:
            if _bytes(packet) <= target_bytes:
                break
            if event.get("kind") != "chapter" and not event.get("at_limit"):
                if event.pop("notes", None):
                    event["notes_ref"] = "recovery/GM_ONLY.json:plot.events." + str(event["id"])
    if _bytes(packet) > target_bytes:
        packet["last"] = {"source": "recovery/TURN.json"}
    if _bytes(packet) > target_bytes:
        # Never remove the sole source excerpt. A GM can open the exact journal.
        passages = packet.get("source_passages") or {}
        for key in reversed(list(passages)[1:]):
            if _bytes(packet) <= target_bytes:
                break
            passages.pop(key)
            packet["source_passages_truncated"] = True
    if _bytes(packet) > target_bytes:
        for actor in (packet.get("present_npcs") or {}).values():
            if _bytes(packet) <= target_bytes:
                break
            actor.pop("policy", None)  # policy is also in guards
    if _bytes(packet) > target_bytes:
        # Do not lose deadlines or characters; safe reduction of optional indices.
        packet["checks"] = (packet.get("checks") or [])[-2:]
        packet["recent_turns"] = (packet.get("recent_turns") or [])[-1:]
    packet["budget"] = {"target_bytes": target_bytes, "hard_bytes": hard_bytes}
    if _bytes(packet) > target_bytes:
        packet["target_exceeded"] = True
    if _bytes(packet) > hard_bytes:
        packet["over_budget"] = True
    else:
        packet.pop("over_budget", None)
    return packet
