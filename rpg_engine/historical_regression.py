"""D1: archived corpus compatibility and source-bound regression probes.

Read only. The caller runs on a pinned isolated state. This is not a full
replay from the original seed: missing earlier deltas are counted, not invented.
Only primary file paths and non-secret counts are printed by the audit.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from . import capabilities, continuity_guard, directives, fight, history_access, hot, identity, medical, news, npc_memory, presence, acquaint


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def corpus(root):
    root = Path(root)
    prose, delta, errors = {}, {}, []
    for pattern, target in (("archive/session-*-turns.jsonl", prose), ("archive/deltas/session-*.jsonl", delta)):
        for path in sorted(root.glob(pattern)):
            for row in rows(path):
                ident = row.get("id")
                try:
                    hot.turn_number(ident)
                except (ValueError, TypeError):
                    errors.append(f"invalid ID: {path.name}")
                    continue
                if ident in target:
                    errors.append(f"duplicate: {ident} in {pattern}")
                target[ident] = row
    return prose, delta, errors


def scan(root):
    prose, delta, errors = corpus(root)
    joined = 0
    for ident, row in delta.items():
        if not isinstance(row.get("d"), dict):
            errors.append(f"{ident}: delta is not object")
            continue
        if ident not in prose:
            continue
        primary = prose[ident]
        # These are projections of the same turn. Preserve both raw records.
        if row.get("at") != primary.get("at"):
            errors.append(f"{ident}: timestamp mismatch")
        turn = dict(primary, schema=hot.TURN_SCHEMA, d=row["d"])
        if row.get("kind"):
            turn["kind"] = row["kind"]
        try:
            hot.validate_turn(hot._normalize_recorded_turn(turn))
            joined += 1
        except (ValueError, TypeError, OverflowError) as exc:
            errors.append(f"{ident}: {exc}")
    return {"prose": len(prose), "deltas": len(delta), "validated": joined,
            "prose_only": len(prose.keys() - delta.keys()),
            "delta_only": len(delta.keys() - prose.keys()), "errors": errors}


def hashes(root):
    root = Path(root)
    paths = set()
    for pattern in ("archive/session-*-turns.jsonl", "archive/deltas/session-*.jsonl",
                    "archive/turns/**/*.md", "recovery/TURN.json"):
        paths.update(p for p in root.glob(pattern) if p.is_file())
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths)}


def check_hashes(root, before):
    after = hashes(root)
    changed = sorted(k for k, v in before.items() if after.get(k) != v)
    assert not changed, "Primary files changed: " + ", ".join(changed[:10])


def verify(root, state, packet, registry):
    """Eight focused cases. Replay exact deltas for bounded subsystems only."""
    root = Path(root)
    primary, patches, errors = corpus(root)
    assert not errors, errors
    out = {}
    def source(ident):
        path = root / "archive/turns" / ident.split(".")[0] / (ident + ".md")
        return path.read_text(encoding="utf-8")
    def delta(ident):
        return patches[ident]["d"]

    # 1. Known sterilisation and latest actual item transition; no duplicate spend.
    assert "стерилизацию медицинской чакрой" in source("s10.text.018")
    learned = capabilities.resolve(registry, "стерилизация")
    assert learned["status"] == "mastered" and "s10.text.018" in learned["sources"]
    key = "shiki_medical_kit"
    transition = delta("s14.text.023")["things"][key]
    sample = {"things": {key: dict(transition["was"])}}
    continuity_guard.apply_things(sample, {"things": {key: transition}}, "s14.text.023", patches["s14.text.023"]["at"])
    assert all(sample["things"][key][k] == v for k, v in transition["now"].items())
    before = copy.deepcopy(sample["things"])
    continuity_guard.apply_things(sample, {"things": {key: transition}}, "s14.text.023", patches["s14.text.023"]["at"])
    assert sample["things"] == before
    assert all(state["things"][key][k] == v for k, v in transition["now"].items())
    out["sterilisation"] = True

    # 2. Permanent sage state, exhaustion exception and eyes explicitly off.
    assert "Режим отшельника угас" in source("s11.text.074")
    assert "постоянное состояние" in source("s13.text.100")
    assert "#убираю шаринган#" in source("s13.text.077")
    assert fight.sage_active(state)
    exhausted = copy.deepcopy(state)
    exhausted["shiki"]["chakra"]["value"] = 0
    assert not fight.sage_active(exhausted)
    assert "Шаринган выключен" in state["shiki"]["body"]
    out["sage_and_eyes"] = True

    # 3. Exact NPC identity, no global role merge.
    rules = identity.active(root)
    rule = next(r for r in rules if r["to"] == "quay_masked_hunter")
    names = presence.roster(root, state)
    met = acquaint.index(root, names)
    assert set(rule["speech_turns"]) <= set(met[rule["to"]]["spoke"])
    assert not set(rule["speech_turns"]) & set(met.get(rule["from"], {}).get("spoke", []))
    assert not set(rule["duplicate_turns"]) & {r["turn"] for r in npc_memory.load_episodes(root, rule["from"])}
    out["identity"] = True

    # 4. Replay original injury and three actual healing deltas.
    ident = "quay_masked_hunter"
    wound = next(w for w in delta("s14.text.007")["fight"]["wounds"] if w["who"] == ident)
    sample = {"injuries": {ident: [dict(wound, turn="s14.text.007", bleed=True)]}}
    for tid in ("s14.text.020", "s14.text.021", "s14.text.023"):
        for row in delta(tid)["heal"]:
            fight._heal(sample, row, tid, at=patches[tid]["at"])
        assert len(sample["injuries"][ident]) == 1
    result = sample["injuries"][ident][0]
    assert result["lost"] == delta("s14.text.023")["heal"][0]["residual"]["lost"]
    assert result["bleed"] is False and result["recovered_at"] == "s14.text.023"
    clinical = medical.context(state)["patients"][ident]["wounds"]
    assert any(w["lost"] == result["lost"] and w["turn"] == "s14.text.007" for w in clinical)
    assert "Самостоятельная ходьба пока не проверена" in source("s14.text.021")
    out["partial_heal"] = True

    # 5. Actual departure: patient remains clinically relevant, not present.
    scopes = history_access.scopes(list(primary.values()), {k:v["d"] for k,v in patches.items()})
    assert ident in scopes["s14.text.012"] and ident not in scopes["s14.text.023"]
    assert ident not in state["scene"]["present"] and ident not in packet.get("present", {})
    assert ident in packet["medical"]["patients"]
    out["departure"] = True

    # 6. An old interrogation is not a remote actor's personal knowledge.
    tid = "s10.text.084"
    actor = "morioka_hideaki"
    patch = delta(tid)["cast"][actor]
    sample = {"scene": {"present": []}, "cast": {actor: {"name": actor}}}
    turn = dict(primary[tid], d=delta(tid))
    npc_memory.apply(sample, turn, {actor: patch})
    assert not sample["cast"][actor].get("memory")
    # A counterfactual explicit message is isolated; no claim it occurred.
    hypothetical = dict(turn, d={"knowledge_events": [{"actor": actor, "claim": "Проверочное сообщение",
                         "source": tid, "channel": "message"}]})
    npc_memory.apply(sample, hypothetical, {})
    assert sample["cast"][actor]["memory"][-1]["what"] == "Проверочное сообщение"
    out["knowledge_channel"] = True

    # 7. Existing directives remain intact; expire only nonbinding cues.
    seed = directives.standing(root)
    active = directives.active(root, state)
    assert len(seed) == 18 and len(active) == 22
    sample = copy.deepcopy(state)
    sample["cursor"]["id"] = "s15.text.200"
    assert directives.active(root, sample) == active
    assert directives.view(root, sample)["standing_ids"] == packet["directives"]["standing_ids"]
    out["directives"] = True

    # 8. Retained real offer, synthetic receipt only in memory; exact replay.
    sample = copy.deepcopy(state)
    offer = sample["_will"]["news"]["current"]
    ident = offer["id"]
    count = sample["_will"]["news"]["delivered"]
    quote = "Проверочное сообщение о событии."
    receipt = {"id": "s14.text.024", "at": packet["lag"]["at"], "gm": "**Посланник:** — " + quote,
               "d": {"news_delivered": [{"id": ident, "channel": "told_in_scene", "quote": quote}]}}
    news.accept(sample, receipt)
    news.accept(sample, receipt)
    assert sample["_will"]["news"]["delivered"] == count + 1
    assert ident in sample["_will"]["news"]["receipts"]
    assert not sample["_will"]["news"].get("current")
    assert state["_will"]["news"]["current"]["id"] == ident
    out["duplicate_news"] = True
    return out
