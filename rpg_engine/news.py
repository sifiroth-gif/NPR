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

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from . import aging
from .heuristic_history import stem, tokens

PULSE = 8            # ходов без вести — мир напоминает о себе…
PULSE_HOURS = 1      # …если прошёл и игровой час (плотная сцена в минуты — не повод)
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


def _favor(hot):
    """Окно «мир не подыгрывает» (favor.py): похвалы, уступки, без цены, трение — для разбора."""
    from . import favor
    return favor.totals(hot)


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
    when = str(event.get("at") or "")[5:16].replace("T", " ")
    outcome = str(event.get("outcome") or "")
    text = f"{when[3:5]}.{when[:2]} {when[6:]} — {name}. {outcome}"
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


# Весть дошла, когда у неё есть канал (диагностика ведущего 25.11.2026: доставку засчитывали по
# словам): её говорит NPC, или в повествовании с её именами есть канал или событие — слух, письмо,
# гонец, «говорят», пришёл, нашли. Имена в речи и мыслях самого Шики или в заявке игрока — не весть.
CHANNEL = re.compile(r"(?<![\wё])(?:слух\w*|вест\w*|извест\w*|письм\w*|записк\w*|донесени\w*|донёс\w*|"
                     r"доклад\w*|сообщ\w*|рассказ\w*|говорят|болтают|шепчутся|гонец\w*|гонц\w*|курьер\w*|"
                     r"торговц\w*|листовк\w*|объявлени\w*|приказ\w*|услышал\w*|узнал\w*|прочитал\w*|"
                     r"передал\w*|принёс\w*|принес\w*|прислал\w*)", re.I)


def _delivered(turns, markers):
    from .echo import EVENT, LABEL, RECALL_ONLY, SENT
    for turn in turns:
        for raw in str(turn.get("gm") or "").split("\n"):
            label = LABEL.match(raw)
            if label:
                if "шики" not in label.group(1).casefold() and _mentions(raw, markers):
                    return True    # NPC говорит о событии
                continue
            if raw.lstrip("*_ ").startswith(("—", "–")):
                continue           # реплика без метки — чья, неизвестно
            for match in SENT.finditer(raw):
                sentence = match.group()
                if (_mentions(sentence, markers) and (CHANNEL.search(sentence) or EVENT.search(sentence))
                        and not RECALL_ONLY.search(sentence)):
                    return True
    return False


C3_START = "s14.text.023"
EVENT_ID = re.compile(r"evt_[0-9a-f]{20}")
DELIVERY_CHANNELS = {"message", "document", "witness", "told_in_scene"}


def modern(hot):
    here = aging._ordinal((hot.get("cursor") or {}).get("id"))
    cut = aging._ordinal(C3_START)
    return here is not None and here >= cut


def event_id(event):
    identity = [event.get(k) for k in ("npc", "plan", "at", "turn", "status", "outcome")]
    return "evt_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:20]


def migrate(hot):
    """Assign IDs to retained events, including a legacy orphaned offer."""
    from . import will
    store = will._state(hot)
    state = store.setdefault("news", {"offered": 0, "delivered": 0, "missed": 0})
    for event in store.get("happened") or []:
        event.setdefault("id", event_id(event))
    current = state.get("current")
    if current and not current.get("id"):
        matches = [e for e in store.get("happened") or [] if all(
                   e.get(k) == current.get(k) for k in ("npc", "plan", "at"))]
        if len(matches) == 1:
            current["id"] = matches[0]["id"]
        else:
            current["id"] = event_id(dict(current, outcome=current.get("text"), status="legacy_offer"))
            current["legacy_offer"] = True
    return state


def validate_receipts(turn):
    rows = (turn.get("d") or {}).get("news_delivered")
    if rows is None:
        return []
    if turn.get("kind") in ("ooc", "technical", "void") or not isinstance(rows, list):
        raise ValueError("news_delivered requires played turn and list")
    gm = str(turn.get("gm") or "")
    from .echo import LABEL
    visible = []
    for raw in gm.splitlines():
        label = LABEL.match(raw)
        if label and "шики" in label.group(1).casefold():
            continue
        if not label and raw.lstrip("*_ ").startswith(("—", "–")):
            continue
        visible.append(raw)
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("news_delivered: expected objects")
        ident, channel, quote = (row.get(k) for k in ("id", "channel", "quote"))
        if not isinstance(ident, str) or not EVENT_ID.fullmatch(ident) or channel not in DELIVERY_CHANNELS:
            raise ValueError("news_delivered: stable event ID and channel required")
        if not isinstance(quote, str) or len(quote.strip()) < 8 or not any(quote in line for line in visible):
            raise ValueError("news_delivered: quote must be visible NPC speech or narration, not Shiki's speech")
        result.append(row)
    return result


