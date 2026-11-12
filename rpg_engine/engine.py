"""One SQLite transaction publishes a confirmed turn and its consequences."""

from contextlib import closing, contextmanager
from itertools import groupby
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile

from . import autonomy, continuity, rng, world
from .history import fts_expression

APPLICATION_ID = 0x52504745
SCHEMA_VERSION = 2
OOC = re.compile(r"^\s*(?:мастеру|ведущему|ooc|gm)\s*:", re.IGNORECASE)

SCHEMA = """
CREATE TABLE campaign (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    setup_json TEXT NOT NULL,
    seed BLOB NOT NULL CHECK(length(seed) = 32),
    revision INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    session INTEGER NOT NULL,
    at TEXT NOT NULL,
    location TEXT NOT NULL
);
CREATE TABLE sessions (
    number INTEGER PRIMARY KEY,
    started_revision INTEGER NOT NULL UNIQUE,
    at TEXT NOT NULL,
    location TEXT NOT NULL
);
CREATE TABLE pending (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    id TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    packet TEXT,
    draft_sha TEXT,
    CHECK((packet IS NULL) = (draft_sha IS NULL))
);
CREATE TABLE rolls (
    turn_id TEXT NOT NULL,
    check_id TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    spec TEXT NOT NULL,
    result TEXT NOT NULL,
    PRIMARY KEY(turn_id, check_id)
);
CREATE TABLE journal (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('turn', 'event')),
    revision INTEGER NOT NULL,
    session INTEGER NOT NULL REFERENCES sessions(number),
    at TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('gm_only', 'player_known')),
    body TEXT NOT NULL,
    player_input TEXT NOT NULL,
    details TEXT NOT NULL,
    UNIQUE(kind, id)
);
CREATE INDEX journal_kind ON journal(kind, seq DESC);
CREATE VIRTUAL TABLE history_fts USING fts5(
    body, player_input, content='journal', content_rowid='seq', tokenize='unicode61'
);
CREATE TRIGGER index_history AFTER INSERT ON journal BEGIN
    INSERT INTO history_fts(rowid, body, player_input)
        VALUES (new.seq, new.body, new.player_input);
END;
"""


class ConflictError(ValueError):
    """A competing or changed operation needs to be reconciled."""


def setup_value(value):
    world.fields(value, ("id", "title", "start_at", "location", "player"),
                 ("rules", "npcs", "operations"), label="campaign")
    world.id_value(value["id"], "campaign id")
    world.text_value(value["title"], "campaign title", 200)
    world.time_value(value["start_at"], "campaign start_at")
    world.text_value(value["location"], "campaign location", 500)
    player = world.fields(value["player"], ("id", "name"), label="player")
    world.id_value(player["id"], "player id")
    world.text_value(player["name"], "player name", 200)
    if not isinstance(value.get("rules", {}), dict):
        raise ValueError("campaign rules: expected an object")
    world.json_value(value.get("rules", {}))
    npcs = world.sequence(value.get("npcs", []), "campaign NPCs", 1000)
    ids = {player["id"]}
    for npc in npcs:
        world.validate_npc(npc)
        if npc["id"] in ids:
            raise ValueError(f"actor id already used: {npc['id']}")
        ids.add(npc["id"])
    operations = world.sequence(value.get("operations", []), "initial operations", 1000)
    for op in operations:
        world.validate_operation(op, value["start_at"])
    public = {k: value[k] for k in ("id", "title", "player", "start_at", "location")}
    public["rules"] = value.get("rules", {})
    return public, [{"op": "upsert_npc", "npc": npc} for npc in npcs] + operations


def revision_value(value):
    if type(value) is not int or not 0 <= value < 2**63 - 1:
        raise ValueError("expected_revision must be a nonnegative SQLite revision")
    return value


