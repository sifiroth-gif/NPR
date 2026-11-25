"""Своя воля NPC: у значимого человека есть цель, следующий шаг со сроком и слабость.

Мир мрачный: люди выбирают себя, лгут, торгуются, ошибаются по своей
слабости и идут против Шики, когда это им выгодно или когда их загнали.
Код ничего не решает за ведущего. Он:

* переносит в скрытую психику NPC заготовку хранителя
  (``memory/_curated/will.GM_ONLY.json``): грани характера и планы, не
  затирая того, что ведущий уже записал сам;
* после каждого хода спрашивает о планах, срок которых прошёл: NPC не ждёт
  Шики и делает свой шаг за кадром — удачно, неудачно или иначе;
* замечает, когда значимый NPC несколько ходов рядом с Шики только отвечает,
  и спрашивает, есть ли у него повод сделать свой ход (повода нет — ожидание
  тоже решение);
* напоминает дать волю NPC, который стал значимым, а её у него нет.

План записывается в ``d.cast.<id>.intent.<ключ>``: ``goal`` (чего хочет),
``next`` (следующий шаг), ``due`` (игровое время шага), ``flaw`` (на чём
ошибётся), ``against_shiki`` (чем это бьёт по Шики), ``if_unopposed`` (что
будет, если никто не помешает), ``status``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from . import npc_memory

SEED = "memory/_curated/will.GM_ONLY.json"
PASSIVE_TURNS = 4          # ходов рядом с Шики без собственного шага
SOON_HOURS = 24
AGENDA_FIELDS = ("goal", "next", "due", "flaw", "against_shiki", "if_unopposed", "status", "canon")
DARK = ("Мир мрачный: NPC выбирает себя, лжёт, торгуется, ошибается по своей слабости и идёт против "
        "Шики, когда ему это выгодно или его загнали. Помощь без выгоды редка и имеет причину.")


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _state(hot):
    return hot.setdefault("_will", {})


def _name(hot, key):
    card = (hot.get("cast") or {}).get(key) or {}
    profile = (hot.get("_psyche") or {}).get(key) or {}
    return card.get("name") or profile.get("name") or key


def fill_names(state_dir, hot):
    """Имя для каждого NPC с волей: карточка уходит из HOT, когда он покидает сцену."""
    for key, profile in (hot.get("_psyche") or {}).items():
        if not isinstance(profile, dict) or profile.get("name") or not profile.get("intent"):
            continue
        name = ((hot.get("cast") or {}).get(key) or {}).get("name")
        if not name:
            try:
                path = Path(state_dir) / "recovery/cast" / f"{key}.json"
                name = json.loads(path.read_text(encoding="utf-8")).get("name")
            except (OSError, ValueError):
                name = None
        if name:
            profile["name"] = name


# ---------------------------------------------------------------- заготовка хранителя

def seed(state_dir, hot):
    """Переносит заготовку хранителя в скрытую психику, если файл изменился."""
    path = Path(state_dir) / SEED
    if not path.is_file():
        return []
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    state = _state(hot)
    if state.get("seed") == digest:
        return []
    data = json.loads(raw.decode("utf-8"))
    changed = []
    for key, will in data.items():
        if key.startswith("_") or not isinstance(will, dict):
            continue
        profile = hot.setdefault("_psyche", {}).setdefault(key, {})
        if will.get("name") and not profile.get("name"):   # лицо вне сыгранного (канон): имя из заготовки
            profile["name"] = str(will["name"])[:80]
        if not (hot.get("cast") or {}).get(key) and not profile.get("name"):
            card = Path(state_dir) / "recovery/cast" / f"{key}.json"
            try:
                profile["name"] = json.loads(card.read_text(encoding="utf-8")).get("name") or key
            except (OSError, ValueError):
                pass
        facets = profile.setdefault("psyche", {})
        for facet, value in (will.get("psyche") or {}).items():
            if facet in npc_memory.FACETS and value and not facets.get(facet):
                facets[facet] = str(value)[:300]
                changed.append(key)
        active = profile.setdefault("intent", {})
        # Поправка хранителя: план уже сыгран, а статус потерян (до починки статусов
        # «done» откатывался в «committed»). Закрывается, только если ещё активен.
        for name, fix in (will.get("close") or {}).items():
            if name in active and isinstance(fix, dict):
                entry = dict(active.pop(name))
                entry["status"] = npc_memory.status_of(fix.get("status") or "fulfilled")
                entry["outcome"] = str(fix.get("outcome") or npc_memory.default_outcome(entry))[:240]
                log = profile.setdefault("intent_done", [])
                log.append({"key": name, "at": fix.get("at") or entry.get("due"), **entry, "by": "keeper"})
                del log[:-npc_memory.LOG_KEEP]
                event = {"npc": key, "name": profile.get("name") or _name(hot, key), "plan": name,
                         "at": fix.get("at") or entry.get("due"), "status": entry["status"],
                         "outcome": entry["outcome"], "turn": (hot.get("cursor") or {}).get("id"), "shown": 0}
                if entry.get("against_shiki"):
                    event["against_shiki"] = str(entry["against_shiki"])[:200]
                if not fix.get("onscreen"):   # сыгранное при Шики — не новость мира для offscreen
                    state.setdefault("happened", []).append(event)
                changed.append(key)
        for name in will.get("drop") or []:   # перепланировка хранителя: несбывшийся план снимается без следа
            if active.pop(name, None) is not None:
                changed.append(key)
        done = {d.get("key") for d in profile.get("intent_done") or []} | set(profile.get("done_keys") or [])
        for name, agenda in (will.get("intent") or {}).items():
            if not isinstance(agenda, dict) or name in done:
                continue
            entry = active.setdefault(name, {"status": "committed", "turn": "keeper"})
            for field in AGENDA_FIELDS:
                if agenda.get(field) and not entry.get(field):
                    entry[field] = str(agenda[field])[:300]
            changed.append(key)
    state["seed"] = digest
    return sorted(set(changed))


# ---------------------------------------------------------------- после хода

def track(hot, turn, inner):
    """Сколько ходов подряд NPC рядом с Шики и когда он последний раз менялся изнутри."""
    if turn.get("kind") == "ooc":
        return
    state = _state(hot)
    streak, touched = state.setdefault("streak", {}), state.setdefault("touched", {})
    present = [k for k in (hot.get("scene") or {}).get("present") or [] if k not in ("shiki", "player")]
    for key in list(streak):
        if key not in present:
            streak.pop(key)
    for key in present:
        streak[key] = int(streak.get(key, 0)) + 1
    for key, own in (inner or {}).items():
        if any(own.get(k) for k in ("intent", "rel", "affect", "psyche", "mem")):
            touched[key] = turn["id"]
        # Свой шаг — новый или изменённый план; запись памяти и чувства — отклик (25.11.2026:
        # Немера отвечала 18 ходов, а каждая строка её памяти обнуляла счёт). Почин в тексте
        # хода сбрасывает счёт отдельно — drama.track.
        if own.get("intent"):
            streak[key] = 0


def agendas(hot):
    """Все активные планы NPC со сроком: [(npc, ключ, план, срок)] по времени."""
    found = []
    for key, profile in (hot.get("_psyche") or {}).items():
        if not isinstance(profile, dict):
            continue
        for name, agenda in (profile.get("intent") or {}).items():
            if isinstance(agenda, dict) and agenda.get("status", "committed") == "committed":
                found.append((key, name, agenda, _parse(agenda.get("due"))))
    found.sort(key=lambda x: (x[3] is None, x[3] or datetime.max))
    return found


def review(hot, turns):
    """Замечания kind=will: просроченный план, пассивный значимый NPC, NPC без воли."""
    cursor = hot.get("cursor") or {}
    now, turn_id = _parse(cursor.get("at")), cursor.get("id")
    notes = []

    def note(text):
        notes.append({"turn": turn_id, "kind": "will", "text": text})

    # Просроченные планы отсутствующих исполняет advance(), присутствующих — must в NEXT.
    if turns:
        streak = _state(hot).get("streak") or {}
        for key, count in streak.items():
            card = (hot.get("cast") or {}).get(key) or {}
            profile = (hot.get("_psyche") or {}).get(key) or {}
            significant = card.get("tier") in ("developed", "deep") or profile.get("intent")
            if count >= PASSIVE_TURNS and significant:
                goal = next((a.get("goal") for a in (profile.get("intent") or {}).values()
                             if isinstance(a, dict) and a.get("goal")), card.get("wants"))
                note(f"«{_name(hot, key)}» {count} ход(ов) рядом с Шики только отвечает. Его цель: "
                     f"«{goal or 'не записана'}». Есть ли у него повод сделать свой ход — надавить, солгать, "
                     "сторговаться, ошибиться, пойти против Шики? Повода нет — ждать тоже его решение.")
        for key in (hot.get("scene") or {}).get("present") or []:
            card = (hot.get("cast") or {}).get(key) or {}
            profile = (hot.get("_psyche") or {}).get(key) or {}
            if card.get("tier") in ("developed", "deep") and not profile.get("psyche") and not profile.get("intent"):
                note(f"«{_name(hot, key)}» появляется уже {card.get('memory_total', 0)} раз, а воли у него нет: "
                     f"задай d.cast.{key}.psyche (decides, deep_fear, boundary) и intent с next, due, flaw.")
    from . import news
    for turn in turns or []:
        for key, own in (((turn.get("d") or {}).get("cast") or {}).items()):
            for name, plan in ((own or {}).get("intent") or {}).items() if isinstance(own, dict) else ():
                closing = str(plan.get("status") or "") if isinstance(plan, dict) else ""
                outcome = str(plan.get("outcome") or "") if isinstance(plan, dict) else ""
                from .arcs import EVASION
                if closing in ("abandoned", "failed", "dropped", "cancelled") and EVASION.search(outcome):
                    # Автономия мира (поправка игрока 23.11.2026): разминка с Шики плана не отменяет.
                    note(f"План «{name}» у «{_name(hot, key)}» закрыт разминкой во времени или месте («{outcome[:90]}»). "
                         "Цель не исчезла: NPC добивается её иначе — ищет, догоняет, приходит туда, где Шики, "
                         f"вмешивается. Верни d.cast.{key}.intent.{name} в committed с новым next и due или "
                         "закрой причиной из самого мира.")
                result = plan.get("if_unopposed") if isinstance(plan, dict) else None
                if result and news.dry(result):
                    note(f"План «{name}» у «{_name(hot, key)}»: исход — бумага («{str(result)[:90]}»). Перепиши "
                         f"d.cast.{key}.intent.{name}.if_unopposed видимой переменой мира, которая может дойти до "
                         "Шики: кто пришёл, исчез, продал, сжёг, объявил, нанял, умер.")
    return notes


# ---------------------------------------------------------------- мир идёт сам

GRACE_HOURS = 1            # час игрового времени после срока: ведущий успевает решить сам
ROLL_FULL, ROLL_PARTIAL = 65, 85   # 1–65 сбылся, 66–85 наполовину, 86–100 сорвался по слабости
HAPPENED_KEEP = 30
SHOW_FOLDS = 3             # сколько свёрток событие висит в NEXT.offscreen
NEWS_DAYS = 14             # событие старше двух игровых недель в NEXT не показывается


def _roll(key, plan, due):
    digest = hashlib.sha256(f"{key}|{plan}|{due}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100 + 1


def advance(hot, always_present=()):
    """Просроченный план отсутствующего NPC исполняется сам: мир не ждёт ведущего.

    Ведущий решает исход сам, если запишет status и outcome до срока и часа после.
    Иначе бросок: сбылся по ``if_unopposed``, наполовину или сорвался по ``flaw``.
    Присутствующие и всегда присутствующие (Немера) не решаются за кадром — их ход
    играется в сцене, ведущему приходит обязательный пункт.
    """
    now = _parse((hot.get("cursor") or {}).get("at"))
    if not now:
        return []
    present = set((hot.get("scene") or {}).get("present") or []) | set(always_present)
    history = _state(hot).setdefault("happened", [])
    events = []
    for key, name, plan, due in agendas(hot):
        if not due or key in present or due + timedelta(hours=GRACE_HOURS) > now:
            continue
        if plan.get("canon"):   # каноническое событие: к сроку случается, если его не изменили в игре
            roll, status = None, "fulfilled"
        else:
            roll = _roll(key, name, plan.get("due"))
            status = "fulfilled" if roll <= ROLL_FULL else "partial" if roll <= ROLL_PARTIAL else "failed"
        entry = dict(plan, status=status)
        entry["outcome"] = npc_memory.default_outcome(entry)[:240]
        profile = hot["_psyche"][key]
        profile["intent"].pop(name, None)
        done = profile.setdefault("intent_done", [])
        done.append({"key": name, "at": plan.get("due"), **entry, "by": "world", "roll": roll,
                     "turn": (hot.get("cursor") or {}).get("id")})
        del done[:-npc_memory.LOG_KEEP]
        event = {"npc": key, "name": _name(hot, key), "plan": name, "at": plan.get("due"), "status": status,
                 "outcome": entry["outcome"], "turn": (hot.get("cursor") or {}).get("id"), "shown": 0}
        if plan.get("against_shiki"):
            event["against_shiki"] = str(plan["against_shiki"])[:200]
        if plan.get("canon"):
            event["canon"] = True
        history.append(event)
        events.append(event)
    from . import news
    if news.modern(hot):
        for event in history:
            event.setdefault("id", news.event_id(event))
        # Undelivered important events must survive aging and a long time skip.
        # Aging archives the spent rows losslessly.
    else:
        del history[:-HAPPENED_KEEP]
    return events


def offscreen(hot):
    """Случившееся за кадром за последние свёртки — для NEXT: весомое первым, мелочь счётом."""
    from . import news
    out = []
    now = _parse((hot.get("cursor") or {}).get("at"))
    for event in _state(hot).get("happened") or []:
        if event.get("shown", 0) >= SHOW_FOLDS:
            continue
        at = _parse(event.get("at"))
        if now and at and (now - at).days > NEWS_DAYS:   # давнее — уже не новость: в архив при старении
            event["shown"] = SHOW_FOLDS
            continue
        cursor = (hot.get("cursor") or {}).get("id")
        if not news.modern(hot) or event.get("shown_at") != cursor:
            event["shown"] = event.get("shown", 0) + 1
            if news.modern(hot):
                event["shown_at"] = cursor
        word = {"fulfilled": "сбылось", "partial": "наполовину", "failed": "сорвалось"}.get(event["status"], event["status"])
        name = event["name"] if event.get("name") and event["name"] != event.get("npc") else _name(hot, event.get("npc"))
        tag = "" if str(event["outcome"]).casefold().startswith(word) else f" ({word})"
        text = f"{str(event.get('at') or '')[5:16].replace('T', ' ')} {name}{tag}: {event['outcome']}"
        if event.get("against_shiki"):
            text += ("" if text.endswith((".", "!", "?", "…", ")")) else ".") + f" Против Шики: {event['against_shiki']}"
        out.append((news.weight(hot, event), text))
    return news.offscreen(hot, out)


def due_here(hot, always_present=()):
    """Присутствующие, чей срок прошёл: их ход играется в этой сцене."""
    now = _parse((hot.get("cursor") or {}).get("at"))
    present = set((hot.get("scene") or {}).get("present") or []) | set(always_present)
    return [(key, name, plan) for key, name, plan, due in agendas(hot)
            if now and due and due <= now and key in present]


def without_plan(hot):
    """Значимые NPC, чей последний план закрыт, а нового нет: кому дать следующий шаг."""
    out = []
    for key, profile in (hot.get("_psyche") or {}).items():
        if not isinstance(profile, dict) or not profile.get("intent_done"):
            continue
        if any(isinstance(a, dict) and a.get("status", "committed") in ("committed", "paused")
               for a in (profile.get("intent") or {}).values()):
            continue
        last = profile["intent_done"][-1]
        out.append((str(last.get("turn") or ""), key, last))
    out.sort(reverse=True)
    return [(key, last) for _, key, last in out]


# ---------------------------------------------------------------- выдача ведущему

def view(hot, key):
    """Воля одного NPC для GM_BRIEF: как решает и какие у него планы."""
    profile = (hot.get("_psyche") or {}).get(key) or {}
    psyche = profile.get("psyche") or {}
    out = {f: psyche[f] for f in ("decides", "deep_fear", "boundary", "contradiction", "mask") if psyche.get(f)}
    plans = {name: {f: a[f] for f in AGENDA_FIELDS if a.get(f)}
             for name, a in (profile.get("intent") or {}).items() if isinstance(a, dict)}
    if plans:
        out["plans"] = plans
    return out


def soon(hot, hours=SOON_HOURS, always_present=()):
    """Чужие планы, чей срок наступит в ближайшие сутки или уже прошёл."""
    now = _parse((hot.get("cursor") or {}).get("at"))
    if not now:
        return []
    horizon = now + timedelta(hours=hours)
    present = set((hot.get("scene") or {}).get("present") or []) | set(always_present)
    return [{"npc": key, "name": _name(hot, key), "plan": name,
             **{f: str(a[f])[:170] for f in ("next", "due", "if_unopposed", "against_shiki") if a.get(f)},
             "overdue": due <= now}
            for key, name, a, due in agendas(hot)
            if due and due <= horizon and key not in present][:6]   # присутствующие — в present_npcs
