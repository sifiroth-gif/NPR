"""Показатели плана памяти: решена ли проблема, видно по цифрам, а не по ощущению.

Главная мера — поправки игрока на 100 ходов по сессиям. Остальное нужно,
чтобы заметить поломку раньше игрока: контрольные вопросы, размеры чтения на
старте чата, события без конца, NPC без облика.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

START_READS = {"main": ("AGENTS.md", "campaigns/shiki/GM_CORE.md", "campaigns/shiki/SHIKI.md",
                        "skills/dark-literary-gm/SKILL.md"),
               "state": ("recovery/GM_BRIEF.json", "recovery/CHECKS.json", "memory/INDEX.md")}


def _session(turn_id):
    match = re.match(r"s(\d+)\.", str(turn_id or ""))
    return int(match.group(1)) if match else 0


def collect(state_dir, source_root=None):
    state_dir = Path(state_dir)
    source_root = Path(source_root) if source_root else Path(__file__).resolve().parents[1]
    turns, fixes = {}, {}
    for path in sorted((state_dir / "archive").glob("session-*-turns.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                if row.get("kind") != "ooc":
                    turns[_session(row.get("id"))] = turns.get(_session(row.get("id")), 0) + 1
    canon = state_dir / "archive/shiki-canon.jsonl"
    if canon.is_file():
        for raw in canon.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                session = _session(json.loads(raw).get("turn"))
                fixes[session] = fixes.get(session, 0) + 1
    per_session = {f"s{s:02}": {"turns": n, "corrections": fixes.get(s, 0),
                                "per_100": round(100 * fixes.get(s, 0) / n, 1) if n else None}
                   for s, n in sorted(turns.items()) if s}
    sizes = {p: (source_root / p).stat().st_size for p in START_READS["main"] if (source_root / p).is_file()}
    sizes.update({p: (state_dir / p).stat().st_size for p in START_READS["state"] if (state_dir / p).is_file()})
    from . import wiki_eval
    evaluation = wiki_eval.run(state_dir, source_root)
    secret = state_dir / "recovery/GM_ONLY.json"
    plot = (json.loads(secret.read_text(encoding="utf-8")).get("plot") or {}) if secret.is_file() else {}
    events = [e for e in (plot.get("events") or {}).values() if e.get("status") == "active"
              and e.get("kind") in ("chapter", "side")]
    endless = [e.get("id") for e in events if not e.get("endings") or not e.get("limit")]
    npc_dir = state_dir / "memory/npc"
    no_look = []
    if npc_dir.is_dir():
        for page in npc_dir.glob("*.md"):
            if "." in page.stem:
                continue
            text = page.read_text(encoding="utf-8")
            if "- внешность:" not in text and re.match(r"# [А-ЯЁ][а-яё]+( [А-ЯЁ][а-яё]+)? ·", text):
                no_look.append(page.stem)
    return {"corrections_per_100_turns": per_session,
            "control_questions": {k: evaluation.get(k) for k in ("passed", "total", "score")},
            "start_read_bytes": {"total": sum(sizes.values()), "files": sizes},
            "hot_bytes": (state_dir / "recovery/HOT.json").stat().st_size
            if (state_dir / "recovery/HOT.json").is_file() else None,
            "active_events": len(events), "events_without_end": endless,
            "named_npc_without_look": sorted(no_look)}
