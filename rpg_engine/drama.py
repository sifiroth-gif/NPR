"""Драма, которую подсказки не держат (диагностика ведущего, 25.11.2026).

Пять жалоб одного корня — мир ждёт Шики:
- ритм ровный: ход за ходом одной длины и по нескольку минут игрового времени;
- NPC только отвечают: запись памяти или чувства считалась «собственным шагом» и
  обнуляла счёт пассивности (Немера отвечала 18 ходов, замечание не выходило);
- давний знакомый возвращается прежним: отношение 1283 года переносится в 1287 без
  прожитых лет (Кэйдзи);
- второстепенные говорят только с Шики, друг с другом — никогда;
- у главной линии нет главы на месяцы вперёд, а замечание отсылает к главе со сроком мира
  через четыре месяца.

Код меряет простое (длина, игровое время, метки речи, даты памяти, статусы событий) и
даёт ведущему один пункт must за раз, не чаще COOLDOWN ходов на вид. Что именно сделать —
решает ведущий по сцене.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .heuristic_history import stem, tokens

RHYTHM_WINDOW = 6      # столько ходов подряд сравнивается
RHYTHM_SPREAD = 0.22   # разброс длины (коэффициент вариации) ниже — ровно
RHYTHM_STEP = 30       # минут игрового времени за ход — мелкий шаг
GAP_DAYS = 30          # не виделись дольше — человек прожил это время без Шики
MEET_DAYS = 2          # записи памяти ближе друг к другу — одна встреча
TALK_TURNS = 3         # двое и больше NPC рядом столько ходов и ни слова друг другу
CHAPTER_GAP_DAYS = 7   # до ближайшей главы мира дальше — у «След Шики» нужна своя
COOLDOWN = 5           # ходов между пунктами одного вида
LABEL = re.compile(r"^\s*\*\*([^*:]{2,60}):\*\*", re.M)
SENT = re.compile(r"[^.!?…\n]+[.!?…]*")
SPEAK = re.compile(r"(?<![\wё])(?:сказал\w*|бросил\w*|ответил\w*|спросил\w*|повернул\w* к|обратил\w*|"
                   r"окликнул\w*|приказал\w*|возразил\w*|перебил\w*|шепнул\w*|кивнул\w* (?:ему|ей)|"
                   r"посмотрел\w* на|толкнул\w*|одёрнул\w*|передал\w*)", re.I)
# Свой шаг NPC, а не отклик: требует, предлагает, приказывает, делает без просьбы.
INITIATIVE = re.compile(r"(?<![\wё])(?:потребовал\w*|предложил\w*|приказал\w*|перебил\w*|сам[аио]?|первы[мй]|"
                        r"первой|без просьбы|без команды|не дожидаясь|напал\w*|схватил\w*|достал\w*|"
                        r"отказал\w*|решил\w*|вмешал\w*|предупредил\w*|позвал\w*|ушл?[аио]?|ушёл|"
                        r"увёл\w*|начал\w*|торгова\w*|поставил\w* услови\w*|солгал\w*|скрыл\w*|"
                        r"потянул\w*|дёрнул\w*|коснул\w*|проверил\w*)", re.I)


def _state(hot):
    return hot.setdefault("fold", {}).setdefault("drama", {})


def _at(value):
    try:
        return datetime.fromisoformat(str(value)[:19])
    except (TypeError, ValueError):
        return None


def _stems(text):
    return {stem(w) for w in tokens(str(text)) if len(w) >= 3}


def _keys(hot, present, always):
    return [k for k in present if k not in ("shiki", "player") and k not in always]


def _name_stems(hot, key):
    name = str(((hot.get("cast") or {}).get(key) or {}).get("name") or key)
    name = re.sub(r"\(.*?\)", " ", name)
    return {stem(w) for w in tokens(name) if len(w) >= 3}


def _who(label, hot, keys):
    words = _stems(label)
    return next((k for k in keys if _name_stems(hot, k) & words), None)


def talked(text, hot, keys):
    """Обратился ли один NPC к другому: подряд две реплики не Шики или фраза с двумя именами и речью."""
    if len(keys) < 2:
        return False
    labels = [m.group(1) for m in LABEL.finditer(text)]
    who = [None if "шики" in label.casefold() else _who(label, hot, keys) for label in labels]
    if any(a and b and a != b for a, b in zip(who, who[1:])):
        return True
    for raw in text.split("\n"):
        if LABEL.match(raw):
            continue
        for match in SENT.finditer(raw):
            words = _stems(match.group())
            named = [k for k in keys if _name_stems(hot, k) & words]
            if len(named) >= 2 and SPEAK.search(match.group()):
                return True
    return False


def initiative(text, hot, key):
    """Свой шаг NPC в тексте хода: в повествовании его имя рядом с глаголом почина."""
    names = _name_stems(hot, key)
    for raw in str(text).split("\n"):
        if LABEL.match(raw):
            continue
        for match in SENT.finditer(raw):
            sentence = match.group()
            if names & _stems(sentence) and INITIATIVE.search(sentence):
                return True
    return False


def track(hot, turn, always=()):
    """После каждого сыгранного хода: ритм, разговор NPC между собой, почин присутствующих."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return
    state = _state(hot)
    text = str(turn.get("gm") or "")
    rows = state.setdefault("rhythm", [])
    if not rows or rows[-1].get("id") != turn.get("id"):
        rows.append({"id": turn.get("id"), "chars": len(text), "at": turn.get("at")})
        del rows[:-(RHYTHM_WINDOW + 1)]
    present = list((hot.get("scene") or {}).get("present") or [])
    keys = _keys(hot, present, always)
    state["talk"] = 0 if len(keys) < 2 or talked(text, hot, keys) else int(state.get("talk") or 0) + 1
    state["talk_with"] = keys[:4]
    # Почин сбрасывает счёт пассивности (will.track считает ходы без своего шага).
    streak = ((hot.get("_will") or {}).get("streak") or {})
    for key in present:
        if key in streak and initiative(text, hot, key):
            streak[key] = 0


