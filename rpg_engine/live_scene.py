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
import hashlib
import json
import re
from pathlib import Path

from . import (continuity_guard, context_dispatcher, dread, entity_memory,
               fight, general_memory, npc_dynamics, npc_memory, plot, scene_gate, scene_packet, will)

FORMAT = "NPR-LIVE-SCENE/1"
PATH = "recovery/GM_BRIEF.json"
POLICY_PATH = "recovery/GATE_POLICY.json"
ALIGNMENT_PATH = "campaigns/shiki/plans/NPC_ALIGNMENTS_GM_ONLY.json"
ALIGNMENT_SCHEMA = "npr-shiki-npc-alignments/1"
ALIGNMENT_CODES = frozenset(("LG", "LN", "LE", "NG", "N", "NE", "CG", "CN", "CE"))
MAX_ACTORS = 8
MAX_GRAPH = 55
MAX_PASSAGES = 12
TARGET_BRIEF_BYTES = scene_packet.TARGET_BYTES
MAX_BRIEF_BYTES = scene_packet.HARD_BYTES


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
            "voice", "body", "state", "seen", "knows", "hides",
            "competence", "limits", "misconceptions")
    profile = {}
    for k in keys:
        if k not in card:
            continue
        val = card[k]
        profile[k] = ([_short(x, 210) for x in val[-8:]]
                      if isinstance(val, list) else _short(val, 850))
        if k in ("stance", "body") and isinstance(profile[k], str):
            since = (card.get("since") or {}).get(k)
            profile[k] += f" [{since}]" if since else " [давняя запись: сверяй со state]"
    # The live packet is private, but do not drop distinctions between
    # a source-backed NPC belief and an unverified descriptive note.
    provenance = card.get("knowledge_provenance")
    if provenance:
        profile["knowledge_provenance"] = provenance[-12:]
    hidden = (hot.get("_npc_knowledge") or {}).get(actor, [])
    if hidden:
        profile["gm_private_beliefs"] = hidden[-10:]
    # Память персонажа (Bleach npc_psyche): что он пережил рядом с Шики,
    # как к нему относится, что чувствует сейчас и чего добивается.
    for k in ("tier", "first", "memory_total"):
        if k in card:
            profile[k] = card[k]
    if card.get("memory"):
        profile["memory"] = [{"turn": m.get("turn"), "ch": m.get("ch"),
                              "what": _short(m.get("what", ""), 200)} for m in card["memory"]]
        profile["memory_file"] = f"{npc_memory.MEMORY_DIR}/{actor}.json"
    inner = npc_memory.inner_view(hot, actor)
    if inner:
        profile["inner"] = inner
    profile["status"] = "present_profile"
    return profile


def load_alignments(state_dir):
    """GM-only, state-local storyline values; no default for unnamed extras."""
    path = Path(state_dir) / ALIGNMENT_PATH
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != ALIGNMENT_SCHEMA:
        raise ValueError("Invalid NPC alignment registry schema")
    characters = data.get("characters")
    if not isinstance(characters, dict):
        raise ValueError("NPC alignments must have a characters map")
    for ident, item in characters.items():
        if ident in ("shiki", "player"):
            raise ValueError("GM cannot assign Shiki an alignment")
        if not isinstance(item, dict) or (
                (item.get("alignment") or {}).get("code") not in ALIGNMENT_CODES):
            raise ValueError("Unknown alignment for " + str(ident))
    return characters


