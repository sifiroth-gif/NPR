"""Player export, continuity receipts and safe intersession transition."""

import json
import os
from pathlib import Path
import tempfile

from . import continuity, transcripts, world, compaction
from .engine import ConflictError, require_revision, revision_value, state


def export_session(con, number):
    session = con.execute("SELECT * FROM sessions WHERE number=?", (number,)).fetchone()
    if session is None:
        raise ValueError("unknown session")
    current = state(con)
    following = con.execute("SELECT * FROM sessions WHERE number=?", (number + 1,)).fetchone()
    end = following["started_revision"] - 1 if following else current["revision"]
    title = json.loads(current["setup_json"])["title"]
    sections = [f"# {title}\n\nСессия {number} · ревизии {session['started_revision']}–{end}"]
    archive = transcripts.rows(con, number)
    if archive:
        for row in archive:
            source = json.loads(row["details"])["transcript"]
            speaker = "Игрок" if source["role"] == "player" else "Ведущий"
            sections.append(f"**{speaker}**\n\n{row['body']}")
        return ("\n\n".join(sections) + "\n").encode("utf-8")
    turn_rows = list(con.execute("""
        SELECT * FROM journal WHERE session=? AND kind='turn' ORDER BY seq
    """, (number,)))
    for row in compaction.recover_session(con, turn_rows, number):
        place = json.loads(row["details"])["location"]
        sections.append(f"## {row['at']} · {place} · {row['id']}\n\n"
                        f"**Игрок**\n\n{row['player_input']}\n\n**Ведущий**\n\n{row['body']}")
    return ("\n\n".join(sections) + "\n").encode("utf-8")


def write_export(path, data):
    path = Path(path)
    if os.path.lexists(path):
        if path.is_symlink() or path.read_bytes() != data:
            raise FileExistsError(f"refusing to replace a different export: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.link(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def close(game, expected, destination, transcript=None):
    revision_value(expected)
    with game.connection(write=True) as con:
        current = state(con)
        receipt = con.execute("SELECT * FROM session_exports WHERE revision=?", (expected,)).fetchone()
        if receipt:
            number = receipt["session"]
            if transcript is not None:
                stored = transcripts.rows(con, number)
                if not stored or json.loads(stored[0]["details"])["transcript"]["digest"] != world.sha_json(transcript):
                    raise ConflictError("session already closed with a different transcript")
        else:
            require_revision(current, expected)
            if con.execute("SELECT 1 FROM pending").fetchone():
                raise ConflictError("confirm the shown response before closing the session")
            if not con.execute("SELECT 1 FROM journal WHERE kind='turn' AND session=?",
                               (current["session"],)).fetchone():
                raise ValueError("cannot close an empty session")
            number = current["session"]
            if transcript is not None:
                start = con.execute("SELECT at FROM sessions WHERE number=?", (number,)).fetchone()[0]
                transcripts.validate(transcript, number, start, current["at"])
                transcripts.index(con, transcript, expected)
        data = export_session(con, number)
        digest = world.sha(data)
        if receipt and receipt["sha256"] != digest:
            raise ValueError("session export changed after closure")
        write_export(destination, data)
        if not receipt:
            con.execute("INSERT INTO session_exports VALUES (?, ?, ?)", (number, expected, digest))
            con.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                        (number + 1, expected + 1, current["at"], current["location"]))
            con.execute("UPDATE campaign SET revision=?, session=?, generation=generation+1",
                        (expected + 1, number + 1))
        if not receipt:
            from pathlib import Path
            export_path = Path(destination)
            try:
                source_path = str(export_path.resolve().relative_to(game.path.parent.resolve()))
            except ValueError:
                source_path = str(export_path.resolve())
            compaction.compact(con, number, archive_path=source_path,
                               archive_sha256=digest)
        return {"closed": number, "next_session": number + 1, "revision": expected + 1,
                "export": str(destination), "sha256": digest,
                "continuity": continuity.notes(con, limit=10)}
