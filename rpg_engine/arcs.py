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

* ``track`` — после хода: все ``keys`` в тексте — событие «идёт» (started), закрывает его
  запись ведущего ``d.arc.<id>``: ``"played"`` или ``"changed: причина"``. Порядок канона
  (``step``): дошли до события — всё, что раньше, случилось (offscreen) или отмечено пропуском.
  ``fixed`` — опорное событие: изменить его может только прямое решение игрока.
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
# s11.text.043: «changed: отряд шёл другим маршрутом, точка перехвата Итачи осталась в стороне» —
# событие отменено опозданием и географией. Автономия мира выше сцены Шики (поправка игрока
# 23.11.2026): действующее лицо добивается цели иначе — находит, догоняет, срывает начатое.
EVASION = re.compile(
    r"маршрут|в стороне|разминул|мимо|на другом (?:подходе|пути)|другим путём|другим путем|не встретил|не встрет"
    r"|не успел|опоздал|не застал|не дождал|не оказалось рядом|не было рядом|не пересекл|точк\w* перехвата"
    r"|шики (?:не было|ушёл|ушел|отсутствовал|был в другом)|сражени\w* за шики не было|за шики не сыгран"
    r"|не сыграно|осталось за кадром|не произошло", re.I)
AGENCY = re.compile(r"шики|мугами|немер", re.I)
AHEAD = 3            # событий арки в NEXT
LOOKAHEAD_DAYS = 3   # событие следующей арки показывается за столько дней
HAPPENED_HOURS = 24  # случившееся без Шики висит в NEXT столько игровых часов
STATUSES = ("played", "changed", "offscreen")
RULE = ("Автономия мира выше сцены Шики: события арки идут сами в свой срок и по порядку, где бы ни был Шики. "
        "Опоздание, другой маршрут, отсутствие Шики их не отменяют — действующее лицо добивается цели иначе: "
        "находит отряд, догоняет, вмешивается в чужую сцену и может сорвать начатое Шики. Изменить исход "
        "(d.arc.<id>: \"changed: причина\") может только поступок Шики, лишивший его цели или средства. "
        "Сыгранное — d.arc.<id>: \"played\". Случившееся без Шики (happened) доходит до него следом.")


PLAYER = re.compile(r"игрок|поправк\w* игрока|по заявке игрока", re.I)


def valid_change(why, fixed=False):
    """Изменение канона принимается, только если его причина — поступок Шики, а не разминка во времени и месте.

    Опорное событие (``fixed``: смерть Гаары после извлечения, жертва Тиё) меняет только прямое решение
    игрока: s11.text.071 «Гаара вырван до 10:00 — извлечение не завершится» оставил Гаару живым вопреки канону.
    """
    text = str(why or "")
    if fixed and not PLAYER.search(text):
        return False
    return bool(AGENCY.search(text) or PLAYER.search(text)) and not EVASION.search(text)


def _step(arc, beat):
    """Порядок канона: события с меньшим step случаются раньше; равный step — одновременно."""
    if isinstance(beat.get("step"), (int, float)):
        return beat["step"]
    return (arc.get("beats") or []).index(beat)