def rhythm(hot):
    rows = _state(hot).get("rhythm") or []
    if len(rows) < RHYTHM_WINDOW + 1:
        return None
    window = rows[-RHYTHM_WINDOW:]
    sizes = [r["chars"] for r in window]
    mean = sum(sizes) / len(sizes)
    if mean < 400:
        return None
    spread = (sum((s - mean) ** 2 for s in sizes) / len(sizes)) ** 0.5 / mean
    times = [_at(r.get("at")) for r in rows[-(RHYTHM_WINDOW + 1):]]
    if None in times:
        return None
    steps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
    if spread >= RHYTHM_SPREAD or max(steps) > RHYTHM_STEP:
        return None
    return (f"Ритм ровный: {RHYTHM_WINDOW} ходов по ≈{round(mean / 100) / 10:g} тыс. знаков и по "
            f"{int(min(steps))}–{int(max(steps))} мин. Смени шаг этим ходом по смыслу сцены: удар в несколько "
            "строк, сжатие до следующего значимого (часы, дни — без решений за Шики) или одна деталь крупно.")


def _events(hot):
    return ((hot.get("_plot") or {}).get("events") or {})


def chapter_gap(hot):
    events = _events(hot)
    if "main" not in events:
        return None
    if any(e.get("status") == "active" and e.get("kind") == "chapter" for e in events.values()):
        return None
    now = _at((hot.get("cursor") or {}).get("at"))
    if not now:
        return None
    starts = sorted(s for s in (_at(e.get("starts")) for e in events.values()
                                if e.get("kind") == "chapter" and e.get("status") == "queued") if s)
    days = (starts[0] - now).days if starts else None
    if days is not None and days <= CHAPTER_GAP_DAYS:
        return None
    when = f"до ближайшей главы мира {days} дн." if days is not None else "глав мира впереди нет"
    return (f"У «{events['main'].get('title') or 'main'}» нет открытой главы, {when} Открой свою главу из "
            "последствий поступков Шики — d.plot.<ключ>: kind chapter, parent main, question, 2–4 endings "
            "(с неудачей), limit. Кто идёт по его следу и чего хочет; в сцену — человеком, вестью, переменой.")


