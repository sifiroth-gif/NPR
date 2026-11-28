"""Actor-local comprehension and affect, independent of factual access and dread.

The draw limits interpretation, never establishes truth. Fictional calibration
is not a psychometric model. Historical turns remain replayable.
"""
from __future__ import annotations

import hashlib
import re

CUTOVER = (18, 102)
LEVELS = ("received", "partial", "hypothesis", "understood")
EVIDENCE = ("explicit", "signs", "opaque")
NOVELTY = {"routine": 7, "new": 13, "disruptive": 19}
DOMAINS = ("general", "medicine", "puppetry", "strategy", "politics",
           "seals", "ninjutsu", "social", "research", "snakes", "sensory")
AFFECT = ("surprise", "confusion", "curiosity", "doubt", "fear", "anger",
          "joy", "denial", "interest", "calm", "none")
SALIENCE = {"ordinary": 0, "personal": 4, "existential": 8}
NOVELTY_AFFECT = {"routine": 0, "new": 4, "disruptive": 8}
REACTIVITY = {"kankuro": 1, "gaara": -1, "shikamaru": -1, "tsunade": 1,
              "orochimaru": -1, "kabuto": -1, "sakura": 1, "kakashi": -1,
              "jiraiya": 1, "chiyo": 0, "western_nest_young_courier": 1}

# Conservative campaign defaults; unknown expertise is not granted by rank.
# Bonus is local to the domain, not a global genius trait.
PROFILES = {
    "kankuro": (0, ("puppetry",)),
    "gaara": (1, ("politics",)),
    "shikamaru": (2, ("strategy",)),
    "tsunade": (1, ("medicine", "politics")),
    "orochimaru": (2, ("research", "ninjutsu", "snakes")),
    "kabuto": (1, ("medicine", "research")),
    "sakura": (0, ("medicine",)),
    "kakashi": (1, ("strategy", "ninjutsu")),
    "jiraiya": (1, ("seals", "ninjutsu")),
    "chiyo": (1, ("puppetry", "medicine")),
    "western_nest_young_courier": (0, ("snakes",)),
}

RULE = (
    "Новый значимый факт/новая трактовка NPC: d.cognition[actor] "
    "{topic,domain,novelty,evidence,stimulus,outcome,interpretation,affect,visible,basis,salience}. "
    "topic — стабильный ключ; stimulus/visible — точные фрагменты сцены. "
    "Прямой факт received не требует успеха. Для понимания: roll + aptitude +4 за domain из domains; "
    "порог routine 7/new 13/disruptive 19. Ниже порога на 5+: received; на 1–4: partial; "
    "достиг: hypothesis; explicit и на 4 выше: understood. signs максимум hypothesis, opaque максимум partial. "
    "Это потолок, не обязательный вывод и не доказательство тайны. "
    "Аффект отдельно: affect_roll + reactivity + новизна (routine/new/disruptive: 0/4/8) "
    "+ salience (ordinary/personal/existential: 0/4/8); <17=0, 17–23=1, ≥24=2; routine+ordinary=0, disruptive ≥1. "
    "Вид: surprise/confusion/curiosity/doubt/fear/anger/joy/denial/interest/calm/none. "
    "При силе ≥1 none нельзя; при 2 или disruptive calm требует basis. Повтор улики не навязывает новое потрясение. "
    "NPC вне состава — new_profiles или new_roll/new_affect_roll; при отставании не используй старые кости."
)


def enabled(turn_id):
    m = re.fullmatch(r"s(\d+)\.text\.(\d+)", str(turn_id or ""))
    return bool(m and tuple(map(int, m.groups())) >= CUTOVER)


def norm(value):
    return " ".join(str(value or "").split())


def profile(actor):
    aptitude, domains = PROFILES.get(actor, (0, ()))
    return {"aptitude": aptitude, "domains": list(domains), "reactivity": REACTIVITY.get(actor, 0)}


