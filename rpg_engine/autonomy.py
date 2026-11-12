"""Bounded NPC decisions on the campaign clock, with transactionally applied effects."""

from datetime import datetime, timedelta
import json

from . import world

SCHEMA = """
CREATE TABLE process_plans (
    id TEXT PRIMARY KEY REFERENCES processes(id),
    plan_json TEXT NOT NULL,
    source_event TEXT NOT NULL
);
CREATE TABLE messages (
    id TEXT PRIMARY KEY REFERENCES processes(id),
    spec TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('in_transit','delivered','failed')),
    source_event TEXT NOT NULL
);
"""


def validate_guard(guard):
    world.fields(guard, ("kind", "subject", "key", "value"), ("source_event",), label="guard")
    if guard["kind"] not in ("fact", "belief"):
        raise ValueError("guard kind must be fact or belief")
    world.id_value(guard["subject"], "guard subject")
    world.id_value(guard["key"], "guard key")
    world.json_value(guard["value"])
    if "source_event" in guard:
        world.id_value(guard["source_event"], "guard source")


def matches(con, guard):
    if guard["kind"] == "fact":
        sql = "SELECT value_json, source_event FROM facts WHERE subject=? AND name=?"
    else:
        sql = "SELECT belief_json, source_event FROM beliefs WHERE actor=? AND topic=?"
    row = con.execute(sql, (guard["subject"], guard["key"])).fetchone()
    return bool(row and row[0] == world.canonical(guard["value"])
                and guard.get("source_event", row[1]) == row[1])


def validate_plan(plan):
    world.fields(plan, ("premise", "options"), ("mainline", "boundary", "phase"),
                 label="process plan")
    world.text_value(plan["premise"], "process premise", 1200)
    if plan.get("boundary", "review") not in ("review", "hard"):
        raise ValueError("process boundary must be review or hard")
    if type(plan.get("mainline", False)) is not bool:
        raise ValueError("process mainline must be true or false")
    if "phase" in plan:
        world.text_value(plan["phase"], "process phase", 200)
    ids = set()
    for option in world.sequence(plan["options"], "process options", 12):
        world.fields(option, ("id", "intent", "status", "outcome"),
                     ("requires", "effects", "delay_seconds", "player_window"), label="process option")
        identifier = world.id_value(option["id"], "option id")
        if identifier in ids:
            raise ValueError("duplicate process option")
        ids.add(identifier)
        world.text_value(option["intent"], "NPC intent", 500)
        world.text_value(option["outcome"], "possible consequence", 1200)
        status = option["status"]
        if status not in ("active", "resolved", "cancelled"):
            raise ValueError("invalid process option status")
        delay = option.get("delay_seconds")
        if status == "active":
            if type(delay) is not int or not 1 <= delay <= 31622400:
                raise ValueError("continuing process requires a later review or completion")
        elif delay is not None:
            raise ValueError("terminal process cannot schedule another review")
        if type(option.get("player_window", False)) is not bool:
            raise ValueError("player_window must be true or false")
        for condition in world.sequence(option.get("requires", []), "option requirements", 30):
            validate_guard(condition)
        for effect in world.sequence(option.get("effects", []), "option effects", 30):
            if not isinstance(effect, dict) or effect.get("op") not in ("set_fact", "delete_fact"):
                raise ValueError("planned effects must be explicit world fact changes")
            world.validate_operation(effect, "0001-01-01T00:00:00")
    if not ids:
        raise ValueError("process plan needs a possible decision")


def validate_operation(op):
    if op["op"] == "configure_process":
        world.fields(op, ("op", "id", "plan"), ("previous",), label="configure_process")
        world.id_value(op["id"], "process id")
        validate_plan(op["plan"])
        if "previous" in op:
            world.id_value(op["previous"], "previous process plan")
    else:
        world.fields(op, ("op", "process", "option", "plan_source", "reason"),
                     ("window_resolved_by",), label="take_process_step")
        for key in ("process", "option", "plan_source"):
            world.id_value(op[key], key)
        world.text_value(op["reason"], "decision reason", 1200)
        if "window_resolved_by" in op:
            world.id_value(op["window_resolved_by"], "window event")


