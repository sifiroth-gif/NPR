"""Validate and project world events into SQLite.

Facts, beliefs and process operations are adapted from the source RPG engine.
"""

import datetime as dt
import hashlib
import json
import re

IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
VISIBILITY = ("gm_only", "player_known")
EVIDENCE = ("witnessed", "reported", "document", "inferred", "uncertain")
CERTAINTY = ("certain", "probable", "uncertain")


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_json(data) -> str:
    return sha(canonical(data).encode('utf-8'))


def id_value(value, label):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ValueError(f'{label}: invalid stable identifier')
    return value


def time_value(value, label):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ValueError(f'{label}: expected YYYY-MM-DDTHH:MM:SS')
    try:
        dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{label}: impossible time') from exc
    return value


def text_value(value, label, max_length=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f'{label}: expected nonempty text, maximum {max_length} characters')
    return value


def json_value(value):
    encoded = canonical(value)
    if len(encoded.encode('utf-8')) > 32768:
        raise ValueError('single stored value exceeds 32 KiB')
    return encoded


def fields(value, required, optional=(), *, label):
    if (not isinstance(value, dict) or not set(required).issubset(value)
            or set(value).difference(required, optional)):
        raise ValueError(f"{label}: required fields {', '.join(required)}"
                         + (f"; optional fields {', '.join(optional)}" if optional else ""))
    return value


