"""Планировщик сюжета: сквозной сюжет → главы → побочные события → случайные сцены.

У каждого события конец записан с рождения: вопрос, концовки, предел.
Без этого ведущий-модель склонна тянуть линию бесконечно. Код не выбирает
исход за ведущего; он считает сцены, следит за пределами и напоминает
развязать событие. Всё хранится только в GM_ONLY (``plot``): концовки — тайна.

Запись в ходе (``TURN.json``)::

    "d": {"plot": {
      "main": {"kind": "main", "title": "…", "question": "…", "endings": ["…"]},
      "ch2":  {"kind": "chapter", "parent": "main", "title": "…", "question": "…",
               "endings": ["…", "…"], "limit": {"scenes": 20, "until": "0006-10-20T00:00:00"}},
      "theft": {"kind": "side", "parent": "ch2", "question": "…", "endings": ["…", "…", "ничего"],
                "limit": {"scenes": 4}, "threads": ["v7"]},
      "street": {"kind": "scene", "title": "уличный торговец"},
      "theft": {"touch": true},
      "street": {"closed": "Торговец ушёл ни с чем."},
      "ch3": {"kind": "chapter", "parent": "main", "status": "queued", "trigger": "…"}
    }}
"""
from __future__ import annotations

from datetime import datetime

KINDS = ("main", "chapter", "side", "scene")
ACTIVE_LIMIT = 5                 # побочные события и случайные сцены одновременно
SCENE_DEFAULT = {"scenes": 2}    # случайная сцена — один-два обмена
CLOSED_KEEP = 60
FIELDS = ("kind", "title", "question", "endings", "limit", "phase", "parent", "threads",
          "trigger", "notes", "status")

RULES = (
    "События ведутся в d.plot. Сквозной сюжет (main) делится на главы (chapter); у каждой главы, "
    "побочного события (side) и случайной сцены (scene) конец записан сразу: question — один вопрос; "
    "endings — 2–4 исхода, среди них неудача и «ничего не вышло»; limit — scenes и/или until. "
    "Случайная сцена — 1–2 обмена, предел по умолчанию 2 сцены, вопрос и концовки не обязательны. "
    "Каждый ход, где событие развивается, отмечай {\"touch\": true}; законченное — {\"closed\": \"исход\"}. "
    "На пределе следующая сцена разрешает событие одной из концовок по сыгранному; продолжение — "
    "только новым событием с новым вопросом. Преграда — лишь заложенная до заявки игрока; после "
    "разгадки новой не добавляй. Одновременно не больше 5 побочных событий и сцен; лишние — status "
    "\"queued\" с trigger. Главу открывай, когда закрыта прежняя."
)


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _events(hot):
    plot = hot.setdefault("_plot", {})
    return plot, plot.setdefault("events", {})


def apply(hot, patches, turn_id, at):
    """Применяет d.plot одного хода. Возвращает id событий, затронутых этим ходом."""
    if not isinstance(patches, dict):
        return []
    plot, events = _events(hot)
    touched = []
    for key, patch in patches.items():
        if patch is None:
            patch = {"closed": "снято ведущим"}
        if not isinstance(patch, dict):
            continue
        event = events.get(key)
        if event is None:
            event = {"id": key, "kind": patch.get("kind") or "side", "status": "active",
                     "opened": turn_id, "opened_at": at, "scenes": 0}
            events[key] = event
        for field in FIELDS:
            if field in patch and patch[field] is not None:
                event[field] = patch[field]
        if event.get("kind") not in KINDS:
            event["kind"] = "side"
        if event["kind"] == "scene" and not event.get("limit"):
            event["limit"] = dict(SCENE_DEFAULT)
        if patch.get("closed"):
            _close(plot, events, key, patch["closed"], turn_id, at)
            touched.append(key)
            continue
        if event.get("status") == "active" and event["kind"] != "main":
            if event.get("last") is None and patch.get("status") == "active":
                event["opened"], event["opened_at"] = turn_id, at   # вышло из очереди
            if event.get("last") != turn_id:
                event["scenes"] = int(event.get("scenes") or 0) + 1
                event["last"] = turn_id
            touched.append(key)
    return touched


