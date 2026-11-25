"""Хоррор как драматургия: что близость Шики делает с конкретными людьми.

Ужас в NPR не создаётся описанием внешности Шики (капюшон, сосуды, кольца,
запах) и не создаётся одинаковыми реакциями тела (сглотнул, пульс, замер).
Он создаётся тем, как меняется чужая модель Шики и что человек после этого
делает. Каждый NPC идёт по своей лестнице, только через увиденное:

    0 тревога → 1 примета → 2 пересмотр → 3 поступок → 4 необратимость

Ведущий пишет в ``d.cast.<ключ>.dread`` (приватно, в ``GM_ONLY.psyche``):

    {"saw": "что NPC увидел или узнал о Шики (факт сыгранного)",
     "reads": "что он теперь думает о Шики",
     "does": "что он делает иначе (обязательно с 3-й ступени)",
     "tells": ["ключ NPC, кому он расскажет"],
     "stage": 2}            # только при первой записи: калибровка по сыгранному

Ступень растёт не больше чем на одну за ход. ``hold: true`` — то же
ощущение подтвердилось, без роста; ``down: "почему"`` — страх ослаб.

Код сам считает ходы без хоррора (``quiet``): после ``QUIET_MAX`` ходов с
NPC в сцене и без ступени или ``d.horror`` в пакет сцены встаёт обязательство
``due``. Оно не исчезает, пока ведущий его не выполнит. Код же отмечает
мотивы внешности Шики и типовые реакции тела, повторённые в последних ходах
(``worn``): их в следующей сцене не использовать.
"""
from __future__ import annotations

from . import prose_lint

LADDER = (
    ("впечатление", "Человек видит Шики и объясняет его по своему опыту: болезнь, рана, слух, запретное искусство — или не придаёт значения. Ауры нет; телом — не больше одного штриха за сцену."),
    ("примета", "NPC замечает конкретный факт о Шики: поступок, точность, знание, последствие прежнего действия. Он запоминает его и проверяет."),
    ("пересмотр", "Знакомое о Шики становится ненадёжным: NPC перечитывает прежний эпизод, документ или слух, и его картина Шики ломается."),
    ("поступок", "Страх или расчёт меняет тактику: сделка, ложь, проверка, защита своих, донос, попытка использовать или устранить Шики."),
    ("необратимость", "NPC принимает решение, которое нельзя отменить: предательство, бегство, союз, покушение, служение. Мир отвечает на это сам."),
)
TOP = len(LADDER) - 1
QUIET_MAX = 3
MOTIF_TURNS = 6
LOG_KEEP = 6


def _short(text, limit=240):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _check(hot, turn_id, kind, text):
    checks = hot.setdefault("_checks", [])
    entry = {"turn": turn_id, "kind": kind, "text": text}
    if entry not in checks:
        checks.append(entry)


def _profile(hot, key):
    return hot.setdefault("_psyche", {}).setdefault(key, {})


def valid(patch):
    return isinstance(patch, dict) and bool(patch.get("saw")) and bool(
        patch.get("reads") or patch.get("hold") or patch.get("down"))


