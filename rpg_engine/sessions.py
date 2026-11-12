"""Player export, continuity receipts and safe intersession transition."""

import json
import os
from pathlib import Path
import tempfile

from . import continuity, world
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
    for row in con.execute("""
        SELECT * FROM journal WHERE session=? AND kind='turn' ORDER BY seq
    """, (number,)):
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


def close(game, expected, destination):
    revision_value(expected)
    with game.connection(write=True) as con:
        current = state(con)
        receipt = con.execute("SELECT * FROM session_exports WHERE revision=?", (expected,)).fetchone()
        if receipt:
            number = receipt["session"]
        else:
            require_revision(current, expected)
            if con.execute("SELECT 1 FROM pending").fetchone():
                raise ConflictError("confirm the shown response before closing the session")
            if not con.execute("SELECT 1 FROM journal WHERE kind='turn' AND session=?",
                               (current["session"],)).fetchone():
                raise ValueError("cannot close an empty session")
            number = current["session"]
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
        return {"closed": number, "next_session": number + 1, "revision": expected + 1,
                "export": str(destination), "sha256": digest,
                "continuity": continuity.notes(con, limit=10)}
