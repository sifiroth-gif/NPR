"""Causal, continuous-time combat contract for NPR campaigns.

Ported in principle from Bleach-RPG-/docs/GM_RULES.md sections 8-9:
no invented round/HP economy, known ability != guaranteed hit,
opponent reaction only if physically possible, effects persist.
The GM authors capabilities, trajectories, anatomical consequences and
residual odds. This validates the contract; it does NOT automate Shiki.
"""
from __future__ import annotations

from copy import deepcopy
import math
from collections.abc import Mapping

from . import world

FRAME_FORMAT = "NPR-COMBAT-FRAME/1"
ATTEMPT_FORMAT = "NPR-COMBAT-ATTEMPT/1"
OUTCOME_FORMAT = "NPR-COMBAT-OUTCOME/1"

KINDS = {"move", "attack", "defend", "technique", "observe", "treat", "interact"}
MOBILITY = {"normal", "limited", "immobile"}
AWARENESS = {"alert", "impaired", "unconscious"}
RESOURCES = ("ready", "strained", "low", "depleted")
SEVERITY = {"minor", "moderate", "severe", "critical"}
RESULTS = {"success", "partial", "failure"}
PHASES = {"physical", "genjutsu", "ninjutsu", "medical", "sensory"}


class CombatContractError(ValueError):
    """Attempt, observation or injury is inconsistent with locked scene data."""


def _dict(value, label):
    if not isinstance(value, dict):
        raise CombatContractError(f"{label} must be an object")
    return value