def _close_earlier(state, arc, beat, turn_id, at, notes):
    """Сцена дошла до события — всё, что по канону раньше него, уже случилось или пропущено.

    s11.text.043: пять печатей сняты, а извлечение (срок 10:00) числилось будущим — Гаару нашли живым.
    """
    for earlier in arc.get("beats") or []:
        if not isinstance(earlier, dict) or earlier is beat or _step(arc, earlier) >= _step(arc, beat):
            continue
        row = state.get(earlier["id"]) or {}
        if row.get("status") in STATUSES:
            continue
        if earlier.get("offscreen"):
            state[earlier["id"]] = {"status": "offscreen", "turn": turn_id, "at": at, "why": f"раньше «{beat['id']}»"}
        elif row.get("status") == "started":
            state[earlier["id"]] = {"status": "played", "turn": turn_id, "why": f"сцена ушла к «{beat['id']}»"}
        else:
            notes.append({"turn": turn_id, "kind": "arc", "text":
                          f"Канон по порядку: «{earlier.get('title')}» должно было случиться раньше «{beat.get('title')}»: "
                          f"{earlier.get('canon')} Сыграй его сейчас там, где отряд, или запиши d.arc.{earlier['id']}."})


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
    notes_early = []
    moved = []
    beats_by_id = {b["id"]: b for _, b in _beats(arcs)}
    ids = set(beats_by_id)
    for key, value in ((turn.get("d") or {}).get("arc") or {}).items():
        if key not in ids:
            continue
        text = str(value or "").strip()
        status = text.split(":", 1)[0].strip().casefold()
        why = text.split(":", 1)[1].strip() if ":" in text else ""
        beat = beats_by_id[key]
        if status == "changed" and not valid_change(why, beat.get("fixed")):
            notes_early.append(_refused(turn["id"], key, why, beat.get("fixed")))
            continue
        if status in STATUSES:
            state[key] = {"status": status, "turn": turn["id"], "why": why[:200]}
            moved.append(key)
    text = f"{turn.get('place') or ''}\n{turn.get('gm') or ''}".casefold()
    now = _parse(turn.get("at"))
    notes = notes_early
    for arc, beat in _beats(arcs):
        if _done(state, beat):
            continue
        start = _parse(arc.get("from"))
        if start and now and now < start:
            continue
        if _matches(beat, text):
            # Упоминание — ещё не исход (s11.text.052: «птица Дейдары» закрыла погоню за 17 ходов до неё).
            # Событие «идёт» и остаётся в NEXT, пока ведущий не отметит played или сцена не уйдёт дальше.
            if (state.get(beat["id"]) or {}).get("status") != "started":
                state[beat["id"]] = {"status": "started", "turn": turn["id"], "late": 0}
                moved.append(beat["id"])
            continue
        due = _parse(beat.get("due"))
        if not beat.get("offscreen") and due and now and due <= now:
            row = state.setdefault(beat["id"], {})
            row["late"] = int(row.get("late") or 0) + 1   # ходов мимо срока
        gate = beat.get("before")
        if gate and not beat.get("offscreen") and re.search(gate, text, re.I):
            notes.append({"turn": turn["id"], "kind": "arc", "text":
                          f"Канон пропущен: сцена дошла до того, перед чем по канону «{beat.get('title')}» "
                          f"({beat.get('where') or 'по пути'}): {beat.get('canon')} Сыграй его следующим ходом "
                          "там, где отряд сейчас, или запиши d.arc."
                          f"{beat['id']}: \"changed: причина\" — что из действий Шики это изменило."})
    for key in moved:   # дошли до события — всё, что по канону раньше, уже случилось
        arc = next(a for a, b in _beats(arcs) if b["id"] == key)
        _close_earlier(state, arc, beats_by_id[key], turn["id"], turn.get("at"), notes)
    return notes


def _refused(turn_id, key, why, fixed=False):
    tail = (" Это опорное событие канона: изменить его может только прямое решение игрока."
            if fixed else " Отменить может только поступок Шики, лишивший его цели или средства.")
    return {"turn": turn_id, "kind": "arc", "text":
            f"Изменение канона «{key}» не принято: «{str(why)[:140]}». Опоздание, маршрут, отсутствие Шики "
            "событие не отменяют — действующее лицо добивается цели иначе и приходит туда, где Шики сейчас." + tail}


def revalidate(hot, arcs):
    """Принятые раньше «changed» без поступка Шики возвращаются в ожидание (s11.text.043)."""
    state = _state(hot)
    ids = {b["id"] for _, b in _beats(arcs)}
    notes = []
    for key in sorted(ids):
        row = state.get(key) or {}
        beat = next((b for _, b in _beats(arcs) if b["id"] == key), {})
        if row.get("status") == "changed" and not valid_change(row.get("why"), beat.get("fixed")):
            notes.append(_refused((hot.get("cursor") or {}).get("id"), key, row.get("why"), beat.get("fixed")))
            state[key] = {"late": int(row.get("late") or 0)}
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
        pending.sort(key=lambda b: (_step(arc, b), _parse(b.get("due")) or datetime.max))
        out.setdefault("next", []).extend(
            f"[{b['id']}]{' (идёт — доведи до канонического итога)' if (state.get(b['id']) or {}).get('status') == 'started' else ''} "
            f"{_line(b, now)}" for b in pending[:AHEAD])
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
                late = int((state.get(beat["id"]) or {}).get("late") or 0)
                goal = f" Его цель — {beat['goal']}: добивается её там, где Шики и отряд сейчас." if beat.get("goal") else ""
                head = (f"Канон опаздывает на {late} ход(а): «{beat.get('title')}» открывает этот ход первым абзацем"
                        if late else f"Канон: «{beat.get('title')}» — срок {due.strftime('%d.%m %H:%M')} прошёл, сыграй сейчас")
                foes = (f" Противник — ref/FOES.json: {', '.join(beat['foes'])} (d.fight.foes под этим ключом; "
                        "именной: свой ход каждый обмен, бросок всегда)." if beat.get("foes") else "")
                return (f"{head}. {beat.get('canon')}{goal} Может ворваться в начатое и сорвать действия Шики; "
                        f"Шики волен ответить. Ни маршрут, ни опоздание событие не отменяют.{foes}")
    return None
