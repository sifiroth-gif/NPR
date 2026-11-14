"""Index a reviewed, verbatim session transcript alongside the world journal."""

import json
import re

from . import world


FORMAT = "npr-session-transcript/1"
SOURCE_FIELD = "json_type(details,'$.transcript')='object'"
# The old turn receipts remain addressable by ID. In a session with a reviewed
# transcript, those receipts are no longer a second, competing history search.
PREFERRED_HISTORY = """(j.kind!='turn' OR NOT EXISTS (
    SELECT 1 FROM journal source WHERE source.kind='event'
    AND source.id='transcript.s' || j.session || '.1'
))"""


def source_id(session, position):
    return f"transcript.s{session}.{position}"


def validate(transcript, session, start, end):
    world.fields(transcript, ("format", "session", "source", "source_sha256", "passages"),
                 label="session transcript")
    if transcript["format"] != FORMAT or transcript["session"] != session:
        raise ValueError("transcript belongs to a different session or format")
    world.text_value(transcript["source"], "transcript source", 300)
    if not re.fullmatch(r"[a-f0-9]{64}", str(transcript["source_sha256"])):
        raise ValueError("transcript requires a source SHA-256")
    passages = world.sequence(transcript["passages"], "transcript passages", 20000)
    if not passages:
        raise ValueError("transcript is empty")
    previous = (0, 0)
    for passage in passages:
        world.fields(passage, ("role", "at", "ranges", "text", "entities"), label="transcript passage")
        if passage["role"] not in ("player", "gm"):
            raise ValueError("invalid transcript speaker")
        at = world.time_value(passage["at"], "transcript game time")
        if not start <= at <= end:
            raise ValueError("transcript passage is outside its session")
        world.text_value(passage["text"], "transcript text", 30000)
        ranges = world.sequence(passage["ranges"], "transcript source ranges", 100)
        if not ranges:
            raise ValueError("transcript passage needs source ranges")
        for span in ranges:
            if (not isinstance(span, list) or len(span) != 4
                    or any(type(i) is not int or i < 1 for i in span)
                    or tuple(span[:2]) < previous or tuple(span[2:]) <= tuple(span[:2])):
                raise ValueError("transcript source ranges overlap or are out of order")
            previous = tuple(span[2:])
        entities = world.sequence(passage["entities"], "transcript entity links", 60)
        for entity in entities:
            world.id_value(entity, "transcript entity")
        if len(entities) != len(set(entities)):
            raise ValueError("duplicate transcript entity link")


def index(con, transcript, revision):
    from .engine import append_entry

    session = transcript["session"]
    digest = world.sha_json(transcript)
    for position, passage in enumerate(transcript["passages"], 1):
        text = passage["text"]
        source = {key: transcript[key] for key in ("source", "source_sha256")}
        source.update({key: passage[key] for key in ("role", "ranges", "entities")})
        source["text_sha256"] = world.sha(text.encode("utf-8"))
        if position == 1:
            source["digest"] = digest
        append_entry(con, entry_id=source_id(session, position), kind="event", revision=revision,
                     session=session, at=passage["at"], visibility="player_known", body=text,
                     details={"transcript": source, "source_status": "exact"})


def rows(con, session):
    from . import compaction
    rows = list(con.execute(f"SELECT * FROM journal WHERE session=? AND kind='event' AND {SOURCE_FIELD} "
                            "ORDER BY seq", (session,)))
    return compaction.recover_session(con, rows, session)


def audit(con, session, revision, start, end):
    passages = rows(con, session)
    if not passages:
        return
    first = json.loads(passages[0]["details"])["transcript"]
    transcript = {"format": FORMAT, "session": session, "source": first["source"],
                  "source_sha256": first["source_sha256"], "passages": []}
    for position, row in enumerate(passages, 1):
        details = json.loads(row["details"])
        source = details["transcript"]
        if (set(details) != {"transcript", "source_status"} or details["source_status"] != "exact"
                or row["id"] != source_id(session, position) or row["revision"] != revision
                or row["visibility"] != "player_known" or row["player_input"]
                or source["source"] != first["source"] or source["source_sha256"] != first["source_sha256"]
                or world.sha(row["body"].encode("utf-8")) != source["text_sha256"]):
            raise ValueError("archived passage disagrees with its source receipt")
        transcript["passages"].append({"at": row["at"], "text": row["body"],
                                       **{key: source[key] for key in ("role", "ranges", "entities")}})
    validate(transcript, session, start, end)
    if first["digest"] != world.sha_json(transcript):
        raise ValueError("archived transcript digest does not match its receipt")
