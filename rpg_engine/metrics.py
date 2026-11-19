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
               "state": ("recovery/GM_BRIEF.json", "recovery/NEXT.json", "memory/INDEX.md")}
TURN_READ = "recovery/NEXT.json"   # единственное чтение ведущего перед обычным ходом


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
            "pace": pace(state_dir),
            "npc_autonomy": autonomy(state_dir),
            "control_questions": {k: evaluation.get(k) for k in ("passed", "total", "score")},
            "start_read_bytes": {"total": sum(sizes.values()), "files": sizes},
            "turn_read_bytes": (state_dir / TURN_READ).stat().st_size if (state_dir / TURN_READ).is_file() else None,
            "hot_bytes": (state_dir / "recovery/HOT.json").stat().st_size
            if (state_dir / "recovery/HOT.json").is_file() else None,
            "active_events": len(events), "events_without_end": endless,
            "named_npc_without_look": sorted(no_look)}


def pace(state_dir, last=20):
    """Скорость игры: минуты между записанными ходами и длина ответа ведущего.

    Время берётся из истории коммитов ``recovery/TURN.json`` (ход записан —
    сцена показана игроку). Промежуток включает и чтение игрока, и его
    раздумье, и подготовку сцены ведущим: это полный цикл хода.
    """
    import statistics
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(state_dir), "log", "--format=%H %ct %s", f"-{last * 3}",
                              "--", "recovery/TURN.json"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    plays, commits = [], {}
    for line in out.splitlines():
        sha, _, rest = line.partition(" ")
        stamp, _, subject = rest.partition(" ")
        match = re.match(r"Play (s\d+\.text\.\d+)", subject)
        if match:
            plays.append((int(stamp), match.group(1)))
            commits[match.group(1)] = sha
    plays = sorted(plays)[-last:]
    # Запись хода без текста сцены (бухгалтерия) и прочитанные страницы: то, что
    # ведущий делает сверх самой сцены.
    write, reads = [], []
    for _, turn_id in plays:
        try:
            raw = subprocess.run(["git", "-C", str(state_dir), "show", f"{commits[turn_id]}:recovery/TURN.json"],
                                 capture_output=True, text=True, check=True).stdout
            turn = json.loads(raw)
        except (OSError, subprocess.CalledProcessError, ValueError):
            continue
        write.append(len(json.dumps({k: v for k, v in turn.items() if k not in ("gm", "player")},
                                    ensure_ascii=False)))
        reads.append(len(turn.get("read") or []))
    gaps = [(b[0] - a[0]) / 60 for a, b in zip(plays, plays[1:])
            if a[1][:3] == b[1][:3] and 0 < b[0] - a[0] < 6 * 3600]
    lengths = {}
    for path in sorted(Path(state_dir, "archive").glob("session-*-turns.jsonl"))[-1:]:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                lengths[row.get("id")] = len(row.get("gm") or "")
    chars = [lengths[t] for _, t in plays if t in lengths]
    if not gaps:
        return {"turns": len(plays)}
    gaps.sort()
    return {"turns": len(plays), "minutes_between_turns_median": round(statistics.median(gaps), 1),
            "minutes_p90": round(gaps[int(0.9 * (len(gaps) - 1))], 1),
            "gm_chars_median": int(statistics.median(chars)) if chars else None,
            "bookkeeping_chars_median": int(statistics.median(write)) if write else None,
            "pages_read_per_turn": round(statistics.mean(reads), 1) if reads else None,
            "note": "полный цикл: чтение игрока, его заявка и подготовка сцены"}


def autonomy(state_dir):
    """Своя воля NPC по сессиям: сколько ходов меняли планы и чем планы кончались."""
    by_session = {}
    for path in sorted(Path(state_dir, "archive/deltas").glob("session-*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            stats = by_session.setdefault(f"s{_session(row.get('id')):02}",
                                          {"turns_with_plans": 0, "fulfilled": 0, "abandoned": 0})
            touched = False
            for patch in ((row.get("d") or {}).get("cast") or {}).values():
                for agenda in ((patch or {}).get("intent") or {}).values() if isinstance(patch, dict) else []:
                    touched = True
                    status = agenda.get("status") if isinstance(agenda, dict) else None
                    if status in ("fulfilled", "abandoned"):
                        stats[status] += 1
            stats["turns_with_plans"] += touched
    return by_session
