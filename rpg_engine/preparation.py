"""One bounded, read-only packet for the next GM response."""

import datetime as dt
import hashlib
import json
from pathlib import Path

from . import autonomy, continuity, world
from .engine import limit_value, state
from .history import recall, source_excerpt

MODES = ("focus", "scene", "montage", "combat", "handoff")
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def _head(game, con, recent):
    current = state(con)
    context = game.read_context(con, audience="gm", recent=recent)
    context["instance"] = hashlib.sha256(current["seed"]).hexdigest()
    context["generation"] = current["generation"]
    return context


def heading(at, location, calendar=None):
    value = dt.datetime.fromisoformat(at)
    if calendar:
        anchor = dt.datetime.fromisoformat(world.time_value(calendar["anchor_at"], "calendar anchor"))
        weekday = (calendar["weekday"] + (value.date() - anchor.date()).days) % 7
        year = value.year + calendar["year_offset"]
    else:
        weekday, year = value.weekday(), value.year
    return f"{WEEKDAYS[weekday]}, {value.day:02}.{value.month:02}.{year:04}, {value:%H:%M} — {location}"


def _profile(con, actor, latest, *, detailed):
    npc = con.execute("SELECT * FROM npcs WHERE id=?", (actor,)).fetchone()
    if npc is None:
        return None
    profile = json.loads(npc["profile_json"])
    compact = ("speech", "goals", "relations", "independent_actions")
    info = {"id": actor, "name": npc["name"], "source_event": npc["source_event"],
            "profile": profile if detailed else {key: profile[key] for key in compact if key in profile}}
    if detailed:
        info["knowledge"] = continuity.project_history(con, actor=actor, at=latest, limit=12)
    current = con.execute("""
        SELECT b.topic,b.belief_json,b.evidence,b.certainty,b.source_event
        FROM beliefs b LEFT JOIN journal j ON j.id=b.source_event AND j.kind='event'
        WHERE b.actor=? ORDER BY j.seq DESC,b.topic LIMIT ?
    """, (actor, 24 if detailed else 3))
    info["current_beliefs"] = [
        {**{k: row[k] for k in ("topic", "evidence", "certainty", "source_event")},
         **({"belief": json.loads(row["belief_json"])} if detailed or len(row["belief_json"]) <= 230
            else {"excerpt": row["belief_json"][:230]})}
        for row in current
    ]
    return info


def _actors(con, head, chosen, seed):
    if chosen:
        return list(dict.fromkeys(chosen)), "GM selection"
    recent = head["recent"][-1] if head["recent"] else {}
    if "scene" in recent:
        return recent["scene"]["actors"], recent["id"]
    if recent and seed.get("revision") == recent["revision"] and seed.get("turn") == recent["id"]:
        return seed.get("actors", []), seed["turn"]
    if recent:
        candidates = con.execute("""
            SELECT id,details FROM journal WHERE kind='turn' AND session=?
            AND json_extract(details,'$.location')=? ORDER BY seq DESC LIMIT 6
        """, (head["session"], head["location"]))
        for row in candidates:
            scene = json.loads(row["details"]).get("scene")
            if scene:
                return scene["actors"], f"{row['id']} (last declared cast at this location)"
    rows = con.execute("""SELECT details FROM journal WHERE kind='event' AND revision=?
                          AND visibility='player_known' ORDER BY seq DESC LIMIT 4""", (head["revision"],))
    actors = {}
    for row in rows:
        actors.update(dict.fromkeys(json.loads(row["details"]).get("witnesses", [])))
    # Witnesses are candidates; they may have left since the event.
    return list(actors)[:8], "recent witnesses; confirm who is still present"


def _plan(pack_path, pack, head):
    if pack_path is None or "session_files" not in pack:
        return None
    rel = pack["session_files"].format(session=head["session"])
    plan = (pack_path.parent / rel).resolve()
    if not plan.is_relative_to(pack_path.parent.resolve()) or not plan.is_file():
        return None
    data = json.loads(plan.read_text(encoding="utf-8"))
    current_plan = data.get("current_situations", {})
    use_current = current_plan.get("from_revision", head["revision"] + 1) <= head["revision"]
    return {"source": rel, "status": "planning material", "title": data.get("title"),
            "objective": (current_plan["objective"] if use_current else
                          data.get("session_objective", data.get("objective", data.get("logline")))),
            "situations": (current_plan["situations"] if use_current else
                           data.get("potential_situations", []))[:6]}


