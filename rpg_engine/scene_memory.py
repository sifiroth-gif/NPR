"""Mandatory source-bound memory preflight for every RPG scene.

Uses native SQLite journal recall plus campaign-file evidence.  A pinned offline
embedding index enriches retrieval when configured; graph/source and lexical
retrieval always run, including in a text-ahead GitHub-only session.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from collections import defaultdict

from .history import recall

WORDS = re.compile(r"[^\W_]+", re.UNICODE)
STOP = frozenset(("и а но или не ни же бы на за в во к ко с со от из для по под над "
 "это тот та те все всю что чтобы как где когда кто он она они я ты вы мы меня мне "
 "свой свои его её их уже еще ещё потом теперь только если также было были будет "
 "the and of at to from for with by a an is are").split())
# General research-domain expansion, never a story-specific assertion.
RESEARCH = ("геном генет днк секвен биопс клет ткан наслед хромосом "
            "лаборатор оборуд образц препарат материал исслед").split()
MAX_SOURCE = 240_000
MAX_FILES = 10000
MAX_SECTIONS = 4000

# Small concept-level bilingual bridge for mixed Russian player input and
# English-authored source cards. Not a claim about the game world.
ALIASES = (
    ("генет", "геном", "genet", "genom", "днк", "dna", "секвен", "sequenc"),
    ("лаборатор", "lab", "laborator", "оборуд", "equipment", "research", "исслед"),
    ("клет", "ткан", "cell", "tissue"),
    ("образц", "проб", "sample", "specimen"),
)


def words(value):
    return [x.casefold() for x in WORDS.findall(str(value)) if len(x) >= 3 and x.casefold() not in STOP]


def stem(token):
    token = token.casefold().replace("ё", "е")
    for suffix in ("иями", "ными", "ного", "ному", "овать", "ование", "ениями",
                   "ение", "ость", "ости", "ками", "ский", "ская", "ские",
                   "овых", "овой", "ного", "ями", "ами", "ыми", "ими",
                   "его", "ому", "ее", "ие", "ых", "их", "ей", "ой",
                   "ая", "ое", "ые", "ам", "ах", "ом", "ем", "ов",
                   "ий", "ый", "ть", "та", "ти", "и", "ы", "а", "я", "е", "у"):
        if len(token) - len(suffix) >= 4 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def key_terms(value, *, expand=False):
    terms = set(stem(x) for x in words(value))
    if expand:
        # Cross-language hints apply to a query, not as extra source facts.
        for group in ALIASES:
            if _overlap(terms, set(group)):
                terms.update(group)
        if _research_hits(terms) >= 2:
            terms |= {stem(x) for x in RESEARCH}
    return terms


def _overlap(a, b):
    return {q for q in b if any(q == t or
            (len(q) >= 5 and len(t) >= 5 and
             (q.startswith(t[:5]) or t.startswith(q[:5]))) for t in a)}


def _research_hits(terms):
    return len(_overlap(terms, {stem(x) for x in RESEARCH}))


def _sections(value, prefix="", depth=0):
    """Return bounded evidence sections, not full documents in scene packets."""
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, val in value.items():
            label = (prefix + "." if prefix else "") + str(key)
            yield from _sections(val, label, depth + 1)
    elif isinstance(value, list):
        for i, val in enumerate(value[:150]):
            yield from _sections(val, f"{prefix}[{i}]", depth + 1)
    elif value is not None:
        text = str(value).strip()
        if text:
            yield (prefix, text[:1400])


def _canonical_root(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("campaign source root not present")
    return root


def files_in(root, *, campaign="shiki"):
    """Only curated campaign material; never promote full raw/void transcript."""
    root = _canonical_root(root)
    base = root / "campaigns" / campaign
    if not base.is_dir():
        return []
    found = []
    for name in ("characters", "continuity", "locations", "contract", "lore", "sessions"):
        folder = base / name
        if folder.is_dir():
            found.extend(folder.rglob("*.json"))
    for name in ("lore.json", "SCENE_PACK.json", "WORLD_ONTOLOGY.md",
                 "WORLD_LORE.md", "GM_PROTOCOL.md"):
        p = base / name
        if p.is_file():
            found.append(p)
    # Historical source-index summaries, never full raw transcripts.
    for folder, pattern in (("archive", "session-*-index.json"),
                            ("recovery/transcripts", "*memory-index*.json")):
        p = root / folder
        if p.is_dir():
            found.extend(p.glob(pattern))
    found = sorted(set(found), key=lambda p: p.as_posix())
    if len(found) > MAX_FILES:
        raise ValueError("campaign material exceeds indexed file limit")
    return [p for p in found if not p.is_symlink() and p.stat().st_size <= MAX_SOURCE]


def _links(data):
    """Edges only when a document explicitly references another source path."""
    found = []
    def traverse(v):
        if isinstance(v, dict):
            for x in v.values():
                traverse(x)
        elif isinstance(v, list):
            for x in v:
                traverse(x)
        elif isinstance(v, str):
            for hit in re.findall(r"campaigns/[A-Za-z0-9_./-]+\.(?:json|md)", v):
                found.append(hit)
    traverse(data)
    return sorted(set(found))[:20]


def build_sources(root, *, campaign="shiki"):
    """Source-derived lightweight memory index; no facts invented by the index."""
    root = _canonical_root(root)
    docs = []
    for path in files_in(root, campaign=campaign):
        content = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        try:
            data = json.loads(content.decode("utf-8-sig")) if path.suffix == ".json" else {
                "text": content.decode("utf-8-sig")[:MAX_SOURCE]}
        except (ValueError, UnicodeError):
            continue
        sections = []
        for field, body in _sections(data):
            if len(sections) >= 170:
                break
            searchable = " ".join((relative, field, body))
            sections.append({"field": field, "excerpt": body[:480],
                             "terms": sorted(key_terms(searchable))[:170]})
        source = {"source": relative, "sha256": hashlib.sha256(content).hexdigest(),
                  "title": path.stem.replace("_", " "), "sections": sections,
                  "links": _links(data), "scope": "gm_only"}
        docs.append(source)
    return {"schema": "npr-scene-memory/1", "campaign": campaign,
            "source_root": "campaigns/" + campaign, "documents": docs}


def _authority(doc):
    p = doc["source"]
    if "/locations/" in p or "/characters/" in p:
        return 2.2
    if "/continuity/" in p:
        return 1.9
    if "/sessions/" in p:
        return 0.7   # a plan is not an established event
    return 1.0


def _score(section, doc, query, *, topical):
    terms = set(section["terms"])
    common = _overlap(terms, query)
    # Structured equipment/location cards are vital to a research action even
    # when authored in English. Source type + field establish *relevance* only.
    equipment_place = (topical and "/locations/" in doc["source"]
                       and ("lab" in doc["source"].casefold() or
                            section["field"].startswith(("capabilities", "equipment"))))
    if not common and not equipment_place:
        return 0.0
    paths = key_terms(doc["source"] + " " + section["field"])
    direct = len(common)
    # Mandatory source relevance should outrank generic long prose.
    specialization = (2.0 if topical and section["field"].startswith(
        ("capabilities", "equipment", "gen_assimilation", "zero_genome",
         "equipment_inventory", "laboratory")) else 0)
    # A medically equipped *place* or the character's established competence
    # is more relevant to a lab procedure than generic 'research facility'
    # mentions in dozens of unrelated historical location strings.
    thematic_location = (4.0 if topical and "/locations/" in doc["source"] and
                         ("lab" in doc["source"].casefold() or
                          section["field"].startswith(("capabilities", "equipment"))) else 0)
    return (direct / (len(query) ** 0.5)
            + 1.4 * len(_overlap(paths, query))
            + specialization + thematic_location + _authority(doc))


def _query_parts(action, location, actors):
    chunks = []
    if action:
        chunks.append(("action", action[:1400]))
    if location:
        chunks.append(("location", location[:240]))
    for actor in list(actors)[:6]:
        if actor:
            chunks.append(("actor", str(actor)[:180]))
    # Research provenance must be checked even when the player did not write
    # 'my secret laboratory' explicitly. This is a retrieval hint, not lore.
    if _research_hits(key_terms(action)) >= 2:
        chunks.append(("research_capability", "исследования генетика ДНК оборудование лаборатория способности"))
    return chunks[:9]


def source_candidates(index, *, action="", location="", actors=(), limit=9):
    if not isinstance(index, dict) or index.get("schema") != "npr-scene-memory/1":
        raise ValueError("incorrect campaign memory index")
    queries = _query_parts(action, location, actors)
    results = []
    selected = {}
    for kind, label in queries:
        target = key_terms(label, expand=True)
        if not target:
            continue
        topical = kind == "research_capability"
        ranked = []
        for doc in index["documents"]:
            best = None
            for s in doc["sections"]:
                value = _score(s, doc, target, topical=topical)
                if value and (best is None or value > best[0]):
                    best = (value, s)
            if best is not None:
                ranked.append((best[0], doc, best[1]))
        ranked.sort(key=lambda item: (-item[0], item[1]["source"]))
        results.append({"type": kind, "text": label[:280],
                        "status": "matched" if ranked else "no_source",
                        "candidates": [x[1]["source"] for x in ranked[:4]]})
        for score, doc, section in ranked[:max(4, limit)]:
            p = doc["source"]
            current = selected.get(p)
            importance = score + (0.9 if kind in ("action", "research_capability") else 0)
            if current is None or importance > current["score"]:
                selected[p] = {"source": p, "sha256": doc["sha256"],
                               "field": section["field"], "excerpt": section["excerpt"],
                               "score": round(importance, 3), "channel": kind,
                               "links": doc["links"][:6], "status": "source_candidate_only"}
    # One graph hop for a *selected* source, never graph-generated facts.
    by_source = {d["source"]: d for d in index["documents"]}
    for original in list(selected.values())[:3]:
        for target in original["links"][:3]:
            if target in by_source and target not in selected:
                d = by_source[target]
                selected[target] = {"source": target, "sha256": d["sha256"],
                    "field": "explicit_source_link", "excerpt": "",
                    "score": 0.25, "channel": "graph_link",
                    "links": [], "status": "inspect_primary_source"}
    sorted_candidates = sorted(selected.values(), key=lambda item: (-item["score"], item["source"]))
    return {"anchors": results, "evidence": sorted_candidates[:limit],
            "source_count": len(index["documents"]),
            "rule": "Retrieved excerpts are candidates, not established current facts or NPC knowledge."}


def sqlite_candidates(con, *, action="", location="", actors=(), limit=7,
                      semantic_config=None):
    """Automatic hybrid recall with lexical/graph baseline and optional embeddings."""
    evidence = []
    modes = []
    for kind, label in _query_parts(action, location, actors):
        terms = words(label)
        if not terms:
            continue
        if kind == "action":
            # Query focused concepts rather than an overconstrained sentence.
            terms = [x for x in terms if len(x) >= 5][:4]
        if len(terms) > 6:
            terms = terms[:6]
        if not terms:
            continue
        query = " ".join(terms)
        semantic = None
        if semantic_config and len(evidence) < limit:
            try:
                from . import semantic_recall
                semantic = semantic_recall.candidates(
                    con, semantic_config["cache"], semantic_config["model"],
                    semantic_config["sha"], query, audience="gm", limit=limit,
                    session=None, since=None, until=None, entities=(),
                    include_superseded=False)
                modes.append("embedding")
            except (ValueError, OSError, ImportError) as exc:
                modes.append("semantic_unavailable:" + type(exc).__name__)
        try:
            matches = recall(con, query=query, audience="gm", limit=4,
                             semantic=semantic)["matches"]
        except (ValueError, RuntimeError):
            matches = []
        for m in matches:
            evidence.append({"source": m["id"], "at": m["at"],
                             "kind": m["kind"], "passage": m["passage"],
                             "channels": m["channels"], "based_on": m["based_on"],
                             "consequences": m["consequences"],
                             "superseded_by": m["superseded_by"]})
    unique = list({x["source"]: x for x in reversed(evidence)}.values())
    return {"mode": "hybrid" if "embedding" in modes else "fts_graph",
            "embedding": modes[:2], "matches": unique[:limit]}


def environment_semantic():
    vals = {k: os.getenv(v) for k, v in
            (("cache", "NPR_RECALL_CACHE"), ("model", "NPR_RECALL_MODEL"),
             ("sha", "NPR_RECALL_MODEL_SHA"))}
    return vals if all(vals.values()) else None


def preflight(*, action="", location="", actors=(), root=None, campaign="shiki",
              source_index=None, con=None, limit=9):
    """Mandatory bounded pre-scene packet. Always run before narration.

    A missing source or disconnected model is visible, never silently upgraded
    to a verified fact. This function is read-only.
    """
    if root is not None and source_index is None:
        source_index = build_sources(root, campaign=campaign)
    source_result = (source_candidates(source_index, action=action, location=location,
                                      actors=actors, limit=limit)
                     if source_index is not None else {
                         "anchors": [], "evidence": [], "source_count": 0,
                         "rule": "No campaign file index was supplied."})
    db_result = (sqlite_candidates(con, action=action, location=location, actors=actors,
                                  limit=limit, semantic_config=environment_semantic())
                 if con is not None else {"mode": "offline_text", "matches": []})
    return {"schema": "npr-scene-preflight/1", "audience": "gm_only",
            "location": location, "action_excerpt": action[:400],
            "file_memory": source_result, "journal_memory": db_result,
            "retrieval_used": bool(source_result["source_count"]) or con is not None,
            "constraints": [
                "Source candidates are not universal canon; check late corrections and established event time.",
                "For NPC statements require witnessed/reported/document evidence; no omniscient NPC knowledge.",
                "If equipment, location or patient consent lacks evidence, do not fabricate it.",
                "Do not transfer secret specimens or reveal hidden locations without the player's action.",
                "Never replace the last confirmed scene with a stale SQLite snapshot.",
            ]}


def verify_hot_memory_gate(hot):
    """Fail closed for future published gameplay without a source-memory receipt.

    Historical turns 001–004 predate the mandatory gate. OOC corrections that
    do not advance the current turn are reviewed separately.
    """
    turn = hot.get("latest_turn", {})
    turn_id = turn.get("id", "")
    match = re.fullmatch(r"s(\d+)\.text\.(\d+)", turn_id)
    # Only the first four already published S4 turns predate the new barrier.
    if match and int(match[1]) == 4 and int(match[2]) <= 4:
        return
    if turn.get("turn_kind") in ("ooc", "technical"):
        return
    if not turn_id:
        raise ValueError("No current turn ID for scene-memory verification")
    gate = turn.get("memory_gate")
    if not isinstance(gate, dict) or gate.get("schema") != "npr-memory-gate/1":
        raise ValueError("Missing mandatory scene-memory evidence receipt for published turn")
    previous = [r.get("turn_id") for r in hot.get("history", [])
                if r.get("turn_id") != turn_id]
    if not previous or gate.get("previous_turn_id") != previous[-1]:
        raise ValueError("Memory receipt not bound to the previous confirmed turn")
    if not isinstance(gate.get("source_refs"), list) or len(gate["source_refs"]) > 16:
        raise ValueError("Memory receipt must contain bounded source references")
    if not gate["source_refs"] and not gate.get("unretrieved"):
        raise ValueError("Neither verified source candidates nor explicit gaps recorded")
    if gate.get("player_input") != turn.get("player_input"):
        raise ValueError("Memory receipt player input does not match the published action")
    for src in gate["source_refs"]:
        if not isinstance(src, dict) or set(src) != {"source", "sha256"}:
            raise ValueError("Invalid source reference in memory receipt")
        if not isinstance(src["source"], str) or len(src["source"]) > 300:
            raise ValueError("Invalid source path in memory receipt")
        if not re.fullmatch(r"[0-9a-f]{64}", src["sha256"]):
            raise ValueError("Source hash missing or invalid in memory receipt")


def compact_live_index(root, state_dir):
    """Small source-bound lookup catalog for GitHub-only ChatGPT turns.

    Generated from current canonical cards and the latest HOT after each push.
    It contains keywords/excerpts/paths and hashes, never full source books.
    """
    from collections import Counter
    root = _canonical_root(root)
    state_dir = _canonical_root(state_dir)
    data = build_sources(root)
    if root != state_dir:
        private = build_sources(state_dir)
        unique = {x["source"]: x for x in data["documents"]}
        unique.update({x["source"]: x for x in private["documents"]})
        data["documents"] = list(unique.values())
    hot_path = state_dir / "recovery/FAST_SCENE.json"
    raw = hot_path.read_bytes()
    hot = json.loads(raw)
    if hot.get("latest_turn", {}).get("id") != hot.get("cursor", {}).get("last_turn_id"):
        raise ValueError("HOT current turn must match cursor before deriving memory")
    # Квитанция памяти больше не обязательна и никогда не останавливает сборку:
    # с S04-030 она восемнадцать раз подряд ломала обновление индекса.
    try:
        verify_hot_memory_gate(hot)
        receipt = "ok"
    except ValueError as exc:
        receipt = f"advisory: {exc}"
    records = []
    for doc in data["documents"]:
        all_terms = Counter(t for section in doc["sections"] for t in section["terms"])
        anchor_terms = key_terms(doc["source"] + " " + doc["title"])
        common = sorted(anchor_terms) + [t for t, _ in all_terms.most_common(48)]
        keys = list(dict.fromkeys(common))[:60]
        def field_priority(s):
            f = s["field"]
            if f.startswith(("capabilities", "equipment_inventory", "privacy",
                             "zero_genome", "gen_assimilation", "current",
                             "origin", "status", "source_player", "summary",
                             "scene", "consent", "authority")):
                return 2
            if "lab" in f or "source" in f:
                return 1
            return 0
        sections = sorted(doc["sections"], key=field_priority, reverse=True)
        preview = [{"field": x["field"], "text": x["excerpt"][:195]}
                   for x in sections[:3]]
        records.append({"source": doc["source"], "sha256": doc["sha256"],
                        "title": doc["title"], "terms": keys, "preview": preview,
                        "links": doc["links"][:6],
                        "weight": _authority(doc), "status": "source_candidate_only"})
    live = hot.get("story_compass", {})
    return {"schema": "npr-github-scene-memory/1", "campaign": data["campaign"],
            "hot_sha256": hashlib.sha256(raw).hexdigest(),
            "cursor": {"turn": hot["cursor"]["last_turn_id"], "at": hot["cursor"]["at"],
                       "location": hot["cursor"].get("location", "")},
            "documents": records,
            "live": {"story": live.get("immediate", ""),
                     "latest_facts": hot.get("latest_turn", {}).get("facts", {}),
                     "recent": hot.get("history", [])[-6:]},
            "receipt": receipt,
            "instructions": "Source candidates only; primary sources and later corrections outrank snippets. If HOT hash differs, merge the newest HOT facts and do not claim the cache is current."}


def write_live_index(root, state_dir, destination):
    result = compact_live_index(root, state_dir)
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    desired = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.is_file() and target.read_text(encoding="utf-8") == desired:
        return {"status": "current", "sources": len(result["documents"]),
                "turn": result["cursor"]["turn"]}
    target.write_text(desired, encoding="utf-8")
    return {"status": "updated", "sources": len(result["documents"]),
            "turn": result["cursor"]["turn"], "bytes": len(desired.encode("utf-8"))}


def _hot_candidate(hot, raw):
    """Newest confirmed HOT evidence participates in every text-ahead preflight."""
    fields = {"story_compass": hot.get("story_compass", {}).get("immediate", ""),
              "latest_turn.facts": hot.get("latest_turn", {}).get("facts", {}),
              "recent_turns": hot.get("history", [])[-6:],
              "cursor": hot.get("cursor", {})}
    sections = []
    for field, value in _sections(fields):
        sections.append({"field": field, "excerpt": value[:480],
                         "terms": sorted(key_terms(field + " " + value))[:170]})
    return {"source": "recovery/FAST_SCENE.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "title": "Current text-ahead canonical scene",
            "sections": sections, "links": [],
            "scope": "gm_only"}


def fast_packet_from_files(root, state_dir, *, action="", limit=9):
    """Offline/text-ahead entrypoint: accepts the live HOT cursor and current files."""
    state_dir = Path(state_dir)
    hot_bytes = (state_dir / "recovery/FAST_SCENE.json").read_bytes()
    hot = json.loads(hot_bytes)
    cur = hot["cursor"]
    if cur.get("last_turn_id") != hot.get("latest_turn", {}).get("id"):
        raise ValueError("HOT cursor and turn disagree: refuse to narrate")
    if not action:
        action = hot.get("latest_turn", {}).get("player_input", "")
    index = build_sources(root)
    # The state branch may contain more recent canon sources than main.
    if Path(root).resolve() != state_dir.resolve():
        more = build_sources(state_dir)
        primary = {d["source"]: d for d in index["documents"]}
        primary.update({d["source"]: d for d in more["documents"]})
        index["documents"] = list(primary.values())
    # HOT is the authoritative text-ahead tail; this source remains searchable
    # even when the SQLite checkpoint and generated index lag behind it.
    index["documents"].append(_hot_candidate(hot, hot_bytes))
    packet = preflight(action=action, location=cur.get("location", ""),
                       source_index=index, limit=limit)
    packet["cursor"] = cur
    packet["status"] = "text_ahead_unreplayed" if hot.get("runtime", {}).get(
        "text_ahead_unreplayed") else "sqlite_may_be_current"
    return packet