def build_for_state(hot, turns, state_dir):
    """Derive a small GM packet with ONLY present story NPC alignments."""
    result = build(hot, turns, alignments=load_alignments(state_dir),
                   entity_ledger=entity_memory.load(state_dir),
                   memory_index=general_memory.load(state_dir, hot=hot))
    # Облик присутствующих — со страниц вики: ведущему не нужно отдельно читать SCENE.md.
    for actor, node in (result.get("present_npcs") or {}).items():
        page = Path(state_dir) / "memory/npc" / f"{actor}.md"
        if page.is_file():
            text = page.read_text(encoding="utf-8")
            for field in ("внешность", "возраст", "пол"):
                match = re.search(rf"^- {field}: (.+?)(?: \[[^\]]*\])?$", text, re.M)
                if match:
                    node.setdefault("look", {})[field] = _short(match.group(1), 260)
        if not (node.get("look") or {}).get("внешность"):
            node.setdefault("look", {})["внешность"] = f"не записан: опиши при первой возможности и внеси в d.cast.{actor}.look"
    # Редкое знание: кто владеет, кто слышал — до реплик, а не после.
    from . import lore
    fences = lore.fences(hot, lore.load(state_dir), (hot.get("scene") or {}).get("present") or [])
    if fences:
        result["fences"] = fences
    # Blob SHA записанного TURN.json: ведущий пишет следующий ход без его чтения.
    turn_path = Path(state_dir) / "recovery/TURN.json"
    if turn_path.is_file():
        raw = turn_path.read_bytes()
        try:
            recorded = json.loads(raw)
            recorded_id = recorded.get("id") if isinstance(recorded, dict) else None
        except (ValueError, UnicodeDecodeError):
            recorded_id = None
        result["turn_file"] = {"path": "recovery/TURN.json",
                               "sha": hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest(),
                               "turn": recorded_id,
                               "rule": "Пиши следующий ход с этим sha. Конфликт — один раз перечитай TURN.json."}
    result.pop("digest", None)
    result["digest"] = _digest(result)
    return result


def build(hot, turns, *, alignments=None, entity_ledger=None, memory_index=None):
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
            if turn["id"] not in passages and len(passages) < MAX_PASSAGES:
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
                         "epistemic_limit": ("Hearing or repeating terminology is not understanding. "
                                             "Infer expertise only from established training and experience; "
                                             "a role alone grants no universal domain knowledge. "
                                             "Allow surprise, concrete clarification, misunderstanding, "
                                             "confident bluffing or denial according to character and risk; "
                                             "do not make every NPC react identically."),
                         "policy": ("A source mentioning an NPC is not proof they knew "
                                    "or said it; actor knowledge and player disclosure "
                                    "require distinct evidence.")}
        # A curated hidden registry applies ONLY to recurring plot characters.
        # Do not invent a value for an unlisted sentry or grant player knowledge.
        alignment = (alignments or {}).get(actor)
        if isinstance(alignment, dict):
            ethical = alignment.get("alignment") or {}
            if ethical.get("code") in ALIGNMENT_CODES:
                actors[actor]["alignment"] = {
                    "code": ethical["code"],
                    "label": ethical.get("label", ""),
                    "status": ethical.get("status", ""),
                    "order": _short(alignment.get("order", ""), 320),
                    "morality": _short(alignment.get("morality", ""), 320),
                    "line": _short(alignment.get("line", ""), 300),
                    "breaking_point": _short(alignment.get("breaking_point", ""), 320),
                    "rule": "GM-only moral compass, not a deterministic decision or NPC knowledge",
                }
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
        if not isinstance(value, dict) or not value.get("due") or value.get("closed"):
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
        "handoff": hot.get("handoff"),  # immutable closed-session pointer until the first S08 turn
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
        "plot": plot.brief(hot),
        "fight": fight.brief(hot),
        "agendas": {"rule": will.DARK, "soon": will.soon(hot)},
        "wiki": {"scene": "memory/SCENE.md", "index": "memory/INDEX.md",
                 "present": {k: f"memory/npc/{k}.md" for k in (scene.get("present") or []) if k != "shiki"},
                 "rule": "О персонаже, месте, вещи — его страница memory/; нет черты — не выдумывай."},
        "continuity": {
            "things": continuity_guard.scene_things(hot, present),
            "gone": (hot.get("things_gone") or [])[-5:],
            "rule": "Вещь, рана, число и место меняются только через d.things was→now.",
        },
        "horror": dread.brief(hot, present),
        "checks": (hot.get("_checks") or [])[-10:],
        "fold_errors": (hot.get("fold") or {}).get("errors", []),
        "needs_profile": npc_memory.needs_profile(hot, present),
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
    if entity_ledger is not None:
        result["entity_memory"] = entity_memory.activate(
            entity_ledger, scene=scene, cursor=cursor)
    if memory_index is not None:
        result["general_memory"], result["context_route"] = context_dispatcher.route(
            hot, memory_index)
    result["npc_dynamics"] = npc_dynamics.view(
        hot, present=scene.get("present") or [], index=memory_index)
    # One source-preserving L0 projector; no destructive overflow cascade.
    # Source archives, full plots, clocks and cast data remain authoritative.
    result = scene_packet.compact(result, hot, target_bytes=TARGET_BRIEF_BYTES,
                                  hard_bytes=MAX_BRIEF_BYTES)
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
    # Present = who stood in the scene before the turn, who entered it in this
    # turn (d.scene.present) and who is always with Shiki (policy, e.g. Nemera).
    joined = (((turn.get("d") or {}).get("scene") or {}).get("present") or [])
    actor_ids = [x for x in dict.fromkeys(list((hot.get("scene") or {}).get("present") or []) +
                                          list(joined) + list(policy.get("always_present") or []))
                 if isinstance(x, str) and x not in ("shiki", "player")]
    # Prior disclosures of present NPCs are handed to the GM before the scene
    # (GM_BRIEF.present_npcs.prior_disclosures, npc/<id>.talk). Citing their
    # turn ids back after the scene was bureaucracy the GM never filled; the
    # fold now flags an NPC repeating an old line instead (wiki repeat check).
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
        actor_patch = (((turn.get("d") or {}).get("cast") or {}).get(actor) or {})
        public_knows = actor_patch.get("knows+", []) if isinstance(actor_patch, dict) else []
        if (evidence.get("visibility") == "gm_only" and
                evidence.get("claim") in (public_knows or [])):
            errors.append("secret NPC belief must not be duplicated in public knows+")
    # Only source-authenticated knowledge may be delivered to a remote actor.
    # Actors already present can learn by hearing/seeing the current scene.
    for actor, patch in (((turn.get("d") or {}).get("cast") or {})).items():
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
                       if isinstance(clock, dict) and clock.get("due") and not clock.get("closed") and
                       earlier < parse_at(clock["due"]) <= later]
            addressed = set((turn.get("d") or {}).get("clocks") or {})
            addressed.update((turn.get("d") or {}).get("world_reviewed") or [])
            if any(x not in addressed for x in crossed):
                errors.append("time jump crossed unreviewed NPC clock deadlines: " +
                              ", ".join(x for x in crossed if x not in addressed))
    return errors