def turn_value(packet):
    world.fields(packet, ("id", "expected_revision", "at", "location", "player_input", "text"),
                 ("events", "input_mode", "scene", "source_ref", "source_status"), label="turn")
    world.id_value(packet["id"], "turn id")
    revision_value(packet["expected_revision"])
    world.time_value(packet["at"], "turn at")
    world.text_value(packet["location"], "turn location", 500)
    world.text_value(packet["text"], "GM response", 20000)
    player_input = packet["player_input"]
    if not isinstance(player_input, str) or len(player_input) > 20000:
        raise ValueError("player_input must be text of at most 20000 characters")
    mode = packet.get("input_mode", "auto")
    if mode not in ("auto", "in_character", "direction", "ooc"):
        raise ValueError("unknown input_mode")
    if mode == "ooc" or (mode == "auto" and OOC.match(player_input)):
        raise ValueError("answer out-of-character messages outside the played journal")
    if "source_ref" in packet:
        world.text_value(packet["source_ref"], "turn source", 1000)
    if "source_status" in packet:
        if packet["source_status"] not in ("exact", "reconstructed") or "source_ref" not in packet:
            raise ValueError("turn source status requires an exact or reconstructed source reference")
        marker = packet["text"].startswith("[Восстановление из сыгранного диалога]")
        if marker != (packet["source_status"] == "reconstructed"):
            raise ValueError("turn reconstruction marker and source status disagree")
    if "scene" in packet:
        scene = world.fields(packet["scene"], ("id", "actors", "beat"), ("focus",), label="scene")
        world.id_value(scene["id"], "scene id")
        actors = world.sequence(scene["actors"], "scene actors", 40)
        for actor in actors:
            world.id_value(actor, "scene actor")
        if len(set(actors)) != len(actors):
            raise ValueError("scene actors must be unique")
        if scene["beat"] not in ("routine", "character", "discovery", "decision",
                                "conflict", "consequence", "transition", "combat"):
            raise ValueError("unknown scene beat")
        if "focus" in scene:
            world.text_value(scene["focus"], "scene focus", 500)
    for event in world.sequence(packet.get("events", []), "turn events", 1000):
        world.validate_event(event)


