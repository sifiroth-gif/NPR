"""Safe FTS5 queries adapted from the source engine's history index."""

import json
import re

from .transcripts import PREFERRED_HISTORY
from . import compaction

TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def fts_expression(query: str, mode: str) -> str:
    # Quote every token, never pass arbitrary user FTS expressions to SQLite.
    terms = TOKEN.findall(query)
    if not terms:
        raise ValueError('Search query has no searchable words')
    if len(terms) > 12:
        raise ValueError('Use a narrower query (maximum 12 terms)')
    op = ' OR ' if mode == 'any' else ' AND '
    return op.join('"' + word.replace('"', '') + '"' for word in terms)


def source_excerpt(text, query="", width=440, bounds=None):
    """Return a literal substring and its offsets in the original UTF-8 source."""
    import hashlib

    if bounds is not None:
        start, end = bounds
    else:
        terms = tuple(term.casefold() for term in TOKEN.findall(query))
        first = next((match for match in TOKEN.finditer(text)
                      if match.group().casefold().startswith(terms)), None) if terms else None
        start = 0 if first is None else max(0, first.start() - width // 4)
        end = min(len(text), start + width)
    return {"text": text[start:end], "chars": [start, end],
            "lines": [text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1],
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _legacy_entities(con):
    # Before the first write to a v1 database the same explicit links can be
    # read from the source JSON. Names in free prose are not actor access.
    from . import continuity
    if con.execute("SELECT 1 FROM sqlite_temp_master WHERE name='journal_entities'").fetchone():
        return
    con.execute("""CREATE TEMP TABLE journal_entities (
        entity TEXT NOT NULL, seq INTEGER NOT NULL, PRIMARY KEY(entity, seq)
    )""")
    con.execute("""CREATE TEMP TABLE journal_edges (
        source INTEGER NOT NULL, dependent INTEGER NOT NULL, PRIMARY KEY(source, dependent)
    )""")
    con.execute("""CREATE TEMP TABLE journal_corrections (
        source INTEGER NOT NULL, correction INTEGER NOT NULL, PRIMARY KEY(source, correction)
    )""")
    for row in con.execute("SELECT seq, kind, details FROM journal ORDER BY seq"):
        continuity.index_entry(con, row["seq"], row["kind"], json.loads(row["details"]))


def _conditions(con, *, audience, entities, session, since, until, include_superseded):
    from . import continuity, world

    for entity in entities:
        world.id_value(entity, "recall entity")
    if session is not None and (type(session) is not int or session < 1):
        raise ValueError("session must be a positive number")
    if since is not None:
        world.time_value(since, "since")
    if until is not None:
        world.time_value(until, "until")
    if since and until and since > until:
        raise ValueError("since is after until")
    native = continuity.available(con)
    if not native and entities:
        _legacy_entities(con)
    linked = native or bool(entities)
    where, params = [PREFERRED_HISTORY], []
    if audience == "player":
        where.append("j.visibility='player_known'")
    for column, operator, value in (("session", "=", session), ("at", ">=", since), ("at", "<=", until)):
        if value is not None:
            where.append(f"j.{column}{operator}?")
            params.append(value)
    for entity in entities:
        where.append("EXISTS (SELECT 1 FROM journal_entities e WHERE e.seq=j.seq AND e.entity=?)")
        params.append(entity)
    allowed_correction = " AND c.visibility='player_known'" if audience == "player" else ""
    if linked:
        correction = f"""(SELECT c.id FROM journal_corrections p JOIN journal c ON c.seq=p.correction
                        WHERE p.source=j.seq{allowed_correction} ORDER BY p.correction DESC LIMIT 1)"""
    else:
        correction = f"""(SELECT c.id FROM journal c, json_each(c.details,'$.supersedes') p
                        WHERE c.kind='event' AND j.kind='event' AND p.value=j.id{allowed_correction}
                        ORDER BY c.seq DESC LIMIT 1)"""
    if not include_superseded:
        where.append(f"{correction} IS NULL")
    return " AND ".join(where) or "1", params, correction, linked


def recall(con, *, query, audience="gm", entities=(), session=None, since=None, until=None,
           limit=8, include_superseded=False, semantic=None):
    """Find source passages using exact FTS, word stems and explicit entity/causal links."""
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError("expected up to 1000 searchable characters")
    terms = TOKEN.findall(query)
    if len(terms) > 12 or (not terms and not entities):
        raise ValueError("give up to 12 searchable words or a stable entity ID")
    condition, params, correction, linked = _conditions(
        con, audience=audience, entities=entities, session=session, since=since,
        until=until, include_superseded=include_superseded)
    candidates = {}
    ranks = {}

    def add(rows, channel, weight):
        for rank, row in enumerate(rows, 1):
            seq = row["seq"]
            candidates[seq] = row
            ranks[seq] = ranks.get(seq, 0) + weight / (20 + rank)
            channels.setdefault(seq, []).append(channel)

    channels = {}
    size = limit * 5
    if terms:
        exact = fts_expression(query, "all")
        # Deliberately conservative fallback for inflected forms and ё/е.
        # These are candidate matches, not a morphological or semantic assertion.
        stems = []
        for token in terms:
            token = token.casefold()
            stem = token[:-2] if len(token) >= 5 and re.search("[а-яё]", token) else token
            for variant in dict.fromkeys((stem, stem.replace("ё", "е"))):
                stems.append('"' + variant + '"' + ("*" if stem != token else ""))
        broad = " OR ".join(stems)
        for name, expression, weight in (("exact", exact, 2.0), ("prefix", broad, 1.0)):
            rows = con.execute(f"""
                SELECT j.*, {correction} AS superseded_by FROM history_fts
                JOIN journal j ON j.seq=history_fts.rowid
                WHERE history_fts MATCH ? AND {condition}
                ORDER BY bm25(history_fts, 1.0, 0.8),j.seq DESC LIMIT ?
            """, (expression, *params, size))
            add(rows, name, weight)
            extra = compaction.source_rows(con, expression, predicate=condition,
                                           params=params, limit=size, correction=correction)
            if extra:
                add(extra, "archive_" + name, weight * 0.85)
    if entities:
        add(con.execute(f"""SELECT j.*, {correction} AS superseded_by FROM journal j
                           WHERE {condition} ORDER BY j.seq DESC LIMIT ?""",
                        (*params, size)), "entity", 0.6)
    if semantic is not None:
        for rank, seq in enumerate(semantic, 1):
            row = con.execute(f"""SELECT j.*, {correction} AS superseded_by FROM journal j
                                  WHERE j.seq=? AND {condition}""", (seq, *params)).fetchone()
            if row is not None:
                candidates[seq] = row
                ranks[seq] = ranks.get(seq, 0) + 1.4 / (20 + rank)
                channels.setdefault(seq, []).append("semantic")
    selected = sorted(candidates, key=lambda seq: (ranks[seq], seq), reverse=True)[:limit]
    results = []
    for seq in selected:
        row = compaction.recover(con, candidates[seq])
        details = json.loads(row["details"])
        def neighbours(inbound):
            if not linked:
                return []
            ref, match = ("source", "dependent") if inbound else ("dependent", "source")
            visible = " AND j.visibility='player_known'" if audience == "player" else ""
            return [dict(r) for r in con.execute(f"""
                SELECT j.kind,j.id,j.at FROM journal_edges e JOIN journal j ON j.seq=e.{ref}
                WHERE e.{match}=?{visible} ORDER BY j.seq DESC LIMIT 5
            """, (seq,))]
        result = {key: row[key] for key in ("id", "kind", "session", "at", "revision", "superseded_by")}
        result.update({"channels": channels[seq], "source_status": details.get(
            "source_status", "reconstructed" if row["body"].startswith(
                "[Восстановление из сыгранного диалога]") else "recorded"),
            "passage": source_excerpt(row["body"], query), "based_on": neighbours(True),
            "consequences": neighbours(False)})
        if "transcript" in details:
            result["transcript"] = details["transcript"]
        if row["kind"] == "turn" and row["player_input"]:
            result["player_passage"] = source_excerpt(row["player_input"], query, width=260)
        if semantic is not None and seq in semantic:
            start, end = semantic[seq]
            body_len = len(row["body"])
            if start < body_len:
                result["semantic_passage"] = {"field": "text", **source_excerpt(
                    row["body"], bounds=(start, min(end, body_len)))}
            elif row["player_input"] and end > body_len + 1:
                result["semantic_passage"] = {"field": "player_input", **source_excerpt(
                    row["player_input"], bounds=(max(0, start-body_len-1), end-body_len-1))}
        if audience == "gm" and "source_ref" in details:
            result["source_ref"] = details["source_ref"]
        results.append(result)
    return {"mode": "hybrid" if semantic is not None else "fast", "matches": results}