def accept(hot, turn):
    """Called inside the atomic turn, before world resolution; amend rolls back."""
    rows = validate_receipts(turn)
    if not rows:
        return
    from . import will
    state = migrate(hot)
    delivered = state.setdefault("receipts", {})
    current = state.get("current")
    events = {e["id"]: e for e in will._state(hot).get("happened") or [] if e.get("id")}
    for row in rows:
        ident = row["id"]
        if ident in delivered:
            continue
        event = events.get(ident)
        offered = current if current and current.get("id") == ident else None
        if not event and not offered:
            raise ValueError("news_delivered: unknown event ID")
        source = event or offered
        at, now = _parse(source.get("at")), _parse(turn.get("at"))
        if at and now and at > now:
            raise ValueError("news_delivered: event has not happened")
        delivered[ident] = {"turn": turn["id"], "channel": row["channel"], "quote": row["quote"]}
        if event:
            event["news"] = "delivered"
        if offered:
            state.pop("current", None)
            current = None
        state["delivered"] = int(state.get("delivered") or 0) + 1
        state["last"], state["last_at"] = aging._ordinal(turn["id"]), turn["at"]


def pending(hot):
    """Свежие события за кадром, ещё не ставшие вестью: весомые, новые — раньше."""
    from . import will
    now = _parse((hot.get("cursor") or {}).get("at"))
    events = []
    for event in will._state(hot).get("happened") or []:
        at = _parse(event.get("at"))
        if modern(hot):
            if event.get("news") in ("delivered", "offered"):
                continue
        elif event.get("news") or (now and at and (now - at).days > will.NEWS_DAYS):
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
    strict = modern(hot)
    if strict:
        state = migrate(hot)
    current = state.get("current")
    if current and played and not strict:
        if _delivered(played, current.get("markers") or []):
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
    state.setdefault("start_at", cursor.get("at"))
    if (hot.get("_fight") or {}).get("active"):
        return _view(state)
    jump = bool(played and previous and at and (at - previous).total_seconds() >= JUMP_HOURS * 3600)
    # Новые игровые сутки без вести — тоже повод: мир не молчит дольше дня.
    day = bool(played and at and since_day(state) and at.date() > since_day(state))
    since = _parse(state.get("last_at"))
    quiet = now - int(state.get("last") or now) >= PULSE and (
        not since or not at or (at - since).total_seconds() >= PULSE_HOURS * 3600)
    long = int(state.get("scene_turns") or 0) >= LONG and now - int(state.get("nudged") or -10**6) >= LONG
    if state.get("current") or not (jump or quiet or long or day):
        return _view(state)
    candidates = pending(hot)
    if candidates:
        event = candidates[0]
        state["current"] = {"text": _text(event, hot)[:400], "markers": _markers(event, hot), "set": now,
                            "set_at": cursor.get("at"),
                            "npc": event.get("npc"), "plan": event.get("plan"), "at": event.get("at"),
                            "why": "скачок времени" if jump else "долгая сцена" if long else
                                   "новые сутки" if day else "давно без вестей"}
        if strict:
            event.setdefault("id", event_id(event))
            state["current"]["id"] = event["id"]
        event["news"] = "offered"
        state["offered"] += 1
        state["last"], state["last_at"] = now, cursor.get("at")
        if long:
            state["nudged"] = now
        return _view(state)
    if long:
        state["nudged"] = now
        return {"must": (f"Сцена идёт {state['scene_turns']} ходов. Понятое сожми до результата и цены; "
                         "мир войдёт, только если есть причина и доступный канал. Не решай за Шики; "
                         "игрок волен продолжить." if strict else
                         f"Сцена идёт {state['scene_turns']} ходов. Понятое игроком сожми до результата и цены "
                         "одним ходом; пусть мир войдёт в сцену сам — чужой человек, весть, помеха или наступивший "
                         "срок, — не решая за Шики; игрок волен продолжить.")}
    return _view(state)


def since_day(state):
    """Дата последней вести (или точки отсчёта) — для правила «не дольше суток без вести»."""
    value = _parse(state.get("last_at") or state.get("start_at"))
    return value.date() if value else None


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
    if current.get("id"):
        return {"id": current["id"], "must": "Весть " + current["id"] + " — " + current["text"] +
                " Передай только доступным Шики каналом; нет канала — отложи, не выдумывай. " +
                "Доставка: d.news_delivered [{id,channel,quote}].", "why": current.get("why")}
    return {"must": (f"Весть мира — {current['text']} Доведи до Шики в ближайшие {HOLD} хода одним видимым фактом "
                     f"(кто пришёл, что пропало, кто ранен, что говорят), {CHANNEL}. Не сочиняй новую интригу и не "
                     "объявляй «новость»: Шики слышит часть, с искажением, и сам решает, что делать."
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
            "echo": {"offered": echo.get("offered", 0), "used": echo.get("used_count", 0),
                     "mentioned": echo.get("mentioned_count", 0)},
            "favor": _favor(hot),
            "rule": STATE_RULE}
    path = Path(state_dir) / PATH
    body = json.dumps(pace, ensure_ascii=False, indent=1) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != body:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return pace
