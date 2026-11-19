"""Пакет следующего хода: одно маленькое чтение вместо пяти больших.

Ведущий в длинном чате уже держит в контексте правила, сцену и сказанное.
Перед каждым ходом ему нужно только то, что код посчитал после его записи:
замечания свёртки, вердикт прозы, sha ``TURN.json``, кости, чужие сроки, волю
присутствующих и границы редкого знания. Раньше за этим перечитывались
``GM_BRIEF`` (42 КБ), ``HOT`` (65 КБ, ради ``lint``), ``CHECKS`` и страницы
присутствующих NPC (25–55 КБ каждый ход) — каждое чтение раздувало чат и
задерживало сцену. ``recovery/NEXT.json`` держится в нескольких килобайтах.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import directives, dread, inquiry, lore, will

PATH = "recovery/NEXT.json"
TARGET_BYTES = 12000
HARD_BYTES = 14000
DUE_HOURS = 6
FIELD = 170
RULE = ("Это всё, что нужно прочитать перед обычным ходом: остальное уже в чате. Страницу memory/ "
        "открывай только для NPC, впервые входящего в сцену в этом чате, или по вопросу о прошлом; "
        "прочитанное в этом чате не перечитывай. cursor не равен твоему последнему ходу — свёртка "
        "отстала: открой CHECKS.json.")


def _short(text, limit=FIELD):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _clock(value):
    at = _parse(value)
    return at.strftime("%d.%m %H:%M") if at else str(value or "")


def next_id(hot):
    cursor = hot.get("cursor") or {}
    try:
        return f"s{int(hot.get('session', 0)):02d}.text.{int(cursor.get('seq', 0)) + 1:03d}"
    except (TypeError, ValueError):
        return None


def _will_line(hot, key):
    profile = (hot.get("_psyche") or {}).get(key) or {}
    plans = [a for a in (profile.get("intent") or {}).values()
             if isinstance(a, dict) and a.get("status", "committed") in ("committed", "active")]
    lines = []
    for plan in plans[:2]:
        bits = [_short(plan.get("goal"), 120)]
        if plan.get("next"):
            bits.append("шаг: " + _short(plan["next"]) + (f" (к {_clock(plan['due'])})" if plan.get("due") else ""))
        if plan.get("flaw"):
            bits.append("слабость: " + _short(plan["flaw"], 120))
        if plan.get("against_shiki"):
            bits.append("против Шики: " + _short(plan["against_shiki"], 120))
        lines.append("; ".join(b for b in bits if b))
    decides = (profile.get("psyche") or {}).get("decides")
    if decides:
        lines.append("решает: " + _short(decides, 120))
    return lines


def _due(hot, brief, always_present=()):
    now = _parse((hot.get("cursor") or {}).get("at"))
    horizon = now + timedelta(hours=DUE_HOURS) if now else None
    out = []
    for clock in ((brief.get("world") or {}).get("clocks") or []):
        due = _parse(clock.get("due"))
        if due and horizon and due <= horizon:
            out.append((due, f"{_clock(clock['due'])} {clock.get('owner') or ''}: {_short(clock.get('what'))}"
                             f" → если никто не помешает: {_short(clock.get('if_unopposed'), 140)}"))
    for item in will.soon(hot, hours=DUE_HOURS, always_present=always_present):
        due = _parse(item.get("due"))
        decide = (due + timedelta(hours=will.GRACE_HOURS)).strftime("%H:%M") if due else "?"
        out.append((due or datetime.max, f"{_clock(item.get('due'))} {item['name']}: {_short(item.get('next'))}"
                                         f" → {_short(item.get('if_unopposed'), 140)} (не решишь до {decide} — "
                                         "решит бросок)"))
    out.sort(key=lambda x: x[0])
    return [text for _, text in out][:8]


def must(hot, always_present=(), entries=None):
    """Обязательное на этот ход: короткий список вместо десятка советов.

    Замечания ведущий пропускал по 12 ходов подряд (s08.053–062); здесь не больше
    трёх пунктов, каждый — одно конкретное действие в этой сцене или записи.
    """
    out = inquiry.must(hot)
    for entry, names, turn in lore.first_sight(hot, entries or [])[:1]:
        out.append(f"След увиденного: {', '.join(names[:5])} впервые столкнулись с «{entry.get('title') or entry['id']}» "
                   f"({turn}). Это не проходит за ход: {entry.get('first_sight') or 'вопросы, отрицание, страх, слух'} "
                   "— в этой сцене и за кадром; запиши в их dread.")
    present = (hot.get("scene") or {}).get("present") or []
    for key, row in dread.pending(hot, present, always_present)[:2]:
        name = will._name(hot, key)
        known = ((hot.get("_psyche") or {}).get(key) or {}).get("dread")
        if row.get("need") and known:
            out.append(f"Реакция на Шики: {name} рядом с {row.get('since')}, в этой встрече реакции нет. "
                       f"Прежде — {dread.summary(hot, key)}. Что изменилось после увиденного сегодня и что "
                       f"из этого сделано здесь: сдвиг, hold или down — d.cast.{key}.dread.")
        elif row.get("need"):
            out.append(f"Реакция на Шики: {name} рядом с {row.get('since')}, отношение к Шики не сыграно. "
                       "Что видит и знает о Шики, как объясняет это по своему опыту и что из этого делает "
                       "здесь — решение, оговорка, проверка, расчёт; равнодушие тоже выбор. Запиши "
                       f"d.cast.{key}.dread: saw, reads, does, stage ({dread.summary(hot, key)}).")
        else:
            out.append(f"{name} {row.get('quiet')} хода рядом с Шики без новой реакции: увидел новое — сдвиг "
                       f"в поступке и d.cast.{key}.dread; нет — dread.hold с тем, что подтвердилось.")
    for key, name, plan in will.due_here(hot, always_present)[:1]:
        out.append(f"{will._name(hot, key)} рядом, а срок плана «{_short(plan.get('next') or plan.get('goal'), 120)}» "
                   f"прошёл — сыграй этот ход в сцене: сделано, сорвалось по слабости или смена тактики; "
                   f"запиши d.cast.{key}.intent.{name}: status и outcome.")
    for key, last in will.without_plan(hot)[:2]:
        plan = last.get("goal") or last.get("outcome") or last.get("next") or last.get("key")
        out.append(f"У {will._name(hot, key)} закрыт план «{_short(plan, 80)}» "
                   f"({last.get('status')}), нового нет: впиши d.cast.{key}.intent.<ключ> — goal, next, due, "
                   "flaw, if_unopposed. Что он делает дальше сам?")
    return out[:3]


def build(hot, brief, checks, entries, always_present=(), rules=None):
    cursor = brief.get("cursor") or {}
    present = [k for k in (hot.get("scene") or {}).get("present") or [] if k != lore.SHIKI]
    pack = {
        "schema": "npr-next/1",
        "cursor": cursor.get("id"),
        "next_id": next_id(hot),
        "header": " ".join(x for x in (cursor.get("date"), cursor.get("time")) if x),
        "place": cursor.get("place"),
        "turn_file": (brief.get("turn_file") or {}).get("sha"),
        "fold_errors": checks.get("fold_errors") or [],
    }
    if rules:
        pack["directives"] = dict(rules, rule="Поправки игрока к ведению: выше GM_CORE и навыка, исполнять до отмены.")
    duty = must(hot, always_present, entries)
    if duty:
        pack["must"] = duty
    happened = will.offscreen(hot)
    if happened:
        pack["offscreen"] = {"rule": "Случилось без Шики, он этого не знает. Это факт мира: последствия "
                                     "встречаются там, куда дойдёт канал — слух, документ, человек, перемена; "
                                     "исход, требующий встречи с Шики, случается в ближайшей возможной сцене.",
                             "events": happened}
    pack["checks"] = [f"[{c.get('kind')}] {c.get('text')}" for c in checks.get("checks") or []]
    last = ((hot.get("lint") or {}).get("recent") or [])[-1:]
    if last and last[0].get("id") == cursor.get("id") and last[0].get("verdict") != "чисто":
        pack["prose"] = {"verdict": last[0].get("verdict"),
                         "hits": [f"{h.get('rule')}: «{_short(h.get('quote'), 90)}»" for h in last[0].get("hits", [])[:3]]}
    people = {}
    for key in present:
        if key in always_present:
            continue
        entry = {"name": lore._name(hot, key), "reaction": dread.summary(hot, key)}
        lines = _will_line(hot, key)
        if lines:
            entry["will"] = lines
        people[key] = entry
    if people:
        pack["present"] = people
    fences = lore.fences(hot, entries, present)
    if fences:
        pack["fences"] = fences
    due = _due(hot, brief, always_present)
    if due:
        pack["due"] = due
    fight = brief.get("fight") or {}
    if fight.get("dice"):
        pack["dice"] = fight["dice"]
    if fight.get("chakra"):
        pack["chakra"] = fight["chakra"]
    if fight.get("active"):
        pack["fight"] = {k: v for k, v in fight.items() if k not in ("dice", "rules", "ref")}
    pack["rule"] = RULE
    _fit(pack)
    return pack


def _size(pack):
    return len(json.dumps(pack, ensure_ascii=False).encode("utf-8"))


def _fit(pack):
    """Сначала укорачиваются сроки, затем замечания; ошибки свёртки не трогаются."""
    while _size(pack) > TARGET_BYTES and len(pack.get("due") or []) > 3:
        pack["due"].pop()
    while _size(pack) > HARD_BYTES and len(pack.get("checks") or []) > 3:
        pack["checks"].pop(0)
    return pack


def _write(state_dir, pack):
    path = Path(state_dir) / PATH
    body = json.dumps(pack, ensure_ascii=False, indent=1) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == body:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return True


def write(state_dir, hot, brief, checks, always_present=()):
    pack = build(hot, brief, checks, lore.load(state_dir), always_present, directives.view(state_dir, hot))
    _write(state_dir, pack)
    return pack


def write_blocked(state_dir, cursor, errors, *, kind="fold_errors", rule):
    """Свёртка не прошла: вместо старого пакета — сама ошибка."""
    path = Path(state_dir) / PATH
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pack = {"schema": "npr-next/1"}
    pack["cursor"] = cursor
    pack[kind] = list(errors)
    pack["blocked_rule"] = rule
    _write(state_dir, pack)
    return pack
