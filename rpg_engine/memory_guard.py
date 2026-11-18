"""Pre-publication entity-claim review; not a general-purpose NLP truth engine.

Explicit world assertions are checked against the sourced ledger. Narrow
high-confidence prose patterns catch obvious narrator contradictions, while
NPC speech, testimony and guesses remain separate epistemic claims.
"""
from __future__ import annotations

from datetime import datetime
import re

from . import entity_memory

ADULT = re.compile(r"\b(?:взросл(?:ый|ая|ое|ого|ому)|совершеннолетн(?:ий|яя|его)|adult)\b", re.I)
YOUNG = re.compile(r"\b(?:мальчик|девочка|подрост(?:ок|ка)|ребенок|ребёнок|teenager)\b", re.I)
MAN = re.compile(r"\b(?:мужчин(?:а|ой|у|ы)|man)\b", re.I)
WOMAN = re.compile(r"\b(?:женщин(?:а|ой|у|ы)|woman)\b", re.I)
NON_ASSERTION = re.compile(
    r"\b(?:сказал|сказала|утверждал|называл|обозвал|решил|казалось|"
    r"предположил|считала|ошибочн|якобы|по\s+словам|слухам|"
    r"не\s+был|не\s+являлся|not|thought|claimed|mistaken)\b", re.I)
AGE_NUMBER = re.compile(r"\b(\d{1,3})\s*(?:лет|года|год|years?\s+old)\b", re.I)
AGE_SEED = re.compile(r"\b(\d{1,3})\b")


def _clean(s):
    return " ".join(str(s or "").casefold().replace("ё", "е").split())


def _now_and_observed_close(fact, now):
    """An age-at-observation claim only constrains nearby game time."""
    observed = fact.get("observed_at") or fact.get("recorded_at")
    if not observed or not now:
        return False
    try:
        days = (datetime.fromisoformat(now) - datetime.fromisoformat(observed)).days
    except (ValueError, TypeError):
        return False
    return 0 <= days <= 365


def _active(card, name):
    return [fact for fact in card.get("facts", [])
            if fact.get("field") == name and not fact.get("superseded_by")]


def assertions_errors(ledger, turn):
    """Validate voluntarily supplied typed assertions (review.entity_claims).

    A narrator must cite an existing fact or a matching proposed delta; NPC
    testimony is not treated as canonical truth.
    """
    claims = (turn.get("review") or {}).get("entity_claims") or []
    if not isinstance(claims, list):
        return ["review.entity_claims must be a list"]
    failures = []
    for n, claim in enumerate(claims):
        if not isinstance(claim, dict):
            failures.append(f"entity_claims[{n}] must be an object")
            continue
        entity, field, value = claim.get("entity"), claim.get("field"), claim.get("value")
        channel = claim.get("channel", "narrator")
        source = claim.get("source")
        if not isinstance(entity, str) or not field or value is None or not source:
            failures.append(f"entity_claims[{n}] requires entity, field, value, source")
            continue
        if channel != "narrator":
            if channel not in ("witness", "reported", "document", "inference"):
                failures.append(f"entity_claims[{n}] unknown epistemic channel")
            elif not claim.get("actor"):
                failures.append(f"entity_claims[{n}] non-narrator claim needs actor")
            continue
        card = ledger.get("entities", {}).get(entity)
        existing = _active(card or {}, field)
        new = (((turn.get("d") or {}).get("entity_facts") or {}).get(entity) or {}).get("facts+", [])
        matched_old = any(f.get("value") == value and f.get("source") == source
                          for f in existing)
        matched_new = any(f.get("field") == field and f.get("value") == value and
                          f.get("source") == source for f in new)
        if not (matched_old or matched_new):
            failures.append(f"entity_claims[{n}]: unverified narrator fact {entity}.{field}")
    return failures


def prose_errors(ledger, turn):
    """Detect only obvious age/sex assertions tied closely to a known alias.

    Silence here is not a proof of consistency. Quoted speech and attribution
    are deliberately excluded to avoid rewriting NPC deception into world truth.
    """
    raw = turn.get("gm") or ""
    if not raw:
        return []
    errors = []
    # New paragraphs and sentence endings bound identity attribution.
    sentences = re.split(r"(?<=[.!?])\s+|\n+", raw)
    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped or stripped.startswith(("—", "-", "«", '"', ">")):
            continue
        if NON_ASSERTION.search(stripped):
            continue
        for entity, card in ledger.get("entities", {}).items():
            forms = [card.get("label", ""), entity] + list(card.get("aliases", []))
            short = []
            for alias in forms:
                if alias and len(_clean(alias)) >= 5:
                    short.append(re.escape(_clean(alias)))
            if not short:
                continue
            expression = "(?<!\\w)(?:" + "|".join(short[:20]) + ")(?!\\w)"
            clean = _clean(stripped)
            match = re.search(expression, clean)
            if match is None:
                continue
            # Descriptors sufficiently near a named referent; exclude other
            # people introduced with place/relative prepositions.
            tail = clean[match.end():match.end() + 70]
            if re.search(r"\b(?:рядом|возле|перед|около|у\s+кровати|врач|сестра)\b", tail[:25]):
                continue
            sex = _active(card, "sex")
            if sex:
                value = _clean(sex[-1]["value"])
                male = any(t in value for t in ("мальчик", "мужск", "male", "man"))
                female = any(t in value for t in ("девоч", "женск", "female", "woman"))
                if male and WOMAN.search(tail[:55]):
                    errors.append(f"{entity}.sex: narrator used female description; source {sex[-1]['source']}")
                if female and MAN.search(tail[:55]):
                    errors.append(f"{entity}.sex: narrator used male description; source {sex[-1]['source']}")
            age = _active(card, "age_at_discovery")
            if age and _now_and_observed_close(age[-1], turn.get("at")):
                old = AGE_SEED.search(str(age[-1]["value"]))
                base = int(old.group(1)) if old else None
                if base is not None and base < 18:
                    if ADULT.search(tail[:65]):
                        errors.append(f"{entity}.age: 'adult' contradicts recent source {age[-1]['source']}")
                    said = AGE_NUMBER.search(tail[:65])
                    if said and abs(int(said.group(1)) - base) > 2:
                        errors.append(f"{entity}.age: incompatible recent age; source {age[-1]['source']}")
    return sorted(set(errors))


def review(ledger, turn):
    errors = entity_memory.claim_errors(ledger, turn)
    errors.extend(assertions_errors(ledger, turn))
    errors.extend(prose_errors(ledger, turn))
    return {"ok": not errors, "errors": sorted(set(errors)),
            "scope": "typed world assertions and limited literal narration only",
            "rule": "No result can certify every sentence of free prose."}
