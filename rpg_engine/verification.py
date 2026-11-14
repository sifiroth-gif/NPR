"""Full consistency checks for a checkpoint or a session boundary."""

from contextlib import closing
import json
import sqlite3

from . import autonomy, continuity, rng, transcripts, world, compaction
from .engine import SCHEMA, SCHEMA_VERSION, state


def audit(game):
    verified = game.verify()
    with game.connection() as con, closing(sqlite3.connect(":memory:")) as replay:
        current = state(con)
        if (current["revision"], current["generation"]) != (verified["revision"], verified["generation"]):
            raise ValueError("campaign changed during audit; retry the current revision")
        replay.row_factory = sqlite3.Row
        replay.execute("PRAGMA foreign_keys=ON")
        replay.executescript(f"PRAGMA user_version={SCHEMA_VERSION};" + SCHEMA + world.SCHEMA
                             + continuity.SCHEMA + autonomy.SCHEMA)
        sessions = list(con.execute("SELECT * FROM sessions ORDER BY number"))
        setup = json.loads(current["setup_json"])
        revisions = set()
        session_starts = {}
        for number, row in enumerate(sessions, 1):
            if row["number"] != number or row["started_revision"] > current["revision"]:
                raise ValueError("invalid session sequence")
            if number > 1:
                revisions.add(row["started_revision"])
                session_starts[row["started_revision"]] = row
            elif (row["started_revision"], row["at"], row["location"]) != (0, setup["start_at"], setup["location"]):
                raise ValueError("initial scene differs from campaign setup")
            replay.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", tuple(row))
        at, location, revision, session = setup["start_at"], setup["location"], 0, 1
        events_by_revision, turns, reviews = {}, {}, {}
        transcript_sessions = set()
        for row in con.execute("SELECT * FROM journal ORDER BY seq"):
            values = dict(row)
            columns = ",".join(values)
            placeholders = ",".join("?" for _ in values)
            replay.execute(f"INSERT INTO journal ({columns}) VALUES ({placeholders})", tuple(values.values()))
            details = json.loads(row["details"])
            continuity.index_entry(replay, row["seq"], row["kind"], details)
            if "transcript" in details:
                if row["kind"] != "event":
                    raise ValueError("transcript must be indexed as a source event")
                transcript_sessions.add(row["session"])
                continue
            if row["kind"] == "event":
                event = {k: v for k, v in details.items() if k != "review_sha"}
                event.update({"id": row["id"], "at": row["at"], "summary": row["body"],
                              "visibility": row["visibility"]})
                for ref in (*event.get("based_on", []), *event.get("supersedes", [])):
                    source = replay.execute("SELECT seq FROM journal WHERE kind='event' AND id=?", (ref,)).fetchone()
                    if not source or source["seq"] >= row["seq"]:
                        raise ValueError(f"missing or forward source for {row['id']}")
                # Older reviewed imports retain their source even if they
                # predate the current provenance format.
                world.apply_operations(replay, event, setup["player"]["id"])
                events_by_revision.setdefault(row["revision"], []).append(row)
                if "review_sha" in details:
                    reviews[row["revision"]] = row
            else:
                if row["revision"] in turns:
                    raise ValueError("multiple player turns at one revision")
                turns[row["revision"]] = row
        for index in range(1, current["revision"] + 1):
            choices = sum(index in collection for collection in (session_starts, turns, reviews))
            if choices != 1:
                raise ValueError(f"revision {index} has no unique owner")
            if index in session_starts:
                row = session_starts[index]
                if (row["at"], row["location"]) != (at, location) or index in events_by_revision:
                    raise ValueError("session opened on a different scene")
                session += 1
            elif index in turns:
                row = turns[index]
                if row["session"] != session or row["at"] < at:
                    raise ValueError("journal scene time or session is inconsistent")
                for event in events_by_revision.get(index, ()):
                    if event["session"] != session or not at <= event["at"] <= row["at"]:
                        raise ValueError(f"event {event['id']} is outside the confirmed scene window")
                    at = event["at"]
                at, location = row["at"], json.loads(row["details"])["location"]
            else:
                row = reviews[index]
                if row["at"] != at or row["session"] != session or len(events_by_revision[index]) != 1:
                    raise ValueError("editorial review changed the game clock or combined with played events")
            revision = index
        if not set(events_by_revision).issubset({0, *turns, *reviews}):
            raise ValueError("orphaned event revision")
        if (revision, session, at, location) != tuple(current[k] for k in ("revision", "session", "at", "location")):
            raise ValueError("current scene cannot be reconstructed from confirmed history")
        tables = ["npcs", "facts", "beliefs", "processes"]
        if continuity.available(con):
            tables += ["memory", "process_plans", "messages", "journal_entities", "journal_edges",
                       "journal_corrections"]
        for table in tables:
            actual = {tuple(row) for row in con.execute(f"SELECT * FROM {table}")}
            rebuilt = {tuple(row) for row in replay.execute(f"SELECT * FROM {table}")}
            if actual != rebuilt:
                raise ValueError(f"{table} projection disagrees with the confirmed journal")
        if continuity.available(con):
            from .sessions import export_session
            for receipt in con.execute("SELECT * FROM session_exports"):
                start = session_starts.get(receipt["revision"] + 1)
                if (start is None or start["number"] != receipt["session"] + 1
                        or world.sha(export_session(con, receipt["session"])) != receipt["sha256"]):
                    raise ValueError("closed session has an invalid export receipt")
                number = receipt["session"]
                if number in transcript_sessions:
                    transcripts.audit(con, number, receipt["revision"], sessions[number - 1]["at"], start["at"])
                    transcript_sessions.remove(number)
        if transcript_sessions:
            raise ValueError("transcript has no corresponding closed session")
        archived = compaction.audit(con)
        checks = 0
        pending = con.execute("SELECT id,base_revision FROM pending").fetchone()
        for row in con.execute("SELECT * FROM rolls"):
            spec = json.loads(row["spec"])
            rng.validate_dice(spec["count"], spec["sides"], spec["modifier"])
            if json.loads(row["result"]) != rng.draw(current["seed"], row["turn_id"], row["check_id"], spec):
                raise ValueError("RNG result disagrees with the saved contract")
            turn = turns.get(row["base_revision"] + 1)
            if not ((turn and turn["id"] == row["turn_id"]) or
                    (pending and tuple(pending) == (row["turn_id"], row["base_revision"]))):
                raise ValueError("RNG reservation has no corresponding played or pending turn")
            checks += 1
        with closing(sqlite3.connect(":memory:")) as index_copy:
            con.backup(index_copy)
            index_copy.execute("INSERT INTO history_fts(history_fts,rank) VALUES ('integrity-check',1)")
        return {**verified, "full_audit": True, "turns": len(turns), "events": sum(
            len(items) for items in events_by_revision.values()), "rolls": checks, "compacted_sessions": len(archived)}
