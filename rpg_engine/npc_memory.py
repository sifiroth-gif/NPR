"""Память персонажей: эпизоды, отношения, чувство, намерения, психика.

Перенос контрактов Bleach ``npc_psyche`` / ``npc_importance`` / ``gm_npc_names``
на одну запись ``TURN.json`` за ход. Ведущий не запускает код: всё ниже
вызывается из ``hot.fold`` в GitHub Actions.

Что делает код сам, без участия ведущего:

* каждому NPC из ``scene.present`` дописывает эпизод «был в этой сцене»
  со строкой хроники хода (наблюдённое, не толкование);
* считает появления и глубину профиля (``tier``), как ``npc_importance``;
* гасит истёкшее чувство по игровым часам, а не по числу реплик;
* ловит молчаливую смену характера и совпадение имён.

Что пишет ведущий в ``d.cast.<ключ>`` (всё необязательно):

* ``mem``    — как NPC это запомнил, своими словами (приватно);
* ``rel``    — сдвиг отношения к Шики или к ``to``: ``{"trust": -1, "why": "…"}``;
* ``affect`` — текущее чувство со сроком: ``{"feel": "…", "hours": 6}``;
* ``intent`` — намерения: ``{"ключ": {"goal": "…", "status": "committed"}}``;
* ``psyche`` — устойчивые грани характера (см. ``FACETS``);
* ``why``    — причина, если меняются ``wants``/``fears``/``voice``/``psyche``.

Психика, отношения, чувство, намерения и личные воспоминания хранятся в
``GM_ONLY.json`` (``psyche``): игрок их не читает. Наблюдённые эпизоды —
открытый факт сыгранного, их полный список лежит в
``archive/npc-memory/<ключ>.json``.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

from . import dread as _dread

MEMORY_DIR = "archive/npc-memory"
REL_DIMS = ("trust", "fear", "respect", "debt", "grievance", "attachment", "dependence")
REL_RANGE = 5
FACETS = ("temper", "values", "deep_fear", "needs", "contradiction", "defense",
          "habits", "boundary", "mask", "decides")
INTENT_STATUS = ("committed", "paused", "fulfilled", "partial", "failed", "abandoned")
# Ведущий пишет статус своими словами. Раньше «done», «in_progress», «partial_success»
# молча возвращались в «committed» — закрытые планы воскресали, а мир стоял.
STATUS_ALIASES = {
    "active": "committed", "in_progress": "committed", "ongoing": "committed", "pending": "committed",
    "blocked": "paused", "stalled": "paused", "waiting": "paused", "on_hold": "paused",
    "done": "fulfilled", "success": "fulfilled", "succeeded": "fulfilled", "completed": "fulfilled",
    "complete": "fulfilled", "happened": "fulfilled", "resolved": "fulfilled",
    "partial_success": "partial", "partially": "partial", "mixed": "partial",
    "failure": "failed", "fail": "failed", "foiled": "failed", "thwarted": "failed",
    "cancelled": "abandoned", "canceled": "abandoned", "dropped": "abandoned", "dead": "abandoned",
}
CLOSED = ("fulfilled", "partial", "failed", "abandoned")
PASSIVE = re.compile(r"^\s*(дожд|жд[её]т|ждать|ожид|сохран|продолжа|наблюда|оставаться|не\s)", re.I)
CHANNELS = ("present", "witnessed", "told", "document", "inferred", "rumor", "mentioned")
BOOKKEEPING = frozenset(("intent", "psyche", "why"))   # запись за кадром, не опыт NPC
ALWAYS_WITNESS = ("nemera",)                          # сенсорная связь: переживает всё с Шики
STABLE = ("wants", "fears", "voice")
INNER_KEYS = ("mem", "rel", "affect", "intent", "psyche", "why", "dread")
CARD_FIRST = 1      # самый ранний эпизод в карточке: откуда NPC знает Шики
CARD_RECENT = 6     # последние эпизоды (было 3: Сакура «забыла» операцию 034); полный список — archive/npc-memory и вики
PRIVATE_MEMORIES = 16   # как в Bleach: рабочая память профиля, полная — в архиве
LOG_KEEP = 8
DEFAULT_AFFECT_HOURS = 6
TITLES = {"Капитан", "Старший", "Старшая", "Младший", "Глава", "Доктор", "Сестра",
          "Командир", "Мастер", "Господин", "Госпожа", "Неизвестный", "Неизвестная",
          "Курьер", "Охранник", "Страж", "Врач", "Дежурный", "Дежурная", "Санитар"}


def _parse(at):
    return _dt.datetime.fromisoformat(f"2000{at[4:]}") if at.startswith("0") else _dt.datetime.fromisoformat(at)


def _short(text, limit=220):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _inner(hot, key):
    return hot.setdefault("_psyche", {}).setdefault(key, {})


def _check(hot, turn_id, kind, text):
    checks = hot.setdefault("_checks", [])
    entry = {"turn": turn_id, "kind": kind, "text": text}
    if entry not in checks:
        checks.append(entry)


# ----------------------------------------------------------------- до слияния

def split_patch(hot, delta, turn_id):
    """Отделяет приватные поля NPC от открытой карточки и ловит дрейф характера.

    Возвращает ``(открытая_дельта, приватные_поля)``. Исходная дельта не меняется.
    """
    cast = (delta or {}).get("cast")
    if not isinstance(cast, dict):
        return delta, {}
    public = dict(delta)
    public["cast"] = {}
    inner = {}
    for key, patch in cast.items():
        if not isinstance(patch, dict):
            public["cast"][key] = patch
            continue
        own = {k: patch[k] for k in INNER_KEYS if k in patch}
        rest = {k: v for k, v in patch.items() if k not in INNER_KEYS}
        public["cast"][key] = rest
        if own:
            inner[key] = own
        card = (hot.get("cast") or {}).get(key)
        if not isinstance(card, dict):
            continue
        for field in STABLE:
            old, new = card.get(field), rest.get(field)
            if old and new and _short(old, 400) != _short(new, 400):
                log = _inner(hot, key).setdefault("drift", [])
                log.append({"turn": turn_id, "field": field, "was": _short(old, 160),
                            "why": _short(own.get("why", ""), 160)})
                del log[:-LOG_KEEP]
                if not own.get("why"):
                    _check(hot, turn_id, "drift",
                           f"{key}: {field} переписано без why; характер не меняется молча")
    return public, inner


def check_names(hot, delta, turn_id, archived=()):
    """Новый NPC не должен носить имя уже существующего (Bleach gm_npc_names).

    Возвращает список замечаний; уже введённых персонажей не переименовывает.
    """
    known = {}
    for key, card in list((hot.get("cast") or {}).items()) + list(archived):
        if isinstance(card, dict) and card.get("name"):
            known[key] = card["name"]
    notes = []
    for key, patch in ((delta or {}).get("cast") or {}).items():
        if not isinstance(patch, dict) or key in known or not patch.get("name"):
            continue
        mine = _proper_words(patch["name"])
        for other, name in known.items():
            if patch["name"].casefold() == name.casefold():
                notes.append(f"{key}: имя «{name}» уже носит {other}")
            elif mine & _proper_words(name):
                same = ", ".join(sorted(mine & _proper_words(name)))
                notes.append(f"{key}: часть имени ({same}) совпадает с {other}")
    return notes


# ------------------------------------------------------- приметы (s10.text.083–088)
# Морита описал заказчицу приметами, которые раньше Минаги дал женщине, оказавшейся
# Тамурой Рэйкой (надорванная левая мочка, негнущийся левый указательный палец).
# Ведущий не связал их и спорил с игроком три хода. Отличительная примета — часть тела
# с особенностью; одна и та же у двух людей — либо это один человек, либо ошибка.

_BODY = (("мочка", r"мочк"), ("ухо", r"у[хш][оаеуи]?$|уш[иек]"), ("палец", r"пал(ец|ьц)"),
         ("указательный", r"указательн"), ("мизинец", r"мизин"), ("безымянный", r"безымян"),
         ("кисть", r"кист"), ("запястье", r"запяст"), ("бровь", r"бров"), ("зуб", r"зуб"), ("губа", r"губ"),
         ("щека", r"щ[её]к"), ("скула", r"скул"), ("челюсть", r"челюст"), ("переносица", r"переносиц"),
         ("ладонь", r"ладон"), ("ноготь", r"ногт|ногот"), ("глаз", r"глаз"), ("веко", r"век[оауи]$"),
         ("нос", r"нос[аеуо]?$"), ("подбородок", r"подбород"), ("висок", r"вис(ок|к)"), ("шея", r"ше[яиюе]$"),
         ("лоб", r"лб[уаео]?$|^лоб$"), ("нога", r"ног[аиуе]?$"), ("колено", r"колен"), ("предплечье", r"предплеч"),
         ("плечо", r"плеч"), ("голова", r"голов"), ("волосы", r"волос"))
_SIDE = (("L", r"лев(ый|ая|ое|ом|ого|ой|ую|ым|ые|ых|ому)$|слева$"),
         ("R", r"прав(ый|ая|ое|ом|ого|ой|ую|ым|ые|ых|ому)$|справа$"))
_ODD = (("надрыв", r"надорв|надрыв|разорван|вырван|порван"), ("шрам", r"шрам|рубц|рубец|рубцов"),
        ("ожог", r"ожог|обожж"), ("перелом", r"сломан|искривл|сросш|сросл|кривой|кривая"),
        ("нет", r"\bнет\b|отсутств|ампутир|культ|обрубл|без (мизинца|пальца|уха|глаза|кисти)"),
        ("протез", r"протез"), ("бельмо", r"бельм"), ("знак", r"татуир|клейм|выжжен"),
        ("родинка", r"родин"), ("хромота", r"хрома|хромо"),
        ("скованность", r"не сгиба|не разгиба|негнущ|почти не (испол|сгиб|двиг|шевел)|онемел|парализ|неподвиж"),
        ("рассечение", r"рассеч|расщеп"), ("выбит", r"выбит|золот\w* зуб|металлическ\w* зуб"),
        ("седина", r"сед\w* прядь|сед\w* прядью|сед\w* полос"), ("разноглазие", r"разного цвета|разноцвет"))


def _concepts(clause):
    """(части тела, стороны, особые приметы) фразы: повреждение или странность части тела."""
    body, sides = set(), set()
    lowered = (clause or "").casefold()
    for word in re.findall(r"[а-яё]{2,}", lowered):
        part = next((name for name, rx in _BODY if re.match(rx, word)), None)
        side = next((name for name, rx in _SIDE if re.match(rx, word)), None)
        if part:
            body.add(part)
        elif side:
            sides.add(side)
    odd = {name for name, rx in _ODD if re.search(rx, lowered)}
    return body, sides, odd


def marks_of(text):
    """Приметы из описания: часть тела с повреждением или странностью («надорвана мочка»)."""
    out = []
    for clause in re.split(r"[.;:!?()\n—]|,\s", text or ""):
        body, sides, odd = _concepts(clause)
        if body and odd:
            out.append((clause.strip(), (body, sides, odd)))
    return out


def _card_text(card):
    parts = [card.get("look"), card.get("body")]
    facts = card.get("facts")
    parts += list(facts.values()) if isinstance(facts, dict) else [facts]
    return " ".join(str(x) for x in parts if x)


def _same_mark(a, b, loose=False):
    """Одна примета: та же часть тела с той же странностью — и та же сторона или вторая часть тела.

    ``loose`` — для новой карточки: зеркальная примета («ожог слева на шее» и «справа») —
    тоже повтор приёма, у разных людей приметы должны различаться.
    """
    body, odd = a[0] & b[0], a[2] & b[2]
    return bool(body and odd and (loose or a[1] & b[1] or len(body) >= 2))


def marks_audit(cards):
    """Пары карточек с одинаковыми или зеркальными приметами — для хранителя."""
    registry = [(key, card.get("name") or key, marks_of(_card_text(card))) for key, card in cards
                if isinstance(card, dict)]
    pairs = []
    for i, (key, name, mine) in enumerate(registry):
        for other, other_name, theirs in registry[i + 1:]:
            hit = next(((m, t) for m, ms in mine for t, ts in theirs if _same_mark(ms, ts, loose=True)), None)
            if hit:
                pairs.append({"a": key, "a_name": name, "a_mark": hit[0][:90],
                              "b": other, "b_name": other_name, "b_mark": hit[1][:90]})
    return pairs


def check_marks(hot, turn, archived=(), always=()):
    """Чужие приметы в ходе: у отсутствующего NPC или у новой карточки — замечание."""
    delta = turn.get("d") or {}
    cast_delta = delta.get("cast") or {}
    present = set((hot.get("scene") or {}).get("present") or []) | set(cast_delta) | set(always) | {"shiki"}
    cards = {}
    for key, card in list(archived) + list((hot.get("cast") or {}).items()):
        if isinstance(card, dict):
            cards[key] = card
    registry = {key: marks_of(_card_text(card)) for key, card in cards.items()}
    notes = []
    # Новая карточка с чужими приметами.
    for key, patch in cast_delta.items():
        if not isinstance(patch, dict) or key in cards:
            continue
        mine = marks_of(_card_text(patch))
        for other, theirs in registry.items():
            hit = next((m for m, ms in mine for t, ts in theirs if _same_mark(ms, ts, loose=True)), None)
            if hit:
                name = cards[other].get("name") or other
                notes.append(f"{key}: примета «{hit[:80]}» уже есть у {name} ({other}). Один человек — "
                             f"веди как {other}; другой — дай ему свои приметы.")
                break
    # Приметы отсутствующего NPC в тексте хода: это он?
    text_marks = marks_of(f"{turn.get('gm') or ''}\n{turn.get('line') or ''}")
    for other, theirs in registry.items():
        if other in present or not theirs:
            continue
        hit = next((m for m, ms in text_marks for t, ts in theirs if _same_mark(ms, ts)), None)
        if hit:
            name = cards[other].get("name") or other
            notes.append(f"В ходе приметы {name} ({other}): «{hit[:90]}». Если это {name} — называй и веди "
                         f"как {other}, сверив `memory/npc/{other}.md`; если другой человек — смени приметы, "
                         "чужие не повторяй.")
    return notes[:3]


def _proper_words(name):
    words = re.findall(r"[А-ЯЁA-Z][а-яёa-z\-]{2,}", name or "")
    if re.search(r"(?:^|\s)[а-яё]", name or ""):
        return set()   # описательное имя: «Дежурная врач отделения»
    return {w for w in words if w not in TITLES}


# ----------------------------------------------------------------- после слияния

def apply(hot, turn, inner):
    """Эпизоды, отношения, чувство, намерения. Вызывается после слияния дельты."""
    turn_id, at = turn["id"], turn["at"]
    if turn.get("kind") == "ooc":
        return
    present = [k for k in (hot.get("scene") or {}).get("present") or []
               if k in (hot.get("cast") or {})]
    # Отсутствующий NPC «узнал» сцену, только если запись хода говорит о его опыте (память,
    # отношение, чувство, страх). План и характер (intent, psyche) ведущий пишет за кадром по
    # просьбе NEXT.must — это не эпизод: иначе NPC «помнит» сцены, где его не было (Мориока и
    # допрос Мориты, s10.text.084; 216 из 345 таких «узнал» до починки).
    touched = set(present) | {k for k, own in inner.items() if k in (hot.get("cast") or {})
                              and (k in ALWAYS_WITNESS or not set(own) <= BOOKKEEPING)}
    line = _short(turn.get("line") or "", 260)
    for key in sorted(touched):
        card = hot["cast"][key]
        channel = "present" if key in present else "told"
        episode = {"turn": turn_id, "at": at, "ch": channel, "what": line}
        _remember(hot, key, card, episode)
    for key, own in inner.items():
        if key not in (hot.get("cast") or {}):
            _check(hot, turn_id, "cast", f"{key}: приватные поля для NPC без карточки")
            continue
        profile = _inner(hot, key)
        if own.get("mem"):
            memories = profile.setdefault("memories", [])
            memories[:] = [m for m in memories if m.get("turn") != turn_id]
            memories.append({"turn": turn_id, "at": at, "as": _short(own["mem"], 400)})
            del memories[:-PRIVATE_MEMORIES]
        if isinstance(own.get("rel"), dict):
            _relation(hot, profile, own["rel"], turn_id)
        if isinstance(own.get("affect"), dict) and own["affect"].get("feel"):
            hours = own["affect"].get("hours", DEFAULT_AFFECT_HOURS)
            try:
                until = _iso(_parse(at) + _dt.timedelta(hours=float(hours)), at)
            except (TypeError, ValueError):
                until = _iso(_parse(at) + _dt.timedelta(hours=DEFAULT_AFFECT_HOURS), at)
            profile["affect"] = {"feel": _short(own["affect"]["feel"], 200), "since": at,
                                 "until": until, "turn": turn_id}
        if "dread" in own:
            _dread.apply(hot, key, own["dread"], turn_id, at)
        if isinstance(own.get("intent"), dict):
            _intents(hot, profile, own["intent"], turn_id, at)
        if isinstance(own.get("psyche"), dict):
            facets = profile.setdefault("psyche", {})
            for facet, value in own["psyche"].items():
                if facet not in FACETS:
                    _check(hot, turn_id, "psyche", f"{key}: неизвестная грань {facet}")
                    continue
                if value is None:
                    facets.pop(facet, None)
                    continue
                if facets.get(facet) and facets[facet] != value and not own.get("why"):
                    _check(hot, turn_id, "drift",
                           f"{key}: psyche.{facet} переписана без why")
                facets[facet] = _short(value, 300)
    for key in touched:
        _tier(hot, key)


def _iso(moment, like):
    text = moment.isoformat(timespec="seconds")
    return like[:4] + text[4:] if like.startswith("0") else text


def _remember(hot, key, card, episode):
    memory = card.setdefault("memory", [])
    memory[:] = [m for m in memory if m.get("turn") != episode["turn"]]
    memory.append(episode)
    total = card.get("memory_total", 0) + 1
    if len(memory) > CARD_FIRST + CARD_RECENT:
        memory[:] = memory[:CARD_FIRST] + memory[-CARD_RECENT:]
    card["memory_total"] = max(total, len(memory))
    card.setdefault("first", memory[0]["turn"])
    hot.setdefault("_mem_log", {}).setdefault(key, []).append(episode)


def _relation(hot, profile, patch, turn_id):
    target = patch.get("to", "shiki")
    rel = profile.setdefault("rel", {}).setdefault(target, {})
    change = {}
    for dim in REL_DIMS:
        if dim not in patch:
            continue
        try:
            step = int(patch[dim])
        except (TypeError, ValueError):
            continue
        value = max(-REL_RANGE, min(REL_RANGE, int(rel.get(dim, 0)) + step))
        rel[dim] = value
        change[dim] = step
    if change:
        log = rel.setdefault("log", [])
        log.append({"turn": turn_id, "change": change, "why": _short(patch.get("why", ""), 200)})
        del log[:-LOG_KEEP]
        if not patch.get("why"):
            _check(hot, turn_id, "rel", f"сдвиг отношения к {target} без why")


SUCCESSOR_HOURS = 12


def _later(at, hours):
    try:
        return (_dt.datetime.fromisoformat(str(at)) + _dt.timedelta(hours=hours)).isoformat()
    except ValueError:
        return None


def status_of(value):
    raw = str(value or "committed").strip().lower().replace(" ", "_").replace("-", "_")
    return STATUS_ALIASES.get(raw, raw)


def default_outcome(entry):
    """Исход без слов ведущего: план сбылся — его последствия; сорвался — по слабости."""
    status = entry.get("status")
    goal = entry.get("goal") or entry.get("next") or ""
    if status == "fulfilled":
        return entry.get("if_unopposed") or f"добился: {goal}"
    goal = goal.rstrip(" .")
    if status == "partial":
        flaw = f" Помешала его слабость: {str(entry['flaw']).rstrip(' .')}." if entry.get("flaw") else ""
        return f"Добился лишь части: {str(entry.get('if_unopposed') or goal).rstrip(' .')}.{flaw}"
    if status == "failed":
        why = f" Подвела его слабость: {str(entry['flaw']).rstrip(' .')}." if entry.get("flaw") else " Ему помешали."
        return f"Сорвалось: {goal}.{why} Цель остаётся."
    return f"Оставил: {goal}."


def _successor_key(active, done, key):
    used = set(active) | {d.get("key") for d in done}
    n = 2
    while f"{key}_{n}" in used:
        n += 1
    return f"{key}_{n}"


PLAN_FIELDS = frozenset(("plan", "goal", "next", "due", "flaw", "against_shiki", "if_unopposed", "status", "outcome"))


def _flat(patch):
    """Плоский план без ключа (s13.text.013–023: intent.goal/next/due у Сирамэ стали девятью «планами»):
    {"plan": "ключ", "goal": …, "next": …} → {"ключ": {"goal": …, "next": …}}."""
    if not patch or not set(patch) <= PLAN_FIELDS or not any(isinstance(v, str) for v in patch.values()):
        return patch
    if not ({"goal", "next", "status"} & set(patch)):
        return patch
    key = "_".join(str(patch["plan"]).split())[:40] if patch.get("plan") else "own_plan"
    return {key: {k: v for k, v in patch.items() if k != "plan"}}


def _intents(hot, profile, patch, turn_id, at):
    active = profile.setdefault("intent", {})
    done = profile.setdefault("intent_done", [])
    flat = _flat(patch)
    if flat is not patch:
        _check(hot, turn_id, "intent", f"план записан без ключа ({', '.join(sorted(patch))}) — принят как "
               f"intent.{next(iter(flat))}; форма: d.cast.<id>.intent.<ключ>.{{goal, next, due, …}}")
        patch = flat
    for key in [k for k, v in active.items() if k in PLAN_FIELDS and isinstance(v, dict)
                and set(v) <= {"goal", "status", "turn"}]:
        active.pop(key)   # обломки прежней плоской записи
    for key, value in patch.items():
        if value is None:
            active.pop(key, None)
            continue
        if isinstance(value, str):
            value = {"goal": value}
        entry = dict(active.get(key, {}))
        before_next = entry.get("next")
        entry.update({k: _short(v, 240) for k, v in value.items() if isinstance(v, str)})
        entry["status"] = status_of(entry.get("status"))
        if entry["status"] not in INTENT_STATUS:
            _check(hot, turn_id, "intent", f"намерение {key}: статус «{entry['status']}» непонятен — "
                   f"оставлен активным; закрыть: fulfilled, partial, failed, abandoned")
            entry["status"] = "committed"
        entry["turn"] = turn_id
        if entry["status"] in CLOSED:
            if not value.get("outcome"):
                entry["outcome"] = _short(default_outcome(entry), 240)
                _check(hot, turn_id, "intent", f"план {key} закрыт без outcome — принято: «{entry['outcome']}». "
                       "outcome — что изменилось в мире")
            # Новый «next» при закрытии — следующий план того же человека, а не подпись к старому.
            successor = value.get("next") if isinstance(value.get("next"), str) and value.get("next") != before_next else None
            closed = {k: v for k, v in entry.items() if k not in ("next", "due")} if successor else entry
            if successor:
                closed["next"] = before_next
            active.pop(key, None)
            done.append({"key": key, "at": at, **closed})
            del done[:-LOG_KEEP]
            if successor:
                plan = {"goal": entry.get("goal", ""), "next": _short(successor, 240), "status": "committed",
                        "turn": turn_id, "after": key}
                for field in ("flaw", "against_shiki"):
                    if entry.get(field):
                        plan[field] = entry[field]
                # Без нового срока следующий шаг — через полдня игрового времени, не «когда-нибудь».
                plan["due"] = value.get("due") if isinstance(value.get("due"), str) else _later(at, SUCCESSOR_HOURS)
                active[_successor_key(active, done, key)] = plan
                entry = plan
            else:
                continue
        else:
            active[key] = entry
        if PASSIVE.match(entry.get("next") or ""):
            _check(hot, turn_id, "intent", f"план {key}: «{entry['next'][:80]}» — ожидание, а не шаг. "
                   "Что он делает сам, пока ждёт: давит, ищет, торгуется, ошибается?")


def _tier(hot, key):
    """Глубина профиля по значимости (Bleach npc_importance): не мотив, а требование."""
    card = hot["cast"][key]
    seen = card.get("memory_total", 0)
    facets = len((_inner(hot, key).get("psyche") or {}))
    tier = "deep" if seen >= 15 or facets >= 6 else "developed" if seen >= 4 else "archetypal"
    card["tier"] = tier


def needs_profile(hot, keys):
    """Кому из присутствующих не хватает психики для их значимости."""
    wanted = {"deep": 5, "developed": 3, "archetypal": 0}
    out = []
    for key in keys:
        card = (hot.get("cast") or {}).get(key) or {}
        have = len(((hot.get("_psyche") or {}).get(key) or {}).get("psyche") or {})
        need = wanted.get(card.get("tier", "archetypal"), 0)
        if have < need:
            out.append({"npc": key, "tier": card.get("tier"), "facets": have, "need": need,
                        "appearances": card.get("memory_total", 0)})
    return out


def refresh(hot):
    """Истёкшее чувство уходит в прошлое по игровым часам."""
    at = (hot.get("cursor") or {}).get("at")
    if not at:
        return
    now = _parse(at)
    for key, profile in (hot.get("_psyche") or {}).items():
        affect = profile.get("affect")
        if affect and affect.get("until") and _parse(affect["until"]) <= now:
            past = profile.setdefault("affect_past", [])
            past.append(affect)
            del past[:-3]
            profile.pop("affect")


def inner_view(hot, key, *, at=None):
    """Приватный срез для пакета сцены ведущего."""
    profile = (hot.get("_psyche") or {}).get(key) or {}
    view = {}
    if profile.get("psyche"):
        view["psyche"] = profile["psyche"]
    rel = profile.get("rel") or {}
    if rel:
        view["rel"] = {target: {**{d: v for d, v in values.items() if d in REL_DIMS},
                                "last_why": [x["why"] for x in values.get("log", [])[-3:] if x.get("why")]}
                       for target, values in rel.items()}
    if profile.get("affect"):
        view["affect"] = profile["affect"]
    if profile.get("intent"):
        view["intent"] = profile["intent"]
    if profile.get("memories"):
        view["remembers"] = profile["memories"][:2] + profile["memories"][-6:] \
            if len(profile["memories"]) > 8 else profile["memories"]
    if profile.get("drift"):
        view["drift"] = profile["drift"][-3:]
    return view


# ----------------------------------------------------------------- архив эпизодов

def flush(state_dir, hot, amended=()):
    """Полная эпизодическая память NPC: ``archive/npc-memory/<ключ>.json``."""
    log = hot.pop("_mem_log", {}) or {}
    root = Path(state_dir) / MEMORY_DIR
    if not log and not amended:
        return 0
    root.mkdir(parents=True, exist_ok=True)
    touched = set(log)
    if amended:
        touched |= {p.stem for p in root.glob("*.json")}
    written = 0
    for key in sorted(touched):
        path = root / f"{key}.json"
        data = (json.loads(path.read_text(encoding="utf-8")) if path.is_file()
                else {"schema": "npr-npc-memory/1", "key": key, "episodes": []})
        episodes = [e for e in data.get("episodes", []) if e.get("turn") not in amended]
        fresh = log.get(key, [])
        seen = {e["turn"] for e in fresh}
        episodes = [e for e in episodes if e.get("turn") not in seen] + fresh
        episodes.sort(key=lambda e: _order(e.get("turn", "")))
        card = (hot.get("cast") or {}).get(key) or {}
        if card.get("name"):
            data["name"] = card["name"]
        data["episodes"] = episodes
        body = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
        if not path.is_file() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
            written += 1
    return written


def _order(turn_id):
    match = re.match(r"s(\d+)\.text\.(\d+)", turn_id or "")
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


# ----------------------------------------------------------------- разовое восстановление

def _stems(name):
    stems = []
    for word in _proper_words(name):
        stem = word[:-2] if len(word) >= 6 else word[:-1] if len(word) >= 5 else word
        stems.append(re.escape(stem))
    return stems


def backfill(state_dir):
    """Восстанавливает эпизоды всех NPC по дословным журналам сыгранных сессий.

    Совпадение имени — указатель «упомянут/был в сцене», а не доказательство,
    что NPC видел или знал всё сказанное в этом ходе (``ch: "mentioned"``).
    Повторный запуск ничего не дублирует.
    """
    state_dir = Path(state_dir)
    cards = {}
    cast_dir = state_dir / "recovery/cast"
    for path in sorted(cast_dir.glob("*.json")):
        cards[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    patterns = {}
    for key, card in cards.items():
        stems = _stems(card.get("name", ""))
        if stems:
            patterns[key] = re.compile(r"(?<![А-Яа-яЁё])(?:" + "|".join(stems) + r")[а-яё]*")
    rows = []
    for path in sorted((state_dir / "archive").glob("session-*-turns.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                if row.get("kind") != "ooc" and row.get("id"):
                    rows.append(row)
    rows.sort(key=lambda r: _order(r["id"]))
    found = {}
    for row in rows:
        text = " ".join(str(row.get(k) or "") for k in ("gm", "line"))
        for key, pattern in patterns.items():
            if pattern.search(text):
                found.setdefault(key, []).append(
                    {"turn": row["id"], "at": row.get("at", ""), "ch": "mentioned",
                     "what": _short(row.get("line") or "", 260)})
        for key, card in cards.items():
            if card.get("seen") == row["id"] and not any(
                    e["turn"] == row["id"] for e in found.get(key, [])):
                found.setdefault(key, []).append(
                    {"turn": row["id"], "at": row.get("at", ""), "ch": "present",
                     "what": _short(row.get("line") or "", 260)})
    root = state_dir / MEMORY_DIR
    root.mkdir(parents=True, exist_ok=True)
    report = {}
    for key, episodes in found.items():
        path = root / f"{key}.json"
        data = (json.loads(path.read_text(encoding="utf-8")) if path.is_file()
                else {"schema": "npr-npc-memory/1", "key": key, "episodes": []})
        have = {e["turn"]: e for e in data.get("episodes", [])}
        for episode in episodes:
            have.setdefault(episode["turn"], episode)
        merged = sorted(have.values(), key=lambda e: _order(e["turn"]))
        data.update({"name": cards[key].get("name", ""), "episodes": merged,
                     "note": "ch=mentioned: имя встречается в ходе; это указатель на источник, "
                             "а не доказательство, что NPC всё видел или знал"})
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        report[key] = len(merged)
    return report


def hydrate_card(card, episodes):
    """Ранние и последние эпизоды из архива — в карточку NPC."""
    if not episodes:
        return card
    card["memory"] = (episodes if len(episodes) <= CARD_FIRST + CARD_RECENT
                      else episodes[:CARD_FIRST] + episodes[-CARD_RECENT:])
    card["memory_total"] = len(episodes)
    card["first"] = episodes[0]["turn"]
    seen = len(episodes)
    card["tier"] = "deep" if seen >= 15 else "developed" if seen >= 4 else "archetypal"
    return card


def sync_cards(state_dir, hot):
    """Архив эпизодов — источник истины: карточки в HOT сверяются с ним после свёртки.

    Так исправление хода (amend), откатывающее карточку к снимку до хода,
    не обнуляет восстановленную память NPC.
    """
    for key, card in (hot.get("cast") or {}).items():
        episodes = load_episodes(state_dir, key)
        if episodes:
            hydrate_card(card, episodes)
            _tier(hot, key)


def load_episodes(state_dir, key):
    path = Path(state_dir) / MEMORY_DIR / f"{key}.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("episodes", [])


def repair_told(state_dir):
    """Хранитель: убрать ложные «узнал» — эпизоды отсутствующих NPC от записи только плана.

    Сверяет каждый эпизод ``told`` с дельтой его хода в ``archive/deltas``: если для этого
    NPC там был лишь intent/psyche/why, эпизод убирается из архива памяти, карточки в
    recovery/cast и HOT.json; ``memory_total`` уменьшается на столько же.
    """
    import glob
    import json as _json
    import os
    state_dir = Path(state_dir)
    deltas = {}
    for path in glob.glob(str(state_dir / "archive/deltas/session-*.jsonl")):
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = _json.loads(line)
                deltas[row["id"]] = row.get("d") or {}

    def false(key, episode):
        if episode.get("ch") != "told" or key in ALWAYS_WITNESS:
            return False
        patch = ((deltas.get(episode.get("turn")) or {}).get("cast") or {}).get(key)
        return isinstance(patch, dict) and bool(patch) and set(patch) <= BOOKKEEPING

    removed = {}
    for path in glob.glob(str(state_dir / "archive/npc-memory/*.json")):
        key = os.path.basename(path)[:-5]
        data = _json.loads(Path(path).read_text(encoding="utf-8"))
        kept = [e for e in data.get("episodes") or [] if not false(key, e)]
        if len(kept) != len(data.get("episodes") or []):
            removed[key] = len(data["episodes"]) - len(kept)
            data["episodes"] = kept
            Path(path).write_text(_json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    def fix_card(key, card):
        memory = card.get("memory")
        if not isinstance(memory, list):
            return False
        kept = [e for e in memory if not false(key, e)]
        if len(kept) == len(memory):
            return False
        card["memory"] = kept
        card["memory_total"] = max(len(kept), int(card.get("memory_total") or 0) - removed.get(key, len(memory) - len(kept)))
        return True

    for path in glob.glob(str(state_dir / "recovery/cast/*.json")):
        key = os.path.basename(path)[:-5]
        card = _json.loads(Path(path).read_text(encoding="utf-8"))
        if fix_card(key, card):
            Path(path).write_text(_json.dumps(card, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    hot_path = state_dir / "recovery/HOT.json"
    hot = _json.loads(hot_path.read_text(encoding="utf-8"))
    if any([fix_card(k, c) for k, c in (hot.get("cast") or {}).items() if isinstance(c, dict)]):
        hot_path.write_text(_json.dumps(hot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"removed": sum(removed.values()), "npcs": len(removed), "top": sorted(removed.items(), key=lambda kv: -kv[1])[:10]}
