"""Structured referent and eyewitness-scope audit for NPC dialogue.

A review cannot move an eyewitness claim from one event to another merely
because both happened in the same forest/city or involved related characters.

This gate is intentionally conservative: it makes ambiguous "I saw it there"
assertions fail closed and checks explicit per-episode witness exclusions.
It does NOT pretend to infer every Russian pronoun or mind-read NPC knowledge.
"""
from __future__ import annotations

import re

WITNESS_VERBS = re.compile(
    r"\b(?:видел|видела|видели|наблюдал|наблюдала|наблюдали|слышал|слышала|слышали)\b",
    re.IGNORECASE,
)
EYEWITNESS_VERBS = re.compile(
    r"\b(?:видел|видела|видели|наблюдал|наблюдала|наблюдали)\b",
    re.IGNORECASE,
)
NEGATION = re.compile(r"\bне\s+(?:видел|видела|видели|наблюдал|наблюдала|наблюдали)\b",re.IGNORECASE)
AMBIGUOUS_REFERENTS = re.compile(
    r"\b(?:в\s+лесу|в\s+лес|там|тогда|это|этого|он|она|они|произошло|случилось|такое)\b",
    re.IGNORECASE,
)
CLAUSE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
MODES = {"witnessed", "reported", "document", "inferred", "unknown"}


def scoped_errors(dialogue: list[dict], reviewed: list[dict],
                  policy: dict, scene_focus_entity: str | None) -> list[str]:
    """Return mismatches for witnessed claims and their exact episode referents."""
    episodes = policy.get("episode_scopes")
    if not episodes:
        return []
    errors: list[str] = []
    for index, (actual, review) in enumerate(zip(dialogue, reviewed), 1):
        if not isinstance(review, dict):
            continue
        actor = review.get("actor")
        if actor == policy.get("player_actor"):
            continue
        sentences = [
            sentence.strip() for sentence in CLAUSE_BOUNDARY.split(actual["text"])
            if sentence.strip()
        ]
        claims = [
            sentence for sentence in sentences
            if WITNESS_VERBS.search(sentence) and not NEGATION.search(sentence)
        ]
        if not claims:
            continue
        provided = review.get("referent_audit")
        if not isinstance(provided, list) or len(provided) != len(claims):
            errors.append(
                f"Line {index}: {actor}: {len(claims)} witnessed/heard clauses "
                f"require exactly as many referent_audit entries")
            continue
        for sentence, record in zip(claims, provided):
            if not isinstance(record, dict) or record.get("quote") != sentence:
                errors.append(f"Line {index}: {actor}: referent audit quote mismatch")
                continue
            event_id = record.get("episode_id")
            mode = record.get("knowledge_type")
            source = record.get("source_event")
            if not isinstance(event_id, str) or event_id not in episodes:
                errors.append(f"Line {index}: {actor}: unknown/unresolved episode {event_id!r}")
                continue
            if mode not in MODES or not isinstance(source, str) or not source.strip():
                errors.append(f"Line {index}: {actor}: missing explicit knowledge mode/source")
                continue
            episode = episodes[event_id]
            entity = episode.get("focus_entity")
            if mode == "witnessed" and actor in episode.get("known_non_witnesses", []):
                errors.append(f"Line {index}: {actor} never witnessed {event_id}")
            if mode == "witnessed" and (
                episode.get("witnessed_by") and
                actor not in episode["witnessed_by"]
            ):
                errors.append(f"Line {index}: {actor} not a verified witness of {event_id}")
            if EYEWITNESS_VERBS.search(sentence) and mode != "witnessed":
                errors.append(f"Line {index}: {actor}: 'I saw' requires witnessed knowledge")
            if AMBIGUOUS_REFERENTS.search(sentence):
                if not isinstance(scene_focus_entity, str) or not scene_focus_entity:
                    errors.append(f"Line {index}: {actor}: implicit referent without scene focus")
                elif entity != scene_focus_entity:
                    # An explicit topic shift must name its subject in this
                    # *same* clause, not in unrelated GM-only plot.
                    named = record.get("explicit_named_shift")
                    if not (isinstance(named, str) and len(named) >= 3 and
                            named.casefold() in sentence.casefold()):
                        errors.append(
                            f"Line {index}: {actor}: implicit referent topic {entity!r} "
                            f"does not match current conversation {scene_focus_entity!r}")
    return errors
