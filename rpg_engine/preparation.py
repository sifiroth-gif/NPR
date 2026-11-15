"""One bounded, read-only packet for the next GM response."""

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from . import autonomy, continuity, world, scene_memory
from .engine import limit_value, state
from .history import recall, source_excerpt
from .transcripts import SOURCE_FIELD

MODES = ("focus", "scene", "montage", "combat", "handoff")
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")



def psychological_presence_contract(campaign_id, npcs, latest_player_input=''):
    """Non-optional GM scene cue for Shiki; independent of prose generation.

    This is a preparation-level contract rather than a lexical prose validator:
    phrases in a response cannot prove a psychologically credible reaction.
    """
    if campaign_id != "naruto-shiki":
        return None
    return {
        "priority": "P0",
        "required": True,
        "trigger": "Whenever Shiki appears, speaks or acts in another NPC's perception",
        "instruction": (
            "Show a DISTINCT, knowledge-bound psychological response through each "
            "relevant NPC's gaze, breathing, posture, chosen distance, voice and "
            "decisions. Relate it causally to Shiki's actual behavior, ability, "
            "reputation and their individual relationship. A hostile or wary NPC "
            "may resist, lie or exploit him. No uniform awe, no mind-reading Shiki, "
            "no repeated grief motifs or mechanical reaction lists."
        ),
        "latest_player_input": latest_player_input[:210],
        "actors": [
            {"id": npc["id"], "name": npc["name"],
             "cue": "Use individual source-bound goals/beliefs, not public GM omniscience",
             "known_topics": [item["topic"] for item in npc.get("current_beliefs", [])][:3],
             "known_source_events": [item["source_event"] for item in npc.get("current_beliefs", [])][:3],
             "independent_aim": npc.get("profile", {}).get("goal",
                                npc.get("profile", {}).get("goals",
                                npc.get("profile", {}).get("independent_actions")))}
            for npc in npcs
        ],
        "empty_cast_warning": (
            "No active NPCs were resolved; identify any visible or speaking NPC "
            "before narrating their response."
        ) if not npcs else None,
    }


def _head(game, con, recent):
    current = state(con)
    context = game.read_context(con, audience="gm", recent=recent)
    context["instance"] = hashlib.sha256(current["seed"]).hexdigest()
    context["generation"] = current["generation"]
    return context


def heading(at, location, calendar=None):
    value = dt.datetime.fromisoformat(at)
    if calendar:
        # Fictional world time skips may establish a newer canonical weekday.
        # Select the latest *explicit* era anchor without altering older scenes.
        epoch = calendar
        for candidate in calendar.get("weekday_anchors", []):
            since = world.time_value(candidate["anchor_at"], "calendar era anchor")
            if since <= at and (epoch is calendar or since > epoch["anchor_at"]):
                epoch = candidate
        anchor = dt.datetime.fromisoformat(world.time_value(epoch["anchor_at"], "calendar anchor"))
        if type(epoch["weekday"]) is not int or not 0 <= epoch["weekday"] < 7:
            raise ValueError("calendar weekday must be 0..6")
        weekday = (epoch["weekday"] + (value.date() - anchor.date()).days) % 7
        year = value.year + calendar["year_offset"]
    else:
        weekday, year = value.weekday(), value.year
    return f"{WEEKDAYS[weekday]}, {value.day:02}.{value.month:02}.{year:04}, {value:%H:%M} — {location}"


def _profile(con, actor, latest, *, detailed):
    npc = con.execute("SELECT * FROM npcs WHERE id=?", (actor,)).fetchone()
    if npc is None:
        return None
    profile = json.loads(npc["profile_json"])
    compact = ("role", "voice", "speech", "goal", "goals", "constraint",
               "knowledge_limits", "relations", "independent_actions", "autonomy")
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


def _plan(pack_path, pack, head, *, handoff=False):
    if pack_path is None or "session_files" not in pack:
        return None
    rel = pack["session_files"].format(session=head["session"])
    plan = (pack_path.parent / rel).resolve()
    if not plan.is_relative_to(pack_path.parent.resolve()) or not plan.is_file():
        return None
    data = json.loads(plan.read_text(encoding="utf-8"))
    current_plan = data.get("current_situations", {})
    use_current = current_plan.get("from_revision", head["revision"] + 1) <= head["revision"]
    result = {"source": rel, "status": "planning material", "title": data.get("title"),
              "objective": (current_plan["objective"] if use_current else
                            data.get("session_objective", data.get("objective", data.get("logline"))))}
    if handoff:
        result.update(start=data.get("start"), closure=data.get("closure"))
    else:
        result["situations"] = (current_plan["situations"] if use_current else
                                data.get("potential_situations", []))[:6]
    return result


