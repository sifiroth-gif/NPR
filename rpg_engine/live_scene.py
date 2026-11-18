"""Source-bounded GM live-scene preparation and turn acceptance.

The single canonical write remains TURN.json. This module creates a derived,
GM-only read packet from accepted turns, actor-scoped evidence, hidden clocks
and the existing HOT projection. It never invents facts or NPC beliefs.
Bleach inspirations: gm_context, gm_scene_recall, gm_scene_gate,
npc_scene_fidelity, gm_world_preflight and gm_event_graph.
"""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path

from . import scene_gate

FORMAT = "NPR-LIVE-SCENE/1"
PATH = "recovery/GM_BRIEF.json"
POLICY_PATH = "recovery/GATE_POLICY.json"
MAX_ACTORS = 8
MAX_GRAPH = 55


def _digest(obj):
    return sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _short(obj, limit=500):
    if isinstance(obj, str):
        return obj[:limit]
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)[:limit]


def _source_turns(turns):
    return {t["id"]: t for t in turns if t.get("id") and t.get("kind") != "ooc"}


def _cast(hot, actor):
    card = (hot.get("cast") or {}).get(actor)
    if not isinstance(card, dict):
        return {"status": "missing_card", "actor": actor}
    keys = ("name", "role", "wants", "fears", "leverage", "stance",
            "voice", "body", "state", "seen", "knows", "hides")
    profile = {k: card[k] for k in keys if k in card}
    # The live packet is private, but do not drop distinctions between
    # a source-backed NPC belief and an unverified descriptive note.
    provenance = card.get("knowledge_provenance")
    if provenance:
        profile["knowledge_provenance"] = provenance[-12:]
    hidden = (hot.get("_npc_knowledge") or {}).get(actor, [])
    if hidden:
        profile["gm_private_beliefs"] = hidden[-10:]
    profile["status"] = "present_profile"
    return profile