def touch_threads(hot, threads, turn_id):
    """Ход, менявший линию d.threads, засчитывается связанному активному событию."""
    _plot, events = _events(hot)
    for event in events.values():
        if (event.get("status") == "active" and event.get("kind") != "main"
                and set(event.get("threads") or []) & set(threads or {})
                and event.get("last") != turn_id):
            event["scenes"] = int(event.get("scenes") or 0) + 1
            event["last"] = turn_id


def _close(plot, events, key, outcome, turn_id, at):
    event = events.pop(key, {"id": key})
    record = {"id": key, "kind": event.get("kind"), "title": event.get("title"),
              "question": event.get("question"), "parent": event.get("parent"),
              "outcome": str(outcome), "turn": turn_id, "at": at, "scenes": event.get("scenes", 0)}
    closed = plot.setdefault("closed", [])
    closed.append({k: v for k, v in record.items() if v not in (None, "")})
    del closed[:-CLOSED_KEEP]


def _over(event, now):
    limit = event.get("limit") or {}
    scenes = int(event.get("scenes") or 0)
    if limit.get("scenes") and scenes >= int(limit["scenes"]):
        return f"{scenes} из {limit['scenes']} сцен"
    until = _parse(limit.get("until"))
    if until and now and now >= until:
        return f"срок {limit['until']} наступил"
    return None


def _label(event):
    return f"«{event.get('title') or event.get('question') or event['id']}» (`{event['id']}`)"


def review(hot, turns):
    """Замечания свёртки для CHECKS.json: событие без конца, предел, перегруз, глава."""
    plot, events = _events(hot)
    now = _parse((hot.get("cursor") or {}).get("at"))
    turn_id = turns[-1]["id"] if turns else (hot.get("cursor") or {}).get("id")
    fresh = {t.get("id") for t in turns}
    notes = []

    def note(text):
        entry = {"turn": turn_id, "kind": "plot", "text": text}
        if entry not in notes:
            notes.append(entry)

    for event in events.values():
        if event.get("status") != "active" or event.get("kind") == "main":
            continue
        if event.get("last") in fresh or event.get("opened") in fresh:
            missing = []
            if event["kind"] != "scene":
                if not event.get("question"):
                    missing.append("question")
                if len(event.get("endings") or []) < 2:
                    missing.append("endings (2–4, с неудачей и «ничего»)")
                if not event.get("limit"):
                    missing.append("limit")
            if missing:
                note(f"Событие {_label(event)} без конца: добавь в d.plot {', '.join(missing)}.")
        over = _over(event, now)
        if over:
            endings = " / ".join(str(e) for e in (event.get("endings") or [])) or "исход по сыгранному"
            note(f"Событие {_label(event)} на пределе ({over}): разреши следующей сценой "
                 f"одной из концовок: {endings}. Новых преград не вводи; продолжение — новым событием.")
    side = [e for e in events.values() if e.get("status") == "active" and e.get("kind") in ("side", "scene")]
    if len(side) > ACTIVE_LIMIT:
        note(f"Активных побочных событий и сцен {len(side)}, больше {ACTIVE_LIMIT}: закрой или отложи "
             f"лишние (status \"queued\" с trigger).")
    main = events.get("main")
    if main:
        chapters = [e for e in events.values() if e.get("kind") == "chapter" and e.get("status") == "active"]
        if not chapters:
            queued = [e for e in events.values() if e.get("kind") == "chapter" and e.get("status") == "queued"]
            hint = f" Следующая в очереди: {_label(queued[0])}." if queued else ""
            note("У сквозного сюжета нет активной главы: открой следующую главу с концовками и пределом."
                 + hint)
    else:
        note("Сквозной сюжет не задан. Ближайшим ходом (можно OOC) запиши d.plot.main — question и endings "
             "по планам campaigns/shiki/plans/*GM_ONLY* — и текущую главу chapter с концовками и пределом.")
    stale = _stale_threads(hot)
    if stale:
        note(f"Линии без движения две сессии и больше: {', '.join(stale[:8])}"
             f"{' и ещё ' + str(len(stale) - 8) if len(stale) > 8 else ''}. Закрой каждую с исходом "
             "(d.threads.<id>.closed) или переведи в событие d.plot с концом (threads: [\"<id>\"]).")
    return notes


