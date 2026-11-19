"""NPC decision pressure: sourced alternatives, not deterministic psychology.

Spectral Thinking's "circle of debunking" is adapted as a GM-only examination
of what an established self-image/need/boundary can lose under real events.
No NPC choices, emotions, Shiki actions, or new facts are generated here.
The existing private NPC psyche, intents and world clocks remain authoritative.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import npc_memory

SCHEMA = "npr-npc-dynamics/1"
MAX_ACTORS = 12
MAX_INTENTS = 4
MAX_CLOCKS = 4
FACET_PAIRS = (
    ("values", "needs", "values_under_pressure"),
    ("mask", "deep_fear", "mask_under_pressure"),
    ("boundary", "contradiction", "boundary_under_pressure"),
)


def _text(value, size=240):
    return " ".join(str(value or "").split())[:size]


def _parse(iso):
    try:
        return datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None


def _clock_relevance(clocks, actor):
    # Owner is a stable ID, not substring matching or lore inference.
    return [(id_, value) for id_, value in clocks.items()
            if isinstance(value, dict) and value.get("owner") == actor
            and not value.get("closed")]


def view(hot, *, present=(), index=None, max_actors=MAX_ACTORS, include_offscreen=True):
    """Read-only GM projection for every active cast member and urgent actor.

    The outcome of any pressure point still needs an autonomous GM decision and
    an ordinary canonical turn delta. A question is never a generated event.
    """
    cursor = hot.get("cursor") or {}
    now = _parse(cursor.get("at"))
    clocks = (hot.get("offscreen") or {}).get("clocks") or {}
    archive_entities = (index or {}).get("entities") or {}
    players = {"shiki", "player"}
    scene = hot.get("scene") or {}
    present = list(dict.fromkeys(present or scene.get("present") or []))
    present = [x for x in present if isinstance(x, str) and x not in players]
    # Preserve all initially present actors. If a scene is enormous, signal
    # omissions rather than silently claiming that the excluded actors are fine.
    ordered = present.copy()
    urgent = []
    unresolved_owners = []
    known_npcs = set((hot.get("cast") or {})) | set((hot.get("_psyche") or {}))
    known_npcs.update(k for k, record in archive_entities.items()
                      if record.get("kind") == "npc")
    if include_offscreen:
        for clock, value in clocks.items():
            if not isinstance(value, dict) or value.get("closed"):
                continue
            owner, due = value.get("owner"), _parse(value.get("due"))
            if owner and owner not in players and due and now and (
                    due <= now + timedelta(hours=24)):
                if owner in known_npcs:
                    if owner not in urgent:
                        urgent.append(owner)
                else:
                    # A clock owner might be an office, building, organization or
                    # a human label without a verified identity. Preserve it in
                    # world time, never fabricate an NPC from a string.
                    unresolved_owners.append({
                        "clock_id": clock,
                        "owner": _text(owner, 130),
                        "due": value.get("due"),
                        "status": "unresolved_actor_or_non_npc_owner",
                    })
        for key, hidden in (hot.get("_psyche") or {}).items():
            if key in players or key in ordered or key in urgent:
                continue
            active = (hidden or {}).get("intent") or {}
            if any(isinstance(v, dict) and v.get("status") == "committed"
                   for v in active.values()):
                urgent.append(key)
    ordered.extend(x for x in urgent if x not in ordered)
    keep = ordered[:max_actors]
    result = []
    for actor in keep:
        card = (hot.get("cast") or {}).get(actor) or {}
        archival = archive_entities.get(actor) or {}
        archival_sources = archival.get("sources") or []
        archived_identity = {}
        for projection in reversed(archival.get("projections") or []):
            for field in ("role", "wants", "fears", "stance"):
                if not card.get(field) and not archived_identity.get(field) and projection.get(field):
                    archived_identity[field] = projection[field]
        # Card fields are current operational state; archive fallback is an
        # explicitly labelled historic projection, not a renewed canon fact.
        card = {**archived_identity, **card}
        inner = npc_memory.inner_view(hot, actor)
        psyche = inner.get("psyche") or {}
        relevant = _clock_relevance(clocks, actor)
        schedule = []
        for id_, clock in relevant[:MAX_CLOCKS]:
            due = _parse(clock.get("due"))
            schedule.append({
                "id": id_, "due": clock.get("due"),
                "what": _text(clock.get("what"), 210),
                "status": "overdue" if due and now and due <= now else "pending",
                "source": clock.get("turn") or clock.get("source") or "GM_ONLY.offscreen.clocks",
            })
        intents = []
        for key, item in (inner.get("intent") or {}).items():
            if isinstance(item, dict) and item.get("status") in ("committed", "paused"):
                intents.append({
                    "id": key, "goal": _text(item.get("goal"), 230),
                    "status": item["status"], "source_turn": item.get("turn"),
                    **{f: _text(item[f], 230) for f in ("next", "due", "flaw", "against_shiki", "if_unopposed")
                       if item.get(f)},
                })
        facets = {k: _text(psyche[k], 220) for k in npc_memory.FACETS
                  if isinstance(psyche.get(k), str) and psyche[k].strip()}
        identity = {
            "role": _text(card.get("role"), 170),
            "wants": _text(card.get("wants"), 210),
            "fears": _text(card.get("fears"), 210),
            "stance": _text(card.get("stance"), 140),
        }
        identity = {k: v for k, v in identity.items() if v}
        tensions = [
            {"kind": name, "anchor": {a: facets[a], b: facets[b]},
             "status": "possibility_to_test_not_a_fact"}
            for a, b, name in FACET_PAIRS if a in facets and b in facets
        ]
        if identity.get("wants") and identity.get("fears"):
            tensions.append({
                "kind": "goal_at_risk",
                "anchor": {"wants": identity["wants"], "fears": identity["fears"]},
                "status": "possibility_to_test_not_a_fact",
            })
        # The intent lacks an explicit due clock. This is an *open choice*,
        # not evidence that the NPC already acted, ignored it or abandoned it.
        unscheduled = [i["id"] for i in intents if i["status"] == "committed"
                       and not relevant]
        if not (actor in present or schedule or intents or facets or identity):
            continue
        result.append({
            "id": actor, "present": actor in present,
            "identity_projection": identity,
            "identity_source": (archival_sources[0] if archived_identity and
                                archival_sources else "HOT.cast"),
            "identity_status": "operational_or_archival_projection_not_new_fact",
            "psyche_facets": facets,
            "pressure_pairs": tensions,
            "current_affect": inner.get("affect"),
            "committed_or_paused_intents": intents[:MAX_INTENTS],
            "world_clocks": schedule,
            "unclocked_intents": unscheduled[:MAX_INTENTS],
            "unoperationalized_goal": (
                {"wants": identity["wants"], "status": "motivation_not_committed_action"}
                if identity.get("wants") and not intents and not relevant else None
            ),
            "rule": "Character may act cruelly, irrationally or self-protectively; plans with a passed due "
                    "were acted on offscreen. "
                    "Pressure pairs are alternative questions, not decisions. "
                    "NPC knows only their source-bound experience. "
                    "Do not auto-advance or resolve any clock here.",
        })
    return {
        "schema": SCHEMA, "actors": result,
        "omitted_actor_ids": ordered[max_actors:],
        "unresolved_clock_owners": unresolved_owners[:MAX_ACTORS],
        "overdue_owner_ids": [a for a in urgent if
                              any(c.get("status") == "overdue"
                                  for c in next((r["world_clocks"] for r in result
                                                if r["id"] == a), []))],
        "rule": "Never drive the player character; never invent NPC action or change canon "
                "without a TURN delta. Existing intent and clock are actionable, not completed.",
    }
