"""Optional offline embedding cache. All returned passages come from the live journal."""

from contextlib import closing
from hashlib import sha256
import heapq
import math
import os
from pathlib import Path
import re
import sqlite3
import struct
import tempfile

from . import world
from .engine import state

FORMAT = "npr-recall/1"
WIDTH, STEP = 560, 430
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE vectors (
    seq INTEGER NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    source_sha TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(seq,start)
);
"""


def model_id(path):
    path = Path(path).resolve(strict=True)
    if not path.is_dir():
        raise ValueError("an offline model directory is required")
    digest = sha256()
    count = 0
    for entry in sorted(path.rglob("*")):
        relative = entry.relative_to(path)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if entry.is_symlink():
            raise ValueError("model files must be materialized, not symlinked")
        if not entry.is_file():
            continue
        count += 1
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        contents = sha256()
        with entry.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                contents.update(chunk)
        digest.update(contents.digest())
    if not count:
        raise ValueError("offline model directory is empty")
    return digest.hexdigest()


def _encoder(model, expected):
    if not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
        raise ValueError("the model SHA-256 must be pinned")
    if model_id(model) != expected:
        raise ValueError("offline model directory differs from the pinned SHA-256")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ValueError("optional offline recall requires sentence-transformers") from exc
    encoder = SentenceTransformer(str(Path(model).resolve()), device="cpu",
                                  local_files_only=True, trust_remote_code=False)
    return lambda texts: encoder.encode(texts, normalize_embeddings=True, show_progress_bar=False)


def _pack(vector):
    values = list(vector)
    if not 1 <= len(values) <= 2048 or not all(math.isfinite(x) for x in values):
        raise ValueError("embedding dimensions or values are invalid")
    return struct.pack(f"<{len(values)}f", *values)


def _unpack(data):
    return struct.unpack(f"<{len(data)//4}f", data)


def _manifest(con, expected):
    current = state(con)
    row = con.execute("SELECT max(seq), count(*) FROM journal").fetchone()
    # Exact source hashes on individual passages provide the final check.
    return {"format": FORMAT, "model": expected, "instance": world.sha(current["seed"]),
            "last_seq": str(row[0] or 0), "count": str(row[1])}


def _cache(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def build(game, cache, model, expected):
    cache = Path(cache)
    if cache.resolve() == game.path.resolve():
        raise ValueError("refusing to use the campaign database as an embedding cache")
    cache.parent.mkdir(parents=True, exist_ok=True)
    with game.connection() as source, tempfile.TemporaryDirectory(
            prefix=".recall-", dir=cache.parent) as tmp:
        manifest = _manifest(source, expected)
        previous = {}
        if cache.exists():
            if cache.is_symlink():
                raise ValueError("refusing to replace a symlinked cache")
            with closing(_cache(cache)) as saved:
                meta = dict(saved.execute("SELECT * FROM meta"))
                if meta.get("format") != FORMAT:
                    raise ValueError("refusing to replace a foreign database")
                if meta == manifest and model_id(model) == expected:
                    return {"status": "current", **manifest}
                if (meta.get("model"), meta.get("instance")) == (expected, manifest["instance"]):
                    previous = {(r["seq"], r["start"], r["end"], r["source_sha"]): r["vector"]
                                for r in saved.execute("SELECT * FROM vectors")}
        encoder = None
        reused = encoded = 0
        candidate = Path(tmp) / "cache.sqlite"
        with closing(sqlite3.connect(candidate)) as index, index:
            index.executescript(SCHEMA)
            index.executemany("INSERT INTO meta VALUES (?,?)", manifest.items())
            for row in source.execute("SELECT seq,body,player_input FROM journal ORDER BY seq"):
                # Separate overlapping passages for response and player input.
                body = row["body"] + "\n" + row["player_input"]
                digest = world.sha(body.encode("utf-8"))
                pending = []
                for start in range(0, len(body), STEP):
                    end = min(len(body), start + WIDTH)
                    key = (row["seq"], start, end, digest)
                    if key in previous:
                        index.execute("INSERT INTO vectors VALUES (?,?,?,?,?)", (*key, previous[key]))
                        reused += 1
                    else:
                        pending.append((key, body[start:end]))
                    if end == len(body):
                        break
                if pending:
                    if encoder is None:
                        encoder = _encoder(model, expected)
                    vectors = encoder(["passage: " + text for _, text in pending])
                    for (key, _), vector in zip(pending, vectors, strict=True):
                        index.execute("INSERT INTO vectors VALUES (?,?,?,?,?)", (*key, _pack(vector)))
                        encoded += 1
            if index.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("recall index failed its integrity check")
        os.replace(candidate, cache)
    return {"status": "updated", "encoded": encoded, "reused": reused, **manifest}


def candidates(con, cache, model, expected, query, *, audience, limit, session,
               since, until, entities, include_superseded):
    from .history import _conditions

    with closing(_cache(cache)) as index:
        if dict(index.execute("SELECT * FROM meta")) != _manifest(con, expected):
            raise ValueError("offline recall cache is out of date; run semantic-index explicitly")
        query_vector = list(_encoder(model, expected)(["query: " + query])[0])
        condition, args, _, _ = _conditions(
            con, audience=audience, entities=entities, session=session, since=since, until=until,
            include_superseded=include_superseded)
        permitted = {row["seq"]: row for row in con.execute(
            f"SELECT j.seq,j.body,j.player_input FROM journal j WHERE {condition}", args)}
        best = {}
        for row in index.execute("SELECT * FROM vectors"):
            source = permitted.get(row["seq"])
            if source is None:
                continue
            body = source["body"] + "\n" + source["player_input"]
            if row["source_sha"] != world.sha(body.encode("utf-8")):
                raise ValueError("embedding source changed; rebuild the recall index")
            if not 0 <= row["start"] < row["end"] <= len(body):
                raise ValueError("embedding passage lies outside the confirmed source")
            vector = _unpack(row["vector"])
            if len(vector) != len(query_vector):
                raise ValueError("embedding dimensions do not match the local model")
            score = sum(a * b for a, b in zip(vector, query_vector))
            start, end = row["start"], row["end"]
            value = (score, -start, end)
            if row["seq"] not in best or value > best[row["seq"]]:
                best[row["seq"]] = value
        selected = heapq.nlargest(limit, best, key=lambda seq: (best[seq], seq))
        return {seq: (-best[seq][1], best[seq][2]) for seq in selected}
