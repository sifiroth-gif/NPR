"""Source-linked memory and projections used by the GM and the audit."""

import json

from . import world

SCHEMA = """
CREATE TABLE memory (
    key TEXT NOT NULL,
    seq INTEGER NOT NULL REFERENCES journal(seq),
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    authority TEXT NOT NULL,
    status TEXT NOT NULL,
    visibility TEXT NOT NULL,
    text TEXT NOT NULL,
    previous TEXT,
    PRIMARY KEY(key, seq)
);
CREATE INDEX memory_subject ON memory(subject, seq);
CREATE TABLE journal_entities (
    entity TEXT NOT NULL,
    seq INTEGER NOT NULL REFERENCES journal(seq),
    PRIMARY KEY(entity, seq)
);
CREATE TABLE journal_edges (
    source INTEGER NOT NULL REFERENCES journal(seq),
    dependent INTEGER NOT NULL REFERENCES journal(seq),
    PRIMARY KEY(source, dependent)
);
CREATE INDEX journal_edges_dependent ON journal_edges(dependent, source);
CREATE TABLE journal_corrections (
    source INTEGER NOT NULL REFERENCES journal(seq),
    correction INTEGER NOT NULL REFERENCES journal(seq),
    PRIMARY KEY(source, correction)
);
CREATE INDEX journal_session_time ON journal(session, at, seq);
CREATE TABLE session_exports (
    session INTEGER PRIMARY KEY REFERENCES sessions(number),
    revision INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);
"""

CATEGORIES = ("canon", "thread", "commitment", "scene", "session", "npc", "gap")
AUTHORITY = {"plan": 0, "setting": 1, "played": 2, "player": 3}
STATUSES = ("open", "resolved", "retracted")


def available(con):
    return con.execute("PRAGMA user_version").fetchone()[0] >= 2


def validate_note(op):
    world.fields(op, ("op", "key", "category", "subject", "authority", "status",
                      "visibility", "text"), ("previous",), label="remember")
    world.id_value(op["key"], "memory key")
    world.id_value(op["subject"], "memory subject")
    world.text_value(op["text"], "memory", 3000)
    world.visibility(op["visibility"])
    if op["category"] not in CATEGORIES or op["authority"] not in tuple(AUTHORITY):
        raise ValueError("invalid memory category or authority")
    if op["status"] not in STATUSES:
        raise ValueError("invalid memory status")
    if "previous" in op:
        world.id_value(op["previous"], "previous memory source")


def apply_note(con, op, event_id):
    prior = con.execute("""
        SELECT m.*, j.id FROM memory m JOIN journal j USING(seq)
        WHERE key = ? ORDER BY seq DESC LIMIT 1
    """, (op["key"],)).fetchone()
    if prior is None:
        if "previous" in op or op["status"] == "retracted":
            raise ValueError("cannot supersede missing memory")
    else:
        if (op.get("previous") != prior["id"] or op["category"] != prior["category"]
                or op["subject"] != prior["subject"]):
            raise ValueError("memory replacement needs its current source and identity")
        if AUTHORITY[op["authority"]] < AUTHORITY[prior["authority"]]:
            raise ValueError("a lower-authority note cannot replace established memory")
        if prior["visibility"] == "gm_only" and op["visibility"] == "player_known":
            # A separate, public scene/event may reveal a fact. The private revision
            # chain itself stays private.
            raise ValueError("use a separate public memory key to reveal private information")
    seq = con.execute("SELECT seq FROM journal WHERE kind='event' AND id=?", (event_id,)).fetchone()[0]
    con.execute("INSERT INTO memory VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (op["key"], seq, op["category"], op["subject"], op["authority"],
                 op["status"], op["visibility"], op["text"], op.get("previous")))


