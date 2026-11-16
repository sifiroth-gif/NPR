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


def _windows(lines, first, last, *, target=2400, max_lines=44, overlap=5):
    """Contiguous anchored spans: every line is indexed, with small overlap."""
    start = first
    while start <= last:
        end, length = start - 1, 0
        while end < last and end - start + 1 < max_lines:
            length += len(lines[end]) + 1
            end += 1
            if length >= target:
                break
        if end < start:
            end = start
        yield start, end, "\n".join(lines[start - 1:end])
        if end == last:
            break
        start = max(start + 1, end - overlap + 1)


def _first_session(state):
    """Session 1 has a complete historical archive but no accepted turn index.

    Include EVERY source line; mark candidates as requiring replay review rather
    than asserting that raw text is confirmed canon. Later corrections override.
    """
    path = state / "archive/session-001.md"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    source = path.relative_to(state).as_posix()
    digest = _digest(path)
    rows = []
    for a, b, chunk in _windows(lines, 1, len(lines)):
        rows.append(_entry(
            key=f"s1.lines.{a:06d}", source=source, body=chunk,
            headline=" ".join(chunk.splitlines()[:3])[:230], at=_at(chunk),
            line_start=a, line_end=b, digest=digest,
            kind="archived_source_review_required", authority=0.65))
    return rows


def _old_session(state, session):
    """Use curated, played-only navigation for ANY reviewed historical session.

    Session 2 uses index.entries; session 3 uses index.index. Neither is
    silently downgraded to a raw-text scrape or promoted beyond its authority.
    """
    catalog = state / f"archive/session-{session:03}-index.json"
    if not catalog.is_file():
        return []
    info = json.loads(catalog.read_text(encoding="utf-8-sig"))
    source_rel = info.get("source") or info.get("primary_source")
    if not source_rel:
        return []
    path = state / source_rel
    if not path.is_file():
        return []
    digest = _digest(path)
    expected = info.get("source_sha256")
    if expected and digest != expected:
        raise ValueError("Historical source index does not match " + source_rel)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    out = []
    for row in info.get("entries", info.get("index", [])):
        if row.get("status") != "played":
            continue
        a, b = row.get("source_lines", [0, 0])
        if not (0 < a <= b <= len(lines)):
            continue
        label = str(row.get("id") or f"s{session}.{row.get('n', a):03}")
        heading = row.get("scene_heading", "") or row.get("location", "")
        player = row.get("player_excerpt", "")
        at = row.get("at") or _at("\n".join(lines[a-1:min(b,a+8)]))
        for number, (start, end, excerpt) in enumerate(_windows(lines, a, b), 1):
            out.append(_entry(
                key=f"{label}.{number:02d}", source=source_rel, body=excerpt,
                at=at or heading, headline=(heading + " " + player)[:470],
                kind="played", line_start=start, line_end=end,
                digest=digest, authority=1.3))
    return out

def _turn_sessions(state):
    """Automatically include all live accepted JSONL sessions and latest TURN.

    Legacy session-4 review exclusions are respected; NEW session numbers are
    discovered from filenames, never hardcoded as a closed set.
    """
    out = []
    for path in sorted((state / "archive").glob("session-*-turns.jsonl")):
        try:
            number = int(path.stem.split("-")[1])
        except (ValueError, IndexError):
            continue
        if number < 4:
            continue  # These sessions have independent reviewed source indexes.
        excluded = set()
        idx = state / f"archive/session-{number:03}-index.json"
        if idx.is_file():
            review = json.loads(idx.read_text(encoding="utf-8-sig"))
            excluded.update(int(n) for n in review.get("ooc_in_live", []))
        digest = _digest(path)
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") in ("ooc", "technical", "void"):
                continue
            m = re.search(r"\.text\.(\d+)$", row.get("id", ""))
            if m and int(m.group(1)) in excluded:
                continue
            body = " ".join((str(row.get("player", "")), str(row.get("gm", "")),
                             str(row.get("line", ""))))
            out.append(_entry(key=row["id"], source=path.relative_to(state).as_posix(),
                              at=row.get("at", ""), headline=row.get("line", ""),
                              body=body, digest=digest, kind="played",
                              line_start=line_no, line_end=line_no))
    # Latest text-ahead TURN precedes the folded journal in GitHub gameplay.
    path = state / "recovery/TURN.json"
    if path.is_file():
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("id") and row.get("kind") not in ("ooc", "technical", "void"):
            digest = _digest(path)
            cur = _entry(key=row["id"], source="recovery/TURN.json",
                         body=" ".join((str(row.get("player","")), str(row.get("gm","")),
                                        str(row.get("line","")), json.dumps(
                                            row.get("d", {}).get("canon+", []), ensure_ascii=False))),
                         headline=row.get("line", ""), at=row.get("at",""),
                         digest=digest, authority=1.5)
            out = [x for x in out if x["id"] != row["id"]] + [cur]
    return out