def build(hot, turns):
    """One bounded read packet: sources, NPC knowledge, world pressure, graph.

    A primary passage is included for every selected past disclosure, so
    routine same-scene factual review needs no extra GitHub roundtrip.
    A larger passage still requires opening the cited exact source.
    """
    from .hot import parse_at

    scene = hot.get("scene") or {}
    cursor = hot.get("cursor") or {}
    present = [x for x in (scene.get("present") or [])
               if x not in ("player", "shiki")][:MAX_ACTORS]
    all_turns = _source_turns(turns)
    disclosures = scene_gate.compact_disclosure_cues(hot, turns, per_actor=8)
    actors = {}
    passages = {}
    node_ids = set()
    nodes, edges = [], []
    for actor in present:
        refs = disclosures.get(actor, [])
        prior = []
        for ref in refs:
            turn = all_turns.get(ref["turn"])
            if not turn:
                continue
            prior.append({
                "turn": turn["id"], "at": turn["at"],
                "source": ref["source"],
                "sha256": scene_gate._source(turn)["sha256"],
                "line": _short(turn.get("line", ""), 220),
                "status": "played_candidate_not_automatic_actor_fact",
            })
            if turn["id"] not in passages:
                original = turn.get("gm", "")
                excerpt = (original if len(original) <= 1550 else
                           original[:550] + "\n[... source excerpt shortened ...]\n" + original[-900:])
                passages[turn["id"]] = {
                    "player": _short(turn.get("player", ""), 240),
                    "gm_excerpt": excerpt,
                    "source": ref["source"],
                }
            if turn["id"] not in node_ids:
                node_ids.add(turn["id"])
                nodes.append({"id": turn["id"], "kind": "played_turn",
                              "at": turn["at"], "line": _short(turn.get("line", ""), 180)})
        for left, right in zip(prior, prior[1:]):
            edges.append({"from": left["turn"], "to": right["turn"],
                          "kind": "chronology_same_actor", "actor": actor,
                          "status": "source_order_only"})
        actor_profile = _cast(hot, actor)
        for belief in (actor_profile.get("knowledge_provenance", []) +
                       actor_profile.get("gm_private_beliefs", [])):
            source = belief.get("source")
            if source in all_turns:
                if source not in node_ids:
                    node_ids.add(source)
                    nodes.append({"id": source, "kind": "knowledge_source",
                                  "at": all_turns[source].get("at", ""),
                                  "line": _short(all_turns[source].get("line", ""), 180)})
                edges.append({"from": source, "to": "npc:" + actor,
                              "kind": "actor_received_by_" + belief.get("channel", "unknown"),
                              "status": "explicit_source_record", "visibility": belief.get("visibility")})
        actors[actor] = {"profile": actor_profile,
                         "prior_disclosures": prior,
                         "policy": ("A source mentioning an NPC is not proof they knew "
                                    "or said it; actor knowledge and player disclosure "
                                    "require distinct evidence.")}
    last = [r for r in turns if r.get("kind") != "ooc"][-3:]
    for row in last:
        if row["id"] not in node_ids:
            node_ids.add(row["id"])
            nodes.append({"id": row["id"], "kind": "recent_turn",
                          "at": row.get("at", ""),
                          "line": _short(row.get("line", ""), 190)})

    now = parse_at(cursor.get("at")) if cursor.get("at") else None
    clocks = []
    for key, value in (hot.get("offscreen") or {}).get("clocks", {}).items():
        if not isinstance(value, dict) or not value.get("due"):
            continue
        try:
            when = parse_at(value["due"])
        except (ValueError, TypeError):
            continue
        remaining = round((when - now).total_seconds() / 60, 1) if now else None
        clocks.append({
            "id": key, "owner": value.get("owner", ""),
            "what": _short(value.get("what", ""), 450),
            "due": value["due"], "minutes": remaining,
            "signs": _short(value.get("signs", ""), 280),
            "if_unopposed": _short(value.get("if_unopposed", ""), 400),
            "status": "overdue_needs_adjudication" if remaining is not None and remaining <= 0
                      else "scheduled",
        })
    clocks.sort(key=lambda x: x["due"])
    truths = (hot.get("offscreen") or {}).get("truth") or {}
    # Do not write historical secret texts into a new long-lived index.
    # The private GM_ONLY is still the single authoritative secret store.
    result = {
        "format": FORMAT,
        "cursor": {k: cursor.get(k) for k in ("id", "at", "place", "time", "date")},
        "scene": {k: _short(scene.get(k, ""), 1800)
                  for k in ("situation", "positions", "pressure")},
        "present_npcs": actors,
        "source_passages": passages,
        "recent_turns": [{"id": r["id"], "line": _short(r.get("line"), 270)}
                         for r in last],
        "evidence_graph": {
            "nodes": nodes[:MAX_GRAPH], "edges": edges[:MAX_GRAPH],
            "scope": "chronological edges only; semantic links are candidates, not causality",
        },
        "world": {
            "clocks": clocks[:24],
            "due_now": [c["id"] for c in clocks if c["status"] == "overdue_needs_adjudication"],
            "threads": {k: _short(v, 350) for k, v in list((hot.get("threads") or {}).items())[:12]},
            "secrets_in": "recovery/GM_ONLY.json",
            "secret_topics": list(truths)[-18:],
            "no_automatic_conclusion": True,
        },
        "shiki": {k: _short(v, 950) for k, v in (hot.get("shiki") or {}).items()
                  if k in ("body", "mental_link", "sheet", "genome")},
        "last": {
            "player": _short((hot.get("last") or {}).get("player", ""), 1200),
            "gm_tail": _short((hot.get("last") or {}).get("gm", "")[-900:], 900),
        },
        "guards": {
            "player_owns_shiki": True,
            "no_repeated_revelations_without_new_source": True,
            "no_npc_knowledge_without_channel": True,
            "clock_deadlines_are_real": True,
            "source_check_if_needed": "rpg_engine.scene_gate.prepare / original played turn",
        },
    }
    result["digest"] = _digest(result)
    return result


def _requires_review(turn):
    player = turn.get("player", "")
    gm = turn.get("gm", "")
    return bool(scene_gate.SENSITIVE.search(player) or
                (scene_gate.NOVELTY.search(gm) and scene_gate.DISCLOSURE.search(gm)))


