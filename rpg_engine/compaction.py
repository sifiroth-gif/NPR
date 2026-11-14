"""Compact confirmed *closed* sessions while retaining byte-exact, source-linked replay.

A single zlib-compressed session bundle holds verbatim narrative once in SQLite.
The journal keeps stable IDs, timestamps, visibility, causality, scene metadata and
short search surrogates. No operation touches an open session, active processes,
NPC knowledge or RNG. A canonical export remains in archive/session-NNN.md.
"""
import hashlib
import json
import re
import sqlite3
import zlib

from . import world

FORMAT = "npr-closed-journal-zlib/1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS archived_journals (
    session INTEGER PRIMARY KEY REFERENCES sessions(number),
    codec TEXT NOT NULL,
    payload BLOB NOT NULL,
    payload_sha256 TEXT NOT NULL,
    original_bytes INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    export_sha256 TEXT NOT NULL,
    archive_path TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS archived_words_fts USING fts5(
    words, content='', detail=none, tokenize='unicode61'
);
"""

def exists(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='archived_journals'").fetchone() is not None

def _utf8sha(data):
    return hashlib.sha256(data).hexdigest()

def _short(text, limit):
    if len(text) <= limit:
        return text
    prefix = round(limit * 0.67)
    suffix = limit - prefix - 32
    return text[:prefix] + "\n[… полный текст в архиве …]\n" + text[-suffix:]

def surrogate(body, player_input):
    return _short(body, 360), _short(player_input, 240)

def _archive(con, session):
    if not exists(con):
        return None
    header = con.execute("SELECT * FROM archived_journals WHERE session=?", (session,)).fetchone()
    if header is None:
        return None
    if header["codec"] != FORMAT:
        raise ValueError("unknown archived journal codec")
    try:
        plain = zlib.decompress(header["payload"])
    except zlib.error as exc:
        raise ValueError("corrupt closed-session archive") from exc
    if len(plain) != header["original_bytes"] or _utf8sha(plain) != header["payload_sha256"]:
        raise ValueError("closed-session archive content hash mismatch")
    rows = json.loads(plain)
    if not isinstance(rows, list) or len(rows) != header["row_count"]:
        raise ValueError("closed-session archive length mismatch")
    return header, {int(r["seq"]):r for r in rows}

def recover_row(row, values):
    if values is None:
        return row
    item = dict(row)
    if (item["id"] != values["id"] or item["kind"] != values["kind"] or
            item["session"] != values["session"]):
        raise ValueError("archived journal identifier mismatch")
    s_body, s_player = surrogate(values["body"], values["player_input"])
    if item["body"] != s_body or item["player_input"] != s_player:
        raise ValueError("archived journal synopsis or input differs from committed source")
    item["body"] = values["body"]
    item["player_input"] = values["player_input"]
    return item

def recover(con, row, cache=None):
    if not exists(con):
        return row
    number = row["session"]
    if cache is not None and number in cache:
        return recover_row(row, cache[number].get(row["seq"]))
    bundle = _archive(con, number)
    if bundle is None:
        return row
    if cache is not None:
        cache[number] = bundle[1]
    return recover_row(row, bundle[1].get(row["seq"]))

def recover_session(con, rows, session):
    bundle = _archive(con, session)
    payload = bundle[1] if bundle else {}
    return [recover_row(row, payload.get(row["seq"])) for row in rows]

def compact(con, session, *, archive_path, archive_sha256):
    """Compact only committed, closed source receipts, in the caller's transaction."""
    if not isinstance(session, int) or session < 1:
        raise ValueError("invalid compact session")
    if not exists(con):
        for command in SCHEMA.strip().split(";"):
            if command.strip():
                con.execute(command)
    prior = con.execute("SELECT session,row_count,original_bytes FROM archived_journals WHERE session=?",
                        (session,)).fetchone()
    if prior:
        return {"session":session,"already_compacted":True,"entries":prior["row_count"],
                "original_bytes":prior["original_bytes"]}
    receipt = con.execute("SELECT * FROM session_exports WHERE session=?", (session,)).fetchone()
    successor = con.execute("SELECT number FROM sessions WHERE number=?", (session+1,)).fetchone()
    current = con.execute("SELECT session FROM campaign WHERE singleton=1").fetchone()
    if not receipt or not successor or not current or session >= current["session"]:
        raise ValueError("only verified closed sessions can be compacted")
    if receipt["sha256"] != archive_sha256:
        raise ValueError("archive export hash disagrees with the session receipt")
    if not isinstance(archive_path,str) or not archive_path.strip() or len(archive_path)>1000:
        raise ValueError("closed session needs an unambiguous original archive path")
    affected=[]
    for row in con.execute("SELECT * FROM journal WHERE session=? ORDER BY seq",(session,)):
        if row["kind"] != "turn" and not (row["kind"] == "event" and
                 "transcript" in json.loads(row["details"])):
            continue
        b, p = surrogate(row["body"], row["player_input"])
        if (b,p)==(row["body"],row["player_input"]):
            continue
        affected.append({"seq":row["seq"],"id":row["id"],"kind":row["kind"],
                         "session":row["session"],"body":row["body"],
                         "player_input":row["player_input"]})
    plain=world.canonical(affected).encode("utf-8")
    packed=zlib.compress(plain,9)
    con.execute("INSERT INTO archived_journals VALUES (?,?,?,?,?,?,?,?)",
                (session,FORMAT,packed,_utf8sha(plain),len(plain),len(affected),
                 archive_sha256,archive_path))
    for row in affected:
        con.execute("INSERT INTO archived_words_fts(rowid,words) VALUES (?,?)",
                    (row["seq"],row["body"]+" "+row["player_input"]))
        b,p=surrogate(row["body"],row["player_input"])
        con.execute("UPDATE journal SET body=?,player_input=? WHERE seq=?",(b,p,row["seq"]))
    # Reindex from the short, still searchable journal surfaces; no full transcripts.
    con.execute("INSERT INTO history_fts(history_fts) VALUES ('rebuild')")
    return {"session":session,"already_compacted":False,"entries":len(affected),
            "original_bytes":len(plain),"packed_bytes":len(packed),
            "search_mode":"short_surrogate_with_exact_entry_hydration"}

