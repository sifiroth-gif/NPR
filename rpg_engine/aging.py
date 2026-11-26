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

# Окна в ходах. Текущая сессия не стареет ни в каком проходе (``_this_session``): игрок продолжает её,
# а не закрывает (23.11.2026). Стареет прошлое — сессии назад.
NORMAL = {"thread_age": 120, "thing_age": 120, "knows": 24, "pact_events": 20, "intent_done": 6,
          "dread_log": 6, "plot_closed": 12, "dormant": 240}
TIGHT = {"thread_age": 60, "thing_age": 60, "knows": 12, "pact_events": 10, "intent_done": 3,
         "dread_log": 3, "plot_closed": 8, "dormant": 120}
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


def _session_of(turn_id):
    match = TURN.search(str(turn_id or ""))
    return int(match.group(1)) if match else None


def _this_session(hot, turn_id):
    """Запись текущей сессии: в оперативной памяти до её конца, в каком бы проходе ни было старение."""
    now = _session_of((hot.get("cursor") or {}).get("id"))
    # Закрытие сессии (session_close): закрываемая уже прошлая — её и разгружает строгий проход.
    handoff = hot.get("handoff") or {}
    if now is not None and handoff.get("closed") == now and handoff.get("next_session"):
        now = int(handoff["next_session"])
    return now is not None and _session_of(turn_id) == now


def _turn_of(value):
    if isinstance(value, dict):
        for key in ("turn", "last_known", "since", "id", "aged_at"):
            if _session_of(value.get(key)) is not None:
                return value.get(key)
    return value if isinstance(value, str) else None


def _split_keep(hot, items, keep):
    """Старое сверх окна — кроме записей текущей сессии: они остаются при любом окне."""
    if not isinstance(items, list) or len(items) <= keep:
        return [], items
    old = [x for x in items[:-keep] if not _this_session(hot, _turn_of(x))]
    kept = [x for x in items if x not in old]
    return old, kept


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
        if _this_session(hot, _turn_of(value)):
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
        if _this_session(hot, value.get("turn") or value.get("since")):
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
        old, shiki["knows"] = _split_keep(hot, knows, w["knows"])
        _append(state_dir, "shiki-knows.jsonl", [{"key": f"{turn}:{i}", "value": v, "aged_at": turn}
                                                 for i, v in enumerate(old)])
        shiki["knows_older"] = f"{AGED_DIR}/shiki-knows.jsonl"
        report["shiki_knows"] = len(old)

    injuries, gone = hot.get("injuries") or {}, []
    for key in list(injuries):
        entries = injuries[key] if isinstance(injuries[key], list) else [injuries[key]]
        last = max((_ordinal((e or {}).get("turn")) or 0 for e in entries if isinstance(e, dict)), default=0)
        now = _ordinal(turn) or 0
        if key != "shiki" and key not in present and last and now - last >= w["thing_age"] \
                and not any(_this_session(hot, (e or {}).get("turn")) for e in entries if isinstance(e, dict)):
            gone.append({"key": key, "value": injuries.pop(key), "aged_at": turn})
    _append(state_dir, "injuries.jsonl", gone)
    report["injuries"] = len(gone)
    if gone or (Path(state_dir) / AGED_DIR / "injuries.jsonl").is_file():
        hot["injuries_older"] = f"{AGED_DIR}/injuries.jsonl"

    pact = hot.get("pact") or {}
    events = pact.get("events")
    if isinstance(events, list) and len(events) > w["pact_events"]:
        old, pact["events"] = _split_keep(hot, events, w["pact_events"])
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
            old, profile["intent_done"] = _split_keep(hot, done, keep_done)
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
            old, dread["log"] = _split_keep(hot, log, keep_log)
            fears += [{"key": f"{key}:{i}", "npc": key, "value": v, "aged_at": turn} for i, v in enumerate(old)]
    _append(state_dir, "npc-plans.GM_ONLY.jsonl", plans)
    _append(state_dir, "npc-dread.GM_ONLY.jsonl", fears)
    report["npc_plans"], report["npc_dread"] = len(plans), len(fears)

    will = hot.get("_will") or {}
    # Исполненные обязанности прошлых сессий больше не требуют полного текста.
    # Архивируем первичную запись, оставляя tombstone с тем же ID: select()
    # не предложит исполненное снова. Неактивное не означает исполненное.
    duties = will.get("obligations") or {}
    compacted = []
    for ident, row in duties.items():
        if not isinstance(row, dict) or not row.get("done") or "text" not in row:
            continue
        if row.get("kind") in ("news", "due", "arc"):
            continue
        if _session_of(row["done"]) is None or any(
                _this_session(hot, row.get(field)) for field in ("since", "last", "done")):
            continue
        compacted.append({"key": ident, "value": row, "aged_at": turn})
    if compacted:
        name = "obligations-done.GM_ONLY.jsonl"
        archived = load(state_dir, name)
        _append(state_dir, name, [r for r in compacted
                                  if archived.get(r["key"], {}).get("value") != r["value"]])
        for record in compacted:
            row = record["value"]
            duties[record["key"]] = {k: row[k] for k in ("kind", "done", "inactive") if k in row}
        report["obligations_done"] = len(compacted)
    happened = will.get("happened")
    if isinstance(happened, list):
        from .will import SHOW_FOLDS
        from . import news
        spent = [e for e in happened if isinstance(e, dict) and e.get("shown", 0) >= SHOW_FOLDS
                 and (not news.modern(hot) or e.get("news") == "delivered"
                      or news.weight(hot, e) < news.MIN_WEIGHT and e.get("news") not in ("offered", "deferred"))]
        keep = [e for e in happened if e not in spent]
        if spent:
            will["happened"] = keep
            _append(state_dir, "world-events.GM_ONLY.jsonl",
                    [{"key": e.get("id") or news.event_id(e), "value": e, "aged_at": turn} for e in spent])
            report["world_events"] = len(spent)

    plot = hot.get("_plot") or {}
    closed = plot.get("closed")
    if isinstance(closed, list) and len(closed) > w["plot_closed"]:
        old, plot["closed"] = _split_keep(hot, closed, w["plot_closed"])
        _append(state_dir, "plot-closed.GM_ONLY.jsonl", [{"key": c.get("id"), "value": c, "aged_at": turn}
                                                          for c in old if isinstance(c, dict)])
        report["plot_closed"] = len(old)
    return report


