"""Мир не подыгрывает: похвала, уступки без цены и отсутствие «нет» у NPC.

s11.text.030–038: Баки впускает в Суну нукенина класса S, жившего год у убийцы
Четвёртого Кадзекагэ; затем «Вы спасли его. Я этого не забуду», отказ от
48 миллионов и свободный выход. Сакура: «Его возможности впечатляют», «после
экзамена ты времени не терял». Какаши берёт неизвестного в отряд за одну
реплику. Ни одного отказа, торга или проверки за восемь ходов: мир подстроился
под желание игрока. Каждая уступка по отдельности объяснима — вместе это
подыгрывание.

Код считает в репликах NPC похвалу Шики, уступки (с ценой или без) и трение —
отказ, условие, торг, недоверие. Замечание к ходу — при явной похвале и уступке
без цены; пункт ``must`` — когда окно последних ходов перекошено. Исход код не
решает: напоминает, что у NPC своя воля.
"""
from __future__ import annotations

import re

from . import lore, presence
from .acquaint import lines

WINDOW = 8          # ходов в окне
MIN_TURNS = 5       # раньше окна не судить
PRAISE_MAX = 4      # похвал за окно — уже перекос
FREE_MAX = 1        # уступок без цены за окно — больше одной перекос
COOLDOWN = 4        # ходов между пунктами must

PRAISE = re.compile(
    r"впечатл|восхищ|поразительн|невероятн|блестящ|великолепн|гениальн|мастерск|превосходн|безупречн"
    r"|(?<![\wё])(?:вы|ты)\s+(?:\w+\s+)?спасл?и?(?![\wё])|спасли\s+(?:его|её|ее|их|нас)|не\s+забуду|в\s+долгу"
    r"|(?<![\wё])спасибо|благодар|никто\s+(?:\w+\s+){0,2}не\s+(?:смог|мог|сумел)"
    r"|чего\s+(?:они|мы|наши\s+\w+)\s+не\s+(?:смогли|могли|сумели)|лучше\s+(?:всех|любого|нас)"
    r"|времени\s+не\s+терял|слишком\s+хорошо|впервые\s+вижу|за\s+несколько\s+минут", re.I)
GRANT = re.compile(
    r"(?<![\wё])(?:вы|ты)\s+свобод|не\s+(?:станем|будем|стану|станет)\s+(?:\w+\s+)?задерж|принимаю\s+(?:ваше|тво[её])\s+"
    r"предложени|добро\s+пожаловать|не\s+возражаю|(?<![\wё])разрешаю|можете\s+идти|как\s+скажете"
    r"|(?:вам|тебе)\s+доверя|отказаться\s+от"
    r"|свободн\w+\s+(?:проход|выход)|запрет\s+на\s+задержание", re.I)
COST = re.compile(
    r"(?<![\wё])(?:но|однако|если|взамен|пока)(?![\wё])|при\s+условии|цен[аеуы](?![\wё])|услови|не\s+более"
    r"|под\s+(?:надзор|охран|присмотр|ответственность)|сопровожд|залог|поклян|отвечаете|не\s+снимаю|не\s+прощ", re.I)
FRICTION = re.compile(
    r"(?<![\wё])нет[.!]|не\s+позволю|не\s+пущу|отказ|откажу|запрещ|(?<![\wё])нельзя|не\s+дам|не\s+отдам"
    r"|не\s+скажу|ни\s+шагу|арест|задержать|под\s+стражу|(?<![\wё])сначала\s+(?:вы|ты)|взамен|услови"
    r"|не\s+верю|лж[её]т|врёт|врете|докажите|докажи|чем\s+(?:вы|ты)\s+заплат|что\s+(?:вы|ты)\s+(?:дадите|дашь)"
    r"|сдайте|сдай\s+оруж|руки\s+на\s+виду|не\s+двигаться", re.I)


def _state(hot):
    return hot.setdefault("fold", {}).setdefault("favor", {"recent": []})