def searchable(con):
    return (exists(con) and con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archived_words_fts'"
    ).fetchone() is not None)

def source_rows(con, expression, *, predicate, params=(), limit=40, correction=None):
    """Exact full-source inverted-index lookup without loading any old prose."""
    if not searchable(con):
        return []
    extra = f", {correction} AS superseded_by" if correction else ""
    return list(con.execute(f"""SELECT j.*{extra} FROM archived_words_fts
        JOIN journal j ON j.seq=archived_words_fts.rowid
        WHERE archived_words_fts MATCH ? AND {predicate}
        ORDER BY j.seq DESC LIMIT ?""", (expression, *params, limit)))

def audit(con, game_dir=None):
    if not exists(con):
        return []
    checked=[]
    for head in con.execute("SELECT session, export_sha256, archive_path,row_count FROM archived_journals ORDER BY session"):
        number=head["session"]
        record = con.execute("SELECT sha256 FROM session_exports WHERE session=?",(number,)).fetchone()
        if record is None or record[0] != head["export_sha256"]:
            raise ValueError("archived journal is not tied to its verified session export")
        item, sources = _archive(con,number)
        if len(sources) != head["row_count"]:
            raise ValueError("archived journal has duplicate source sequences")
        for seq,source in sources.items():
            row=con.execute("SELECT * FROM journal WHERE seq=? AND session=?",(seq,number)).fetchone()
            if row is None:
                raise ValueError("archived journal references missing row")
            recover_row(row,source)
            # The full-source term index must point to every compressed entry.
            if searchable(con):
                term=next(iter(re.findall(r"[^\W_]+",source["body"]+" "+source["player_input"])),None)
                if term and not con.execute("SELECT 1 FROM archived_words_fts WHERE rowid=? AND archived_words_fts MATCH ?",(seq,'"'+term+'"')).fetchone():
                    raise ValueError("archived fulltext index missing source token")
        if game_dir is not None:
            from pathlib import Path
            path=Path(head["archive_path"])
            if not path.is_absolute():
                path=Path(game_dir)/path
            if not path.is_file() or _utf8sha(path.read_bytes())!=head["export_sha256"]:
                raise ValueError("closed session primary archive is missing or changed")
        checked.append({"session":number,"rows":len(sources),"bytes":item["original_bytes"],
                        "compressed_bytes":len(item["payload"])})
    return checked

def compact_game(game, through=None):
    """Idempotent post-close migration; verifies archive before changing SQLite."""
    from . import sessions
    from pathlib import Path
    with game.connection(write=True) as con:
        current=con.execute("SELECT session FROM campaign WHERE singleton=1").fetchone()["session"]
        if through is None: through=current-1
        if type(through) is not int or through>=current or through<1:
            raise ValueError("compact range must end before the active session")
        results=[]
        for number in range(1,through+1):
            if exists(con) and con.execute("SELECT 1 FROM archived_journals WHERE session=?",(number,)).fetchone():
                results.append(compact(con,number,archive_path="existing",archive_sha256="existing"))
                continue
            receipt=con.execute("SELECT sha256 FROM session_exports WHERE session=?",(number,)).fetchone()
            if receipt is None:
                raise ValueError(f"session {number} has no closed export receipt")
            path=game.path.parent/"archive"/f"session-{number:03}.md"
            if not path.exists() or _utf8sha(path.read_bytes())!=receipt["sha256"]:
                raise ValueError(f"session {number} archive missing or SHA differs; compaction refused")
            results.append(compact(con,number,archive_path=str(path.relative_to(game.path.parent)),
                                   archive_sha256=receipt["sha256"]))
        return results