def attach_history_graph(packet, historical):
    """Connect lexical history candidates without inventing causality.

    A search hit is only a source pointer. A candidate link NEVER makes an
    actor a witness or attributes the underlying action to them.
    """
    graph = packet.setdefault("evidence_graph", {"nodes": [], "edges": []})
    existing = {node["id"] for node in graph["nodes"]}
    seen_edges = {(e["from"], e["to"], e["kind"]) for e in graph["edges"]}
    actors = packet.get("present_npcs") or {}
    for hit in (historical or {}).get("matches", [])[:12]:
        ident = "historical:" + str(hit.get("id"))
        if ident not in existing:
            graph["nodes"].append({
                "id": ident, "kind": "historical_candidate",
                "source": hit.get("source"), "lines": hit.get("lines"),
                "at": hit.get("at"), "headline": _short(hit.get("headline", ""), 220),
                "source_sha256": hit.get("source_sha256"),
                "status": "candidate_only",
            })
            existing.add(ident)
        # Evidence is tied to a search query, never to made-up actor knowledge.
        target = "query:history"
        edge = (ident, target, "retrieved_for_query")
        if edge not in seen_edges:
            graph["edges"].append({
                "from": ident, "to": target, "kind": "retrieved_for_query",
                "score": hit.get("score"),
                "status": "candidate_only",
            })
            seen_edges.add(edge)
        for actor in actors:
            name = actors[actor].get("profile", {}).get("name", actor)
            actor_terms = set(scene_gate._words(name))
            matched = set(scene_gate._words(hit.get("headline", "") +
                                               " " + hit.get("preview", "")))
            if actor_terms & matched:
                key = (ident, "npc:" + actor, "lexical_co_mention")
                if key not in seen_edges:
                    graph["edges"].append({
                        "from": ident, "to": "npc:" + actor,
                        "kind": "lexical_co_mention",
                        "status": "candidate_only",
                    })
                    seen_edges.add(key)
    graph["nodes"] = graph["nodes"][:MAX_GRAPH]
    graph["edges"] = graph["edges"][:MAX_GRAPH]
    return packet


def write(path, packet):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != body:
        path.write_text(body, encoding="utf-8")
    return len(body.encode("utf-8"))