def draw(turn_id, actor=""):
    # Separate deterministic lane: never consume combat or world-plan dice.
    raw = hashlib.sha256(f"npc-cognition/1|{turn_id}|{actor}".encode()).digest()
    return int.from_bytes(raw[:8], "big") % 20 + 1


def affect_draw(turn_id, actor=""):
    raw = hashlib.sha256(f"npc-affect/1|{turn_id}|{actor}".encode()).digest()
    return int.from_bytes(raw[:8], "big") % 20 + 1


def actor_key(actor, prior):
    # Known newcomers retain their own draw; anonymous newcomers use the
    # published fallback rather than silently inventing an unseen die.
    return actor if actor in prior or actor in PROFILES else ""


def newcomer_packet(turn_id):
    return {"for": turn_id, "new_roll": draw(turn_id), "new_affect_roll": affect_draw(turn_id),
            "new_profiles": {k: {**profile(k), "roll": draw(turn_id, k),
                                 "affect_roll": affect_draw(turn_id, k)} for k in PROFILES},
            "rule": RULE}


def affect_strength(roll, actor, novelty, salience="ordinary", *, repeated=False):
    if repeated:
        return 0  # an identical stimulus cannot compel another emotional jolt
    if novelty == "routine" and salience == "ordinary":
        return 0
    score = roll + REACTIVITY.get(actor, 0) + NOVELTY_AFFECT[novelty] + SALIENCE[salience]
    return max(1 if novelty == "disruptive" else 0, 0 if score < 17 else 1 if score < 24 else 2)


def ceiling(roll, actor, domain, novelty, evidence):
    p = profile(actor)
    score = roll + p["aptitude"] + (4 if domain in p["domains"] else 0)
    margin = score - NOVELTY[novelty]
    limit = 0 if margin <= -5 else 1 if margin < 0 else 2
    if evidence == "explicit" and margin >= 4:
        limit = 3
    if evidence == "opaque":
        limit = min(limit, 1)
    return limit


def rows(turn):
    return (turn.get("d") or {}).get("cognition") or {}


def validate(turn):
    value = (turn.get("d") or {}).get("cognition")
    if value is None:
        return
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError("d.cognition: actor -> object (max 16)")
    if turn.get("kind") in ("ooc", "technical", "void") and value:
        raise ValueError("d.cognition: only played scenes")
    for actor, row in value.items():
        if not isinstance(actor, str) or not actor or actor == "shiki" or not isinstance(row, dict):
            raise ValueError("d.cognition: invalid NPC")
        for field in ("topic", "stimulus"):
            if not isinstance(row.get(field), str) or not norm(row[field]) or len(row[field]) > 240:
                raise ValueError(f"d.cognition.{actor}.{field}: required text <=240")
        if not re.fullmatch(r"[a-z0-9_]{1,64}", row["topic"]):
            raise ValueError("d.cognition.topic: stable ASCII identifier")
        for field, choices in (("domain", DOMAINS), ("novelty", NOVELTY), ("evidence", EVIDENCE),
                               ("outcome", LEVELS), ("affect", AFFECT)):
            if not isinstance(row.get(field), str) or row[field] not in choices:
                raise ValueError(f"d.cognition.{actor}.{field}: invalid value")
        if not isinstance(row.get("salience", "ordinary"), str) or row.get("salience", "ordinary") not in SALIENCE:
            raise ValueError(f"d.cognition.{actor}.salience: invalid value")
        for field in ("interpretation", "visible", "basis"):
            if field in row and (not isinstance(row[field], str) or len(row[field]) > 240):
                raise ValueError(f"d.cognition.{actor}.{field}: text <=240")
        if row["outcome"] in ("partial", "hypothesis", "understood") and not norm(row.get("interpretation")):
            raise ValueError("d.cognition: interpretation required above received")


def _book(hot, actor):
    return ((hot.get("_psyche") or {}).get(actor) or {}).get("cognition") or {}