def _stale_threads(hot):
    """Линии HOT.threads, не менявшиеся две сессии и не привязанные к событию."""
    session = int(hot.get("session") or 0)
    linked = {t for e in (hot.get("_plot") or {}).get("events", {}).values() for t in e.get("threads") or []}
    stale = []
    for key, value in (hot.get("threads") or {}).items():
        last = (value or {}).get("turn") if isinstance(value, dict) else None
        if not last or key in linked:
            continue          # без хода — постоянная заметка (договор, цель), не событие
        try:
            last_session = int(str(last)[1:3])
        except ValueError:
            continue
        if session - last_session >= 2:
            stale.append(key)
    return stale


def brief(hot):
    """Короткий план для GM_BRIEF: что идёт, сколько осталось, что ждёт."""
    plot, events = _events(hot)
    now = _parse((hot.get("cursor") or {}).get("at"))

    def view(event):
        limit = event.get("limit") or {}
        left = None
        if limit.get("scenes"):
            left = max(0, int(limit["scenes"]) - int(event.get("scenes") or 0))
        item = {k: event.get(k) for k in ("id", "kind", "title", "question", "endings", "limit",
                                          "phase", "parent", "threads", "notes") if event.get(k)}
        item["scenes"] = event.get("scenes", 0)
        if left is not None:
            item["scenes_left"] = left
        if _over(event, now):
            item["at_limit"] = True
        return item

    active = [view(e) for e in events.values() if e.get("status") == "active" and e.get("kind") != "main"]
    order = {"chapter": 0, "side": 1, "scene": 2}
    active.sort(key=lambda e: (order.get(e["kind"], 3), e["id"]))
    queued = [{k: e.get(k) for k in ("id", "kind", "title", "trigger", "parent") if e.get(k)}
              for e in events.values() if e.get("status") == "queued"]
    main = events.get("main")
    return {"rules": RULES,
            "main": ({k: main.get(k) for k in ("title", "question", "endings", "notes") if main.get(k)}
                     if main else None),
            "active": active, "queued": queued[:10],
            "recently_closed": (plot.get("closed") or [])[-5:]}


def page(hot):
    """Страница memory/PLOT.GM_ONLY.md."""
    data = brief(hot)
    lines = ["# Сюжет и события", "", "ТОЛЬКО ВЕДУЩИЙ. Концовки событий — тайна.", "", "## Правила", data["rules"], ""]
    main = data["main"]
    lines.append("## Сквозной сюжет")
    if main:
        lines.append(f"- {main.get('title', '')}: {main.get('question', '')}")
        for ending in main.get("endings") or []:
            lines.append(f"    - концовка: {ending}")
        for item in main.get("notes") or []:
            lines.append(f"    - условие: {item}")
    else:
        lines.append("- не задан")
    lines += ["", f"## Идут сейчас ({len(data['active'])})"]
    for event in data["active"]:
        mark = " · НА ПРЕДЕЛЕ" if event.get("at_limit") else ""
        left = f", осталось {event['scenes_left']}" if "scenes_left" in event else ""
        lines.append(f"- [{event['kind']}] {event.get('title') or event['id']} (`{event['id']}`): "
                     f"{event.get('question', '')} · сцен {event['scenes']}{left}{mark}")
        for ending in event.get("endings") or []:
            lines.append(f"    - концовка: {ending}")
    if data["queued"]:
        lines += ["", "## В очереди"]
        lines += [f"- [{e.get('kind')}] {e.get('title') or e['id']} (`{e['id']}`): {e.get('trigger', '')}"
                  for e in data["queued"]]
    if data["recently_closed"]:
        lines += ["", "## Недавно закрыты"]
        lines += [f"- {e.get('turn')}: {e.get('title') or e['id']} — {e.get('outcome')}"
                  for e in data["recently_closed"]]
    return "\n".join(lines) + "\n"