def packet(game, *, mode="focus", actors=(), target=None, queries=(), recent=None, pack_path=None):
    if mode not in MODES:
        raise ValueError("unknown scene preparation mode")
    recent = (2 if mode == "focus" else 3) if recent is None else recent
    limit_value(recent, 1, 8)
    if len(actors) > 10 or len(queries) > 4:
        raise ValueError("select at most 10 actors and 4 history questions")
    with game.connection() as con:
        head = _head(game, con, recent)
        pack = {}
        if pack_path:
            pack = json.loads(pack_path.read_text(encoding="utf-8"))
        else:
            for path in sorted(Path(__file__).resolve().parents[1].glob("campaigns/*/SCENE_PACK.json")):
                candidate = json.loads(path.read_text(encoding="utf-8"))
                if candidate.get("campaign_id") == head["campaign"]["id"]:
                    pack, pack_path = candidate, path
                    break
        if pack and pack.get("campaign_id") != head["campaign"]["id"]:
            raise ValueError("scene pack belongs to another campaign")
        setup_rules = head["campaign"].pop("rules")
        if not pack and len(world.canonical(setup_rules)) <= 1500:
            head["campaign"]["rules"] = setup_rules
        head.pop("next_process")
        target = world.time_value(target, "scene target") if target is not None else head["at"]
        if target < head["at"]:
            raise ValueError("scene target is before the confirmed scene")
        seed = pack.get("current_scene", {})
        actor_ids, actor_source = _actors(con, head, actors, seed)
        player_id = head["campaign"]["player"]["id"]
        for actor in actor_ids:
            world.id_value(actor, "scene actor")
        npcs = [_profile(con, actor, head["at"], detailed=mode in ("scene", "combat"))
                for actor in actor_ids if actor != player_id]
        if any(npc is None for npc in npcs):
            raise ValueError("scene context contains an unknown NPC")
        scopes = (player_id, *actor_ids)
        active = autonomy.queue(con, at=head["at"], until=target)
        recent_turns = [
            {**{k: t[k] for k in ("id", "at", "location", "session", "revision", "source_status")},
             "source_ref": t.get("source_ref"),
             "player": source_excerpt(t["player_input"], width=280 if mode == "focus" else 550),
             "gm": source_excerpt(t["text"], width=440 if mode == "focus" else 1100),
             "scene": t.get("scene")}
            for t in head.pop("recent")
        ]
        # Latest played response and pending draft are available in full on handoff.
        if mode == "handoff":
            last = game.read_context(con, audience="gm", recent=1)["recent"]
            head["last_confirmed_turn"] = last[0] if last else None
        if head.get("pending") and mode != "handoff":
            # Prepare/confirm uses the exact pending text; exposing it separately
            # prevents treating an unobserved draft as a played scene.
            head["pending"] = {**head["pending"], "status": "unconfirmed"}
        last_beats = [json.loads(row[0]).get("scene", {}).get("beat") for row in con.execute(
            "SELECT details FROM journal WHERE kind='turn' ORDER BY seq DESC LIMIT 3")]
        pinned = []
        for spec in pack.get("pin_facts", []):
            row = con.execute("""SELECT value_json,source_event FROM facts WHERE subject=? AND name=?""",
                              (spec["subject"], spec["name"])).fetchone()
            if row:
                pinned.append({**spec, "value": json.loads(row[0]), "source_event": row[1]})
        current_events = []
        for row in con.execute("""SELECT id,at,body,visibility FROM journal
                                 WHERE kind='event' AND revision=? ORDER BY seq DESC LIMIT 5""",
                               (recent_turns[-1]["revision"] if recent_turns else head["revision"],)):
            current_events.append({"id": row["id"], "at": row["at"], "visibility": row["visibility"],
                                   "summary": source_excerpt(row["body"], width=850)})
        repairs = [dict(row) for row in con.execute("""SELECT id,at,body FROM journal
                     WHERE kind='event' AND json_array_length(details,'$.supersedes')>0
                     ORDER BY seq DESC LIMIT 4""")]
        source_anchors = []
        for event_id in pack.get("anchors", []):
            row = con.execute("SELECT at, body FROM journal WHERE kind='event' AND id=?", (event_id,)).fetchone()
            if row:
                source_anchors.append({"id": event_id, "at": row["at"], "summary": source_excerpt(row["body"])})
        result = {
            "format": "npr-gm-context/1", "mode": mode, "confirmed": head, "heading": heading(
                head["at"], head["location"], pack.get("calendar")),
            "actors_source": actor_source, "actors": npcs, "recent_turns": recent_turns,
            "latest_events": current_events, "corrections": repairs, "campaign_anchors": source_anchors,
            "pinned_facts": pinned, "memory": continuity.notes(con, subjects=scopes, limit=10),
            "world": active, "rules": pack.get("rules", []),
            "pacing": {"review_due": bool(active["due"]), "past_due": any(
                x["due_at"] < head["at"] for x in active["due"]),
                "routine_run": len(last_beats) >= 3 and all(b == "routine" for b in last_beats[-3:]),
                "focus": (recent_turns[-1].get("scene") or {}).get("focus") if recent_turns else None},
            "recall": [recall(con, query=query, limit=5) for query in queries],
        }
        if mode in ("scene", "montage", "handoff"):
            result["session_plan"] = _plan(pack_path, pack, head)
        return result
