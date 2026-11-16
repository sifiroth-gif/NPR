"""Fast, source-bound historical recall over all played NPR sessions.

Bleach-inspired generic heuristics: inflection/stems, document frequency,
bounded fuzzy matching, date-aware scoring and explicit source neighbors.
This index is *derived*, never canon, never NPC knowledge, and is rebuilt by
the existing hot-fold workflow. No model download or network dependency.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
from hashlib import sha256
import json
import math
from pathlib import Path
import re

SCHEMA = "npr-heuristic-history/1"
DESTINATION = "archive/heuristic-recall.json"
WORD = re.compile(r"[^\W_]+", re.UNICODE)
STOP = frozenset(("а и но или либо да не ни же бы как что чтобы кто какой где когда "
    "свой свои ваше уже еще ещё нам вам нас вас они оно она он мы вы ты я это тот "
    "мне меня при по на к ко с со из за для без от до об под над между также "
    "was were have has the and for with from this that of an in on at to").split())
SUFFIXES = tuple(sorted(("иями", "ениями", "ирование", "ирования", "ированный",
    "ованные", "ование", "ством", "ность", "ности", "ского", "скому",
    "скому", "тель", "либо", "ющий", "ующая", "ового", "овому",
    "ими", "ыми", "ами", "ями", "ному", "ными", "ому", "его",
    "ией", "ией", "ого", "ская", "ские", "ский", "ание", "ение",
    "сть", "ией", "ах", "ях", "ов", "ев", "ом", "ем", "им",
    "ый", "ий", "ой", "ая", "ое", "ые", "ие", "ых", "их",
    "ам", "ям", "ть", "ет", "ут", "ют", "ов", "ы", "и",
    "а", "я", "у", "ю", "е"), key=len, reverse=True))
LATIN = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh",
    "з":"z","и":"i","й":"y","к":"k","л":"l","м":"m","н":"n",
    "о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
    "х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ъ":"",
    "ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})
MAX_RECORDS = 6000


def tokens(text):
    return [w.casefold().replace("ё", "е") for w in WORD.findall(str(text))
            if len(w) >= 3 and w.casefold() not in STOP]


def stem(word):
    word = word.casefold().replace("ё", "е")
    if len(word) < 5:
        return word
    if re.search("[а-я]", word):
        for ending in SUFFIXES:
            if word.endswith(ending) and len(word) - len(ending) >= 4:
                return word[:-len(ending)]
    else:
        for ending in ("ingly", "ation", "ments", "ment", "ing", "ers", "ed", "es", "s"):
            if word.endswith(ending) and len(word) - len(ending) >= 5:
                return word[:-len(ending)]
    return word


def _lexicon(text):
    # Index every distinct rare term before pruning; include temporal anchors.
    words = [stem(w) for w in tokens(text)]
    freq = Counter(words)
    ordered = sorted(freq, key=lambda w: (-min(freq[w], 5), -len(w), w))
    chosen = list(dict.fromkeys(ordered[:100] +
        [w for w in words if re.fullmatch(r"(?:12|0\d)\d{2}|\d{1,2}", w)][:30] +
        [w for w in words if len(w) >= 9][:35]))
    return " ".join(chosen[:155])


def _digest(path):
    return sha256(path.read_bytes()).hexdigest()


def _at(text):
    m = re.search(r"000\d-\d\d-\d\dT\d\d:\d\d(?::\d\d)?", str(text))
    return m.group(0) if m else ""


def _entry(*, key, source, body, at="", headline="", kind="played",
           line_start=0, line_end=0, links=(), authority=1.0, digest=""):
    content = " ".join((str(key), str(at), str(headline), str(body)))
    # Terms are based on whole evidence, not just the short preview.
    lex = _lexicon(content)
    intro = " ".join(str(headline).split())[:300]
    paragraphs = [p.strip() for p in str(body).split("\n") if p.strip()]
    sample = " ".join(paragraphs[:5])[:460]
    return {"id": str(key), "source": str(source), "at": str(at),
            "kind": kind, "headline": intro, "preview": sample,
            "terms": lex, "sha256": digest, "start": line_start,
            "end": line_end, "links": list(links)[:8],
            "authority": authority, "status": "source_candidate_only"}


def _index_rows(state, session):
    path = state / f"archive/session-{session:03}-index.json"
    if not path.is_file():
        return []
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj.get("entries", obj.get("index", []))


def _old_session(state, session):
    path = state / f"archive/session-{session:03}-source.md"
    if not path.is_file():
        return []
    source = path.relative_to(state).as_posix()
    lines = path.read_text(encoding="utf-8").splitlines()
    digest = _digest(path)
    out = []
    for row in _index_rows(state, session):
        if row.get("status") != "played":
            continue
        a, b = row.get("source_lines", [0, 0])
        if not (0 < a <= b <= len(lines)):
            continue
        full = "\n".join(lines[a-1:b])
        # Prefer actual user turn and scene material, not raw metadata.
        headline = row.get("scene_heading", "") or row.get("location", "")
        if row.get("player_excerpt"):
            headline = str(headline or "") + " " + row["player_excerpt"]
        out.append(_entry(key=row.get("id", f"s{session}.{a}"), source=source,
                          body=full, at=row.get("at", "") or
                          (_at(full) if session == 3 else ""),
                          headline=headline, digest=digest,
                          line_start=a, line_end=b))
    return out


def _turn_sessions(state):
    out = []
    for path in sorted((state / "archive").glob("session-*-turns.jsonl")):
        try:
            number = int(path.stem.split("-")[1])
        except (ValueError, IndexError):
            continue
        if number < 4:  # Earlier sources are reviewed, not raw journal.
            continue
        digest = _digest(path)
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") in ("ooc", "technical", "void"):
                continue
            body = " ".join((str(row.get("player", "")), str(row.get("gm", "")),
                             str(row.get("line", ""))))
            out.append(_entry(key=row["id"], source=path.relative_to(state).as_posix(),
                              at=row.get("at", ""), headline=row.get("line", ""),
                              body=body, digest=digest,
                              line_start=line_no, line_end=line_no))
    # The newest TURN can be ahead of the folded, immutable journal.
    path = state / "recovery/TURN.json"
    if path.is_file():
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("id") and row.get("kind") not in ("ooc", "technical", "void"):
            digest = _digest(path)
            cur = _entry(key=row["id"], source="recovery/TURN.json",
                         body=" ".join((str(row.get("player","")), str(row.get("gm","")),
                                        str(row.get("line","")))),
                         headline=row.get("line", ""), at=row.get("at",""),
                         digest=digest, authority=1.15)
            out = [x for x in out if x["id"] != row["id"]] + [cur]
    return out


def _canon(root, state):
    # Existing canonical source resolver retains field-level provenance.
    from . import scene_memory
    base = scene_memory.build_sources(root)
    if Path(root).resolve() != Path(state).resolve():
        private = scene_memory.build_sources(state)
        docs = {d["source"]: d for d in base["documents"]}
        docs.update({d["source"]: d for d in private["documents"]})
    else:
        docs = {d["source"]: d for d in base["documents"]}
    out = []
    for doc in docs.values():
        source = doc["source"]
        if source.endswith("-index.json"):
            continue  # Historical index references already resolved to played text.
        body = " ".join(s["field"]+" "+s["excerpt"] for s in doc["sections"])
        if not body:
            continue
        favored = [s for s in doc["sections"] if s["field"].startswith(
            ("gen_assimilation", "timeline", "phases", "chronology", "effective",
             "source_player", "contract", "origin", "canon", "summary"))]
        preview = " ".join(s["field"]+"="+s["excerpt"][:150]
                           for s in (favored or doc["sections"])[:4])
        entry = _entry(key=source, source=source, body=body,
                       headline=doc["title"], kind="canon_card",
                       links=doc["links"], digest=doc["sha256"],
                       authority=2.25 if "/characters/" in source or "/continuity/" in source else 1.65)
        entry["preview"] = preview[:670]
        # Source index already tokenized whole sections; include all terms
        # rather than relying on the first 1,400-character preview.
        all_terms = Counter(t for sec in doc["sections"] for t in sec["terms"])
        if all_terms:
            entry["terms"] = " ".join(w for w, _ in all_terms.most_common(135))
        out.append(entry)
    return out


def build(root, state):
    """Compile played source spans and canonical cards, never raw rejected drafts."""
    root, state = Path(root), Path(state)
    entries = _old_session(state, 2) + _old_session(state, 3)
    entries += _turn_sessions(state)
    entries += _canon(root, state)
    if len(entries) > MAX_RECORDS:
        raise ValueError("Heuristic source limit exceeded")
    # Collisions favor latest current turn and explicit canonical card.
    records = {row["id"]: row for row in entries}
    docs = list(records.values())
    frequency = Counter(w for row in docs for w in set(row["terms"].split()))
    return {"schema": SCHEMA, "source_policy": "played_spans_and_canonical_cards_only",
            "records": docs, "documents": len(docs), "df": dict(frequency),
            "rule": "Candidate evidence, not NPC knowledge. Read primary source and later corrections."}


def save(root, state, output=None):
    state = Path(state)
    output = Path(output) if output is not None else state / DESTINATION
    data = build(root, state)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_file() and output.read_text(encoding="utf-8") == payload:
        return {"status": "current", "records": data["documents"], "path": str(output)}
    output.write_text(payload, encoding="utf-8")
    return {"status": "updated", "records": data["documents"], "path": str(output),
            "bytes": len(payload.encode("utf-8"))}


def _similar(a, b):
    if a == b:
        return 1.0
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return 0.87
    if len(a) >= 6 and len(b) >= 6 and a[:3] == b[:3]:
        score = SequenceMatcher(None, a, b).ratio()
        if score >= 0.76:
            return score * 0.88
    return 0.0


def query(index, question, *, limit=9, sessions=(), no_guess=True):
    """Fast weighted lexical retrieval from the pre-built whole-history index.

    Returns source pointers with game time and fields to VERIFY, never new facts.
    """
    if index.get("schema") != SCHEMA:
        raise ValueError("Invalid heuristic history index")
    if not isinstance(question, str) or not question.strip() or not 1 <= limit <= 30:
        raise ValueError("Expected non-empty query and limit 1..30")
    parsed = list(dict.fromkeys(stem(w) for w in tokens(question)))[:16]
    candidates = []
    df = index["df"]
    total = max(1, index["documents"])
    for item in index["records"]:
        if sessions and item["kind"] == "played":
            match = re.search(r"session-(\d{3})", item["source"])
            if match and int(match.group(1)) not in sessions:
                continue
        lex = set(item["terms"].split())
        exact = set(parsed) & lex
        if not exact:
            # Work on rare typo/inflection variants, bounded for speed.
            if not any(any(w.startswith(q[:4]) for w in lex)
                       for q in parsed if len(q) >= 5):
                continue
        score, matched = 0.0, 0
        details = []
        for q in parsed:
            variants = (q, q.translate(LATIN)) if re.search("[а-я]", q) else (q,)
            similarity = 1.0 if q in lex else max(
                (_similar(v, w) for v in variants for w in lex if
                 w[:3] == v[:3] or w.startswith(v[:3])), default=0.0)
            if similarity < 0.73:
                continue
            weight = max(1.0, min(5.2, 1.0 + math.log((total + 1) / (df.get(q, 0)+1))))
            score += similarity * similarity * weight
            matched += 1
            details.append(q)
        if not matched:
            continue
        coverage = matched / len(parsed)
        score = score / max(1.0, sum(max(1.0, min(5.2, 1.0 +
            math.log((total+1)/(df.get(q, 0)+1)))) for q in parsed))
        # Dated canon cards do not lose to recent generic turn summaries.
        score += min(0.06, 0.015 * item["authority"])
        score += 0.13 * coverage
        headline = stem(" ".join(tokens(item["headline"])))
        if len(parsed) >= 2 and sum(q in headline for q in parsed) >= 2:
            score += 0.09
        candidates.append((score, coverage, item, details))
    candidates.sort(key=lambda hit: (-hit[1], -hit[0], hit[2]["source"], hit[2]["id"]))
    selected, per_session = [], defaultdict(int)
    for score, coverage, row, details in candidates:
        source = row["source"]
        bucket = re.search(r"session-\d{3}", source)
        group = bucket.group(0) if bucket else source
        if per_session[group] >= max(2, math.ceil(limit / 2)) and len(selected) >= 3:
            continue
        per_session[group] += 1
        selected.append({"id": row["id"], "source": source,
                         "lines": [row["start"], row["end"]],
                         "source_sha256": row["sha256"], "at": row["at"],
                         "kind": row["kind"], "score": round(score, 4),
                         "coverage": round(coverage, 4), "terms": details,
                         "headline": row["headline"], "preview": row["preview"],
                         "links": row["links"], "status": "candidate_only"})
        if len(selected) >= limit:
            break
    relevant = bool(selected) and any(x["coverage"] >= 0.55 for x in selected)
    return {"schema": "npr-heuristic-answer/1", "query": question,
            "index_documents": total, "method": "rarity_stems_fuzzy_date_context",
            "matches": selected,
            "confidence": "candidate_evidence" if relevant else "insufficient_evidence",
            "limits": ["Source relevance is not a claim about chronology or NPC knowledge",
                       "Check the cited primary source and newest corrections before claiming a fact",
                       "Empty result does not prove an event never occurred"]}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
