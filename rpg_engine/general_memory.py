"""Deterministic general campaign-memory projection and bounded retrieval.

Architectural adaptations: Hindsight-style source/temporal/graph search lanes;
memU-style ingestion after committed turns; OpenViking-style L0/L1/L2 output.
No embedding service, probabilistic inference or external persistence is used.
The projection is PRIVATE/GM-only and never becomes independent canon.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

from . import entity_memory
from .heuristic_history import stem

FORMAT = "npr-general-memory/1"
PATH = "recovery/GENERAL_MEMORY_GM_ONLY.json"
MAX_BRIEF_ENTITIES = 7
MAX_BRIEF_BYTES = 6700
MENTIONS_EARLIEST = 1
MENTIONS_LATEST = 3
WORD = re.compile(r"[\w-]{3,}", re.UNICODE)
STRUCTURED_ID = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _read(path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(value):
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _tokens(value):
    return {stem(x) for x in WORD.findall(_norm(value))}


def _short(value, length=220):
    return " ".join(str(value or "").split())[:length]


def _match(text, alias):
    alias = _norm(alias)
    return bool(alias and re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", _norm(text)))


def _add(entities, key, kind, label, *, aliases=(), source="", projection=None):
    card = entities.setdefault(key, {
        "id": key, "kind": kind, "label": label, "aliases": [],
        "facts": [], "projections": [], "sources": [], "mentions": [],
        "mention_count": 0,
    })
    if card["kind"] == "unresolved" and kind != "unresolved":
        card["kind"] = kind
    if label and card["label"] == key:
        card["label"] = label
    for name in (key.replace("_", " "), label, *aliases):
        if (isinstance(name, str) and len(name) > 2 and
                name not in card["aliases"] and len(card["aliases"]) < 24):
            card["aliases"].append(name)
    if source and source not in card["sources"] and len(card["sources"]) < 24:
        card["sources"].append(source)
    if projection and isinstance(projection, dict) and projection:
        if projection not in card["projections"]:
            card["projections"].append(projection)
        if len(card["projections"]) > 3:
            card["projections"] = card["projections"][:1] + card["projections"][-2:]
    return card


def _source_rows(state_dir):
    root = Path(state_dir)
    archive = root / "archive"
    if not archive.is_dir():
        return
    for path in sorted(archive.glob("session-*-turns.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict) and row.get("id") and row.get("kind") != "ooc":
                    yield path.relative_to(root).as_posix(), row
    # Early campaigns have canonical Markdown transcripts, not JSONL turns.
    # Route them by precise line window, avoiding duplicate indexing when a
    # playable JSONL replay for that session already exists.
    present_sessions = {m.group(1) for p in archive.glob("session-*-turns.jsonl")
                        if (m := re.fullmatch(r"session-(\d{3})-turns\.jsonl", p.name))}
    texts = defaultdict(list)
    for p in archive.glob("session-*.md"):
        m = re.fullmatch(r"session-(\d{3})(?:-(source|reviewed))?\.md", p.name)
        if m and m.group(1) not in present_sessions:
            texts[m.group(1)].append((2 if m.group(2) == "source" else
                                      1 if m.group(2) == "reviewed" else 0, p))
    for number, paths in sorted(texts.items()):
        path = max(paths, key=lambda pair: (pair[0], pair[1].name))[1]
        lines = path.read_text(encoding="utf-8").splitlines()
        for i in range(0, len(lines), 30):
            body = "\n".join(lines[i:i + 35])
            if not body.strip():
                continue
            source = f"{path.relative_to(root).as_posix()}:L{i + 1}-{min(len(lines), i + 35)}"
            yield source, {"id": f"archive.s{number}.L{i + 1:06}", "gm": body, "at": "",
                           "player": "", "line": ""}
    # The submitted turn may not yet have been folded by asynchronous GitHub CI.
    recent = _read(root / "recovery/TURN.json", {})
    if isinstance(recent, dict) and recent.get("id") and recent.get("kind") != "ooc":
        yield "recovery/TURN.json", recent


def _remember(card, ref):
    card["mention_count"] += 1
    items = card["mentions"]
    if items and items[-1]["turn"] == ref["turn"]:
        return
    items.append(ref)
    if len(items) > MENTIONS_EARLIEST + MENTIONS_LATEST:
        del items[MENTIONS_EARLIEST]


def build(state_dir, hot, ledger=None):
    """Build from existing canonical projections and played source evidence.

    A source mentioning an entity produces a candidate pointer, never a fact.
    """
    root = Path(state_dir)
    ledger = ledger if ledger is not None else entity_memory.load(root)
    entities, links = {}, []

    # Explicit facts are the only entries represented as verified canon.
    for key, saved in ledger.get("entities", {}).items():
        card = _add(entities, key, saved.get("kind", "entity"), saved.get("label", key),
                    aliases=saved.get("aliases", []))
        card["facts"] = [dict(f) for f in saved.get("facts", [])]
        card["sources"].extend(f["source"] for f in card["facts"] if f.get("source"))
        card["sources"] = list(dict.fromkeys(card["sources"]))

    # All historical NPC cards survive HOT trimming; don't restrict to cast of this scene.
    cast_dir = root / "recovery/cast"
    cast_paths = list(sorted(cast_dir.glob("*.json"))) if cast_dir.is_dir() else []
    for path in cast_paths:
        saved = _read(path, {})
        if not isinstance(saved, dict):
            continue
        key = saved.get("key") or path.stem
        label = saved.get("name") or key
        src = path.relative_to(root).as_posix()
        projection = {k: _short(saved[k], 260) for k in (
            "role", "wants", "fears", "stance", "body", "state", "voice", "seen"
        ) if isinstance(saved.get(k), str) and saved[k]}
        _add(entities, key, "npc", label, source=src, projection=projection)

    for key, saved in (hot.get("cast") or {}).items():
        if not isinstance(saved, dict):
            continue
        _add(entities, key, "npc", saved.get("name") or key,
             source="recovery/HOT.json",
             projection={k: _short(saved[k], 260) for k in (
                 "role", "wants", "fears", "stance", "state"
             ) if isinstance(saved.get(k), str) and saved[k]})

    for key, saved in (hot.get("things") or {}).items():
        if not isinstance(saved, dict):
            continue
        source = saved.get("turn") or saved.get("since") or "recovery/HOT.json"
        _add(entities, key, "item", saved.get("name") or key.replace("_", " "),
             source=source, projection={k: _short(v, 100) for k, v in saved.items()
                                        if k in ("holder", "place", "since", "turn", "status")})
        if isinstance(saved.get("holder"), str) and saved["holder"]:
            links.append({"from": key, "to": saved["holder"],
                          "type": "held_by", "source": source, "status": "projected"})

    for key, saved in (hot.get("threads") or {}).items():
        if not isinstance(saved, dict):
            continue
        source = saved.get("turn") or "recovery/HOT.json"
        _add(entities, "thread:" + key, "thread", saved.get("name") or key.replace("_", " "),
             aliases=(key,), source=source,
             projection={"state": _short(saved.get("state"), 330)})

    for key, saved in ((hot.get("offscreen") or {}).get("clocks") or {}).items():
        if not isinstance(saved, dict):
            continue
        source = saved.get("source") or saved.get("turn") or "recovery/GM_ONLY.json"
        _add(entities, "clock:" + key, "clock", saved.get("what") or key,
             aliases=(key,), source=source,
             projection={k: _short(saved.get(k), 210) for k in ("due", "what", "owner")
                         if saved.get(k)})
        owner = saved.get("owner")
        if owner in entities or owner in (hot.get("cast") or {}):
            links.append({"from": "clock:" + key, "to": owner, "type": "owned_by",
                          "source": source, "status": "projected"})

    # Locations have a stable id from their literal place string. The original
    # place is preserved exactly; fragments are retrieval aliases only.
    place = (hot.get("cursor") or {}).get("place") or ""
    if place:
        key = "place:" + sha256(place.encode("utf-8")).hexdigest()[:16]
        aliases = [p.strip() for p in place.split(",") if len(p.strip()) >= 8]
        _add(entities, key, "place", place, aliases=aliases,
             source=(hot.get("cursor") or {}).get("id") or "recovery/HOT.json")

    # Unknown identifier-like names in scene are provisional *pointers*, not
    # newly invented persons or canonical facts.
    scene = hot.get("scene") or {}
    active_text = " ".join(str(scene.get(field) or "") for field in
                           ("situation", "positions", "pressure"))
    for name in sorted(set(STRUCTURED_ID.findall(_norm(active_text)))):
        if name not in entities and len(name) < 85:
            _add(entities, name, "unresolved", name,
                 source=(hot.get("cursor") or {}).get("id") or "recovery/HOT.json")
    for name in scene.get("present", []):
        if isinstance(name, str) and name not in entities and name not in ("player", "shiki"):
            _add(entities, name, "unresolved", name,
                 source=(hot.get("cursor") or {}).get("id") or "recovery/HOT.json")

    # Reconstruct the entire roster of *historically referenced* entities from
    # explicit turn structure. Removing a thing or NPC from HOT must never erase
    # its identity and exact source from long-term retrieval.
    sources = list(_source_rows(root))
    for path, row in sources:
        if row["id"].startswith("archive.s"):
            continue  # Markdown is evidence; never authoritative structure.
        at, turn_id = row.get("at", ""), row["id"]
        delta = row.get("d") or {}
        for key, patch in (delta.get("cast") or {}).items():
            if not isinstance(key, str):
                continue
            _add(entities, key, "npc", (patch or {}).get("name") or key if isinstance(patch, dict) else key,
                 source=turn_id)
        for key, patch in (delta.get("things") or {}).items():
            if not isinstance(key, str):
                continue
            new = patch.get("now") if isinstance(patch, dict) else None
            details = new if isinstance(new, dict) else patch if isinstance(patch, dict) else {}
            _add(entities, key, "item", details.get("name") or key.replace("_", " "),
                 source=turn_id)
        for key in (delta.get("threads") or {}):
            if isinstance(key, str):
                _add(entities, "thread:" + key, "thread", key.replace("_", " "),
                     aliases=(key,), source=turn_id)
        for key in (delta.get("clocks") or {}):
            if isinstance(key, str):
                _add(entities, "clock:" + key, "clock", key.replace("_", " "),
                     aliases=(key,), source=turn_id)
        for name in ((delta.get("scene") or {}).get("present") or []):
            if isinstance(name, str) and name not in ("shiki", "player") and name not in entities:
                _add(entities, name, "unresolved", name, source=turn_id)
        # Canon amendments and explicit world events are memory too.
        for i, text in enumerate(delta.get("canon+") or []):
            if isinstance(text, str) and text:
                _add(entities, f"canon:{turn_id}:{i}", "canon", _short(text, 180),
                     source=turn_id)
        for i, event in enumerate(delta.get("pact_events") or []):
            if isinstance(event, dict):
                eid = event.get("id") or f"{turn_id}.{i}"
                _add(entities, "event:" + eid, "event", event.get("type") or eid,
                     source=turn_id,
                     projection={"at": at, "kind": event.get("type", "")})
        old_place = row.get("place")
        if isinstance(old_place, str) and old_place:
            key = "place:" + sha256(old_place.encode("utf-8")).hexdigest()[:16]
            aliases = [p.strip() for p in old_place.split(",") if len(p.strip()) >= 8]
            _add(entities, key, "place", old_place, aliases=aliases, source=turn_id)

    # Canonical relationship facts become source-stamped graph edges.
    # Mere mentions or co-occurrence never imply a relation.
    for key, card in entities.items():
        for fact in card.get("facts", []):
            value = fact.get("value")
            if (fact.get("field") in ("relationship", "link") and
                    isinstance(value, dict) and
                    value.get("target") in entities and value.get("type")):
                links.append({"from": key, "to": value["target"],
                              "type": value["type"], "source": fact["source"],
                              "status": "verified"})

    # Explicit source mentions let the GM expand from short memory to the
    # earliest and newest *verbatim* played evidence, not a lossy summary.
    # All aliases are lexical candidate hints: no asserted causality or knowledge.
    pattern = {}
    for key, card in entities.items():
        if card["kind"] in ("canon", "event", "clock", "thread", "place"):
            continue  # explicit source pointers already exist; avoid costly scans
        forms = list(dict.fromkeys(_norm(a) for a in card["aliases"] if len(_norm(a)) >= 4))
        if forms:
            pattern[key] = re.compile("|".join(
                r"(?<!\w)" + re.escape(a) + r"(?!\w)"
                for a in sorted(forms, key=len, reverse=True)[:18]))

    processed = set()
    for source, row in sources:
        if row["id"] in processed:  # folded and current TURN may overlap
            continue
        processed.add(row["id"])
        body = " ".join(str(row.get(k) or "") for k in ("player", "gm", "line"))
        if not body:
            continue
        clean = _norm(body)
        for key, regex in pattern.items():
            m = regex.search(clean)
            if m is None:
                continue
            snippet = _short(body[max(0, m.start() - 100):m.start() + 180], 260)
            _remember(entities[key], {
                "turn": row["id"], "source": source, "at": row.get("at", ""),
                "excerpt": snippet, "status": "source_candidate_only",
            })

    return {
        "schema": FORMAT,
        "cursor": (hot.get("cursor") or {}).get("id"),
        "entities": entities, "links": links,
        "conflicts": ledger.get("conflicts", []),
        "source_turns_indexed": len(processed),
        "contract": "Verified facts only from source-bound ledger; "
                    "cards and source mentions remain projections/candidates.",
    }


def save(state_dir, hot, ledger=None):
    value = build(state_dir, hot, ledger)
    target = Path(state_dir) / PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    contents = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if not target.is_file() or target.read_text(encoding="utf-8") != contents:
        target.write_text(contents, encoding="utf-8")
    return value


def load(state_dir, *, hot=None, ledger=None):
    result = _read(Path(state_dir) / PATH)
    if result and result.get("schema") == FORMAT:
        if hot is None or result.get("cursor") == (hot.get("cursor") or {}).get("id"):
            return result
    if hot is None:
        return None
    return build(state_dir, hot, ledger)


def _fact_valid(fact, at):
    if fact.get("superseded_by"):
        return False
    if at and fact.get("valid_from") and at < fact["valid_from"]:
        return False
    if at and fact.get("valid_until") and at >= fact["valid_until"]:
        return False
    return True


def retrieve(index, *, action="", scene=None, cursor=None, limit=MAX_BRIEF_ENTITIES,
             level=0, include_neighbors=True):
    """Four transparent ranking signals: exact identity, lexical, time, links.

    Exact identity always outranks weaker signals; no semantic search channel
    can turn candidate evidence into an authoritative fact.
    """
    if index is None:
        return {"schema": FORMAT, "status": "not_indexed", "activated": []}
    cursor = cursor or {}
    scene = scene or {}
    scene_text = " ".join(str(scene.get(k) or "") for k in
                          ("situation", "positions", "pressure", "present"))
    scene_text += " " + str(cursor.get("place") or "")
    question_terms = _tokens(action)
    scored = {}
    channels = {}
    for key, card in index.get("entities", {}).items():
        forms = [key, card.get("label", "")] + card.get("aliases", [])
        by_action = bool(action and any(_match(action, a) for a in forms if len(a) >= 4))
        by_scene = any(_match(scene_text, a) for a in forms if len(a) >= 4)
        profile_text = " ".join(str(p) for p in card.get("projections", []))
        facts_text = " ".join(str(f.get("value")) for f in card.get("facts", []))
        overlap = len(question_terms & _tokens(card.get("label", "") + " " + facts_text + " " + profile_text))
        score = (100 if by_action else 0) + (70 if by_scene else 0) + min(overlap, 6) * 2
        if score == 0:
            continue
        scored[key] = score
        channels[key] = (["exact_action"] if by_action else []) + (["scene_anchor"] if by_scene else []) + (
            ["lexical_candidate"] if overlap else [])

    if include_neighbors:
        originals = set(scored)
        for edge in index.get("links", []):
            left, right = edge.get("from"), edge.get("to")
            if left in originals and right in index["entities"] and right not in scored:
                scored[right] = 6
                channels[right] = ["explicit_edge_candidate"]
            elif right in originals and left in index["entities"] and left not in scored:
                scored[left] = 6
                channels[left] = ["explicit_edge_candidate"]

    selected = sorted(scored, key=lambda x: (-scored[x], x))
    chosen = []
    for key in selected[:max(1, limit)]:
        card = index["entities"][key]
        evidence = [f for f in card.get("facts", []) if _fact_valid(f, cursor.get("at", ""))]
        priority = {"identity", "sex", "age_at_discovery", "birth_date",
                    "species", "origin", "relationship", "discovery"}
        evidence.sort(key=lambda f: (f["field"] not in priority, f["field"]))
        item = {"id": key, "kind": card["kind"], "label": card["label"],
                "channels": channels[key],
                "facts": evidence[:8],
                "status": "verified_facts" if evidence else "no_verified_facts"}
        if level >= 1:
            item["projections"] = card.get("projections", [])[:2]
            item["sources"] = card.get("sources", [])[:12]
            item["mention_count"] = card.get("mention_count", 0)
        if level >= 2:
            item["source_candidates"] = card.get("mentions", [])[:MENTIONS_EARLIEST + MENTIONS_LATEST]
            item["links"] = [edge for edge in index.get("links", [])
                             if key in (edge.get("from"), edge.get("to"))][:8]
        chosen.append(item)
    result = {"schema": FORMAT, "level": level, "activated": chosen,
              "total_matched": len(selected),
              "omitted": max(0, len(selected) - len(chosen)),
              "conflicts": index.get("conflicts", []),
              "rule": "L0 identity/facts; L1 projections/sources; L2 played excerpts. "
                      "Neither co-occurrence nor projections establish canon."}
    return result


def compact(packet, max_bytes=MAX_BRIEF_BYTES):
    """Bound hot context by dropping optional evidence, never identity facts."""
    value = json.loads(json.dumps(packet, ensure_ascii=False))
    def size():
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    for item in reversed(value.get("activated", [])):
        if size() <= max_bytes:
            break
        item.pop("source_candidates", None)
        item.pop("projections", None)
        item.pop("links", None)
    while size() > max_bytes and len(value.get("activated", [])) > 1:
        value["activated"].pop()
        value["omitted"] += 1
    if size() > max_bytes:
        value["over_budget"] = True
    return value
