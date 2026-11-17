"""Source-reviewed, actor-scoped pre-scene memory gate for HOT campaigns.

Ports the *contracts* of Bleach gm_scene_recall / gm_event_graph /
gm_scene_gate / npc_scene_fidelity to NPR's append-only played journal.
No NLP heuristic creates canon, new NPC knowledge, or player actions.

An ordinary current-scene response can use HOT. If a player asks what an NPC
said, knew, concealed, promised, or previously revealed, run prepare() first.
Before presenting the draft run review_draft() with the reviewed source IDs.
The gate fails closed on missing acknowledgements. It does not pretend that
word overlap proves semantic identity: the GM still judges actual relevance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

from . import heuristic_history

FORMAT = "NPR-SOURCE-REVIEWED-SCENE/1"
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
SENSITIVE = re.compile(
    r"молчал|скрыва|умолч|говорил|рассказ|призн|зна[ею]|помн|"
    r"зачем|почему|кто\s+приказ|повтор|раньше|прежде|обещал|"
    r"сообщал|документ|показал|предъяв|выяснил", re.I)
DISCLOSURE = re.compile(
    r"рассказ|призн|объяснил|сказал|сообщил|предъяв|показал|"
    r"подтвердил|отрицал|отверг|выдал|раскрыл|предложил|"
    r"назвал|дал\s+ответ|выписк|папк|запрос", re.I)
NOVELTY = re.compile(
    r"впервые|только\s+теперь|перв(?:ый|ое)\s+раз|"
    r"ещ[её]\s+не\s+говорил|прежде\s+молчал|не\s+рассказывал|"
    r"наконец\s+признал|скрывал\s+от\s+вас|новое\s+признание", re.I)
STOP = set(("это этот эта эти того того мне меня тебя вам вами ваших "
            "сейчас тогда после перед сегодня вчера завтра снова сразу может "
            "лишь уже ещё однако будто затем другой именно потому очень "
            "было была были будет есть будут чтобы когда которые своего своей "
            "своих сказал сказала спросил ответил спросила ответила"
            ).split())


def _json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _words(value):
    return tuple(dict.fromkeys(
        heuristic_history.stem(m.group().casefold()) for m in TOKEN.finditer(str(value))
        if len(m.group()) >= 3 and m.group().casefold() not in STOP
    ))


def _actor_forms(hot, actor):
    ident = str(actor).casefold()
    profile = (hot.get("cast") or {}).get(actor) or {}
    label = profile.get("name") or actor
    forms = {ident}
    for word in _words(label):
        if len(word) >= 4:
            forms.add(word)
    return forms


def _mentions(row, forms):
    words = set(_words(row.get("line", "") + " " + row.get("gm", "")))
    return bool(forms & words)


def _source(row):
    return {
        "turn": row["id"], "at": row.get("at", ""),
        "line": row.get("line", "").strip(),
        "source": "archive/session-%03d-turns.jsonl" % int(row["id"][1:3]),
        "sha256": sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "status": "played_source",
    }


def played_turns(state_dir):
    """Current session only: cheap authoritative read; TURN wins over stale fold.

    Older-session search is provided by the existing full-history heuristic
    shards. Do not read GM_ONLY, discarded drafts, or summaries as new canon.
    """
    root = Path(state_dir)
    hot = _json(root / "recovery/HOT.json", {})
    latest = _json(root / "recovery/TURN.json", {})
    session = int(hot.get("session", 1))
    if latest.get("id"):
        match = re.fullmatch(r"s(\d+)\.text\.(\d+)", latest["id"])
        if match:
            session = int(match.group(1))
    log = root / ("archive/session-%03d-turns.jsonl" % session)
    turns = []
    if log.is_file():
        for raw in log.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                item = json.loads(raw)
                if item.get("id") and item.get("kind") != "ooc":
                    turns.append(item)
    if latest.get("id") and latest.get("kind") != "ooc":
        turns = [row for row in turns if row["id"] != latest["id"]]
        turns.append(latest)
    turns.sort(key=lambda row: int(row["id"].rsplit(".", 1)[-1]))
    return hot, turns


def _relevance(line, action_tokens, context_tokens, *, actor_match, disclosure):
    words = set(_words(line))
    overlap = words & action_tokens
    contextual = words & context_tokens
    score = 2.5 * len(overlap) + 0.65 * len(contextual)
    if actor_match:
        score += 3.0
    if disclosure:
        score += 2.5
    if len(overlap) >= 2:
        score += 2.0
    return round(score, 3)


def prepare(state_dir, *, player_action, actors, limit=10):
    """Make an actor-scoped replay review plus a small candidate event graph.

    Explicit earlier disclosures are retained even when the player's new
    question uses vague referents such as 'what had you concealed?'.
    """
    if not isinstance(player_action, str) or not isinstance(actors, (list, tuple)):
        raise ValueError("action and actors must be explicit")
    if not actors or len(actors) > 8 or not 1 <= limit <= 30:
        raise ValueError("select 1..8 actors and limit 1..30")
    hot, turns = played_turns(state_dir)
    action_terms = set(_words(player_action))
    recent = " ".join(row.get("line", "") for row in turns[-4:])
    context_terms = set(_words(recent))
    actor_packets = {}
    graph_nodes = {}
    graph_edges = {}
    for actor in actors:
        forms = _actor_forms(hot, actor)
        matches = []
        for i, row in enumerate(turns):
            if not _mentions(row, forms):
                continue
            line = row.get("line", "") or (row.get("gm", "")[:300])
            disclosure = bool(DISCLOSURE.search(line))
            score = _relevance(line, action_terms, context_terms,
                               actor_match=True, disclosure=disclosure)
            result = {**_source(row), "score": score,
                      "disclosure_candidate": disclosure,
                      "terms": list(set(_words(line)) & action_terms)}
            matches.append(result)
        # Include BOTH the most relevant disclosure and the current scene.
        # Recency alone must never erase old accepted admissions.
        disclosures = sorted((item for item in matches if item["disclosure_candidate"]),
                             key=lambda x: (-x["score"], x["turn"]))
        recently = list(reversed(matches[-3:]))
        selected = {}
        for item in disclosures[:max(4, limit // 2)] + recently:
            selected[item["turn"]] = item
        for item in sorted(matches, key=lambda x: (-x["score"], x["turn"])):
            if len(selected) >= limit:
                break
            selected[item["turn"]] = item
        selected_list = sorted(selected.values(), key=lambda x: int(x["turn"].rsplit(".",1)[-1]))
        # The source list is a candidate; the GM must actually read any
        # relevant original player+GM text before certifying disclosure novelty.
        known = (hot.get("cast") or {}).get(actor, {}).get("knows", [])
        known = [value for value in known if isinstance(value, str)]
        actor_packets[actor] = {
            "played_disclosures": selected_list,
            "npc_knows_cues": known[-5:],
            "known_vs_disclosed": "An actor's knowledge does not mean the actor told the player.",
        }
        for item in selected_list:
            graph_nodes[item["turn"]] = {
                "id": item["turn"], "at": item["at"], "source": item["source"],
                "status": "played", "label": item["line"][:260],
            }
        for left, right in zip(selected_list, selected_list[1:]):
            key = (left["turn"], right["turn"], actor)
            graph_edges[key] = {"from": key[0], "to": key[1],
                                "type": "chronology_same_actor",
                                "actor": actor, "status": "source_order_only"}
    sensitive = bool(SENSITIVE.search(player_action))
    # Prioritize source review, not a large graph for every routine exchange.
    required = []
    if sensitive:
        for packet in actor_packets.values():
            for item in packet["played_disclosures"]:
                if item["disclosure_candidate"]:
                    required.append(item["turn"])
    required = list(dict.fromkeys(required))[:min(limit, 12)]
    graph = {"nodes": list(graph_nodes.values())[:limit * len(actors)],
             "edges": list(graph_edges.values())[:limit * len(actors)]}
    return {
        "format": FORMAT, "current_turn": (hot.get("cursor") or {}).get("id"),
        "latest_turn": turns[-1]["id"] if turns else None,
        "action": player_action, "actors": actor_packets,
        "graph": graph, "review_required": required,
        "review_reason": "past_claim_or_NPC_disclosure" if sensitive else "none",
        "source_rule": ("Review cited original played turns. A prior player disclosure, "
                        "NPC knowledge, and a new NPC confession are different facts."),
    }


def review_draft(packet, draft, *, reviewed=(), new_revelation=False,
                 novelty_explanation="", novelty_source=""):
    """Fail closed if a source-sensitive draft was not reviewed.

    This cannot semantically prove that paraphrases differ. Explicit human/GM
    acknowledgement of the *primary* turns is required before publishing.
    """
    if packet.get("format") != FORMAT or not isinstance(draft, str) or not draft.strip():
        raise ValueError("Invalid scene-gate packet or draft")
    checked = set(reviewed)
    required = set(packet.get("review_required", ()))
    missing = sorted(required - checked)
    errors = []
    if missing:
        errors.append("Unreviewed prior disclosures: " + ", ".join(missing))
    if new_revelation and not required and not checked:
        errors.append("A proposed new NPC revelation needs explicit prior-source review")
    overlap = []
    tokens = set(_words(draft))
    for actor_data in packet.get("actors", {}).values():
        for item in actor_data.get("played_disclosures", ()):
            prior = set(_words(item.get("line", "")))
            common = tokens & prior
            if len(common) >= 3 and len(common) >= max(3, int(0.25 * len(prior))):
                overlap.append({"turn": item["turn"], "shared_terms": sorted(common)[:10]})
    if (new_revelation or NOVELTY.search(draft)) and overlap:
        # In Bleach's scene gate, an acknowledged source is not sufficient:
        # the draft must distinguish a genuinely NEW fact from an already
        # played disclosure. This prevents the s07.030 Arakawa regression.
        if len(novelty_explanation.strip()) < 40 or not novelty_source.strip():
            errors.append(
                "Already-disclosed facts overlap a novelty claim; provide "
                "a source-bound contrast explaining the NEW information")
    if new_revelation and not checked:
        errors.append("Novelty claim without source review")
    return {"ready": not errors, "errors": errors,
            "overlap_candidates_not_proof": overlap,
            "reviewed": sorted(checked),
            "advice": "Check each earlier actor disclosure in its source, then re-evaluate the scene."}


def historical_candidates(root, state_dir, query, *, limit=5):
    """Leverage NPR's Bleach-inspired all-session graph/heuristic shards.

    A candidate is never automatically an NPC belief or established new fact.
    """
    if not query.strip():
        return []
    path = Path(state_dir) / heuristic_history.DESTINATION
    index = (heuristic_history.load(path) if path.is_file()
             else heuristic_history.build(root, state_dir))
    return heuristic_history.query(index, query, limit=limit)["matches"]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", required=True, type=Path)
    p.add_argument("--player-action", default="")
    p.add_argument("--actor", action="append", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--draft", type=Path)
    p.add_argument("--reviewed", action="append", default=[])
    p.add_argument("--new-revelation", action="store_true")
    p.add_argument("--novelty-explanation", default="")
    p.add_argument("--novelty-source", default="")
    args = p.parse_args(argv)
    result = prepare(args.state_dir, player_action=args.player_action,
                     actors=args.actor, limit=args.limit)
    if args.draft:
        result["review"] = review_draft(
            result, args.draft.read_text(encoding="utf-8"),
            reviewed=args.reviewed, new_revelation=args.new_revelation,
            novelty_explanation=args.novelty_explanation,
            novelty_source=args.novelty_source)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not args.draft or result["review"]["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