def sequence(value, label, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{label}: expected a list of at most {limit} entries")
    return value


def visibility(value):
    if value not in VISIBILITY:
        raise ValueError("visibility must be gm_only or player_known")
    return value


def notes_value(value):
    if not isinstance(value, str) or len(value) > 5000:
        raise ValueError("notes: expected text of at most 5000 characters")
    return value


def validate_npc(npc):
    fields(npc, ("id", "name"), ("profile",), label="NPC")
    id_value(npc["id"], "NPC id")
    text_value(npc["name"], "NPC name", 200)
    if not isinstance(npc.get("profile", {}), dict):
        raise ValueError("NPC profile: expected an object")
    json_value(npc.get("profile", {}))


OP_FIELDS = {
    "upsert_npc": ("npc",),
    "set_fact": ("subject", "name", "value", "visibility"),
    "delete_fact": ("subject", "name"),
    "set_belief": ("actor", "topic", "belief", "evidence", "certainty", "visibility"),
    "delete_belief": ("actor", "topic"),
    "start_process": ("id", "owner", "goal", "due_at", "visibility", "notes"),
    "reschedule_process": ("id", "due_at", "notes"),
    "finish_process": ("id", "status", "outcome"),
}


def validate_operation(op, at):
    kind = op.get("op") if isinstance(op, dict) else None
    if not isinstance(kind, str) or kind not in OP_FIELDS:
        raise ValueError(f"unknown world operation: {kind!r}")
    fields(op, ("op", *OP_FIELDS[kind]), label=kind)
    if "visibility" in op:
        visibility(op["visibility"])
    if kind == "upsert_npc":
        validate_npc(op["npc"])
    elif kind in ("set_fact", "delete_fact"):
        id_value(op["subject"], "fact subject")
        id_value(op["name"], "fact name")
        if kind == "set_fact":
            json_value(op["value"])
    elif kind in ("set_belief", "delete_belief"):
        id_value(op["actor"], "belief actor")
        id_value(op["topic"], "belief topic")
        if kind == "set_belief":
            json_value(op["belief"])
            if op["evidence"] not in EVIDENCE or op["certainty"] not in CERTAINTY:
                raise ValueError("invalid belief evidence or certainty")
    else:
        id_value(op["id"], "process id")
        if kind == "start_process":
            id_value(op["owner"], "process owner")
            text_value(op["goal"], "process goal")
        if kind in ("start_process", "reschedule_process"):
            due = time_value(op["due_at"], "process due_at")
            if due < at or (kind == "reschedule_process" and due == at):
                raise ValueError("process due_at must move the process forward")
            notes_value(op["notes"])
        else:
            if op["status"] not in ("resolved", "cancelled"):
                raise ValueError("process status must be resolved or cancelled")
            json_value(op["outcome"])


def validate_event(event):
    fields(event, ("id", "at", "summary", "visibility"),
           ("operations", "based_on", "witnesses", "knowledge_routes"), label="event")
    id_value(event["id"], "event id")
    at = time_value(event["at"], "event at")
    text_value(event["summary"], "event summary", 4000)
    visibility(event["visibility"])
    for source in sequence(event.get("based_on", []), "event based_on", 30):
        id_value(source, "source event id")
    witnesses = set()
    for actor in sequence(event.get("witnesses", []), "event witnesses", 100):
        id_value(actor, "event witness")
        if actor in witnesses:
            raise ValueError("duplicate event witness")
        witnesses.add(actor)
    routes = {}
    for route in sequence(event.get("knowledge_routes", []), "event knowledge_routes", 100):
        fields(route, ("actor", "via", "source"), ("source_actor",),
               label="event knowledge route")
        actor = id_value(route["actor"], "knowledge recipient")
        if route["via"] not in ("reported", "document", "inferred"):
            raise ValueError("invalid knowledge route")
        text_value(route["source"], "knowledge source", 1000)
        if "source_actor" in route:
            id_value(route["source_actor"], "knowledge reporter")
        if actor in routes:
            raise ValueError("duplicate personal knowledge route")
        routes[actor] = route
    for op in sequence(event.get("operations", []), "event operations", 1000):
        validate_operation(op, at)
        if op["op"] != "set_belief":
            continue
        # Legacy events without provenance metadata remain readable.
        # New Shiki drafts require annotations through prepare_reviewed_turn.
        if not ("witnesses" in event or "knowledge_routes" in event):
            continue
        actor = op["actor"]
        if op["evidence"] == "witnessed":
            if actor not in witnesses:
                raise ValueError("direct NPC belief requires named witness")
        elif op["evidence"] in ("reported", "document"):
            if actor not in routes:
                raise ValueError("reported/document NPC belief requires an explicit information route")
            if routes[actor]["via"] != op["evidence"]:
                raise ValueError("knowledge route disagrees with belief evidence")
        elif actor not in witnesses and actor not in routes:
            raise ValueError("NPC belief requires witness or explicit information route")
        elif actor in routes and routes[actor]["via"] != op["evidence"]:
            raise ValueError("knowledge route disagrees with belief evidence")


SCHEMA = """
CREATE TABLE npcs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    source_event TEXT NOT NULL
);
CREATE TABLE facts (
    subject TEXT NOT NULL,
    name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('gm_only', 'player_known')),
    source_event TEXT NOT NULL,
    PRIMARY KEY(subject, name)
);
CREATE TABLE beliefs (
    actor TEXT NOT NULL,
    topic TEXT NOT NULL,
    belief_json TEXT NOT NULL,
    evidence TEXT NOT NULL,
    certainty TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('gm_only', 'player_known')),
    source_event TEXT NOT NULL,
    PRIMARY KEY(actor, topic)
);
CREATE TABLE processes (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES npcs(id),
    goal TEXT NOT NULL,
    started_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('gm_only', 'player_known')),
    notes TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'resolved', 'cancelled')),
    outcome_json TEXT,
    source_event TEXT NOT NULL
);
CREATE INDEX active_due ON processes(due_at, id) WHERE status = 'active';
"""


def apply_operations(con, event, player_id):
    """Apply validated operations inside the caller's transaction."""
    event_id = event["id"]
    for op in event.get("operations", []):
        kind = op["op"]
        if kind == "upsert_npc":
            npc = op["npc"]
            if npc["id"] == player_id:
                raise ValueError("the player character cannot become an NPC")
            con.execute("""INSERT INTO npcs VALUES (?, ?, ?, ?)
                           ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                           profile_json=excluded.profile_json, source_event=excluded.source_event""",
                        (npc["id"], npc["name"], canonical(npc.get("profile", {})), event_id))
        elif kind == "set_fact":
            con.execute("""INSERT INTO facts VALUES (?, ?, ?, ?, ?)
                           ON CONFLICT(subject, name) DO UPDATE SET value_json=excluded.value_json,
                           visibility=excluded.visibility, source_event=excluded.source_event""",
                        (op["subject"], op["name"], canonical(op["value"]), op["visibility"], event_id))
        elif kind == "delete_fact":
            cursor = con.execute("DELETE FROM facts WHERE subject = ? AND name = ?",
                                 (op["subject"], op["name"]))
            require_row(cursor, "cannot delete a missing fact")
        elif kind == "set_belief":
            actor = op["actor"]
            if actor != player_id and not con.execute("SELECT 1 FROM npcs WHERE id = ?", (actor,)).fetchone():
                raise ValueError(f"unknown belief actor: {actor}")
            con.execute("""INSERT INTO beliefs VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(actor, topic) DO UPDATE SET belief_json=excluded.belief_json,
                           evidence=excluded.evidence, certainty=excluded.certainty,
                           visibility=excluded.visibility, source_event=excluded.source_event""",
                        (actor, op["topic"], canonical(op["belief"]), op["evidence"],
                         op["certainty"], op["visibility"], event_id))
        elif kind == "delete_belief":
            cursor = con.execute("DELETE FROM beliefs WHERE actor = ? AND topic = ?",
                                 (op["actor"], op["topic"]))
            require_row(cursor, "cannot delete a missing belief")
        elif kind == "start_process":
            if not con.execute("SELECT 1 FROM npcs WHERE id = ?", (op["owner"],)).fetchone():
                raise ValueError(f"process owner must be an NPC: {op['owner']}")
            if con.execute("SELECT 1 FROM processes WHERE id = ?", (op["id"],)).fetchone():
                raise ValueError(f"process id already used: {op['id']}")
            con.execute("""INSERT INTO processes VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?)""",
                        (op["id"], op["owner"], op["goal"], event["at"], op["due_at"],
                         op["visibility"], op["notes"], event_id))
        elif kind == "reschedule_process":
            cursor = con.execute("""UPDATE processes SET due_at = ?, notes = ?, source_event = ?,
                                    visibility = CASE WHEN ? = 'gm_only' THEN 'gm_only' ELSE visibility END
                                    WHERE id = ? AND status = 'active'""",
                                 (op["due_at"], op["notes"], event_id, event["visibility"], op["id"]))
            require_row(cursor, f"no active process {op['id']} to reschedule")
        else:
            cursor = con.execute("""UPDATE processes SET status = ?, outcome_json = ?, source_event = ?,
                                    visibility = CASE WHEN ? = 'gm_only' THEN 'gm_only' ELSE visibility END
                                    WHERE id = ? AND status = 'active'""",
                                 (op["status"], canonical(op["outcome"]), event_id,
                                  event["visibility"], op["id"]))
            require_row(cursor, f"no active process {op['id']} to finish")


def require_row(cursor, message):
    if cursor.rowcount != 1:
        raise ValueError(message)


def due_before(con, at):
    return con.execute("""SELECT id, due_at FROM processes WHERE status = 'active' AND due_at < ?
                          ORDER BY due_at, id LIMIT 1""", (at,)).fetchone()
