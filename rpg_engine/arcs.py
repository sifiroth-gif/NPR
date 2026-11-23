"""Канон Naruto внутри главы: события арки по порядку и сроку, без пропусков.

s11.text.041–042: отряд Какаши дошёл до убежища Акацуки, миновав всё, что по
канону случилось в дороге: подкрепление команды Гая, перехват подставным Итачи,
бой Гая с подставным Кисамэ, снятие пяти печатей. Вместо этого появились
выдуманные мёртвый перевозчик и «удары из-за плиты». Главы в плане сюжета были
(``ch13_kazekage``), а событий внутри главы ведущий не помнил — игрок дважды
сам подсказывал канон (Сакура в s11.text.033, Тиё в s11.text.039).

Хранитель ведёт список событий арки ``memory/_curated/canon_arcs.GM_ONLY.json``::

    {"arcs": [{"id": "kazekage_rescue", "title": "…", "from": "0010-03-12T03:00:00",
               "until": "0010-03-18T00:00:00",
               "beats": [{"id": "itachi_puppet", "title": "…", "due": "0010-03-15T09:00:00",
                          "where": "…", "who": "…", "canon": "что случается, если Шики не вмешался",
                          "keys": ["итачи"], "before": "пещер|убежищ", "offscreen": false}]}]}

Код:

* ``track`` — после хода отмечает событие сыгранным, если в тексте все его
  ``keys``; запись ведущего ``d.arc.<id>``: ``"played"`` или ``"changed: причина"``.
  Сцена дошла до ``before`` (вход в убежище), а событие до него не сыграно —
  замечание kind=arc: канон пропущен.
* ``advance`` — событие ``offscreen`` (без Шики: бой Гая, извлечение) по сроку
  случается само и уходит в ``NEXT.arc.happened``.
* ``view`` — ближайшие события арки: срок, место, кто, что по канону.
* ``must`` — просроченное событие с Шики рядом: сыграть сейчас или записать причину.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

SEED = "memory/_curated/canon_arcs.GM_ONLY.json"
AHEAD = 3            # событий арки в NEXT
LOOKAHEAD_DAYS = 3   # событие следующей арки показывается за столько дней
HAPPENED_HOURS = 24  # случившееся без Шики висит в NEXT столько игровых часов
STATUSES = ("played", "changed", "offscreen")
RULE = ("Канон Naruto — опорная траектория: события арки идут сами в свой срок и по порядку, даже без Шики. "
        "Не пропускай и не заменяй выдумкой; изменить исход может только причина из действий Шики или мира — "
        "тогда d.arc.<id>: \"changed: причина\". Сыгранное отметь d.arc.<id>: \"played\". Случившееся без "
        "Шики (happened) доходит до него следом: участник, весть, след.")


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def load(state_dir):
    try:
        data = json.loads((Path(state_dir) / SEED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [a for a in data.get("arcs") or [] if isinstance(a, dict) and a.get("id") and a.get("beats")]


def _state(hot):
    return hot.setdefault("_plot", {}).setdefault("canon", {})


def _beats(arcs):
    for arc in arcs:
        for beat in arc.get("beats") or []:
            if isinstance(beat, dict) and beat.get("id"):
                yield arc, beat


def _done(state, beat):
    return (state.get(beat["id"]) or {}).get("status") in STATUSES


def _matches(beat, text):
    keys = [k for k in beat.get("keys") or [] if str(k).strip()]
    return bool(keys) and all(re.search(k, text, re.I) for k in keys)


def _now(hot):
    return _parse((hot.get("cursor") or {}).get("at"))


def track(hot, turn, arcs):
    """После хода: запись ведущего d.arc, узнанные по ключам события и пропуск канона."""
    if not arcs or turn.get("kind") in ("ooc", "technical", "void"):
        return []
    state = _state(hot)
    ids = {b["id"] for _, b in _beats(arcs)}
    for key, value in ((turn.get("d") or {}).get("arc") or {}).items():
        if key not in ids:
            continue
        text = str(value or "").strip()
        status = text.split(":", 1)[0].strip().casefold()
        if status in STATUSES:
            state[key] = {"status": status, "turn": turn["id"], "why": text.split(":", 1)[1].strip()[:200]
                          if ":" in text else ""}
    text = f"{turn.get('place') or ''}\n{turn.get('gm') or ''}".casefold()
    now = _parse(turn.get("at"))
    notes = []
    for arc, beat in _beats(arcs):
        if _done(state, beat):
            continue
        start = _parse(arc.get("from"))
        if start and now and now < start:
            continue
        if _matches(beat, text):
            state[beat["id"]] = {"status": "played", "turn": turn["id"], "why": "по тексту сцены"}
            continue
        gate = beat.get("before")
        if gate and not beat.get("offscreen") and re.search(gate, text, re.I):
            notes.append({"turn": turn["id"], "kind": "arc", "text":
                          f"Канон пропущен: сцена дошла до того, перед чем по канону «{beat.get('title')}» "
                          f"({beat.get('where') or 'по пути'}): {beat.get('canon')} Сыграй его следующим ходом "
                          "там, где отряд сейчас, или запиши d.arc."
                          f"{beat['id']}: \"changed: причина\" — что из действий Шики это изменило."})
    return notes


def advance(hot, arcs):
    """Срок прошёл — событие без Шики случилось само: offscreen с ходом свёртки."""
    now = _now(hot)
    state = _state(hot)
    out = []
    for _, beat in _beats(arcs):
        due = _parse(beat.get("due"))
        if beat.get("offscreen") and due and now and due <= now and not _done(state, beat):
            state[beat["id"]] = {"status": "offscreen", "turn": (hot.get("cursor") or {}).get("id"),
                                 "at": now.isoformat(), "why": "срок канона"}
            out.append(beat["id"])
    return out


def _active(arcs, now):
    for arc in arcs:
        start, end = _parse(arc.get("from")), _parse(arc.get("until"))
        if now and start and start - timedelta(days=LOOKAHEAD_DAYS) <= now and (not end or now <= end):
            yield arc


def _when(due, now):
    if not due or not now:
        return ""
    hours = (due - now).total_seconds() / 3600
    stamp = due.strftime("%d.%m %H:%M")
    span = f"{int(abs(hours))} ч" if abs(hours) >= 1 else f"{int(abs(hours) * 60)} мин"
    if hours < 0:
        return f"{stamp}, просрочено на {span}"
    return f"{stamp}, через {span}" if hours < 72 else stamp


def _line(beat, now):
    parts = [beat.get("title") or beat["id"], _when(_parse(beat.get("due")), now), beat.get("where"),
             beat.get("who") and f"кто: {beat['who']}", beat.get("canon") and f"по канону: {beat['canon']}"]
    return " — ".join(str(p) for p in parts if p)


def view(hot, arcs):
    """NEXT.arc: ближайшие события арки и случившееся без Шики."""
    now = _now(hot)
    state = _state(hot)
    out = {}
    for arc in _active(arcs, now):
        pending = [b for b in arc.get("beats") or [] if isinstance(b, dict) and b.get("id") and not _done(state, b)]
        if not pending:
            continue
        pending.sort(key=lambda b: (_parse(b.get("due")) or datetime.max))
        out.setdefault("next", []).extend(f"[{b['id']}] {_line(b, now)}" for b in pending[:AHEAD])
        out.setdefault("arcs", []).append(arc.get("title") or arc["id"])
    happened = []
    for _, beat in _beats(arcs):
        row = state.get(beat["id"]) or {}
        at = _parse(row.get("at"))
        if row.get("status") == "offscreen" and at and now and now - at <= timedelta(hours=HAPPENED_HOURS):
            happened.append(f"{beat.get('title')}: {beat.get('canon')}")
    if happened:
        out["happened"] = happened[:3]
    if out:
        out["next"] = out.get("next", [])[:AHEAD]
        out["rule"] = RULE
    return out


def must(hot, arcs):
    """Пункт must: просроченное событие, где Шики рядом, ещё не сыграно."""
    now = _now(hot)
    state = _state(hot)
    for arc in _active(arcs, now):
        for beat in arc.get("beats") or []:
            if not isinstance(beat, dict) or not beat.get("id") or beat.get("offscreen") or _done(state, beat):
                continue
            due = _parse(beat.get("due"))
            if due and now and due <= now:
                return (f"Канон: «{beat.get('title')}» — срок {due.strftime('%d.%m %H:%M')} прошёл. "
                        f"{beat.get('canon')} Сыграй в этой сцене (Шики волен вмешаться и изменить исход) "
                        f"или запиши d.arc.{beat['id']}: \"changed: причина\".")
    return None