def validate_admission(hot, turn, *, policy, accepted=()):
    """Fail closed on absent source review, fabricated actor knowledge and ignored clocks.

    Enabled explicitly by policy; never retroactively rejects legacy turns.
    The check establishes provenance, NOT semantic equivalence of paraphrases.
    """
    from .hot import parse_at
    if turn.get("kind") == "ooc":
        return []
    errors = []
    if not policy.get("enabled", False):
        return errors
    current = (hot.get("cursor") or {}).get("id")
    review = turn.get("review") or {}
    if not isinstance(review, dict):
        return ["review must be an object"]
    if review.get("predecessor") != current:
        errors.append("stale review: predecessor must equal accepted HOT cursor")
    known = _source_turns(accepted)
    actor_ids = [x for x in ((hot.get("scene") or {}).get("present") or [])
                 if x in (hot.get("cast") or {}) and x not in ("shiki", "player")]
    if actor_ids and _requires_review(turn):
        sources = review.get("sources")
        if not isinstance(sources, list):
            errors.append("source-sensitive scene must provide review.sources")
            sources = []
        if any(not isinstance(x, str) or x not in known for x in sources):
            errors.append("review.sources must refer to exact prior played turns")
        required = []
        for actor in actor_ids:
            cues = ((hot.get("scene") or {}).get("npc_disclosures_to_player") or {}).get(actor, [])
            required.extend(x["turn"] for x in cues if x["turn"] in known)
        # A small deterministic subset: older and latest actor admissions.
        required = list(dict.fromkeys(required))
        selected = required[:3] + required[-2:]
        if any(x not in sources for x in selected):
            errors.append("missing earliest/latest prior NPC disclosure source: " +
                          ", ".join(x for x in selected if x not in sources))
        if (scene_gate.NOVELTY.search(turn.get("gm", "")) and
                scene_gate.DISCLOSURE.search(turn.get("gm", ""))):
            contrast = review.get("novelty_contrast")
            if not isinstance(contrast, str) or len(contrast.strip()) < 40:
                errors.append("new NPC revelation requires source-bound novelty_contrast")
    # Validate every new source-bound NPC knowledge event, including private
    # memories. A hidden belief may use the same storage transaction without
    # making its content public.
    events = (turn.get("d") or {}).get("knowledge_events") or []
    for evidence in events:
        if not isinstance(evidence, dict):
            errors.append("knowledge_events contains non-object evidence")
            continue
        source = evidence.get("source")
        channel = evidence.get("channel")
        actor = evidence.get("actor")
        if (not evidence.get("claim") or channel not in
                ("message", "witness", "document", "inference", "told_in_scene")):
            errors.append("knowledge_event requires claim and valid channel")
        if source not in known and not (
                source == turn["id"] and actor in actor_ids
                and channel in ("witness", "told_in_scene")):
            errors.append("knowledge_event source not prior/observed: " + str(source))
        if (evidence.get("visibility") == "gm_only" and
                evidence.get("claim") in ((turn.get("d") or {})
                                           .get("cast", {}).get(actor, {}).get("knows+") or [])):
            errors.append("secret NPC belief must not be duplicated in public knows+")
    # Only source-authenticated knowledge may be delivered to a remote actor.
    # Actors already present can learn by hearing/seeing the current scene.
    for actor, patch in (turn.get("d") or {}).get("cast", {}).items():
        if not isinstance(patch, dict) or not patch.get("knows+"):
            continue
        if actor not in actor_ids and not any(
            isinstance(ev, dict) and ev.get("actor") == actor and
            ev.get("source") in known and ev.get("channel") in
            ("message", "witness", "document", "inference")
            for ev in (turn.get("d") or {}).get("knowledge_events", [])):
            errors.append(actor + ": offscreen knows+ requires knowledge_events with source/channel")
    if policy.get("enforce_due", True):
        prev_at = (hot.get("cursor") or {}).get("at")
        if prev_at:
            earlier, later = parse_at(prev_at), parse_at(turn["at"])
            crossed = [key for key, clock in (hot.get("offscreen") or {}).get("clocks", {}).items()
                       if isinstance(clock, dict) and clock.get("due") and
                       earlier < parse_at(clock["due"]) <= later]
            addressed = set((turn.get("d") or {}).get("clocks") or {})
            addressed.update((turn.get("d") or {}).get("world_reviewed") or [])
            if any(x not in addressed for x in crossed):
                errors.append("time jump crossed unreviewed NPC clock deadlines: " +
                              ", ".join(x for x in crossed if x not in addressed))
    return errors


def write(path, packet):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        path.write_text(body, encoding="utf-8")
    return len(body.encode("utf-8"))