def apply(hot, key, patch, turn_id, at):
    """Одна запись ``dread`` для NPC ``key``."""
    if not isinstance(patch, dict):
        _check(hot, turn_id, "horror", f"{key}: dread должен быть объектом")
        return False
    if not patch.get("saw"):
        _check(hot, turn_id, "horror",
               f"{key}: dread без saw. Страх растёт только из увиденного или полученного по каналу")
        return False
    profile = _profile(hot, key)
    state = profile.get("dread")
    if state is None:
        stage = patch.get("stage", 0 if patch.get("hold") else 1)
        try:
            stage = max(0, min(TOP, int(stage)))
        except (TypeError, ValueError):
            stage = 1
        state = {"stage": stage, "since": turn_id, "log": []}
        profile["dread"] = state
        change = "калибровка"
    else:
        old = int(state.get("stage", 0))
        if patch.get("down"):
            stage, change = max(0, old - 1), "ослаб"
        elif (patch.get("hold") or patch.get("stage") == old
              or (_short(patch["saw"], 200) == (state.get("log") or [{}])[-1].get("saw"))):
            stage, change = old, "подтвердилось"
        else:
            if "stage" in patch and patch["stage"] != old + 1 and patch["stage"] != old:
                _check(hot, turn_id, "horror",
                       f"{key}: ступень {old}→{patch['stage']} прыжком; лестница идёт по одной ступени за ход")
            stage, change = min(TOP, old + 1), "рост"
        state["stage"] = stage
    if state["stage"] >= 3 and not patch.get("does") and change in ("рост", "калибровка"):
        _check(hot, turn_id, "horror",
               f"{key}: ступень «{LADDER[state['stage']][0]}» без does. С третьей ступени страх виден в поступке")
    for field in ("reads", "does"):
        if patch.get(field):
            state[field] = _short(patch[field])
    state["turn"] = turn_id
    state["step"] = LADDER[state["stage"]][0]
    log = state.setdefault("log", [])
    log.append({"turn": turn_id, "at": at, "change": change, "saw": _short(patch["saw"], 200),
                **({"does": _short(patch["does"], 200)} if patch.get("does") else {}),
                **({"why": _short(patch["down"], 160)} if patch.get("down") else {})})
    del log[:-LOG_KEEP]
    for other in patch.get("tells") or []:
        if not isinstance(other, str) or other in (key, "shiki"):
            continue
        heard = _profile(hot, other).get("dread")
        reads = _short(f"слух от {key}: {patch.get('reads') or patch['saw']}", 240)
        if heard is None:
            _profile(hot, other)["dread"] = {"stage": 1, "step": LADDER[1][0], "since": turn_id,
                                             "turn": turn_id, "reads": reads, "rumor": True,
                                             "log": [{"turn": turn_id, "at": at, "change": "слух",
                                                      "saw": f"рассказ {key}"}]}
        else:
            heard.setdefault("log", []).append({"turn": turn_id, "at": at, "change": "слух",
                                                "saw": f"рассказ {key}"})
            del heard["log"][:-LOG_KEEP]
    return True


def after_turn(hot, turn, inner, delta):
    """Счётчик ходов без хоррора, обязательство и изношенные мотивы."""
    if turn.get("kind") == "ooc":
        return
    turn_id = turn["id"]
    state = hot.setdefault("_horror", {})
    cast = hot.get("cast") or {}
    skip = set(turn.get("_always") or ())
    present = [k for k in ((hot.get("scene") or {}).get("present") or [])
               if k != "shiki" and k in cast and k not in skip]
    fed = any(valid((inner.get(k) or {}).get("dread")) for k in present)
    # Реакция каждого присутствующего на Шики: первая — в ту же встречу, дальше —
    # при новом увиденном. Общая заметка d.horror её не заменяет: с s08.058 ведущий
    # закрывал счётчик фразой «реакций обычных людей нет», пока рядом были люди.
    react = state.setdefault("react", {})
    for key in list(react):
        if key not in present:
            react.pop(key)
    for key in present:
        row = react.setdefault(key, {"since": turn_id, "need": True, "quiet": 0})
        if valid((inner.get(key) or {}).get("dread")):
            row.update(need=False, quiet=0, last=turn_id)
        else:
            row["quiet"] = int(row.get("quiet", 0)) + 1
    if delta.get("horror"):
        fed = fed or not present
        log = state.setdefault("log", [])
        log[:] = [x for x in log if x.get("turn") != turn_id]
        log.append({"turn": turn_id, "what": _short(delta["horror"], 300)})
        del log[:-LOG_KEEP]
    if present:
        if fed:
            state["quiet"] = 0
            state.pop("due", None)
        else:
            state["quiet"] = int(state.get("quiet", 0)) + 1
            if state["quiet"] >= QUIET_MAX:
                since = (state.get("due") or {}).get("since", turn_id)
                state["due"] = {"since": since, "npcs": present, "quiet": state["quiet"]}
                _check(hot, turn_id, "horror",
                       f"{state['quiet']} ход(а) подряд рядом с Шики ни у кого не записана реакция на него "
                       f"({', '.join(present)}). Что каждый увидел и решил — d.cast.<id>.dread; d.horror "
                       "её не заменяет. Нового не было — dread.hold с тем, что подтвердилось.")
    motifs = prose_lint.motifs(turn.get("gm") or "")
    recent = [m for m in state.get("motifs", []) if m.get("turn") != turn_id]
    recent.append({"turn": turn_id, **motifs})
    state["motifs"] = recent[-MOTIF_TURNS:]
    counts = {}
    for item in state["motifs"]:
        names = {part for look in item.get("look", []) for part in look.split("+")}
        for name in names | set(item.get("soma", [])):
            counts[name] = counts.get(name, 0) + 1
    state["worn"] = sorted(name for name, n in counts.items() if n >= 2)
    if len(motifs["look"]) > 1:
        _check(hot, turn_id, "look",
               f"внешность Шики описана {len(motifs['look'])} раза ({', '.join(motifs['look'])}). "
               "Ужас — в чужом поступке и последствии, а не в облике")
    if len(motifs["soma"]) > 2:
        _check(hot, turn_id, "soma",
               f"типовые реакции тела: {', '.join(motifs['soma'])}. Замени одним поступком")


