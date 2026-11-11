"""Pre-scene epistemic gate: no NPC inherits the GM's knowledge.

Adapted as a lightweight NPR-specific version of the actor-scoped-knowledge
and scene-gate principles in sifiroth-gif/Bleach-RPG-.
No NLP model or heuristic is a substitute for source-bound claims.
This module reads the existing world.beliefs table; it does not create a
second canonical state or imply that player_known means known to all NPCs.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from .engine import Campaign

FORMAT = "NPR-NPC-KNOWLEDGE-PREFLIGHT/1"
DIALOGUE = re.compile(r"^\s*\*\*(.+?):\*\*\s*[—–-]\s*(.*?)\s*$")
UNCERTAINTY = ("может", "возможно", "кажется", "предполож", "вероятно", "похоже",
               "не знаю", "допустим", "а если", "полагаю", "вдруг", "наверное")


class KnowledgeLeak(ValueError):
    """A proposed NPC utterance has no justified acquisition path."""


def _norm(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def dialogue_lines(draft: str) -> list[dict]:
    """Read only signed NPC dialogue, not descriptive prose or GM notes."""
    out = []
    for line in draft.splitlines():
        match = DIALOGUE.match(line)
        if match:
            out.append({"speaker": match[1].strip(), "text": match[2].strip()})
    return out


def actor_ledger(game: Campaign, actor: str) -> dict:
    """Actor-indexed *beliefs* only; global facts and GM plans never leak here."""
    memory = game.world(audience="gm", subject=actor)
    return {record["topic"]: record for record in memory["beliefs"]}


def _verified_source(game: Campaign, belief: dict) -> bool:
    src = belief.get("source_event")
    if src == "campaign-setup":
        return True
    if not isinstance(src, str) or not src:
        return False
    try:
        game.entry("event", src, audience="gm")
    except ValueError:
        return False
    return True


def _topic_matches(text: str, rule: dict) -> bool:
    normalized = _norm(text)
    return any(_norm(term) in normalized for term in rule["triggers"])


def _reasons(game: Campaign, actor: str, item: dict, protected: dict,
             source: dict | None) -> list[str]:
    errors = []
    topic = item.get("topic")
    stance = item.get("stance")
    if topic not in protected:
        return [f"{actor}: no registered topic {topic!r}; register it before asserting it"]
    if stance not in ("assert", "report", "suspect", "question", "deny"):
        return [f"{actor}: invalid epistemic stance for {topic!r}"]
    utterance = item["utterance"]
    # A suspiciously specific QUESTION can leak hidden GM information too.
    # Even conjecture needs a personal evidential hook, not future plot notes.
    if source is None:
        errors.append(f"{actor}: no personal belief/source for {topic}; GM lore is not NPC knowledge")
        return errors
    if stance == "question":
        if "?" not in utterance:
            errors.append(f"{actor}: question stance without question mark for {topic}")
        if not _verified_source(game, source):
            errors.append(f"{actor}: question about {topic} lacks verified personal source")
        return errors
    if stance == "suspect" and not (
        "?" in utterance or any(term in _norm(utterance) for term in UNCERTAINTY)
    ):
        errors.append(f"{actor}: supposed hypothesis stated as fact about {topic}")
        return errors
    if stance == "deny" and "не" not in _norm(utterance):
        errors.append(f"{actor}: a denial must be explicit for {topic}")
        return errors
    if not _verified_source(game, source):
        errors.append(f"{actor}: belief {topic} lacks a verified event source")
    if stance == "report" and source["evidence"] not in ("reported", "document", "witnessed"):
        errors.append(f"{actor}: cannot report {topic} from {source['evidence']} alone")
    if stance == "assert" and source["certainty"] == "uncertain":
        errors.append(f"{actor}: uncertain belief about {topic} presented as certain")
    return errors


def validate_scene(game: Campaign, draft: str, review: dict, policy: dict) -> dict:
    """Fail closed on tracked high-risk claims and the completeness of review.

    The review is GM-only, and every dialogue line gets one annotated row.
    A protected claim requires a source-bound actor belief (or an explicit
    question/denial). Source status is *belief*, not objective truth.
    The review is not semantic parsing: false classifications are a GM error.
    """
    if not isinstance(review, dict) or review.get("format") != FORMAT:
        raise KnowledgeLeak("Missing/current NPR knowledge preflight review")
    if not isinstance(draft, str) or not isinstance(policy, dict):
        raise KnowledgeLeak("Invalid scene/policy")
    protected = policy.get("topics", {})
    if not isinstance(protected, dict) or any(
        not isinstance(k, str) or not isinstance(v.get("triggers"), list) or
        not v["triggers"] or any(not isinstance(s, str) or not s.strip() for s in v["triggers"])
        for k, v in protected.items()
    ):
        raise KnowledgeLeak("Malformed protected topic registry")
    speakers = dict(policy.get("speakers", {}))
    for name, actor in review.get("additional_speakers", {}).items():
        if name in speakers and speakers[name] != actor:
            raise KnowledgeLeak("Speaker alias attempts to override policy")
        speakers[name] = actor
    raw = review.get("lines")
    if not isinstance(raw, list):
        raise KnowledgeLeak("Preflight needs one reviewed entry per dialogue line")
    dialogue = dialogue_lines(draft)
    if len(dialogue) != len(raw):
        raise KnowledgeLeak(f"Preflight covers {len(raw)} lines; actual dialogue has {len(dialogue)}")
    ledger_cache = {}
    seen = Counter()
    errors = []
    for index, (actual, rec) in enumerate(zip(dialogue, raw), 1):
        actor = speakers.get(actual["speaker"])
        if not actor:
            errors.append(f"Line {index}: unknown speaker {actual['speaker']!r}")
            continue
        if not isinstance(rec, dict) or rec.get("actor") != actor or rec.get("text") != actual["text"]:
            errors.append(f"Line {index}: speaker/text does not match actual dialogue")
            continue
        if actor not in ledger_cache:
            ledger_cache[actor] = actor_ledger(game, actor)
        ledger = ledger_cache[actor]
        claims = rec.get("claims")
        if not isinstance(claims, list):
            errors.append(f"Line {index}: claims must be an explicit list (may be empty)")
            continue
        claimed = set()
        for claim in claims:
            if not isinstance(claim, dict):
                errors.append(f"Line {index}: malformed claim")
                continue
            topic = claim.get("topic")
            if topic in claimed:
                errors.append(f"Line {index}: duplicate topic {topic}")
                continue
            claimed.add(topic)
            if actor != policy.get("player_actor"):
                errors.extend(f"Line {index}: {x}" for x in _reasons(
                    game, actor, {**claim, "utterance":actual["text"]}, protected, ledger.get(topic)
                ))
        for topic, rule in protected.items():
            if actor != policy.get("player_actor") and _topic_matches(actual["text"], rule) and topic not in claimed:
                errors.append(f"Line {index}: {actor} mentions protected {topic} without actor-scoped evidence review")
        seen[actor] += 1
    if errors:
        raise KnowledgeLeak("; ".join(errors))
    return {"status":"ready", "lines_checked":len(dialogue),
            "actor_lines":dict(seen), "protected_topics":list(protected)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args()
    game = Campaign(args.db)
    game.verify()
    review = json.loads(args.review.read_text(encoding="utf-8"))
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    result = validate_scene(game, args.draft.read_text(encoding="utf-8"), review, policy)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