def _int(value, label, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise CombatContractError(f"{label} must be integer >= {minimum}")
    return value


def _number(value, label, *, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise CombatContractError(f"{label} must be finite >= {minimum}")
    return float(value)


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CombatContractError(f"{label} must be nonempty text")
    return value


def _position(value, label):
    if not isinstance(value, list) or len(value) != 2:
        raise CombatContractError(f"{label} must be a 2D position in meters")
    return [_number(item, label) for item in value]


def _injuries(body, label):
    injuries = _dict(body, label)
    for site, injury in injuries.items():
        _nonempty(site, "injury anatomical site")
        _dict(injury, "injury")
        if injury.get("severity") not in SEVERITY:
            raise CombatContractError("injury severity invalid")
        if not isinstance(injury.get("functions_lost", []), list) or any(
            not isinstance(x, str) for x in injury.get("functions_lost", [])
        ):
            raise CombatContractError("injury functions_lost must be a list of text")
        _nonempty(injury.get("mechanism"), "injury mechanism")
    return injuries


def validate_frame(frame):
    _dict(frame, "combat frame")
    if frame.get("format") != FRAME_FORMAT:
        raise CombatContractError("unknown combat frame format")
    world.id_value(frame.get("id"), "combat frame id")
    world.id_value(frame.get("player_id"), "player id")
    _int(frame.get("at_ms"), "frame at_ms")
    actors = _dict(frame.get("actors"), "actors")
    if frame["player_id"] not in actors:
        raise CombatContractError("player must be listed, but has no autonomous actions")
    if not actors:
        raise CombatContractError("combat frame has no actors")
    for actor_id, data in actors.items():
        world.id_value(actor_id, "actor id")
        _dict(data, "actor state")
        _position(data.get("position_m"), f"{actor_id}.position_m")
        if data.get("consciousness") not in AWARENESS:
            raise CombatContractError("actor consciousness not established")
        if data.get("mobility") not in MOBILITY:
            raise CombatContractError("actor mobility not established")
        if data.get("chakra") not in RESOURCES or data.get("stamina") not in RESOURCES:
            raise CombatContractError("chakra/stamina must be explicit qualitative resource bands")
        perceived = data.get("perceives", [])
        if not isinstance(perceived, list) or any(not isinstance(x, str) for x in perceived):
            raise CombatContractError("perceives must be a list of actor IDs")
        skills = _dict(data.get("skills"), "skills")
        for skill, score in skills.items():
            _nonempty(skill, "skill name")
            if _number(score, "skill score") > 5:
                raise CombatContractError("skill score out of 0..5")
        techniques = data.get("learned", [])
        if not isinstance(techniques, list) or any(not isinstance(x, str) for x in techniques):
            raise CombatContractError("learned techniques must be list")
        _injuries(data.get("injuries", {}), "injuries")
        _int(data.get("ready_at_ms", frame["at_ms"]), "ready_at_ms")
        if "reaction_min_ms" in data:
            _int(data["reaction_min_ms"], "reaction_min_ms", minimum=1)
    for actor_id, data in actors.items():
        for target in data["perceives"]:
            if target not in actors or target == actor_id:
                raise CombatContractError("unknown or self perception target")
    return frame


def distance_m(frame, source, target):
    validate_frame(frame)
    actors = frame["actors"]
    if source not in actors or target not in actors:
        raise CombatContractError("distance target unknown to scene")
    a = actors[source]["position_m"]
    b = actors[target]["position_m"]
    return math.hypot(b[0] - a[0], b[1] - a[1])


def assess_attempt(frame, attempt):
    """Preflight only. Never calls RNG or presumes a successful hit."""
    validate_frame(frame)
    _dict(attempt, "attempt")
    if attempt.get("format") != ATTEMPT_FORMAT:
        raise CombatContractError("unknown attempt format")
    world.id_value(attempt.get("id"), "attempt id")
    who = attempt.get("actor")
    if who not in frame["actors"]:
        raise CombatContractError("unknown combat actor")
    actor = frame["actors"][who]
    kind = attempt.get("kind")
    if kind not in KINDS:
        raise CombatContractError("unknown attempt kind")
    started = _int(attempt.get("start_ms"), "start_ms")
    duration = _int(attempt.get("duration_ms"), "duration_ms", minimum=1)
    if started < frame["at_ms"]:
        raise CombatContractError("attack cannot begin before the scene frame")
    if started < actor.get("ready_at_ms", frame["at_ms"]):
        raise CombatContractError("actor is committed to a prior action")
    if actor["consciousness"] == "unconscious":
        raise CombatContractError("unconscious actor cannot take a voluntary action")
    if actor["mobility"] == "immobile" and kind == "move":
        raise CombatContractError("immobile actor cannot move voluntarily")
    _nonempty(attempt.get("intent"), "attempt intent")
    source = _dict(attempt.get("source"), "attempt source")
    if who == frame["player_id"]:
        if source.get("kind") != "player_declaration":
            raise CombatContractError("GM cannot invent a player voluntary action")
    elif source.get("kind") != "npc_intention":
        raise CombatContractError("NPC action needs its own intention and source")
    _nonempty(source.get("ref"), "player declaration or NPC agenda reference")

    ability = attempt.get("ability")
    if ability is not None:
        _nonempty(ability, "ability")
        if ability not in actor["skills"]:
            raise CombatContractError(f"unlearned skill {ability!r} cannot be used")
    technique = attempt.get("technique")
    if technique is not None and technique not in actor["learned"]:
        raise CombatContractError(f"unlearned technique {technique!r}")

    # Shunshin improves movement, not grapple/contact attack proficiency.
    if kind == "attack" and ability == "shunshin":
        raise CombatContractError("shunshin is locomotion, not taijutsu accuracy")
    if technique == "shunshin" and kind != "move":
        raise CombatContractError("shunshin must resolve as real movement first")
    if ability == "raiton" and kind in ("attack", "technique") and actor["skills"]["raiton"] < 1:
        raise CombatContractError("unstable student Raiton cannot be assumed battle-reliable")

    target = attempt.get("target")
    if target is not None and target not in frame["actors"]:
        raise CombatContractError("target not in locked combat frame")
    reach = attempt.get("reach_m")
    current_distance = None
    if kind == "attack":
        if target is None:
            raise CombatContractError("attack requires a target")
        if reach is None:
            raise CombatContractError("attack requires a grounded range")
        reach = _number(reach, "reach_m")
        current_distance = distance_m(frame, who, target)
        if current_distance > reach + 1e-9:
            raise CombatContractError(
                "target outside current reach; resolve movement separately before attack")
        if attempt.get("path_clear") is not True:
            raise CombatContractError("attack path is not confirmed clear")
    if kind == "move":
        goal = _position(attempt.get("destination_m"), "destination_m")
        if attempt.get("path_clear") is not True:
            raise CombatContractError("blocked/unknown path: shunshin is not teleportation")
        current_distance = math.dist(actor["position_m"], goal)
    if technique == "chakra_scalpel" and kind == "attack" and ability not in ("taijutsu", "chakra_scalpel"):
        raise CombatContractError("chakra scalpel contact requires grounded hand-to-hand skill")
    if technique and actor["chakra"] == "depleted":
        raise CombatContractError("depleted chakra does not sustain a chakra technique")

    target_state = frame["actors"].get(target)
    reaction = "none"
    if kind in ("attack", "technique") and target_state:
        if target_state["consciousness"] == "unconscious":
            reaction = "not_possible_unconscious"
        elif who not in target_state["perceives"]:
            reaction = "not_yet_detected"
        elif target_state.get("ready_at_ms", frame["at_ms"]) >= started + duration:
            reaction = "already_committed"
        elif target_state.get("reaction_min_ms") is None:
            reaction = "uncertain_no_grounded_timing"
        elif target_state["reaction_min_ms"] <= duration:
            reaction = "possible_not_guaranteed"
        else:
            reaction = "too_late_given_observed_window"

    return {
        "format":"NPR-COMBAT-PREFLIGHT/1","id":attempt["id"],
        "actor":who,"kind":kind,"start_ms":started,"end_ms":started+duration,
        "measured_distance_m":None if current_distance is None else round(current_distance, 4),
        "reaction_window":reaction,
        "outcome":"UNRESOLVED — player and NPC actions and chance remain causal",
        "note":"No automatic hit, dodge, knockout, paralysis, healing or player decision",
    }


def validate_bands(bands):
    if not isinstance(bands, list) or not bands:
        raise CombatContractError("roll bands required")
    bands = sorted(bands, key=lambda x:x.get("low", -1))
    last = 0
    for band in bands:
        _dict(band, "roll band")
        a = _int(band.get("low"), "roll low", minimum=1)
        b = _int(band.get("high"), "roll high", minimum=1)
        if a != last+1 or b < a or b > 100:
            raise CombatContractError("bands must partition 1..100 without gaps/overlap")
        if band.get("result") not in RESULTS:
            raise CombatContractError("invalid band result")
        last = b
    if last != 100:
        raise CombatContractError("bands must cover 1..100")
    return bands


def contested_roll(game, *, turn_id, expected_revision, check_id, bands, rationale):
    """Draw ONCE, after a causal probability contract was frozen.

    Existing Campaign.roll persists spec+seed for stable IDs and rejects
    reroll with altered bands/rationale. Do not call for clear outcomes.
    """
    world.id_value(check_id, "combat check id")
    _nonempty(rationale, "pre-roll basis and constraints")
    bands = validate_bands(bands)
    contract = world.canonical({"mechanic":"combat-band-v1", "bands":bands,
                                "rationale":rationale})
    if len(contract) > 4800:
        raise CombatContractError("roll contract explanation too large")
    roll = game.roll(turn_id=turn_id, expected_revision=expected_revision,
                     check_id=check_id, count=1, sides=100,
                     purpose=contract)
    number = roll["result"]["total"]
    category = next(b["result"] for b in bands if b["low"] <= number <= b["high"])
    return {"check_id":check_id,"result":category,
            "roll":roll,"bands":bands,"predeclared_basis":rationale}


def apply_outcome(frame, outcome):
    """Persist functional injuries and resources across actions, not fake HP.

    The caller may turn this new frame into an ordinary gm_only world fact.
    All externally observable consequences still need an appropriate scene.
    """
    validate_frame(frame)
    _dict(outcome, "outcome")
    if outcome.get("format") != OUTCOME_FORMAT:
        raise CombatContractError("unknown outcome format")
    world.id_value(outcome.get("id"), "outcome id")
    if outcome.get("result") not in RESULTS:
        raise CombatContractError("unknown combat result")
    _nonempty(outcome.get("cause"), "outcome cause")
    at_ms = _int(outcome.get("at_ms"), "outcome at_ms")
    if at_ms <= frame["at_ms"]:
        raise CombatContractError("combat outcome must advance time")
    changes = outcome.get("changes")
    if not isinstance(changes, list) or not changes:
        raise CombatContractError("outcome must contain causal changes")
    updated = deepcopy(frame)
    for patch in changes:
        _dict(patch, "combat change")
        actor_id = patch.get("actor")
        if actor_id not in updated["actors"]:
            raise CombatContractError("combat result actor not in scene")
        actor = updated["actors"][actor_id]
        allowed = {"actor","position_m","chakra","stamina","consciousness","mobility",
                   "injuries","treatments","ready_at_ms"}
        if set(patch) - allowed:
            raise CombatContractError("unknown consequence property")
        if "position_m" in patch:
            actor["position_m"] = _position(patch["position_m"], "consequence position")
        for resource in ("chakra","stamina"):
            if resource in patch:
                new = patch[resource]
                if new not in RESOURCES:
                    raise CombatContractError("invalid resource band")
                if RESOURCES.index(new) < RESOURCES.index(actor[resource]) and not patch.get("treatments"):
                    raise CombatContractError("resource cannot recover without explicit cause")
                actor[resource] = new
        if "consciousness" in patch:
            if patch["consciousness"] not in AWARENESS:
                raise CombatContractError("invalid consciousness")
            actor["consciousness"] = patch["consciousness"]
        if "mobility" in patch:
            if patch["mobility"] not in MOBILITY:
                raise CombatContractError("invalid mobility")
            actor["mobility"] = patch["mobility"]
        if "injuries" in patch:
            injuries = _injuries(patch["injuries"], "consequence injuries")
            for site, injury in injuries.items():
                previous = actor["injuries"].get(site)
                if previous and SEVERITY_LEVEL[injury["severity"]] < SEVERITY_LEVEL[previous["severity"]] and not patch.get("treatments"):
                    raise CombatContractError("injury cannot heal without treatment/time/cause")
                actor["injuries"][site] = deepcopy(injury)
        if "ready_at_ms" in patch:
            ready = _int(patch["ready_at_ms"], "ready_at_ms")
            if ready < at_ms:
                raise CombatContractError("ready_at_ms cannot predate consequence")
            actor["ready_at_ms"] = ready
        if "treatments" in patch:
            treatments = patch["treatments"]
            if not isinstance(treatments, list) or not treatments or any(
                not isinstance(x,str) or not x.strip() for x in treatments):
                raise CombatContractError("treatments need explicit causal explanation")
    updated["at_ms"] = at_ms
    return updated


SEVERITY_LEVEL = {"minor":1,"moderate":2,"severe":3,"critical":4}


def outcome_world_event(frame, *, event_id, at, summary):
    """Generate a PRIVATE persisted frame via NPR's existing world operations.

    This never automatically publishes unobservable enemy conditions to
    players, and never replaces the full narrative source record.
    """
    validate_frame(frame)
    world.id_value(event_id,"event id")
    world.time_value(at,"event time")
    world.text_value(summary,"event summary",4000)
    return {"id":event_id,"at":at,"summary":summary,"visibility":"gm_only",
            "operations":[{"op":"set_fact","subject":"combat:"+frame["id"],
                           "name":"frame","value":frame,"visibility":"gm_only"}]}
