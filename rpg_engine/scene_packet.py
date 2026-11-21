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
MAX_THREAD_DETAILS = 3


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
            if event.get("kind") == "chapter" or resolve_soon:
                record["notes"] = [_short(x, 175) for x in event["notes"][:3]]
            else:
                record["notes_ref"] = "recovery/GM_ONLY.json:plot.events." + str(event["id"])
        active.append(record)
    packet["plot"] = {
        "main": {k: v for k, v in (source.get("main") or {}).items()
                 if k in ("title", "question")},
        "active": active,
        "queued": [{k: v for k, v in event.items() if k in ("id", "kind", "title")}
                   for event in source.get("queued") or []],
        "queued_ref": "recovery/GM_ONLY.json:plot.events",
        "source": "recovery/GM_ONLY.json:plot",
        "rules": source.get("rules") or "",
    }


def _actors(packet):
    """Retain identity, actions, knowledge and sources; de-duplicate dynamics."""
    present = packet.get("present_npcs") or {}
    limits = {node.pop("epistemic_limit", None) for node in present.values() if isinstance(node, dict)}
    limits.discard(None)
    if len(limits) == 1 and isinstance(packet.get("guards"), dict):
        packet["guards"]["npc_epistemic_limit"] = limits.pop()   # одно правило на всех, не копия на каждого
    for actor, node in present.items():
        profile = node.get("profile") or {}
        knows = profile.get("knows")
        if isinstance(knows, list):
            if len(knows) > 4:
                profile["knows_total"] = len(knows)
            profile["knows"] = [_short(v, 245) for v in knows[-4:]]
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
        if len(prior) > 3:
            node["prior_disclosure_ids"] = [ref["turn"] for ref in prior]
            node["prior_disclosures"] = [prior[0], *prior[-2:]]
        for ref in node.get("prior_disclosures") or []:
            if "line" in ref:
                ref["line"] = _short(ref["line"], 130)
        if prior:
            node["source_ref"] = "archive/session-*-turns.jsonl"
        node.pop("policy", None)  # canonical epistemic rule is already in guards
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
                inner = (present[actor].get("profile") or {}).get("inner")
                if isinstance(inner, dict):   # одна копия психики и планов, а не две
                    inner.pop("psyche", None)
                    inner.pop("intent", None)
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
            "state": _short(state, 220),
            "source": "memory/threads/" + key + ".md",
        }
        if isinstance(value, dict) and value.get("turn"):
            detail[key]["turn"] = value["turn"]
    world["threads"] = detail
    world["omitted_thread_ids"] = [key for key in all_threads if key not in detail]
    world["thread_sources"] = "memory/INDEX.md"
    world["secret_topics"] = (world.get("secret_topics") or [])[-6:]
    world["secrets_in"] = "recovery/GM_ONLY.json"
    # Keep full imminent clock mechanics, summarize later deadlines by source.
    # Every clock ID and due timestamp must survive even in an oversized packet.
    for index, clock in enumerate(world.get("clocks") or []):
        clock["source"] = "recovery/GM_ONLY.json:offscreen.clocks." + str(clock.get("id"))
        if index >= 2 and clock.get("status") != "overdue_needs_adjudication":
            clock["what"] = _short(clock.get("what"), 145)
            clock["signs"] = _short(clock.get("signs"), 115)
            clock["if_unopposed"] = _short(clock.get("if_unopposed"), 135)


def _deduplicate_memory(packet):
    """One copy of identical verified facts, with an in-packet reference."""
    record_by_id = {
        item["id"]: item for item in (packet.get("entity_memory") or {}).get("activated") or []
        if isinstance(item, dict) and item.get("id")
    }
    for item in (packet.get("general_memory") or {}).get("activated") or []:
        entity = record_by_id.get(item.get("id"))
        if not entity or not item.get("facts"):
            continue
        general_facts = item["facts"]
        entity_facts = entity.get("facts") or []
        def keys(facts):
            return sorted((f.get("field"), f.get("source"), str(f.get("value")))
                          for f in facts)
        if len(general_facts) == len(entity_facts) and keys(general_facts) == keys(entity_facts):
            item["facts"] = [{"field": f.get("field"), "source": f.get("source")}
                             for f in general_facts]
            item["facts_ref"] = "entity_memory.activated[" + str(item["id"]) + "].facts"


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
        short = _short(excerpt, 560)
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
    _deduplicate_memory(packet)
    _passages(packet)
    packet["checks"] = _current_checks(packet.get("checks") or [], (hot.get("cursor") or {}).get("id"))
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
    # A routine scene already has actor-scoped chronological source IDs;
    # the large node graph is generated on demand by scene_gate for a historical
    # claim. Keep only tiny order anchors; never infer cause or actor knowledge.
    edges = []
    for actor_id, actor in (packet.get("present_npcs") or {}).items():
        refs = actor.get("prior_disclosures") or []
        if len(refs) >= 2:
            edges.append({"from": refs[0]["turn"], "to": refs[-1]["turn"],
                          "kind": "chronology_same_actor", "actor": actor_id,
                          "status": "source_order_only"})
    packet["evidence_graph"] = {
        "nodes": [], "edges": edges, "source": "archive/session-*-turns.jsonl",
        "scope": "source order only; no semantic entailment",
    }
    horror = packet.get("horror") or {}
    if horror.get("rule"):
        horror["rule"] = "campaigns/shiki/ref/HORROR.md"
    # Do not duplicate empty NPC identity projections in general memory.
    for item in (packet.get("general_memory") or {}).get("activated") or []:
        if item.get("id") in (packet.get("present_npcs") or {}) and not item.get("facts"):
            for field in ("label", "channels", "status"):
                item.pop(field, None)
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
        for key in reversed(list(passages)[2:]):
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
        packet["recent_turns"] = (packet.get("recent_turns") or [])[-1:]
    # Последняя ступень: длинные описания укорачиваются, ключи, сроки и ссылки на источник целы.
    for section, limit in (("continuity", 180), ("horror", 180), ("general_memory", 160),
                           ("present_npcs", 220), ("fight", 160), ("world", 220), ("plot", 260)):
        if _bytes(packet) <= target_bytes:
            break
        if section in packet:
            packet[section] = _shorten(packet[section], limit)
    packet["budget"] = {"target_bytes": target_bytes, "hard_bytes": hard_bytes}
    if _bytes(packet) > target_bytes:
        packet["target_exceeded"] = True
    if _bytes(packet) > hard_bytes:
        packet["over_budget"] = True
    else:
        packet.pop("over_budget", None)
    return packet


def _shorten(value, limit):
    """Строки длиннее limit — до limit с многоточием; структура и ключи прежние."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"
    if isinstance(value, list):
        return [_shorten(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: (v if k in ("source", "source_ref", "turn", "id", "due", "at", "sha256") else _shorten(v, limit))
                for k, v in value.items()}
    return value


STATE_CHECKS = ("plot", "fight", "memory", "will")


def _current_checks(checks, cursor):
    """Замечания к последнему ходу и текущему состоянию — все; старые остаются в CHECKS.json."""
    keep = [c for c in checks if c.get("turn") == cursor or c.get("kind") in STATE_CHECKS]
    return keep[-8:] or checks[-2:]