def configure(con, op, event):
    process = con.execute("SELECT * FROM processes WHERE id=? AND status='active'", (op["id"],)).fetchone()
    if process is None:
        raise ValueError("plan requires an existing active NPC process")
    old = con.execute("SELECT source_event FROM process_plans WHERE id=?", (op["id"],)).fetchone()
    if op.get("previous") != (old[0] if old else None):
        raise ValueError("updated process plan needs the current plan source")
    for option in op["plan"]["options"]:
        for guard in option.get("requires", []):
            if guard["kind"] == "belief" and guard["subject"] != process["owner"]:
                raise ValueError("NPC options may use only their own beliefs")
    con.execute("""INSERT INTO process_plans VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET plan_json=excluded.plan_json,
                   source_event=excluded.source_event""",
                (op["id"], world.canonical(op["plan"]), event["id"]))


def take_step(con, op, event, player_id):
    process = con.execute("""
        SELECT p.*, a.plan_json, a.source_event AS plan_source
        FROM processes p JOIN process_plans a USING(id)
        WHERE p.id=? AND p.status='active'
    """, (op["process"],)).fetchone()
    if process is None or process["plan_source"] != op["plan_source"]:
        raise ValueError("NPC decision has no current process plan")
    if event["at"] != process["due_at"]:
        raise ValueError("NPC decision must execute at its scheduled boundary")
    option = next((item for item in json.loads(process["plan_json"])["options"]
                   if item["id"] == op["option"]), None)
    if option is None or not all(matches(con, g) for g in option.get("requires", [])):
        raise ValueError("NPC decision is absent or its causal requirements failed")
    if option.get("player_window"):
        source = op.get("window_resolved_by")
        addressed = con.execute("""
            SELECT e.at FROM journal e JOIN journal t ON t.revision=e.revision
            WHERE e.kind='event' AND e.id=? AND e.visibility='player_known' AND t.kind='turn'
        """, (source,)).fetchone()
        if source not in event.get("based_on", []) or addressed is None or addressed["at"] != event["at"]:
            raise ValueError("show the player's intervention window before applying the outcome")
    outcome = {"option": option["id"], "outcome": option["outcome"], "reason": op["reason"],
               "plan_source": process["plan_source"], "repeated": 1}
    if option["status"] == "active":
        try:
            last = json.loads(process["notes"])
        except (TypeError, ValueError):
            last = {}
        if (isinstance(last, dict) and last.get("plan_source") == process["plan_source"]
                and last.get("option") == option["id"] and not option.get("effects")):
            outcome["repeated"] = last["repeated"] + 1
            if outcome["repeated"] > 3:
                raise ValueError("stalled NPC process needs a changed premise or a terminal decision")
        later = (datetime.fromisoformat(event["at"])
                 + timedelta(seconds=option["delay_seconds"])).isoformat()
        lifecycle = {"op": "reschedule_process", "id": process["id"], "due_at": later,
                     "notes": world.canonical(outcome)}
    else:
        lifecycle = {"op": "finish_process", "id": process["id"], "status": option["status"],
                     "outcome": outcome}
    world.apply_operations(con, {**event, "operations": [*option.get("effects", []), lifecycle]},
                           player_id, process_step=True)


def validate_message(op, at):
    if op["op"] == "deliver_message":
        world.fields(op, ("op", "id", "status"), label="deliver_message")
        if op["status"] not in ("delivered", "failed"):
            raise ValueError("message result must be delivered or failed")
    else:
        world.fields(op, ("op", "id", "sender", "recipient", "topic", "claim",
                          "channel", "deliver_at", "knowledge_source"),
                     ("requires", "deception_reason"), label="schedule_message")
        for key in ("sender", "recipient", "topic", "knowledge_source"):
            world.id_value(op[key], key)
        if op["sender"] == op["recipient"] or world.time_value(op["deliver_at"], "delivery") <= at:
            raise ValueError("message needs another recipient and future delivery")
        world.text_value(op["channel"], "message channel", 350)
        world.json_value(op["claim"])
        for guard in world.sequence(op.get("requires", []), "channel conditions", 20):
            validate_guard(guard)
            if guard["kind"] != "fact":
                raise ValueError("message channel conditions describe physical access")
        if "deception_reason" in op:
            world.text_value(op["deception_reason"], "deception motive", 650)
    world.id_value(op["id"], "message id")