def notes(con, *, audience="gm", subjects=(), key=None, history=False, limit=12):
    if not available(con):
        return []
    conditions, params = [], []
    if not history:
        conditions.append("m.seq = (SELECT max(p.seq) FROM memory p WHERE p.key=m.key)")
        conditions.append("m.status != 'retracted'")
    if audience == "player":
        conditions.extend(("m.visibility='player_known'", "j.visibility='player_known'"))
    if key is not None:
        conditions.append("m.key=?")
        params.append(key)
    elif subjects:
        placeholders = ",".join("?" for _ in subjects)
        conditions.append(f"""(m.subject IN ({placeholders})
            OR m.category IN ('canon','scene','session','gap')
            OR (m.category IN ('thread','commitment') AND m.status='open'))""")
        params.extend(subjects)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    order = "m.seq DESC" if history or key else """
        CASE WHEN m.category IN ('gap','canon','scene') AND m.status='open' THEN 0
             WHEN m.status='open' THEN 1 ELSE 2 END, m.seq DESC"""
    return [dict(row) for row in con.execute(f"""
        SELECT m.key,m.category,m.subject,m.authority,m.status,m.text,m.previous,
               j.id AS source_event,j.at,j.session
        FROM memory m JOIN journal j USING(seq){where} ORDER BY {order},m.key LIMIT ?
    """, (*params, limit))]


def index_entry(con, seq, kind, details):
    entities = set()
    if kind == "turn":
        entities.update(details.get("scene", {}).get("actors", []))
    else:
        entities.update(details.get("transcript", {}).get("entities", []))
        entities.update(details.get("witnesses", []))
        for route in details.get("knowledge_routes", []):
            entities.update(route[key] for key in ("actor", "source_actor") if key in route)
        for op in details.get("operations", []):
            entities.update(op[key] for key in ("actor", "subject", "owner", "process", "sender", "recipient")
                            if key in op)
            if "npc" in op:
                entities.add(op["npc"]["id"])
            if op["op"] in ("start_process", "reschedule_process", "finish_process", "configure_process",
                            "update_npc", "schedule_message", "deliver_message"):
                entities.add(op["id"])
        sources = set(details.get("based_on", [])) | set(details.get("supersedes", []))
        for op in details.get("operations", []):
            sources.update(op[key] for key in ("previous", "plan_source", "knowledge_source")
                           if key in op)
        for source in sources:
            con.execute("""
                INSERT INTO journal_edges(source, dependent)
                SELECT seq, ? FROM journal WHERE kind='event' AND id=? AND seq < ?
            """, (seq, source, seq))
        for source in set(details.get("supersedes", [])):
            con.execute("""
                INSERT INTO journal_corrections(source, correction)
                SELECT seq, ? FROM journal WHERE kind='event' AND id=? AND seq < ?
            """, (seq, source, seq))
    con.executemany("INSERT INTO journal_entities VALUES (?, ?)",
                    ((entity, seq) for entity in sorted(entities)))


def migrate_history(con):
    for row in con.execute("SELECT seq, kind, details FROM journal ORDER BY seq"):
        index_entry(con, row["seq"], row["kind"], json.loads(row["details"]))


def project_history(con, *, actor, at, limit):
    """Return only this actor's recorded beliefs, including their former versions."""
    if available(con):
        candidates = con.execute("""
            SELECT j.* FROM journal_entities e JOIN journal j USING(seq)
            WHERE e.entity=? AND j.kind='event' AND j.at<=? ORDER BY j.seq DESC
        """, (actor, at))
    else:
        candidates = con.execute(
            "SELECT * FROM journal WHERE kind='event' AND at<=? ORDER BY seq DESC", (at,))
    results = []
    for row in candidates:
        details = json.loads(row["details"])
        for op in reversed(details.get("operations", [])):
            if op["op"] not in ("set_belief", "delete_belief") or op.get("actor") != actor:
                continue
            route = next((r for r in details.get("knowledge_routes", [])
                          if r["actor"] == actor), None)
            if op.get("evidence") != "witnessed" and route and route["via"] != op.get("evidence"):
                route = None
            results.append({**op, "source_event": row["id"], "known_at": row["at"],
                            "session": row["session"], "route": route,
                            "witnessed_here": actor in details.get("witnesses", [])})
            if len(results) == limit:
                return results
    return results