def seed_motifs(hot, rows):
    """Разово: мотивы по последним сыгранным ходам, чтобы ``worn`` работал сразу."""
    state = hot.setdefault("_horror", {})
    if "motifs" in state:
        return False
    recent = [r for r in rows if r.get("gm") and r.get("kind") != "ooc"][-MOTIF_TURNS:]
    state["motifs"] = [{"turn": r["id"], **prose_lint.motifs(r["gm"])} for r in recent]
    counts = {}
    for item in state["motifs"]:
        names = {part for look in item["look"] for part in look.split("+")}
        for name in names | set(item["soma"]):
            counts[name] = counts.get(name, 0) + 1
    state["worn"] = sorted(name for name, n in counts.items() if n >= 2)
    return True


def pending(hot, present, always_present=()):
    """Присутствующие без реакции на Шики: [(ключ, строка состояния)] — новые первыми."""
    react = (hot.get("_horror") or {}).get("react") or {}
    rows = []
    for key in present:
        row = react.get(key)
        if key == "shiki" or key in always_present or not row:
            continue
        if row.get("need") and row.get("quiet", 0) >= 1:
            rows.append((0, key, row))
        elif row.get("quiet", 0) >= QUIET_MAX:
            rows.append((1, key, row))
    rows.sort(key=lambda r: (r[0], -r[2].get("quiet", 0)))
    return [(key, row) for _, key, row in rows]


def summary(hot, key):
    """Одна строка: ступень, как он видит Шики, что делает; рычаг."""
    profile = (hot.get("_psyche") or {}).get(key) or {}
    card = (hot.get("cast") or {}).get(key) or {}
    dread = profile.get("dread") or {}
    lever = (profile.get("psyche") or {}).get("deep_fear") or card.get("fears") or ""
    bits = []
    if dread:
        stage = int(dread.get("stage", 0))
        bits.append(f"{LADDER[stage][0]}: {dread.get('reads') or ''}".strip(": "))
        if dread.get("does"):
            bits.append(f"делает: {dread['does']}")
    else:
        bits.append("реакции на Шики ещё нет")
    if lever:
        bits.append(f"рычаг: {_short(lever, 120)}")
    return _short("; ".join(bits), 330)


def brief(hot, present):
    """Блок ``horror`` пакета сцены: что делать с хоррором в этой сцене."""
    cast = hot.get("cast") or {}
    state = hot.get("_horror") or {}
    npcs = {}
    for key in present:
        if key == "shiki" or key not in cast:
            continue
        profile = (hot.get("_psyche") or {}).get(key) or {}
        dread = profile.get("dread")
        lever = ((profile.get("psyche") or {}).get("deep_fear") or cast[key].get("fears") or "")
        if dread is None:
            npcs[key] = {"stage": None, "lever": _short(lever, 200),
                         "next": "Откалибруй: первая запись dread со stage по сыгранному "
                                 "(0–4) и saw из конкретного прежнего хода."}
            continue
        stage = int(dread.get("stage", 0))
        npcs[key] = {"stage": stage, "step": LADDER[stage][0],
                     "reads": dread.get("reads", ""), "does": dread.get("does", ""),
                     "lever": _short(lever, 200),
                     "next": ("Новый факт — возможный сдвиг; иначе hold/down. " + LADDER[min(TOP, stage + 1)][1])
                     if stage < TOP else "Решение принято; последствия, hold/down, не повторяй страх."}
    return {
        "rule": "Ужас создают чужие решения и необратимые последствия, а не облик Шики. "
                "Внешность — одна деталь при первой встрече или при перемене; телом — не больше одного штриха. "
                "Реакция из знания и мотива; страх не обязателен. Смотри lever.",
        "npcs": npcs,
        "quiet": state.get("quiet", 0),
        "due": state.get("due"),
        "worn": state.get("worn", []),
        "recent": (state.get("log") or [])[-3:],
    }