def connect(path, *, write=False):
    mode = "rw" if write else "ro"
    con = sqlite3.connect(path.resolve().as_uri() + f"?mode={mode}", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


@contextmanager
def new_database(path):
    """Publish only a complete file and fail if the destination already exists."""
    if os.path.lexists(path):
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".campaign-", suffix=".sqlite", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        yield temporary
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def state(con):
    return con.execute("SELECT * FROM campaign WHERE singleton = 1").fetchone()


def require_revision(current, expected):
    if current["revision"] != expected:
        raise ConflictError(f"expected revision {expected}; current revision {current['revision']}")


def journal_entry(con, kind, entry_id):
    return con.execute("SELECT * FROM journal WHERE kind = ? AND id = ?",
                       (kind, entry_id)).fetchone()


def append_entry(con, *, entry_id, kind, revision, session, at, visibility, body,
                 details, player_input=""):
    row = con.execute("""INSERT INTO journal
                   (id, kind, revision, session, at, visibility, body, details, player_input)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry_id, kind, revision, session, at, visibility, body, world.canonical(details),
                 player_input))
    if continuity.available(con):
        continuity.index_entry(con, row.lastrowid, kind, details)


def apply_event(con, event, *, player_id, revision, session):
    if journal_entry(con, "event", event["id"]) is not None:
        raise ConflictError(f"event id already used: {event['id']}")
    for source in (*event.get("based_on", []), *event.get("supersedes", [])):
        if journal_entry(con, "event", source) is None:
            raise ValueError(f"unknown source event: {source}")
    provenance = {"based_on": event.get("based_on", []),
                  "operations": event.get("operations", [])}
    for key in ("witnesses", "knowledge_routes", "source_ref", "supersedes"):
        if key in event:
            provenance[key] = event[key]
    append_entry(con, entry_id=event["id"], kind="event", revision=revision, session=session,
                 at=event["at"], visibility=event["visibility"], body=event["summary"],
                 details=provenance)
    world.apply_operations(con, event, player_id)


def require_clear_time(con, at):
    due = world.due_before(con, at)
    if due is not None:
        raise ValueError(f"review process {due['id']} at {due['due_at']} before advancing to {at}")


def apply_turn_events(con, current, packet):
    cursor = current["at"]
    if packet["at"] < cursor:
        raise ValueError("the scene clock cannot move backwards")
    player_id = json.loads(current["setup_json"])["player"]["id"]
    scene = packet.get("scene")
    for at, events in groupby(packet.get("events", []), key=lambda event: event["at"]):
        if not cursor <= at <= packet["at"]:
            raise ValueError("events must be chronological and inside the turn's time window")
        require_clear_time(con, at)
        for event in events:
            if any("window_resolved_by" in op for op in event.get("operations", [])) and not packet["player_input"].strip():
                raise ValueError("an NPC outcome after a player window requires the player's reply")
            apply_event(con, event, player_id=player_id, revision=current["revision"] + 1,
                        session=current["session"])
        cursor = at
    require_clear_time(con, packet["at"])
    if scene:
        for actor in scene["actors"]:
            if actor != player_id and not con.execute("SELECT 1 FROM npcs WHERE id=?", (actor,)).fetchone():
                raise ValueError(f"unknown scene actor: {actor}")


def reserve_turn(con, turn_id, expected):
    pending = con.execute("SELECT * FROM pending").fetchone()
    if pending is None:
        con.execute("INSERT INTO pending(singleton, id, base_revision) VALUES (1, ?, ?)",
                    (turn_id, expected))
        pending = con.execute("SELECT * FROM pending").fetchone()
    if pending["id"] != turn_id or pending["base_revision"] != expected:
        raise ConflictError(f"working turn is {pending['id']} at revision {pending['base_revision']}")
    return pending


def open_draft(pending):
    packet = json.loads(pending["packet"])
    if (packet["id"] != pending["id"] or packet["expected_revision"] != pending["base_revision"]
            or world.sha_json([pending["generation"], packet]) != pending["draft_sha"]):
        raise ValueError("stored draft does not match its receipt")
    return packet


def draft_receipt(pending):
    return {"status": "prepared", "turn_id": pending["id"], "base_revision": pending["base_revision"],
            "draft_sha": pending["draft_sha"], "text": open_draft(pending)["text"]}


def confirmed_receipt(entry):
    return {"status": "confirmed", "turn_id": entry["id"], "revision": entry["revision"],
            "session": entry["session"], "at": entry["at"], "text": entry["body"],
            "draft_sha": json.loads(entry["details"])["draft_sha"]}


def public_entry(row, audience):
    item = {key: row[key] for key in ("id", "kind", "revision", "session", "at")}
    item["text"] = row["body"]
    if row["kind"] == "turn":
        item["player_input"] = row["player_input"]
        details = json.loads(row["details"])
        item["location"] = details["location"]
        item["source_status"] = details.get("source_status", "reconstructed" if
            row["body"].startswith("[Восстановление из сыгранного диалога]") else "recorded")
        if audience == "gm":
            item.update({key: details[key] for key in ("scene", "source_ref") if key in details})
    elif audience == "gm":
        item.update(json.loads(row["details"]))
        item["visibility"] = row["visibility"]
    return item


def audience_value(audience):
    if audience not in ("player", "gm"):
        raise ValueError("audience must be player or gm")


def limit_value(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"limit must be between {lower} and {upper}")


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Campaign:
    def __init__(self, path):
        self.path = Path(path)

    @classmethod
    def create(cls, path, setup):
        game = cls(path)
        public, operations = setup_value(setup)
        at, location = public["start_at"], public["location"]
        with new_database(game.path) as temporary:
            with closing(connect(temporary, write=True)) as con, con:
                con.executescript(f"PRAGMA application_id = {APPLICATION_ID}; "
                                  f"PRAGMA user_version = {SCHEMA_VERSION};\n" + SCHEMA + world.SCHEMA
                                  + continuity.SCHEMA + autonomy.SCHEMA)
                con.execute("INSERT INTO campaign VALUES (1, ?, ?, 0, 0, 1, ?, ?)",
                            (world.canonical(public), secrets.token_bytes(32), at, location))
                con.execute("INSERT INTO sessions VALUES (1, 0, ?, ?)", (at, location))
                if operations:
                    apply_event(con, {"id": "campaign-setup", "at": at, "summary": "Campaign setup",
                                      "visibility": "gm_only", "operations": operations},
                                player_id=public["player"]["id"], revision=0, session=1)
        return game

    @contextmanager
    def connection(self, *, write=False):
        with closing(connect(self.path, write=write)) as con, con:
            if con.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise ValueError("unsupported campaign database or schema")
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            version = con.execute("PRAGMA user_version").fetchone()[0]
            if version not in (1, SCHEMA_VERSION):
                raise ValueError("unsupported campaign database or schema")
            if write and version == 1:
                # executescript would commit the caller's transaction.
                for statement in (continuity.SCHEMA + autonomy.SCHEMA).split(";"):
                    if statement.strip():
                        con.execute(statement)
                continuity.migrate_history(con)
                con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            yield con

    def roll(self, *, turn_id, expected_revision, check_id, count=1, sides=20, modifier=0, purpose=""):
        world.id_value(turn_id, "turn id")
        world.id_value(check_id, "check id")
        revision_value(expected_revision)
        rng.validate_dice(count, sides, modifier)
        world.notes_value(purpose)
        spec = {"count": count, "sides": sides, "modifier": modifier, "purpose": purpose}
        contract = world.canonical(spec)
        with self.connection(write=True) as con:
            row = con.execute("SELECT * FROM rolls WHERE turn_id = ? AND check_id = ?",
                              (turn_id, check_id)).fetchone()
            if row is not None:
                if row["base_revision"] != expected_revision or row["spec"] != contract:
                    raise ConflictError("roll already recorded with a different contract")
                result = json.loads(row["result"])
            else:
                if journal_entry(con, "turn", turn_id) is not None:
                    raise ConflictError("turn already confirmed")
                current = state(con)
                require_revision(current, expected_revision)
                pending = reserve_turn(con, turn_id, expected_revision)
                if pending["packet"] is not None:
                    raise ConflictError("reopen the draft before adding a new roll")
                result = rng.draw(current["seed"], turn_id, check_id, spec)
                con.execute("INSERT INTO rolls VALUES (?, ?, ?, ?, ?)",
                            (turn_id, check_id, expected_revision, contract, world.canonical(result)))
                con.execute("UPDATE campaign SET generation = generation + 1")
            return {"turn_id": turn_id, "check_id": check_id, "base_revision": expected_revision,
                    "spec": spec, "result": result}

    def prepare(self, packet):
        turn_value(packet)
        turn_id = packet["id"]
        encoded = world.canonical(packet)
        with self.connection(write=True) as con:
            old = journal_entry(con, "turn", turn_id)
            if old is not None:
                if json.loads(old["details"])["packet_sha"] != world.sha_json(packet):
                    raise ConflictError("turn id already confirmed with different content")
                return confirmed_receipt(old)
            current = state(con)
            require_revision(current, packet["expected_revision"])
            pending = reserve_turn(con, turn_id, packet["expected_revision"])
            if pending["packet"] is not None:
                if pending["packet"] != encoded:
                    raise ConflictError("a different draft exists; reopen it by its receipt first")
                return draft_receipt(pending)

            # Validate the full candidate against live state, then retain only its exact draft.
            con.execute("SAVEPOINT candidate")
            try:
                apply_turn_events(con, current, packet)
            finally:
                con.execute("ROLLBACK TO candidate")
                con.execute("RELEASE candidate")
            generation = pending["generation"] + 1
            receipt = world.sha_json([generation, packet])
            con.execute("UPDATE pending SET packet = ?, draft_sha = ?, generation = ?",
                        (encoded, receipt, generation))
            con.execute("UPDATE campaign SET generation = generation + 1")
            return draft_receipt(con.execute("SELECT * FROM pending").fetchone())

    def reopen(self, turn_id, draft_sha):
        world.id_value(turn_id, "turn id")
        with self.connection(write=True) as con:
            pending = con.execute("SELECT * FROM pending WHERE id = ?", (turn_id,)).fetchone()
            if pending is None:
                raise ConflictError("no pending turn with this id")
            if pending["packet"] is not None:
                if pending["draft_sha"] != draft_sha:
                    raise ConflictError("draft receipt changed")
                con.execute("UPDATE pending SET packet = NULL, draft_sha = NULL")
                con.execute("UPDATE campaign SET generation = generation + 1")
            return {"status": "open", "turn_id": turn_id, "base_revision": pending["base_revision"]}

    def confirm(self, turn_id, draft_sha):
        world.id_value(turn_id, "turn id")
        with self.connection(write=True) as con:
            old = journal_entry(con, "turn", turn_id)
            if old is not None:
                result = confirmed_receipt(old)
                if result["draft_sha"] != draft_sha:
                    raise ConflictError("turn was confirmed with a different receipt")
                return result
            pending = con.execute("SELECT * FROM pending WHERE id = ?", (turn_id,)).fetchone()
            if pending is None or pending["packet"] is None or pending["draft_sha"] != draft_sha:
                raise ConflictError("no prepared draft matches this receipt")
            packet = open_draft(pending)
            current = state(con)
            require_revision(current, pending["base_revision"])
            apply_turn_events(con, current, packet)
            revision = current["revision"] + 1
            details = {"location": packet["location"], "packet_sha": world.sha_json(packet),
                       "draft_sha": draft_sha, "input_mode": packet.get("input_mode", "auto")}
            details.update({key: packet[key] for key in ("scene", "source_ref", "source_status")
                            if key in packet})
            append_entry(con, entry_id=turn_id, kind="turn", revision=revision,
                         session=current["session"], at=packet["at"], visibility="player_known",
                         body=packet["text"], details=details, player_input=packet["player_input"])
            con.execute("""UPDATE campaign SET revision = ?, at = ?, location = ?,
                                                generation = generation + 1""",
                        (revision, packet["at"], packet["location"]))
            con.execute("DELETE FROM pending")
            return confirmed_receipt(journal_entry(con, "turn", turn_id))

    def next_session(self, expected_revision):
        revision = revision_value(expected_revision) + 1
        with self.connection(write=True) as con:
            recorded = con.execute("SELECT * FROM sessions WHERE started_revision = ?", (revision,)).fetchone()
            if recorded is not None:
                return dict(recorded)
            current = state(con)
            require_revision(current, expected_revision)
            if con.execute("SELECT 1 FROM pending").fetchone() is not None:
                raise ConflictError("complete the working turn before opening another session")
            number = current["session"] + 1
            con.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                        (number, revision, current["at"], current["location"]))
            con.execute("""UPDATE campaign SET revision = ?, session = ?, generation = generation + 1""",
                        (revision, number))
            return {"number": number, "started_revision": revision,
                    "at": current["at"], "location": current["location"]}

    def context(self, *, audience="player", recent=3):
        audience_value(audience)
        limit_value(recent, 0, 50)
        with self.connection() as con:
            return self.read_context(con, audience=audience, recent=recent)

    def read_context(self, con, *, audience, recent):
        current = state(con)
        recent_turns = con.execute("SELECT * FROM journal WHERE kind = 'turn' ORDER BY seq DESC LIMIT ?",
                                   (recent,)).fetchall()
        context = {"campaign": json.loads(current["setup_json"]), "revision": current["revision"],
                   "session": current["session"], "at": current["at"], "location": current["location"],
                   "recent": [public_entry(row, audience) for row in reversed(recent_turns)]}
        if audience == "gm":
            next_process = con.execute("""SELECT id, owner, goal, due_at FROM processes
                                          WHERE status = 'active' ORDER BY due_at, id LIMIT 1""").fetchone()
            context["next_process"] = dict(next_process) if next_process is not None else None
            pending = con.execute("SELECT * FROM pending").fetchone()
            context["pending"] = self._pending_context(con, pending)
        return context

    def record_review(self, event, expected_revision):
        """Record a sourced correction, plan, or memory without adding fictional dialogue."""
        revision_value(expected_revision)
        world.validate_event(event)
        if not event.get("source_ref"):
            raise ValueError("review requires a primary source reference")
        allowed = ("remember", "assert_state", "set_fact", "delete_fact", "configure_process")
        if any(op["op"] not in allowed for op in event.get("operations", [])):
            raise ValueError("apply played consequences and knowledge through a game turn")
        digest = world.sha_json(event)
        with self.connection(write=True) as con:
            previous = journal_entry(con, "event", event["id"])
            if previous is not None:
                if json.loads(previous["details"]).get("review_sha") != digest:
                    raise ConflictError("review id already used with different content")
                return {"id": event["id"], "revision": previous["revision"], "sha256": digest}
            current = state(con)
            require_revision(current, expected_revision)
            if con.execute("SELECT 1 FROM pending").fetchone():
                raise ConflictError("resolve the working turn before applying a correction")
            if event["at"] != current["at"]:
                raise ValueError("a review is entered at the current scene time")
            revision = expected_revision + 1
            apply_event(con, event, player_id=json.loads(current["setup_json"])["player"]["id"],
                        revision=revision, session=current["session"])
            row = journal_entry(con, "event", event["id"])
            details = {**json.loads(row["details"]), "review_sha": digest}
            con.execute("UPDATE journal SET details=? WHERE seq=?",
                        (world.canonical(details), row["seq"]))
            con.execute("UPDATE campaign SET revision=?, generation=generation+1", (revision,))
            return {"id": event["id"], "revision": revision, "sha256": digest}

    @staticmethod
    def _pending_context(con, pending):
        if pending is None:
            return None
        rows = con.execute("SELECT * FROM rolls WHERE turn_id = ? ORDER BY check_id", (pending["id"],))
        result = {"turn_id": pending["id"], "base_revision": pending["base_revision"],
                  "rolls": [{"check_id": row["check_id"], "spec": json.loads(row["spec"]),
                             "result": json.loads(row["result"])} for row in rows]}
        if pending["packet"] is not None:
            result["draft_sha"] = pending["draft_sha"]
            result["packet"] = open_draft(pending)
        return result

    def world(self, *, audience="player", subject=None):
        audience_value(audience)
        if subject is not None:
            world.id_value(subject, "subject id")
        with self.connection() as con:
            result = {}
            for table, owner, value, order in (
                ("facts", "subject", "value", "subject, name"),
                ("beliefs", "actor", "belief", "actor, topic"),
                ("processes", "owner", "outcome", "due_at, id"),
            ):
                conditions, params = [], []
                if audience == "player":
                    conditions.append("visibility = 'player_known'")
                    if table == "beliefs":
                        conditions.append("actor = ?")
                        params.append(json.loads(state(con)["setup_json"])["player"]["id"])
                if subject is not None:
                    conditions.append(f"{owner} = ?")
                    params.append(subject)
                where = " WHERE " + " AND ".join(conditions) if conditions else ""
                rows = con.execute(f"SELECT * FROM {table}{where} ORDER BY {order}", params)
                result[table] = []
                for row in rows:
                    item = dict(row)
                    encoded = item.pop(value + "_json")
                    item[value] = json.loads(encoded) if encoded is not None else None
                    if audience == "player":
                        item.pop("source_event")
                        if table == "processes":
                            item.pop("notes")
                    result[table].append(item)
            if audience == "gm":
                if subject is None:
                    result["npcs"] = [dict(row) for row in con.execute("SELECT id, name FROM npcs ORDER BY id")]
                else:
                    npc = con.execute("SELECT * FROM npcs WHERE id = ?", (subject,)).fetchone()
                    result["npc"] = dict(npc) if npc is not None else None
                    if npc is not None:
                        result["npc"]["profile"] = json.loads(result["npc"].pop("profile_json"))
            return result

    def entry(self, kind, entry_id, *, audience="player"):
        audience_value(audience)
        world.id_value(entry_id, "journal entry id")
        if kind not in ("turn", "event"):
            raise ValueError("journal kind must be turn or event")
        with self.connection() as con:
            row = journal_entry(con, kind, entry_id)
            if row is None or (audience == "player" and row["visibility"] != "player_known"):
                raise ValueError("journal entry is unavailable")
            return public_entry(row, audience)

    def search(self, query, *, audience="player", limit=10, mode="all"):
        audience_value(audience)
        limit_value(limit, 1, 50)
        if mode not in ("all", "any") or not isinstance(query, str) or len(query) > 1000:
            raise ValueError("expected up to 1000 characters and search mode all or any")
        match = fts_expression(query, mode)
        visible = " AND j.visibility = 'player_known'" if audience == "player" else ""
        with self.connection() as con:
            rows = con.execute(f"""SELECT j.* FROM history_fts JOIN journal j ON j.seq = history_fts.rowid
                                   WHERE history_fts MATCH ?{visible}
                                   ORDER BY bm25(history_fts), j.seq DESC LIMIT ?""", (match, limit))
            return [public_entry(row, audience) for row in rows]

    def verify(self):
        with self.connection() as con:
            if (con.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or con.execute("PRAGMA foreign_key_check").fetchone() is not None):
                raise ValueError("campaign SQLite integrity check failed")
            current = state(con)
            if current is None:
                raise ValueError("campaign database has no campaign")
            pending = con.execute("SELECT * FROM pending").fetchone()
            if pending is not None:
                require_revision(current, pending["base_revision"])
                if pending["packet"] is not None:
                    open_draft(pending)
            return {"campaign_id": json.loads(current["setup_json"])["id"],
                    "instance": hashlib.sha256(current["seed"]).hexdigest(),
                    "revision": current["revision"], "generation": current["generation"],
                    "session": current["session"], "at": current["at"],
                    "pending": pending is not None, "ok": True}

    def checkpoint(self, destination):
        destination = Path(destination)
        with new_database(destination) as temporary:
            with self.connection() as source, closing(connect(temporary, write=True)) as target:
                source.backup(target)
            result = Campaign(temporary).verify()
            result["sha256"] = file_sha256(temporary)
        return {**result, "path": str(destination)}
