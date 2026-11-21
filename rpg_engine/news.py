"""Вести мира и темп: случившееся за кадром доходит до Шики, а не копится списком.

Свёртка сама исполняет чужие планы (``will.advance``): за три игровых дня их
набирается десяток. Ведущий видел их сплошным списком с пассивным правилом,
почти ничего не доводил до Шики, и мир казался неподвижным, хотя жил. Здесь:

* **вес события** — канон мира, ход против Шики, провал или полууспех, человек,
  знакомый Шики, — выше; канцелярский исход («получит перечень, но не…») — ниже;
* **NEXT.offscreen** — только весомые (до ``TOP``), остальные счётом;
* **весть**: после скачка времени или ``PULSE`` ходов без вестей одна весомая
  весть становится пунктом ``must`` — довести до Шики к месту через доступный
  ему канал. Доставленной она считается, когда её имена появились в тексте
  ведущего; не доставленная за ``HOLD`` ходов снимается (счёт — в PACE.json);
* **темп**: сцена дольше ``LONG`` ходов — мир входит в неё сам (весть,
  чужой человек, помеха, срок), не решая за Шики;
* **PACE.json** — числа для разбора ведения: по ним, а не по самоощущению.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import aging
from .heuristic_history import stem, tokens

PULSE = 8            # ходов без вести — мир напоминает о себе…
PULSE_HOURS = 3      # …если и игровых часов прошло столько (плотная сцена в час — не повод)
JUMP_HOURS = 6       # скачок игрового времени — повод для вести
HOLD = 3             # ходов держится поручение довести весть…
HOLD_HOURS = 3       # …и игровых часов; в закрытой плотной сцене весть ждёт выхода
HOLD_MAX = 12        # но не дольше стольких ходов
LONG = 12            # ходов в одной сцене — мир входит в неё
TOP = 5              # весомых событий за кадром в NEXT.offscreen
MIN_WEIGHT = 1
PATH = "recovery/PACE.json"
BUREAU = re.compile(r"перечен|списо?к|хронолог|сверк|протокол|журнал|расхожден|копи[юяийе]|подтвержден|"
                    r"оценк|запис|реестр|отчёт|отчет|документальн", re.I)
HEDGE = re.compile(r"\bно не\b|,\s*не\s+\w+|\bбез\s+(полн|фактич|восстанов|устан)|\bне\s+(установ|восстанов|устран)", re.I)
NAME = re.compile(r"[А-ЯЁ][а-яё]{3,}")
COMMON = frozenset(stem(w) for w in ("Шики", "Мугами", "Неизвестный", "Неустановленный", "Старший", "Младший",
                                     "Дежурный", "Дежурная", "Принимающий", "Глава"))
CHANNEL = ("через то, что у Шики есть: посредник или брокер, письмо, заказ, слух в дороге или на рынке, "
           "след, тело, перемена в знакомом месте")
STATE_RULE = ("Числа для разбора ведения (OOC): отвечай по ним — что было, что меняется со следующего хода. "
              "Без извинений, покаяния и оценок себя: длину сцены выбирает игрок, ведущий отвечает за мир вокруг.")


def dry(text):
    """Канцелярский исход: бумага и оговорка вместо перемены мира."""
    text = str(text or "")
    return bool(BUREAU.search(text) and HEDGE.search(text))


def weight(hot, event):
    """Насколько событие может стать вестью для Шики."""
    if event.get("canon"):
        return 8
    score = 0
    if event.get("against_shiki"):
        score += 3
    score += {"failed": 2, "partial": 1}.get(event.get("status"), 0)
    profile = (hot.get("_psyche") or {}).get(event.get("npc")) or {}
    if profile.get("dread"):           # встречал Шики и как-то к нему отнёсся
        score += 2
    if dry(event.get("outcome")):
        score -= 3
    return score


def _text(event, hot):
    from . import will
    name = event.get("name") if event.get("name") and event["name"] != event.get("npc") else will._name(hot, event.get("npc"))
    word = {"fulfilled": "сбылось", "partial": "наполовину", "failed": "сорвалось"}.get(event.get("status"), "")
    when = str(event.get("at") or "")[5:16].replace("T", " ")
    outcome = str(event.get("outcome") or "")
    tag = f" ({word})" if word and not event.get("canon") and not outcome.casefold().startswith(word) else ""
    text = f"{when} {name}{tag}: {outcome}"
    if event.get("against_shiki"):
        text += f" Против Шики: {event['against_shiki']}"
    return " ".join(text.split())


def _markers(event, hot):
    """Основы имён, по которым видно, что весть дошла до текста ведущего."""
    from . import will
    source = f"{event.get('name') or ''} {will._name(hot, event.get('npc'))}"
    if event.get("canon"):
        source += " " + str(event.get("outcome") or "")
    found = {stem(w.casefold()) for w in NAME.findall(source)} - COMMON
    return sorted(b for b in found if len(b) >= 4)


def _mentions(text, markers):
    words = {stem(w) for w in tokens(text)}
    return any(m in words or any(w.startswith(m) for w in words if len(m) >= 5) for m in markers)


def pending(hot):
    """Свежие события за кадром, ещё не ставшие вестью: весомые, новые — раньше."""
    from . import will
    now = _parse((hot.get("cursor") or {}).get("at"))
    events = []
    for event in will._state(hot).get("happened") or []:
        at = _parse(event.get("at"))
        if event.get("news") or (now and at and (now - at).days > will.NEWS_DAYS):
            continue
        if weight(hot, event) >= MIN_WEIGHT:
            events.append(event)
    events.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    events.sort(key=lambda e: -weight(hot, e))
    return events


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def step(hot, applied):
    """Доставка вести, темп сцены; возвращает пункт для NEXT или None."""
    from . import will
    state = will._state(hot).setdefault("news", {"offered": 0, "delivered": 0, "missed": 0})
    cursor = hot.get("cursor") or {}
    now = aging._ordinal(cursor.get("id"))
    at = _parse(cursor.get("at"))
    if now is None:
        return None
    played = [t for t in applied or [] if t.get("kind") not in ("ooc", "technical", "void")]
    text = " ".join(f"{t.get('gm') or ''} {t.get('line') or ''}" for t in played)
    current = state.get("current")
    if current and played:
        if _mentions(text, current.get("markers") or []):
            state["delivered"] += 1
            _mark(hot, current, "delivered")
            state.pop("current")
            state["last"], state["last_at"] = now, cursor.get("at")
        elif _expired(current, now, at):
            state["missed"] += 1
            _mark(hot, current, "missed")
            state.pop("current")
            state["last"], state["last_at"] = now, cursor.get("at")
    # Темп: сколько ходов подряд одна сцена (место до первой точки и присутствующие).
    scene = hot.get("scene") or {}
    root = str(cursor.get("place") or "").split(". ", 1)[0]
    key = json.dumps([root, sorted(scene.get("present") or [])], ensure_ascii=False)
    if played:
        if state.get("scene_key") == key:
            state["scene_turns"] = int(state.get("scene_turns") or 0) + len(played)
        else:
            state["scene_key"], state["scene_turns"], state["scene_since"] = key, len(played), played[0].get("id")
    previous = _parse(state.get("at"))
    state["at"] = cursor.get("at")
    state.setdefault("last", now)
    if (hot.get("_fight") or {}).get("active"):
        return _view(state)
    jump = bool(played and previous and at and (at - previous).total_seconds() >= JUMP_HOURS * 3600)
    since = _parse(state.get("last_at"))
    quiet = now - int(state.get("last") or now) >= PULSE and (
        not since or not at or (at - since).total_seconds() >= PULSE_HOURS * 3600)
    long = int(state.get("scene_turns") or 0) >= LONG and now - int(state.get("nudged") or -10**6) >= LONG
    if state.get("current") or not (jump or quiet or long):
        return _view(state)
    candidates = pending(hot)
    if candidates:
        event = candidates[0]
        state["current"] = {"text": _text(event, hot)[:400], "markers": _markers(event, hot), "set": now,
                            "set_at": cursor.get("at"),
                            "npc": event.get("npc"), "plan": event.get("plan"), "at": event.get("at"),
                            "why": "скачок времени" if jump else "долгая сцена" if long else "давно без вестей"}
        event["news"] = "offered"
        state["offered"] += 1
        state["last"], state["last_at"] = now, cursor.get("at")
        if long:
            state["nudged"] = now
        return _view(state)
    if long:
        state["nudged"] = now
        return {"must": (f"Сцена идёт {state['scene_turns']} ходов. Понятое игроком сожми до результата и цены "
                         "одним ходом; пусть мир войдёт в сцену сам — чужой человек, весть, помеха или наступивший "
                         "срок, — не решая за Шики; игрок волен продолжить.")}
    return _view(state)


def _expired(current, now, at):
    """Весть снята: прошло HOLD ходов и HOLD_HOURS игровых часов — или HOLD_MAX ходов."""
    turns = now - int(current.get("set") or now)
    if turns >= HOLD_MAX:
        return True
    since = _parse(current.get("set_at"))
    hours_ok = not since or not at or (at - since).total_seconds() >= HOLD_HOURS * 3600
    return turns >= HOLD and hours_ok


def _mark(hot, current, status):
    from . import will
    for event in will._state(hot).get("happened") or []:
        if (event.get("npc"), event.get("plan"), event.get("at")) == (current.get("npc"), current.get("plan"), current.get("at")):
            event["news"] = status


def _view(state):
    current = state.get("current")
    if not current:
        return None
    long = state.get("scene_turns", 0) >= LONG
    return {"must": (f"Весть мира: {current['text']} — доведи до Шики в ближайшие {HOLD} хода, к месту, {CHANNEL}. "
                     "Не объявляй «новость»: Шики слышит часть, с искажением, и сам решает, что с ней делать."
                     + (f" Сцена идёт {state['scene_turns']} ходов — повод." if long else "")),
            "why": current.get("why")}


def offscreen(hot, texts):
    """[(вес, текст)] за кадром → весомые до TOP и счёт остальных."""
    ranked = sorted(texts, key=lambda wt: -wt[0])
    shown = [t for w, t in ranked if w >= MIN_WEIGHT][:TOP]
    rest = len(ranked) - len(shown)
    if rest > 0:
        shown.append(f"…и ещё {rest} мелких исходов за кадром: до Шики не доходят, пока их не принесёт канал.")
    return shown


def write_pace(state_dir, hot):
    """recovery/PACE.json: темп, главы, вести и эхо — числа для разбора ведения."""
    state = (hot.get("_will") or {}).get("news") or {}
    echo = (hot.get("fold") or {}).get("echo") or {}
    events = ((hot.get("_plot") or {}).get("events") or {})
    chapters = [{"id": e.get("id") or k, "title": e.get("title"), "scenes": e.get("scenes", 0),
                 "limit": (e.get("limit") or {}).get("scenes"), "last_touched": e.get("last")}
                for k, e in events.items() if e.get("kind") == "chapter" and e.get("status") == "active"]
    ahead = sorted(({"id": e.get("id") or k, "title": e.get("title"), "starts": e.get("starts")}
                    for k, e in events.items() if e.get("kind") == "chapter" and e.get("status") == "queued"),
                   key=lambda e: (e["starts"] is None, str(e["starts"])))[:3]
    pace = {"schema": "npr-pace/1", "cursor": (hot.get("cursor") or {}).get("id"),
            "scene": {"turns": state.get("scene_turns", 0), "since": state.get("scene_since")},
            "chapters": chapters, "next_chapters": ahead,
            "news": {k: state.get(k, 0) for k in ("offered", "delivered", "missed")}
                    | {"pending": len(pending(hot)), "carrying": bool(state.get("current"))},
            "echo": {"offered": echo.get("offered", 0), "used": echo.get("used_count", 0)},
            "rule": STATE_RULE}
    path = Path(state_dir) / PATH
    body = json.dumps(pace, ensure_ascii=False, indent=1) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != body:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return pace