def measure(turn, names, always=()):
    """Счёт хода: похвала, уступки, уступки без цены, трение; и примеры."""
    rows = []
    for label, text in lines(turn.get("gm")):
        if "шики" in label.casefold():
            continue
        key = presence.resolve(label, names)
        if key in always:
            continue
        rows.append((key, label, text))
    out = {"id": turn.get("id"), "praise": 0, "grant": 0, "free": 0, "friction": 0, "quotes": []}
    for i, (key, label, text) in enumerate(rows):
        if PRAISE.search(text):
            out["praise"] += 1
            if len(out["quotes"]) < 2:
                out["quotes"].append(f"{label}: «{text[:90]}»")
        if FRICTION.search(text):
            out["friction"] += 1
        if GRANT.search(text):
            out["grant"] += 1
            # Цена — в той же реплике или в двух следующих репликах того же говорящего.
            same = [t for k, lb, t in rows[i + 1:i + 6] if (k and k == key) or lb == label][:2]
            if not any(COST.search(t) for t in [text] + same):
                out["free"] += 1
                if len(out["quotes"]) < 3:
                    out["quotes"].append(f"{label}: «{text[:90]}» — без цены")
    return out


def track(hot, turn, names, always=()):
    """Записывает счёт хода в окно; замечание kind=favor — при явном подыгрывании в этом ходе."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    row = measure(turn, names, always)
    state = _state(hot)
    recent = [r for r in state.get("recent") or [] if r.get("id") != row["id"]]
    state["recent"] = (recent + [{k: v for k, v in row.items() if k != "quotes"}])[-WINDOW:]
    if row["free"] or row["praise"] >= 3:
        what = []
        if row["praise"] >= 3:
            what.append(f"похвала Шики в {row['praise']} репликах")
        if row["free"]:
            what.append(f"уступок без цены — {row['free']}")
        return [{"turn": row["id"], "kind": "favor", "text":
                 f"Подыгрывание: {', '.join(what)} ({'; '.join(row['quotes'][:2])}). NPC уступает по своей "
                 "причине и за свою цену — условие, надзор, услуга, риск для него самого; восхищение — "
                 "редкость, не реакция по умолчанию."}]
    return []


def seed(hot, rows, names, always=()):
    """Пустое окно — из последних сыгранных ходов архива, один раз."""
    state = _state(hot)
    if state.get("recent") or state.get("seeded"):
        return
    played = [r for r in rows if r.get("kind") not in ("ooc", "technical", "void")][-WINDOW:]
    state["recent"] = [{k: v for k, v in measure(r, names, always).items() if k != "quotes"} for r in played]
    state["seeded"] = True


def totals(hot):
    recent = _state(hot).get("recent") or []
    return {"turns": len(recent), **{k: sum(int(r.get(k) or 0) for r in recent)
                                    for k in ("praise", "grant", "free", "friction")}}


def must(hot):
    """Пункт must, если окно перекошено: похвала, уступки без цены, ни одного «нет»."""
    state = _state(hot)
    recent = state.get("recent") or []
    if len(recent) < MIN_TURNS:
        return None
    total = totals(hot)
    skew = (total["praise"] >= PRAISE_MAX or total["free"] > FREE_MAX
            or (total["friction"] == 0 and total["grant"] + total["praise"] >= 2))
    if not skew:
        return None
    from .aging import _ordinal
    now = _ordinal(recent[-1].get("id")) or 0
    if now - int(state.get("must_at") or -COOLDOWN) < COOLDOWN and state.get("must_turn") != recent[-1].get("id"):
        return None
    state["must_at"], state["must_turn"] = now, recent[-1].get("id")
    why = []
    if total["praise"] >= PRAISE_MAX:
        why.append(f"похвал и благодарностей Шики — {total['praise']}")
    if total["free"] > FREE_MAX:
        why.append(f"уступок без цены — {total['free']}")
    if total["friction"] == 0:
        why.append("ни одного отказа, условия или недоверия")
    return (f"Мир подыгрывает Шики: за {total['turns']} ходов {', '.join(why)}. "
            "В этом ходе у присутствующего — своё против желания Шики по его карточке (служба, страх, выгода): "
            "условие, торг, проверка, отказ или ложь. Заявка игрока — намерение, исход решает мир; спорное — костью. "
            "Не наказание и не враждебность ради неё — его воля.")
