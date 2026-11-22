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
from pathlib import Path

from . import aging
from .heuristic_history import stem, tokens

GAP = 6            # не чаще раза в шесть ходов
COOLDOWN = 120     # одну нить — не раньше чем через 120 ходов
FRESH = 25         # то, чего касались последние 25 ходов, — не прошлое
MIN_SHARED = 2     # минимум общих значимых основ
MIN_SCORE = 7.0    # сумма редкости общих основ: созвучие, а не совпадение служебных слов
NAME = re.compile(r"(?<=[\s(«\"])([А-ЯЁ][а-яё]{3,}|[А-ЯЁA-Z]-?\d+)")
COMMON = frozenset(stem(w) for w in (
    "шики мугами шиноби время место сцене сцена после через между может только этого который "
    "которые нужно сейчас своей своих своим своего свою очень более менее всего всех также "
    "человек людей можно нельзя когда тогда снова опять почти около рядом перед кроме деревни "
    "деревня страна страны стране ходом ходов игрок ведущий").split())
RULE = ("Давнее, созвучное сцене: вплети в ближайшие ходы, когда ляжет, — деталью, документом, человеком, "
        "слухом; связь не объявляй.")
HOLD = 4           # столько ходов эхо держится в NEXT, пока не вплетено
NEAR = 8           # сколько ближайших по смыслу эпизодов получают надбавку
NEAR_BONUS = 4.0   # надбавка ближайшему; дальше — по убыванию ранга


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


def _candidates(state_dir, hot):
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
    yield from _history(state_dir, hot, now)


def _history(state_dir, hot, now):
    """Эпизоды прошлых сессий из исторического индекса (без загрузки — без них)."""
    path = Path(state_dir) / "archive/heuristic-recall/manifest.json"
    if not path.is_file():
        return
    try:
        from . import heuristic_history
        index = heuristic_history.load(path)
    except (OSError, ValueError, KeyError):
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


def pick(state_dir, hot, texts=()):
    """Одна давняя нить для NEXT.echo или None; состояние — в HOT.fold.echo.

    Эхо держится до HOLD ходов, пока ведущий его не вплёл, — один ход в NEXT
    оказывался слишком коротким, чтобы сцена успела к нему повернуть.
    """
    cursor = (hot.get("cursor") or {}).get("id")
    now = aging._ordinal(cursor)
    if now is None:
        return None
    state = hot.setdefault("fold", {}).setdefault("echo", {})
    if state.get("at") == cursor:          # свёртка без нового хода: то же эхо
        return state.get("current")
    current = state.get("current")
    if current and texts and _used(state, texts):
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
    used = state.setdefault("used", {})
    pool = [c for c in _candidates(state_dir, hot) if now - used.get(c[1], -10**6) >= COOLDOWN
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
        score = (sum(rarity.values()) + (0.5 if kind == "линия" else 0)) * weight + bonus
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