def packet(game, *, mode="focus", actors=(), target=None, queries=(), recent=None, pack_path=None, player_action=None):
    if mode not in MODES:
        raise ValueError("unknown scene preparation mode")
    recent = {"focus": 2, "handoff": 1}.get(mode, 3) if recent is None else recent
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
        if npcs and pack_path and (relative := pack.get("npc_portrayal_file")):
            path = (pack_path.parent / relative).resolve()
            if not path.is_relative_to(pack_path.parent.resolve()):
                raise ValueError("NPC portrayal file is outside its campaign")
            portrayals = json.loads(path.read_text(encoding="utf-8"))
            for npc in npcs:
                if npc["id"] in portrayals:
                    npc["portrayal"] = portrayals[npc["id"]]
        scopes = (player_id, *actor_ids)
        active = autonomy.queue(con, at=head["at"], until=target)
        compact = mode in ("focus", "handoff")
        recent_turns = [
            {**{k: t[k] for k in ("id", "at", "location", "session", "revision", "source_status")},
             "source_ref": t.get("source_ref"),
             "player": source_excerpt(t["player_input"], width=280 if compact else 550),
             "gm": source_excerpt(t["text"], width=440 if compact else 1100),
             "scene": t.get("scene")}
            for t in head.pop("recent")
        ]
        # Working drafts must be exact. For a long played turn, resume from its
        # final beat and leave the full entry available by its stable ID.
        if mode == "handoff":
            last = game.read_context(con, audience="gm", recent=1)["recent"]
            if last and len(last[0]["text"]) > 840:
                turn = last[0]
                text = turn.pop("text")
                turn["tail"] = source_excerpt(text, bounds=(len(text) - 840, len(text)))
                turn["lookup"] = f"entry turn {turn['id']} --audience gm"
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
        for row in con.execute(f"""SELECT id,at,body,visibility FROM journal
                                 WHERE kind='event' AND revision=?
                                 AND NOT COALESCE(({SOURCE_FIELD}),0) ORDER BY seq DESC LIMIT 5""",
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
            "pinned_facts": pinned, "memory": continuity.notes(con, subjects=scopes, limit=4 if compact else 10),
            "world": active, "rules": (pack.get("fast_rules", pack.get("rules", []))
                                       if mode == "focus" else pack.get("rules", [])),
            "pacing": {"review_due": bool(active["due"]), "past_due": any(
                x["due_at"] < head["at"] for x in active["due"]),
                "routine_run": len(last_beats) >= 3 and all(b == "routine" for b in last_beats[-3:]),
                "focus": (recent_turns[-1].get("scene") or {}).get("focus") if recent_turns else None},
            "recall": [recall(con, query=query, limit=5) for query in queries],
        }
        presence = psychological_presence_contract(head["campaign"]["id"], npcs,
                  recent_turns[-1]["player"]["text"] if recent_turns else "")
        if presence is not None:
            result["npc_psychological_presence"] = presence
        if mode == "focus":
            # The old focus mode omitted the active plan entirely. Over a long
            # text-only session this caused the main plot to disappear from
            # operational attention. Include a *bounded* spine, not the entire plan.
            active_plan = _plan(pack_path, pack, head)
            if active_plan:
                result["scene_spine"] = {
                    "source": active_plan["source"],
                    "objective": active_plan.get("objective"),
                    "next_causes": [
                        {"id": beat.get("id"), "cause": beat.get("cause", beat.get("conflict")),
                         "stop": beat.get("stop")}
                        for beat in active_plan.get("situations", [])[:2]
                        if isinstance(beat, dict)
                    ],
                    "scene_advance_rule": (
                        "Resolve a causal consequence or new concrete pressure; "
                        "never ask a character to repeat information already confirmed. "
                        "Protect the player's significant choice.")
                }
        if mode in ("scene", "montage", "handoff"):
            result["session_plan"] = _plan(pack_path, pack, head, handoff=mode == "handoff")
        # Mandatory: recall runs on EVERY packet, not only when --query was supplied.
        # A newer private state worktree overrides packaged historical sources.
        source_root = Path(os.environ.get("NPR_CAMPAIGN_STATE_DIR",
                                        Path(__file__).resolve().parents[1]))
        campaign_slug = pack_path.parent.name if pack_path else head["campaign"]["id"].split("-")[-1]
        result["scene_memory"] = scene_memory.preflight(
            action=player_action if player_action is not None else (
                recent_turns[-1]["player"]["text"] if recent_turns else ""),
            location=head["location"], actors=[npc["name"] for npc in npcs],
            root=source_root, campaign=campaign_slug, con=con, limit=8)
        return result
