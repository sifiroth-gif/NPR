"""Эхо прошлого: изредка NEXT предлагает ведущему давнюю нить, созвучную сцене.

Игроку нравится, когда прошлое возвращается (контракт №1184 сам лёг в дело
С-17). Код не решает, что связано: он находит в давнем — ушедших в архив
линиях и вещах, долго не тронутых линиях, эпизодах прошлых сессий — то, что
созвучно нынешней сцене по редким словам, и не чаще раза в несколько ходов
кладёт одну нить в ``NEXT.echo``. Вплетать или нет — решает ведущий.

Если доступен смысловой слой (``meaning``), эпизоды прошлых сессий, близкие
сцене по смыслу, получают надбавку и проходят при одной общей редкой основе
вместо двух: смысл без хотя бы одного общего редкого слова эхом не считается.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

from . import aging, history_access, identity
from .heuristic_history import stem, tokens

GAP = 6            # не чаще раза в шесть ходов
COOLDOWN = 120     # одну нить — не раньше чем через 120 ходов
FRESH = 25         # дополнительная защита от недавних ходов
MIN_AGE_DAYS = 60  # Эхо — месяцы и годы по игровому календарю, не по числу ходов
MIN_SHARED = 2     # минимум общих значимых основ
MIN_SCORE = 7.0    # сумма редкости общих основ: созвучие, а не совпадение служебных слов
NAME = re.compile(r"(?<=[\s(«\"])([А-ЯЁ][а-яё]{3,}|[А-ЯЁA-Z]-?\d+)")
COMMON = frozenset(stem(w) for w in (
    "шики мугами шиноби время место сцене сцена после через между может только этого который "
    "которые нужно сейчас своей своих своим своего свою очень более менее всего всех также "
    "человек людей можно нельзя когда тогда снова опять почти около рядом перед кроме деревни "
    "деревня страна страны стране ходом ходов игрок ведущий").split())
RULE = ("Давнее, созвучное сцене, — верни событием, доступным Шики: встреча, поступок, весть, возвращение знакомого, "
        "вещь или последствие прежнего решения, перемена отношений. Упоминание, имя или воспоминание исполнением не "
        "считаются (код проверяет). Нет повода — отложи до правдоподобного события; связь не объявляй.")
# Эхо — событие, не слово (игрок, s13.text.008–009, 24.11.2026): засчитывается, когда давняя нить
# действует в сцене — её человек говорит или стоит в составе, вещь меняет владельца, в предложении
# с её приметой что-то происходит (пришёл, прислал, весть, нашли, требует, сгорел…). Примета лишь
# в воспоминании или в речи Шики — «упомянуто»: эхо остаётся в NEXT до HOLD ходов.
EVENT = re.compile(
    r"(?<![\wё])(?:приш[её]л\w*|пришл\w*|появил\w*|вернул\w*|возвра\w*|прибыл\w*|прислал\w*|принес\w*|принёс\w*|"
    r"доставил\w*|письм\w*|записк\w*|весть|вести|известие|слух\w*|донос\w*|наш[её]л\w*|нашли|ждал\w*|ждёт|ждут|"
    r"встретил\w*|напал\w*|требу\w*|предложил\w*|сгорел\w*|умер\w*|погиб\w*|продал\w*|отдал\w*|забрал\w*|"
    r"украл\w*|окликнул\w*|постучал\w*|догнал\w*|выследил\w*|заплатил\w*|узнал\w*|стоял\w*|сидел\w*|"
    r"протянул\w*|положил\w*|бросил\w*|ударил\w*|остановил\w*|перегородил\w*|позвал\w*|просил\w*)", re.I)
RECALL_ONLY = re.compile(r"(?<![\wё])(?:вспомн\w*|дума\w*|мысл\w*|вспомина\w*|напомина\w*|подумал\w*|когда-то|некогда|памят\w*|"
                         r"помнил\w*|рассказывал\w*|говорили когда)", re.I)
LABEL = re.compile(r"^\s*\*\*([^*:]{2,60}):\*\*")
SENT = re.compile(r"[^.!?…\n]+[.!?…]*")
HOLD = 4           # столько ходов эхо держится в NEXT, пока не вплетено
NEAR = 8           # сколько ближайших по смыслу эпизодов получают надбавку
NEAR_BONUS = 4.0   # надбавка ближайшему; дальше — по убыванию ранга


def _when(value):
    """Игровая календарная дата; неизвестный источник не считается давним."""
    match = re.search(r"(?<!\d)\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?", str(value or ""))
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(0))
    except ValueError:
        return None


def _age_days(today, then):
    return (today.date() - then.date()).days if today and then else None


def _history_index(state_dir):
    path = Path(state_dir) / "archive/heuristic-recall/manifest.json"
    if not path.is_file():
        return None
    try:
        from . import heuristic_history
        return heuristic_history.load(path)
    except (OSError, ValueError, KeyError):
        return None


def _dated_turns(index):
    """Индекс первоисточников: точное время из архивного хода, не ordinal сессии."""
    result = {}
    for row in (index or {}).get("records") or []:
        if row.get("kind") != "played":
            continue
        at = _when(row.get("at"))
        if at is None:
            continue
        rid = str(row.get("id") or "")
        result[rid] = at
        base = _turn_of(rid)
        if base not in result or at < result[base]:
            result[base] = at
    return result


def _old_source(candidate, today, dated):
    source = str(candidate[2] or "")
    when = _when(source) or dated.get(source) or dated.get(_turn_of(source))
    age = _age_days(today, when)
    return age if age is not None and age >= MIN_AGE_DAYS else None


def _stems(text):
    out = {}
    for word in tokens(text):
        if len(word) < 5:
            continue
        base = stem(word)
        if base and base not in COMMON:
            out.setdefault(base, word)
    return out


def _context(hot):
    scene = hot.get("scene") or {}
    cursor = hot.get("cursor") or {}
    names = [str(((hot.get("cast") or {}).get(k) or {}).get("name") or "") for k in scene.get("present") or []]
    parts = [cursor.get("place"), scene.get("situation"), scene.get("pressure"),
             ((hot.get("last") or {}).get("line")), " ".join(names)]
    for event in ((hot.get("_plot") or {}).get("events") or {}).values():
        if isinstance(event, dict) and event.get("status") == "active":
            parts.append(event.get("title"))
    return " ".join(str(p) for p in parts if p)


def _about_present(candidate, present):
    """Нить о присутствующем NPC — это сама сцена, а не забытое прошлое."""
    keys, names = present
    _, key, _, text, _ = candidate
    tail = key.split(":", 1)[-1].casefold()
    if any(part in tail for k in keys for part in k.split("_") if len(part) >= 4):
        return True
    return bool({stem(w.casefold()) for w in NAME.findall(" " + str(text))} & names)


def _candidates(state_dir, hot, index=None):
    now = aging._ordinal((hot.get("cursor") or {}).get("id")) or 0
    for key, row in aging.load(state_dir, "threads.jsonl").items():
        value = row.get("value") or {}
        yield ("линия", f"thread:{key}", value.get("turn") or value.get("last_known"),
               f"{key}: {value.get('state') if isinstance(value, dict) else value}", 1.0)
    for key, value in (hot.get("threads") or {}).items():
        mark = (value or {}).get("turn") if isinstance(value, dict) else None
        if now - (aging._ordinal(mark) or now) >= FRESH:
            yield ("линия", f"thread:{key}", mark, f"{key}: {(value or {}).get('state')}", 1.0)
    for key, row in aging.load(state_dir, "things.jsonl").items():
        value = row.get("value")
        yield ("вещь", f"thing:{key}", (value or {}).get("turn") if isinstance(value, dict) else None,
               f"{key}: {json.dumps(value, ensure_ascii=False)[:300]}", 0.6)
    yield from _history(state_dir, hot, now, index=index)


def _history(state_dir, hot, now, index=None):
    """Эпизоды прошлых сессий из датированного исторического индекса."""
    if index is None:
        index = _history_index(state_dir)
    if not index:
        return
    for row in index["records"]:
        if row.get("kind") != "played":
            continue
        rid = str(row.get("id") or "")
        turn = aging._ordinal(rid) or aging._ordinal(row.get("source"))
        if turn is not None and now - turn < FRESH * 2:
            continue
        yield ("эпизод", f"history:{rid}", rid, f"{row.get('headline')} {row.get('preview')}", 0.8)


def _near(state_dir, context_text, pool):
    """Надбавка эпизодам прошлых сессий, ближайшим к сцене по смыслу; без слоя — пусто."""
    keys = {c[1] for c in pool if c[0] == "эпизод"}
    if not keys:
        return {}
    try:
        from . import meaning
        found = meaning.search(state_dir, context_text, limit=NEAR,
                               allow=lambda rid: f"history:{rid}" in keys)
    except Exception:  # производный слой не мешает эху
        return {}
    return {f"history:{rid}": NEAR_BONUS * (1 - rank / NEAR) for rank, (rid, _) in enumerate(found or [])}


def _used(state, texts):
    """Вплетено: в новом тексте ведущего — то, чего в сцене не было, когда эхо выбрали.

    Общие со сценой слова эха (по ним его и нашли) ничего не доказывают: они и так
    звучат в следующем ходе. Считаются только «свежие» основы — имена и редкие
    слова давней нити, которых не было в контексте сцены.
    """
    fresh = state.get("fresh")
    if not fresh:
        return False
    words = {stem(w) for text in texts for w in tokens(text) if len(w) >= 4}
    return any(m in words for m in fresh)


def _event_use(state, hot, turns):
    """(событие, упоминание): давняя нить вернулась в сцену действием или лишь словом."""
    fresh = set(state.get("fresh") or []) - {stem("шики")}   # сам Шики в сцене всегда
    if not fresh:
        return False, False
    cast = hot.get("cast") or {}
    mention = False
    for turn in turns:
        delta = turn.get("d") or {}
        names = []   # кто в составе и в дельте, вещи, сменившие владельца
        for key in list((delta.get("cast") or {}).keys()) + list(((delta.get("scene") or {}).get("present") or [])):
            names.append(str((cast.get(key) or {}).get("name") or ((delta.get("cast") or {}).get(key) or {}).get("name") or ""))
        for key, change in (delta.get("things") or {}).items():
            names.append(f"{key} {json.dumps(change, ensure_ascii=False)}")
        if {stem(w) for n in names for w in tokens(n) if len(w) >= 4} & fresh:
            return True, True
        for raw in str(turn.get("gm") or "").split("\n"):
            label = LABEL.match(raw)
            if label and "шики" not in label.group(1).casefold():
                if {stem(w) for w in tokens(label.group(1)) if len(w) >= 4} & fresh:
                    return True, True      # человек давней нити говорит в сцене
                continue
            if label or raw.lstrip("*_ ").startswith(("—", "–")):
                if {stem(w) for w in tokens(raw) if len(w) >= 4} & fresh:
                    mention = True         # в речи (в том числе Шики) — слово, не событие
                continue
            for match in SENT.finditer(raw):
                sentence = match.group()
                if not ({stem(w) for w in tokens(sentence) if len(w) >= 4} & fresh):
                    continue
                if EVENT.search(sentence) and not RECALL_ONLY.search(sentence):
                    return True, True
                mention = True
    return False, mention


def _present(hot):
    """Ключи и основы имён присутствующих: их нити — сама сцена, не давнее."""
    cast = hot.get("cast") or {}
    keys, names = set(), set()
    for key in (hot.get("scene") or {}).get("present") or []:
        if key in ("shiki", "player"):
            continue
        keys.add(str(key).casefold())
        name = (cast.get(key) or {}).get("name") or ((hot.get("_psyche") or {}).get(key) or {}).get("name") or ""
        names.update(stem(w.casefold()) for w in NAME.findall(" " + name))
    return keys, names - COMMON


def pick(state_dir, hot, texts=(), turns=None):
    """Одна давняя нить для NEXT.echo или None; состояние — в HOT.fold.echo.

    Эхо держится до HOLD ходов, пока ведущий его не вплёл, — один ход в NEXT
    оказывался слишком коротким, чтобы сцена успела к нему повернуть.
    """
    cursor = (hot.get("cursor") or {}).get("id")
    now = aging._ordinal(cursor)
    if now is None:
        return None
    state = hot.setdefault("fold", {}).setdefault("echo", {})
    if state.get("age_policy") != "calendar-60d/1":
        # Старое предложение могло быть "давним" лишь по числу ходов.
        # При смене правила не блокируем ближайшую настоящую давнюю нить.
        state["current"], state["last"], state["at"] = None, None, None
        state["age_policy"] = "calendar-60d/1"
    if state.get("method") != "event":     # до 25.11.2026 «использовано» считалось по словам — счёт заново
        state["before_event"] = {"offered": state.get("offered", 0), "used_by_words": state.get("used_count", 0)}
        state["offered"], state["used_count"], state["mentioned_count"] = (1 if state.get("current") else 0), 0, 0
        state["method"] = "event"
    if state.get("at") == cursor:          # свёртка без нового хода: то же эхо
        return state.get("current")
    current = state.get("current")
    if current and turns:
        event, mention = _event_use(state, hot, turns)
        if event:
            state["used_count"] = int(state.get("used_count") or 0) + 1
            current = state["current"] = None
        elif mention and state.get("mentioned_for") != current.get("from"):
            state["mentioned_count"] = int(state.get("mentioned_count") or 0) + 1
            state["mentioned_for"] = current.get("from")
    elif current and texts and _used(state, texts):   # старый вызов без ходов — по словам
        state["used_count"] = int(state.get("used_count") or 0) + 1
        current = state["current"] = None
    if current and state.get("last") is not None and now - state["last"] < HOLD:
        state["at"] = cursor
        return current
    if state.get("last") is not None and now - state["last"] < GAP:
        state["current"], state["at"] = None, cursor
        return None
    context_text = _context(hot)
    present = _present(hot)
    context = _stems(context_text)
    names = {stem(w.casefold()) for w in NAME.findall(" " + context_text)}
    index = _history_index(state_dir)
    dated = _dated_turns(index)
    today = _when((hot.get("cursor") or {}).get("at"))
    used = state.setdefault("used", {})
    pool = [c for c in _candidates(state_dir, hot, index=index)
            if now - used.get(c[1], -10**6) >= COOLDOWN
            and _old_source(c, today, dated) is not None
            and not _about_present(c, present)]
    found_all = [_stems(c[3]) for c in pool]
    df = {}
    for found in found_all:
        for base in found:
            df[base] = df.get(base, 0) + 1
    total = max(1, len(pool))
    near = _near(state_dir, context_text, pool)
    best = None
    for (kind, key, source, text, weight), found in zip(pool, found_all):
        shared = [s for s in context if s in found]
        bonus = near.get(key, 0.0)
        if len(shared) < (1 if bonus else MIN_SHARED):
            continue
        # Эпизод прошлого созвучен через лицо или место, а не через общее ремесло
        # («стерилизация» в двух операциях — совпадение, не эхо).
        if kind == "эпизод" and not ({stem(w.casefold()) for w in NAME.findall(" " + text)} & set(shared)):
            continue
        rarity = {s: math.log((total + 1) / (df.get(s, 0) + 1)) * (1.5 if s in names else 1.0) for s in shared}
        # При сопоставимой тематической связи предпочтительнее годы, а не недели.
        age_days = _old_source((kind, key, source, text, weight), today, dated) or 0
        age_bonus = min(2.0, (age_days / 365) * 2.0)
        score = (sum(rarity.values()) + (0.5 if kind == "линия" else 0)) * weight + bonus + age_bonus
        if score < MIN_SCORE or max(rarity.values()) < math.log((total + 1) / 4):
            continue
        if best is None or score > best[0]:
            words = [found[s] for s in sorted(shared, key=lambda s: -rarity[s])]
            best = (score, kind, key, source, text, words)
    state["at"] = cursor
    if not best:
        state["current"] = None
        return None
    _, kind, key, source, text, shared = best
    state["last"], used[key] = now, now
    # «Свежие» приметы давней нити: имена и по-настоящему редкие слова (≤2 источников из
    # всего пула), которых не было в сцене. Обычные длинные слова («который», «сейчас»)
    # звучат в любом ходе и вплетённым эхо не делают.
    names = {stem(w.casefold()) for w in NAME.findall(" " + text)} - COMMON
    rare = {stem(w) for w in tokens(text) if len(w) >= 6 and df.get(stem(w), 0) <= 2}
    state["fresh"] = sorted((names | rare) - set(context))[:12]
    state["offered"] = int(state.get("offered") or 0) + 1
    state["used"] = dict(sorted(used.items(), key=lambda kv: kv[1])[-60:])
    state["current"] = {"kind": kind, "from": source, "what": " ".join(str(text).split())[:220],
                        "echoes": ", ".join(shared[:4]), "rule": RULE}
    return state["current"]


# --- Давнее присутствующих: историческая память NPC о Шики по теме сцены (24.11.2026) ---------------
# s12.text.043: Орочимару «впервые» поражён Шаринганом Шики, которого видел ещё в s09; память и мысли
# NPC просочились в прозу. Эхо выше нарочно молчит о присутствующих («это сама сцена»), а NEXT.knows —
# лишь сжатые метки. Здесь — эпизоды, которые этот человек прожил рядом с Шики, созвучные нынешней сцене
# по редким словам и (если есть смысловой слой) по смыслу: его память берётся из них, а не из догадки.
PAST_TURNS = 4       # давних эпизодов на присутствующего (игрок, 24.11.2026: «давай 4 эпизода по теме»)
PAST_SCENE = 12      # последние ходы — нынешняя сцена, не прошлое
PAST_CHARS = 190
PAST_MIN = 4.0       # сумма редкости общих со сценой основ
PAST_SENTENCE = 4.0  # редкость темы в самом предложении (с надбавкой за имя): ниже — пустое упоминание
PAST_POOL = 400      # смысловой слой: ближайших к сцене записей прошлых сессий за один запрос
PAST_NEAR = 12       # из них у каждого присутствующего надбавку получают ближайшие
PAST_MEANING = 10.0  # надбавка ближайшему по смыслу (сравнима с редкостью двух-трёх общих слов)
SENTENCE = re.compile(r"[^.!?…\n]+[.!?…]*")


def _turn_of(rid):
    return ".".join(str(rid).split(".")[:3])


def _session(row):
    match = re.match(r"s0*(\d+)\.", str(row.get("id") or ""))
    return int(match.group(1)) if match else None


def _source_text(state_dir, row, cache):
    src = str(row.get("source") or "")
    path = Path(state_dir) / src
    if src not in cache:
        cache[src] = None
        try:
            if src.endswith("-turns.jsonl"):
                cache[src] = {r.get("id"): r.get("gm") or "" for r in
                              (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())}
            elif src.endswith(".md"):
                cache[src] = path.read_text(encoding="utf-8").split("\n")
        except (OSError, ValueError):
            pass
    data = cache[src]
    if isinstance(data, dict):
        return data.get(row.get("id")) or ""
    if isinstance(data, list):
        return "\n".join(data[max(0, int(row.get("start") or 1) - 1):int(row.get("end") or 0)])
    return ""


META = re.compile(r"(?<![а-яё])(?:мастер\w*|ведущ\w*|канонич\w*|сюжет\w*|игрок\w*|сессии|вне игры)(?![а-яё])", re.I)


def _best_sentence(text, name_stems, topic, weight, loose=False):
    """Предложение о теме сцены, где этот человек назван (в нём или в предыдущем): его действие или слово.
    Служебные заметки ведущего («канонические линии сдвигаются») — не память персонажа."""
    sents = [" ".join(m.group(0).replace("*", "").split()) for m in SENTENCE.finditer(text)]
    sents = [x for x in sents if len(x) >= 12 and not x.startswith(">") and not META.search(x)]
    stems = [{stem(w) for w in tokens(x) if len(w) >= 4} for x in sents]
    best = None
    for i, (sent, words) in enumerate(zip(sents, stems)):
        here = bool(words & name_stems)
        before = i > 0 and bool(stems[i - 1] & name_stems)
        if not (here or before):
            continue
        shared = words & topic if here else (words | stems[i - 1]) & topic
        if not shared and not loose:   # одно имя без темы сцены — не память о ней («За дверью стихли шаги Кабуто»)
            continue
        score = sum(weight(x) for x in shared) + (1.5 if here else 0)
        if best is None or score > best[0]:
            best = (score, sent if here else f"{sents[i - 1]} {sent}")
    if best and best[0] >= PAST_SENTENCE:
        return best
    if loose:   # близко по смыслу, а общих слов нет: его самое содержательное предложение в том ходе
        rich = [(sum(weight(x) for x in words), sent) for sent, words in zip(sents, stems)
                if words & name_stems and len(sent) >= 25]
        return max(rich) if rich else None
    return None


VERBISH = ("ил", "ал", "ял", "ел", "ла", "ли", "ло", "ет", "ит", "ют", "ут", "ат", "ят", "ся", "сь", "ть",
           "ший", "вший", "ющий", "ящий", "емый", "имый", "но", "ее", "ей")
TOPIC_DF = 120       # чаще — общее слово хроники, не тема сцены


def _topic(context_words, present_names, df):
    """Основы темы сцены: редкие слова, не имена присутствующих и не глагольные формы."""
    return {s for s, w in context_words.items() if s not in present_names and int(df.get(s, 0)) <= TOPIC_DF
            and not str(w).casefold().endswith(VERBISH)}


def present_past(state_dir, hot, skip=()):
    """{key: ["sNN.text.NNN — «…»"]}: до PAST_TURNS эпизодов прошлых сессий каждого присутствующего.

    Только прошлые сессии: нынешняя — в HOT и хронике. Общее со сценой считается без имён
    присутствующих (иначе любой эпизод, где они упомянуты вместе, «созвучен»): по теме — техникам,
    вещам, местам, событиям.
    """
    path = Path(state_dir) / "archive/heuristic-recall/manifest.json"
    if not path.is_file():
        return {}
    from . import heuristic_history
    try:
        index = heuristic_history.load(path)
    except (OSError, ValueError, KeyError):
        return {}
    now = aging._ordinal((hot.get("cursor") or {}).get("id")) or 0
    context_text = _context(hot)
    df, total = index.get("df") or {}, max(1, len(index.get("records") or []))
    cast, out, cache = hot.get("cast") or {}, {}, {}
    session = int(hot.get("session") or 0)
    today = _when((hot.get("cursor") or {}).get("at"))
    rows = [r for r in index.get("records") or [] if r.get("kind") == "played"
            and not str(r.get("source") or "").startswith("recovery/")
            and _session(r) is not None and _session(r) < session
            and (_age_days(today, _when(r.get("at"))) or -1) >= MIN_AGE_DAYS]
    idf = lambda base: math.log((total + 1) / (int(df.get(base, 0)) + 1))
    keys, present_names = _present(hot)
    topic = _topic(_stems(context_text), present_names | {stem("шики"), stem("мугами")}, df)
    sem = {}
    try:   # смысловой слой (e5-small в свёртке GitHub Actions): один запрос на сцену
        from . import meaning
        allowed = {r.get("id") for r in rows}
        sem = dict(meaning.search(state_dir, context_text, limit=PAST_POOL, allow=lambda rid: rid in allowed) or [])
    except Exception:   # слой необязателен: без него — по словам
        sem = {}
    hot.setdefault("fold", {})["past"] = {"at": (hot.get("cursor") or {}).get("id"), "meaning": bool(sem)}
    from . import npc_memory, presence, acquaint
    names = presence.roster(state_dir, hot)
    rules = identity.active(state_dir)
    primary = acquaint.turns(state_dir)
    scopes = history_access.scopes(primary, history_access.deltas(state_dir))
    for key in (hot.get("scene") or {}).get("present") or []:
        if key in ("shiki", "player") or key in skip:
            continue
        name = (cast.get(key) or {}).get("name") or ((hot.get("_psyche") or {}).get(key) or {}).get("name") or ""
        name_stems = {stem(w.casefold()) for w in NAME.findall(" " + name)} - COMMON
        # A shared surname is not identity and mention is not eyewitness knowledge.
        # Stable-ID presence evidence or this actor's exact labelled speech is required.
        try:
            episodes = npc_memory.load_episodes(state_dir, key)
        except (OSError, ValueError):
            episodes = []
        witnessed = {_turn_of(ep.get("turn")) for ep in episodes
                     if ep.get("ch") in history_access.WITNESS}
        excluded = identity.excluded_episodes(state_dir, key)
        own = []
        evidence = {}
        for row in rows:
            source_turn = _turn_of(row.get("id"))
            turn = aging._ordinal(source_turn)
            if turn is not None and now - turn < PAST_SCENE:
                continue
            if source_turn in excluded or not history_access.PLAYED.fullmatch(source_turn):
                continue
            terms = set(str(row.get("terms") or "").split())
            scope = scopes.get(source_turn)
            if scope is not None:
                scope = history_access.corrected_scope(rules, source_turn, scope)
            direct = key in scope if scope is not None else source_turn in witnessed
            # Only exact played rows, no arbitrary old export window masquerading as testimony.
            if row.get("id") != source_turn or not str(row.get("source", "")).endswith("-turns.jsonl"):
                continue
            if not direct and not terms & name_stems:
                continue
            text = _source_text(state_dir, row, cache)
            if not text:
                continue
            speech = history_access.own_speech(text, key, source_turn, names, rules, scope)
            if not direct and not speech:
                continue
            shared = terms & topic
            rarity = sum(idf(s) for s in shared)
            evidence[row["id"]] = (direct, speech)
            own.append((rarity, row))
        ranked = sorted((r.get("id") for _, r in own if r.get("id") in sem), key=lambda rid: -sem[rid])
        near = {rid: PAST_MEANING * (1 - rank / PAST_NEAR) for rank, rid in enumerate(ranked[:PAST_NEAR])}
        picked, seen = [], set()
        for score, row in sorted(((s + near.get(r.get("id"), 0.0), r) for s, r in own),
                                 key=lambda x: -x[0]):
            turn = _turn_of(row.get("id"))
            if score < PAST_MIN or turn in seen:
                continue
            direct, speech = evidence[row["id"]]
            # Speech is this actor's own utterance, not objective truth. Never
            # concatenate the next narrator sentence or another actor's words.
            ranked_speech = [(sum(idf(s) for s in (_stems(line).keys() & topic)), line)
                             for line in speech if not META.search(line)]
            best = max(ranked_speech, default=(0, ""))
            if best[0] >= PAST_SENTENCE:
                sent = best[1]
                item = f"{turn} — сказал: «{sent[:PAST_CHARS - 1] + '…' if len(sent) > PAST_CHARS else sent}»"
            elif direct:
                item = f"{turn} — присутствовал; конкретное знание сверить по ходу и каналу."
            else:
                continue
            seen.add(turn)
            picked.append(item)
            if len(picked) >= PAST_TURNS:
                break
        if picked:
            out[key] = picked
    return out