RESTORE = {   # файл архива → куда вернуть
    "threads.jsonl": "threads", "things.jsonl": "things", "injuries.jsonl": "injuries",
    "shiki-knows.jsonl": "shiki_knows", "pact-events.jsonl": "pact_events",
    "npc-plans.GM_ONLY.jsonl": "plans", "npc-dread.GM_ONLY.jsonl": "dread", "plot-closed.GM_ONLY.jsonl": "plot",
}


def _of_session(row, session, kind):
    """Запись сыграна в этой сессии (давнее, ушедшее по своему возрасту, не возвращается)."""
    value = row.get("value")
    if kind == "injuries" and isinstance(value, list):
        return any(_session_of((e or {}).get("turn")) == int(session) for e in value if isinstance(e, dict))
    if kind == "pact_events":
        return _session_of((value or {}).get("id") if isinstance(value, dict) else value) == int(session)
    source = _turn_of(value) if isinstance(value, dict) else (value if kind == "shiki_knows" else None)
    if _session_of(source) is not None:
        return _session_of(source) == int(session)
    return kind in ("plans", "dread")   # исход плана без отметки хода: история NPC, возвращается


def restore(state_dir, hot, session):
    """Возвращает в HOT/GM_ONLY то, что ушло в архив в ходе сессии ``session`` (разгрузки 23.11.2026).

    Архив не трогается (дописывается только); вернувшееся снова не стареет до конца сессии.
    Уже показанные вести мира (world-events) не возвращаются: они сыграли.
    """
    prefix = f"s{int(session):02d}."
    report = {}
    for name, kind in RESTORE.items():
        path = Path(state_dir) / AGED_DIR / name
        try:
            rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
        except (OSError, ValueError):
            continue
        rows = [r for r in rows if str(r.get("aged_at") or "").startswith(prefix) and _of_session(r, session, kind)]
        back = 0
        for row in rows:
            value, key = row.get("value"), row.get("key")
            if kind in ("threads", "things", "injuries"):
                box = hot.setdefault(kind, {})
                if key and key not in box:
                    box[key] = value
                    back += 1
            elif kind == "shiki_knows":
                knows = hot.setdefault("shiki", {}).setdefault("knows", [])
                if value not in knows:
                    knows.insert(0, value)
                    back += 1
            elif kind == "pact_events":
                events = hot.setdefault("pact", {}).setdefault("events", [])
                if value not in events:
                    events.insert(0, value)
                    back += 1
            elif kind in ("plans", "dread"):
                profile = (hot.setdefault("_psyche", {})).setdefault(row.get("npc"), {})
                if kind == "plans":
                    done = profile.setdefault("intent_done", [])
                    if not any(isinstance(d, dict) and d.get("key") == (value or {}).get("key")
                               and d.get("at") == (value or {}).get("at") for d in done):
                        done.append(value)
                        done.sort(key=lambda d: str((d or {}).get("at") or ""))
                        back += 1
                else:
                    log = profile.setdefault("dread", {}).setdefault("log", [])
                    if value not in log:
                        log.append(value)
                        log.sort(key=lambda v: _ordinal((v or {}).get("turn")) or 0)
                        back += 1
            elif kind == "plot":
                closed = hot.setdefault("_plot", {}).setdefault("closed", [])
                if not any(isinstance(c, dict) and c.get("id") == key for c in closed):
                    closed.append(value)
                    back += 1
        if back:
            report[name] = back
    return report
