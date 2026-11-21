"""Старение оперативной памяти: давнее уходит из HOT и GM_ONLY в историю.

HOT держит текущую сцену и то, что ещё живёт; всё давнее — в ``archive/aged/``
(построчно, дословно, с ходом ухода) и на страницах вики, откуда ведущий
достаёт адресно. Ничего не теряется: уходит то, чего давно не касались и что
не нужно нынешней сцене или действующему сюжету.

Вызывается каждой свёрткой. Если HOT всё равно близок к пределу, второй
проход сжимает окна — вместо блокировки свёртки.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

AGED_DIR = "archive/aged"
TURN = re.compile(r"s(\d{2})\.text\.(\d{3,4})")
SESSION_SPAN = 120          # приблизительная длина сессии в ходах для сравнения через сессии

NORMAL = {"thread_age": 40, "thing_age": 40, "knows": 12, "pact_events": 10, "intent_done": 3,
          "dread_log": 3, "plot_closed": 8, "dormant": 120}
TIGHT = {"thread_age": 15, "thing_age": 15, "knows": 6, "pact_events": 5, "intent_done": 1,
         "dread_log": 2, "plot_closed": 4, "dormant": 60}
# dormant: NPC не появлялся столько ходов и без живого плана — его история сжимается до последнего
# исполненного плана и последней записи страха (полностью — в archive/aged, вернётся — восстановится по нужде).
DONE_BRIEF = ("key", "at", "status", "outcome", "turn", "by")   # закрытый план: исход без всего плана


def _ordinal(turn_id):
    match = TURN.search(str(turn_id or ""))
    return int(match.group(1)) * SESSION_SPAN + int(match.group(2)) if match else None


def _age(hot, turn_id):
    now, then = _ordinal((hot.get("cursor") or {}).get("id")), _ordinal(turn_id)
    return None if now is None or then is None else now - then


def _append(state_dir, name, rows):
    if not rows:
        return
    path = Path(state_dir) / AGED_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


def load(state_dir, name):
    """Строки архива старения (последняя запись по ключу — действующая)."""
    path = Path(state_dir) / AGED_DIR / name
    rows = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row.get("key")] = row
    except (OSError, ValueError):
        pass
    return rows


CLOSED = ("fulfilled", "partial", "failed", "abandoned")
ALWAYS = ("shiki", "nemera")   # Шики и та, что в нём, присутствуют всегда: их линии и вещи не стареют


def _in_play(hot):
    """Что нужно сейчас: присутствующие, линии действующих событий, слова сцены."""
    scene = hot.get("scene") or {}
    present = set(scene.get("present") or []) | set(ALWAYS)
    live_threads = set()
    for event in ((hot.get("_plot") or {}).get("events") or {}).values():
        if isinstance(event, dict) and event.get("status") == "active":
            live_threads.update(event.get("threads") or [])
    text = " ".join(str(scene.get(k) or "") for k in ("situation", "pressure", "positions"))
    text += " " + str(((hot.get("last") or {}).get("line")) or "")
    return present, live_threads, text


def _mentioned(key, text):
    return bool(key) and key in text


def apply(state_dir, hot, *, size=None, limit=None):
    """Один-два прохода старения; возвращает отчёт для CHECKS."""
    report = _pass(state_dir, hot, NORMAL)
    if size and limit and size(hot) > limit:
        tight = _pass(state_dir, hot, TIGHT)
        report = {k: report.get(k, 0) + tight.get(k, 0) for k in set(report) | set(tight)}
        report["tight"] = 1
    return {k: v for k, v in report.items() if v}


def _pass(state_dir, hot, w):
    turn = (hot.get("cursor") or {}).get("id")
    present, live_threads, text = _in_play(hot)
    report = {}

    threads, gone = hot.get("threads") or {}, []
    for key, value in list(threads.items()):
        age = _age(hot, (value or {}).get("turn") if isinstance(value, dict) else None)
        if age is None:   # без отметки хода: считаем по последнему известному упоминанию
            age = _age(hot, (value or {}).get("last_known")) if isinstance(value, dict) else None
        if key.startswith(ALWAYS):
            continue
        if age is not None and age >= w["thread_age"] and key not in live_threads and not _mentioned(key, text):
            gone.append({"key": key, "value": threads.pop(key), "aged_at": turn})
    _append(state_dir, "threads.jsonl", gone)
    report["threads"] = len(gone)
    if gone or (Path(state_dir) / AGED_DIR / "threads.jsonl").is_file():
        hot["threads_older"] = f"{AGED_DIR}/threads.jsonl"

    things, gone = hot.get("things") or {}, []
    for key, value in list(things.items()):
        value = value if isinstance(value, dict) else {"value": value}
        holder = value.get("holder")
        age = _age(hot, value.get("turn") or value.get("since"))
        if holder in present or key.startswith(ALWAYS) or _mentioned(key, text):
            continue
        if age is not None and age >= w["thing_age"]:
            gone.append({"key": key, "value": things.pop(key), "aged_at": turn})
    _append(state_dir, "things.jsonl", gone)
    report["things"] = len(gone)
    if gone or (Path(state_dir) / AGED_DIR / "things.jsonl").is_file():
        hot["things_older"] = f"{AGED_DIR}/things.jsonl"

    shiki = hot.get("shiki") or {}
    knows = shiki.get("knows")
    if isinstance(knows, list) and len(knows) > w["knows"]:
        old, shiki["knows"] = knows[:-w["knows"]], knows[-w["knows"]:]
        _append(state_dir, "shiki-knows.jsonl", [{"key": f"{turn}:{i}", "value": v, "aged_at": turn}
                                                 for i, v in enumerate(old)])
        shiki["knows_older"] = f"{AGED_DIR}/shiki-knows.jsonl"
        report["shiki_knows"] = len(old)

    injuries, gone = hot.get("injuries") or {}, []
    for key in list(injuries):
        entries = injuries[key] if isinstance(injuries[key], list) else [injuries[key]]
        last = max((_ordinal((e or {}).get("turn")) or 0 for e in entries if isinstance(e, dict)), default=0)
        now = _ordinal(turn) or 0
        if key != "shiki" and key not in present and last and now - last >= w["thing_age"]:
            gone.append({"key": key, "value": injuries.pop(key), "aged_at": turn})
    _append(state_dir, "injuries.jsonl", gone)
    report["injuries"] = len(gone)
    if gone or (Path(state_dir) / AGED_DIR / "injuries.jsonl").is_file():
        hot["injuries_older"] = f"{AGED_DIR}/injuries.jsonl"

    pact = hot.get("pact") or {}
    events = pact.get("events")
    if isinstance(events, list) and len(events) > w["pact_events"]:
        old, pact["events"] = events[:-w["pact_events"]], events[-w["pact_events"]:]
        _append(state_dir, "pact-events.jsonl", [{"key": e.get("id") if isinstance(e, dict) else None,
                                                  "value": e, "aged_at": turn} for e in old])
        pact["events_older"] = f"{AGED_DIR}/pact-events.jsonl"
        report["pact_events"] = len(old)

    # Скрытое (GM_ONLY): истории планов и страха, исполненное за кадром, закрытый сюжет.
    plans, fears = [], []
    now = _ordinal(turn) or 0
    touched = ((hot.get("_will") or {}).get("touched") or {})
    for key, profile in (hot.get("_psyche") or {}).items():
        if not isinstance(profile, dict):
            continue
        live_plan = any(isinstance(p, dict) and p.get("status") not in CLOSED
                        for p in (profile.get("intent") or {}).values())
        seen = _ordinal(touched.get(key))
        dormant = (key not in present and not live_plan and seen is not None and now - seen >= w["dormant"])
        keep_done, keep_log = (1, 1) if dormant else (w["intent_done"], w["dread_log"])
        done = profile.get("intent_done")
        if isinstance(done, list) and len(done) > keep_done:
            old, profile["intent_done"] = done[:-keep_done], done[-keep_done:]
            plans += [{"key": f"{key}:{d.get('key')}", "npc": key, "value": d, "aged_at": turn} for d in old]
            # Ключи исполненного остаются: заготовка хранителя не воскресит закрытый план.
            profile["done_keys"] = list(dict.fromkeys((profile.get("done_keys") or []) +
                                                      [d.get("key") for d in old if d.get("key")]))[-80:]
        if isinstance(profile.get("intent_done"), list):
            profile["intent_done"] = [{k: d[k] for k in DONE_BRIEF if k in d} if i < len(profile["intent_done"]) - 1
                                      and isinstance(d, dict) else d
                                      for i, d in enumerate(profile["intent_done"])]
        dread = profile.get("dread")
        log = dread.get("log") if isinstance(dread, dict) else None
        if isinstance(log, list) and len(log) > keep_log:
            old, dread["log"] = log[:-keep_log], log[-keep_log:]
            fears += [{"key": f"{key}:{i}", "npc": key, "value": v, "aged_at": turn} for i, v in enumerate(old)]
    _append(state_dir, "npc-plans.GM_ONLY.jsonl", plans)
    _append(state_dir, "npc-dread.GM_ONLY.jsonl", fears)
    report["npc_plans"], report["npc_dread"] = len(plans), len(fears)

    will = hot.get("_will") or {}
    happened = will.get("happened")
    if isinstance(happened, list):
        from .will import SHOW_FOLDS
        spent = [e for e in happened if isinstance(e, dict) and e.get("shown", 0) >= SHOW_FOLDS]
        keep = [e for e in happened if e not in spent]
        if spent:
            will["happened"] = keep
            _append(state_dir, "world-events.GM_ONLY.jsonl",
                    [{"key": f"{e.get('npc')}:{e.get('plan')}", "value": e, "aged_at": turn} for e in spent])
            report["world_events"] = len(spent)

    plot = hot.get("_plot") or {}
    closed = plot.get("closed")
    if isinstance(closed, list) and len(closed) > w["plot_closed"]:
        old, plot["closed"] = closed[:-w["plot_closed"]], closed[-w["plot_closed"]:]
        _append(state_dir, "plot-closed.GM_ONLY.jsonl", [{"key": c.get("id"), "value": c, "aged_at": turn}
                                                          for c in old if isinstance(c, dict)])
        report["plot_closed"] = len(old)
    return report