def view(hot, actor, turn_id):
    past = sorted(_book(hot, actor).items(), key=lambda item: item[1].get("turn", ""), reverse=True)
    return {**profile(actor), "roll": draw(turn_id, actor), "affect_roll": affect_draw(turn_id, actor),
            "topics": {k: v for k, v in past[:3]},
            "last_read": (((hot.get("_psyche") or {}).get(actor) or {}).get("dread") or {}).get("reads", "")}


def evidence_id(stimulus):
    return hashlib.sha256(norm(stimulus).casefold().encode()).hexdigest()


# Independent lexical backstop, not a semantic classifier. Questions are
# deliberately exempt; the source still has to support any actual inference.
INFERENCE = re.compile(r"^(?:так\s+вот|значит|стало\s+быть|выходит|получается|следовательно|теперь\s+я\s+понимаю)\b", re.I)


def spoken_inferences(hot, turn, local):
    from .acquaint import lines
    d = turn.get("d") or {}
    names = {}
    for key in local - {"shiki"}:
        card = (hot.get("cast") or {}).get(key) or {}
        patch = (d.get("cast") or {}).get(key) or {}
        name = patch.get("name") or card.get("name")
        if isinstance(name, str):
            names.setdefault(norm(name).casefold(), []).append(key)
    found = set()
    for label, speech in lines(turn.get("gm")):
        speech = norm(speech).lstrip("—–- ")
        candidates = names.get(norm(label).casefold(), [])
        if len(candidates) == 1 and "?" not in speech and INFERENCE.match(speech):
            found.add(candidates[0])
    return found


def _local(hot, turn):
    d = turn.get("d") or {}
    before = set((hot.get("scene") or {}).get("present") or [])
    after = set((d.get("scene") or {}).get("present") or [])
    return before | after | set(turn.get("_spoke") or ()) | set(turn.get("_always") or ())


def triggers(hot, turn, local):
    """Structured evidence of new acquisition or a fresh interpretation.
    Routine dialogue with neither needs no record. Never infer hidden truths
    from free text or a character's profession.
    """
    d = turn.get("d") or {}
    needed = set()
    for actor, patch in (d.get("cast") or {}).items():
        if actor == "shiki" or actor not in local or not isinstance(patch, dict):
            continue
        if patch.get("knows+"):
            needed.add(actor)
        dread = patch.get("dread") or {}
        old = ((hot.get("_psyche") or {}).get(actor) or {}).get("dread") or {}
        if isinstance(dread, dict) and dread.get("reads") and norm(dread["reads"]) != norm(old.get("reads")):
            needed.add(actor)
    for event in d.get("knowledge_events") or []:
        if event.get("actor") in local and event.get("actor") != "shiki":
            needed.add(event["actor"])
    return needed | spoken_inferences(hot, turn, local)


def assess(row, actor, roll, affect_roll, previous, gm):
    """Return effective ceiling and errors without mutating state."""
    problems = []
    limit = ceiling(roll, actor, row["domain"], row["novelty"], row["evidence"])
    repeated = bool(previous and (evidence_id(row["stimulus"]) in (previous.get("evidence_ids") or [])
                                  or norm(previous.get("stimulus")) == norm(row["stimulus"])))
    if repeated:
        # Same evidence is not a new die; no improvement without new source.
        limit = LEVELS.index(previous["outcome"])
    if LEVELS.index(row["outcome"]) > limit:
        problems.append(f"предел понимания {LEVELS[limit]}, заявлено {row['outcome']}")
    if norm(row["stimulus"]) not in gm:
        problems.append("stimulus не найден в сыгранной сцене")
    if norm(row["stimulus"]) == norm(row.get("visible")):
        problems.append("реакция NPC не может быть собственным источником")
    if row["affect"] != "none" and (not norm(row.get("visible")) or norm(row["visible"]) not in gm):
        problems.append("эмоция/реакция не подтверждена видимой репликой или поступком")
    strength = affect_strength(affect_roll, actor, row["novelty"], row.get("salience", "ordinary"), repeated=repeated)
    if strength and row["affect"] == "none":
        problems.append(f"значимый стимул (сила аффекта {strength}) остался без реакции")
    if not repeated and (strength == 2 or row["novelty"] == "disruptive") and row["affect"] == "calm" and not norm(row.get("basis")):
        problems.append("спокойствие при сильном воздействии без индивидуального основания")
    return limit, strength, problems