def _canon(root, state):
    """All canonical cards plus each explicitly recorded correction, not only HOT."""
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
            continue
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
        all_terms = Counter(t for sec in doc["sections"] for t in sec["terms"])
        if all_terms:
            entry["terms"] = " ".join(w for w, _ in all_terms.most_common(230))
        out.append(entry)
    path = state / "archive/shiki-canon.jsonl"
    if path.is_file():
        digest = _digest(path)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            rule = row.get("rule", "")
            if not rule:
                continue
            out.append(_entry(key=f"canon.{lineno:05d}", source="archive/shiki-canon.jsonl",
                              body=rule, headline=row.get("turn", ""),
                              kind="correction", line_start=lineno, line_end=lineno,
                              digest=digest, authority=3.0))
    return out

def build(root, state):
    """Compile played source spans and canonical cards, never raw rejected drafts."""
    root, state = Path(root), Path(state)
    entries = _first_session(state)
    # Discover all curated session indexes; preserve future session additions.
    for index in sorted((state / "archive").glob("session-*-index.json")):
        number = int(index.name.split("-")[1])
        if number > 1 and number < 4:
            entries.extend(_old_session(state, number))
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
    """Shard a derived cache so each GitHub-hosted lookup file stays readable.

    Existing GitHub Actions hot-fold calls this automatically after every turn.
    Each shard is content-checked, old identical shards are not rewritten.
    """
    state = Path(state)
    folder = Path(output).parent if output is not None else state / "archive/heuristic-recall"
    data = build(root, state)
    folder.mkdir(parents=True, exist_ok=True)
    shards, chunks, size = [], [], 0
    changed = False

    def flush():
        nonlocal changed, chunks, size
        if not chunks:
            return
        name = f"shard-{len(shards):03d}.json"
        path = folder / name
        content = json.dumps(chunks, ensure_ascii=False, separators=(",", ":"),
                             sort_keys=True) + "\n"
        digest = sha256(content.encode("utf-8")).hexdigest()
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            changed = True
        shards.append({"file": name, "sha256": digest, "records": len(chunks)})
        chunks, size = [], 0

    for entry in data["records"]:
        estimate = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        if chunks and (len(chunks) >= 65 or size + estimate > 115_000):
            flush()
        chunks.append(entry)
        size += estimate
    flush()

    hot_path = state / "recovery/HOT.json"
    hot = json.loads(hot_path.read_text(encoding="utf-8")) if hot_path.is_file() else {}
    manifest = {"schema": SCHEMA, "documents": data["documents"],
                "cursor": hot.get("cursor", {}).get("id", ""),
                "shards": shards,
                "rule": "Read exact source; correction > event; actor knowledge is separate."}
    path = folder / "manifest.json"
    content = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
        changed = True
    return {"status": "updated" if changed else "current",
            "records": data["documents"], "shards": len(shards),
            "path": str(path)}


def load(path):
    """Fail closed if a shard was modified or mixed with another index build."""
    path = Path(path)
    manifest_path = path / "manifest.json" if path.is_dir() else path
    info = json.loads(manifest_path.read_text(encoding="utf-8"))
    if info.get("schema") != SCHEMA:
        raise ValueError("Invalid heuristic manifest")
    records = []
    for item in info["shards"]:
        shard = manifest_path.parent / item["file"]
        raw = shard.read_bytes()
        if sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Heuristic shard hash mismatch: " + str(shard))
        batch = json.loads(raw)
        if len(batch) != item["records"]:
            raise ValueError("Heuristic shard count mismatch: " + str(shard))
        records.extend(batch)
    if len(records) != info["documents"]:
        raise ValueError("Incomplete heuristic history index")
    frequency = Counter(w for row in records for w in set(row["terms"].split()))
    return {"schema": SCHEMA, "source_policy": "played_and_reviewed_candidate",
            "records": records, "documents": len(records),
            "df": dict(frequency), "cursor": info.get("cursor", "")}


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