def passive(hot, always=()):
    """Самый долгий пассивный значимый NPC рядом с Шики (по will.streak) — пункт must."""
    from . import will
    streak = ((hot.get("_will") or {}).get("streak") or {})
    present = set((hot.get("scene") or {}).get("present") or [])
    best = None
    for key, count in streak.items():
        if key not in present or count < will.PASSIVE_TURNS * 2:
            continue
        card = (hot.get("cast") or {}).get(key) or {}
        profile = (hot.get("_psyche") or {}).get(key) or {}
        if not (card.get("tier") in ("developed", "deep") or profile.get("intent") or key in always):
            continue
        if best is None or count > best[1]:
            best = (key, count, profile)
    if not best:
        return None
    key, count, profile = best
    goal = next((a.get("goal") for a in (profile.get("intent") or {}).values()
                 if isinstance(a, dict) and a.get("goal") and a.get("status", "committed") == "committed"), None)
    goal = goal or ((hot.get("cast") or {}).get(key) or {}).get("wants") or "не записана"
    return (f"{will._name(hot, key)}: {count} ходов без своего шага — только отвечает. Цель: "
            f"«{str(goal)[:140]}». Этим или ближайшим ходом — её почин: требование, торг, проверка, ложь, "
            f"поступок против Шики или ради себя; новый план — d.cast.{key}.intent.")


def talk(hot):
    state = _state(hot)
    if int(state.get("talk") or 0) < TALK_TURNS:
        return None
    from . import will
    names = ", ".join(will._name(hot, k) for k in state.get("talk_with") or [])
    return (f"{names} рядом {state['talk']} ходов и говорят только с Шики. У каждого свой интерес к другому — "
            "спор, приказ, старый счёт, сделка, недоверие: пусть один обратится к другому, Шики может слушать.")


def must(hot, always=()):
    """Один пункт за свёртку: глава > почин > разговор > ритм; вид — не чаще COOLDOWN ходов."""
    from .aging import _ordinal
    state = _state(hot)
    now = _ordinal((hot.get("cursor") or {}).get("id")) or 0
    said = state.setdefault("said", {})
    for kind, item in (("chapter", chapter_gap(hot)), ("passive", passive(hot, always)),
                       ("talk", talk(hot)), ("rhythm", rhythm(hot))):
        if not item:
            continue
        if said.get(kind) is not None and now - said[kind] < COOLDOWN and said.get(f"{kind}@") != now:
            continue
        said[kind], said[f"{kind}@"] = now, now
        return item
    return None


def _card(state_dir, key):
    try:
        return json.loads((Path(state_dir) / "recovery/cast" / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _span(days):
    years, rest = divmod(days, 365)
    months = rest // 30
    parts = ([f"{years} г."] if years else []) + ([f"{months} мес."] if months else [])
    return " ".join(parts) or f"{days} дн."


def gaps(state_dir, hot, always=()):
    """{npc: строка} для NEXT.present.<id>.gap: давний знакомый вернулся после долгой разлуки."""
    now = _at((hot.get("cursor") or {}).get("at"))
    if not now:
        return {}
    out = {}
    for key in _keys(hot, (hot.get("scene") or {}).get("present") or [], always):
        from . import history_access, npc_memory
        episodes = npc_memory.load_episodes(state_dir, key) or _card(state_dir, key).get("memory") or []
        dates = sorted(d for d in (_at(m.get("at")) for m in episodes
                                   if isinstance(m, dict) and m.get("ch") in history_access.WITNESS)
                       if d and d <= now)
        if len(dates) < 2:
            continue
        start = len(dates) - 1           # нынешняя встреча: записи без разрыва больше MEET_DAYS
        while start and (dates[start] - dates[start - 1]).days <= MEET_DAYS:
            start -= 1
        if not start or (now - dates[start]).days > MEET_DAYS:
            continue
        days = (dates[start] - dates[start - 1]).days
        if days < GAP_DAYS:
            continue
        out[key] = (f"Не виделись с {dates[start - 1]:%d.%m}.{dates[start - 1].year + 1277} — {_span(days)}. "
                    "Он прожил это время без Шики: служба, потери, новые люди, другие убеждения. Что изменилось — "
                    "видно в поступке и речи; прежнее отношение — отправная точка, не готовая реплика.")
    return out