def review(hot, turn, *, packet=None):
    """Read-only pre/postcheck; a published scene cannot be unplayed by a lint."""
    if turn.get("kind") in ("ooc", "technical", "void") or not (enabled(turn.get("id")) or rows(turn)):
        return [], {}
    validate(turn)
    d = turn.get("d") or {}
    gm = norm(turn.get("gm"))
    local = _local(hot, turn)
    prior = set((hot.get("scene") or {}).get("present") or [])
    data, errors = {}, []
    for actor in sorted(triggers(hot, turn, local) - set(rows(turn))):
        errors.append(f"{actor}: новая осведомлённость/трактовка без d.cognition; знание не равно пониманию")
    for actor, row in rows(turn).items():
        if actor not in local:
            errors.append(f"{actor}: d.cognition без присутствия или доступного наблюдения")
            continue
        if packet is None:
            key = actor_key(actor, prior)
            roll, affect_roll = draw(turn["id"], key), affect_draw(turn["id"], key)
            prev = _book(hot, actor).get(row["topic"])
        else:
            cp = packet.get("cognition") or {}
            p = ((packet.get("present") or {}).get(actor) or {}).get("cognition") or {}
            p = p or (cp.get("new_profiles") or {}).get(actor) or {}
            roll = p.get("roll", cp.get("new_roll"))
            affect_roll = p.get("affect_roll", cp.get("new_affect_roll"))
            prev = (p.get("topics") or {}).get(row["topic"])
            if (cp.get("for") != turn["id"] or type(roll) is not int or not 1 <= roll <= 20
                    or type(affect_roll) is not int or not 1 <= affect_roll <= 20):
                # Unknown/stale packet: only factual reception is safe to project.
                if row["outcome"] != "received":
                    errors.append(f"{actor}: нет свежего когнитивного броска")
                roll, affect_roll = 1, 1
        limit, strength, problems = assess(row, actor, roll, affect_roll, prev, gm)
        if row["outcome"] == "hypothesis":
            claim = norm(row.get("interpretation"))
            facts = ((d.get("cast") or {}).get(actor) or {}).get("knows+") or []
            if isinstance(facts, str):
                facts = [facts]
            if any(norm(fact) == claim for fact in facts):
                problems.append("гипотеза записана как достоверный knows+")
        errors.extend(f"{actor}: {p}" for p in problems)
        if not problems:
            data[actor] = {"topic": row["topic"], "turn": turn["id"],
                           "stimulus": row["stimulus"], "outcome": row["outcome"],
                           "interpretation": row.get("interpretation", ""),
                           "affect": row["affect"], "affect_strength": strength, "limit": LEVELS[limit],
                           "evidence_ids": list(dict.fromkeys(
                               list((prev or {}).get("evidence_ids") or []) + [evidence_id(row["stimulus"])]))}
    return errors, data


def apply(hot, turn, prior_hot):
    errors, accepted = review(prior_hot, turn)
    for error in errors:
        hot.setdefault("_checks", []).append({"turn": turn["id"], "kind": "cognition", "text": error})
    for actor, value in accepted.items():
        book = hot.setdefault("_psyche", {}).setdefault(actor, {}).setdefault("cognition", {})
        topic = value.pop("topic")
        book[topic] = value


def inspect(packet, turn):
    """Use only the already supplied NEXT (no connector round-trip)."""
    if not packet.get("cognition") and not rows(turn):
        return []
    present = packet.get("present") or {}
    pseudo = {"scene": {"present": list(present)},
              "cast": {k: {"name": v.get("name")} for k, v in present.items()},
              "_psyche": {k: {"cognition": (v.get("cognition") or {}).get("topics", {}),
                              "dread": {"reads": (v.get("cognition") or {}).get("last_read", "")}}
                          for k, v in present.items()}}
    return review(pseudo, turn, packet=packet)[0]