def schedule_message(con, op, event, player_id):
    known = con.execute("""SELECT belief_json,source_event FROM beliefs
                           WHERE actor=? AND topic=?""", (op["sender"], op["topic"])).fetchone()
    if known is None or known["source_event"] != op["knowledge_source"]:
        raise ValueError("message sender has no matching personal source")
    if known["belief_json"] != world.canonical(op["claim"]) and "deception_reason" not in op:
        raise ValueError("message differs from what the sender knows; declare its motive")
    if op["recipient"] != player_id and not con.execute(
            "SELECT 1 FROM npcs WHERE id=?", (op["recipient"],)).fetchone():
        raise ValueError("unknown message recipient")
    timer = {"op": "start_process", "id": op["id"], "owner": op["sender"],
             "goal": "Deliver a message", "due_at": op["deliver_at"], "visibility": "gm_only",
             "notes": op["channel"]}
    world.apply_operations(con, {**event, "operations": [timer]}, player_id, process_step=True)
    con.execute("INSERT INTO messages VALUES (?,?,'in_transit',?)",
                (op["id"], world.canonical(op), event["id"]))


def deliver_message(con, op, event, player_id):
    row = con.execute("""
        SELECT m.*,p.due_at FROM messages m JOIN processes p USING(id)
        WHERE m.id=? AND m.status='in_transit' AND p.status='active'
    """, (op["id"],)).fetchone()
    if row is None or row["due_at"] != event["at"] or row["source_event"] not in event.get("based_on", []):
        raise ValueError("delivery must cite the scheduled message and execute at its due time")
    message = json.loads(row["spec"])
    delivered = all(matches(con, guard) for guard in message.get("requires", []))
    if delivered != (op["status"] == "delivered"):
        raise ValueError("delivery outcome conflicts with the actual channel")
    belief = {"op": "set_belief", "actor": message["recipient"], "topic": message["topic"],
              "belief": message["claim"], "evidence": "reported", "certainty": "probable",
              "visibility": "gm_only"}
    same_topic = [item for item in event.get("operations", []) if item.get("op") == "set_belief"
                  and (item["actor"], item["topic"]) == (message["recipient"], message["topic"])]
    if delivered and message["recipient"] != player_id:
        route = {"actor": message["recipient"], "via": "reported", "source": op["id"],
                 "source_actor": message["sender"]}
        if same_topic != [belief] or route not in event.get("knowledge_routes", []):
            raise ValueError("NPC delivery needs a sourced report in the recipient's knowledge")
    elif same_topic:
        raise ValueError("no personal belief is assigned by a failed or player-facing delivery")
    elif delivered and event["visibility"] != "player_known":
        raise ValueError("show the delivered message to the player without assigning a reaction")
    lifecycle = {"op": "finish_process", "id": op["id"],
                 "status": "resolved" if delivered else "cancelled", "outcome": {"delivery": op["status"]}}
    world.apply_operations(con, {**event, "operations": [lifecycle]}, player_id, process_step=True)
    con.execute("UPDATE messages SET status=?, source_event=? WHERE id=?",
                (op["status"], event["id"], op["id"]))


def queue(con, *, at, until, limit=12):
    """Forecast only; choosing an option or reading the queue never advances time."""
    ready = con.execute("PRAGMA user_version").fetchone()[0] >= 2
    plan_columns = "a.plan_json, a.source_event AS plan_source" if ready else "NULL AS plan_json, NULL AS plan_source"
    join = " LEFT JOIN process_plans a USING(id)" if ready else ""
    rows = con.execute(f"""
        SELECT p.*, {plan_columns} FROM processes p{join}
        WHERE p.status='active' AND p.due_at<=? ORDER BY p.due_at,p.id LIMIT ?
    """, (until, limit + 1)).fetchall()
    total_due = len(rows) > limit
    items = []
    for row in rows[:limit]:
        item = {k: row[k] for k in ("id", "owner", "goal", "due_at", "source_event", "notes")}
        if row["plan_json"] is not None:
            plan = json.loads(row["plan_json"])
            item.update({"plan_source": row["plan_source"], "premise": plan["premise"],
                         "phase": plan.get("phase"), "mainline": plan.get("mainline", False),
                         "boundary": plan.get("boundary", "review"),
                         "eligible": [option for option in plan["options"]
                                      if all(matches(con, g) for g in option.get("requires", []))]})
        if ready:
            message = con.execute("SELECT spec,source_event FROM messages WHERE id=? AND status='in_transit'",
                                  (row["id"],)).fetchone()
            if message:
                item.update({"boundary": "hard", "delivery": json.loads(message["spec"]),
                             "message_source": message["source_event"]})
        items.append(item)
    following = con.execute("""
        SELECT id,owner,goal,due_at FROM processes WHERE status='active'
        AND due_at>? ORDER BY due_at,id LIMIT 1
    """, (until,)).fetchone()
    return {"scene_at": at, "target_at": until, "due": items, "more_due": total_due,
            "next": dict(following) if following else None}
