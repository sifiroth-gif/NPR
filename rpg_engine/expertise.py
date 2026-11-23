"""Речь NPC по его знанию: медик говорит как медик своего мира, не как Шики.

s11.text.033–034: Сакура, ученица медика, через ход после заявки игрока
рассуждает «угнетение комплекса I», «венозная адвентиция», «протеолитический
препарат и сорбирующий контур»; врач Песка тут же подтверждает. Это словарь
игрока и Шики — молекулярная химия, которой в мире нет ни в школах, ни в
госпиталях. NPC подхватил чужой язык и стал знатоком за один ход.

Уровни (``memory/_curated/expertise.json`` хранителя, иначе по карточке):

* ``none`` — не медик: о болезни — симптомами, слухом, страхом;
* ``clinical`` — медик мира: раны, яды, противоядия, чакровая диагностика,
  анатомия; без молекулярной химии;
* ``science`` — уровень Шики: каналы, митохондрии, ферменты, геном.

Код замечает реплику NPC со словами уровня выше его собственного. Услышанное от
Шики можно повторить вопросом («комплекс I — это что?»), но не рассуждать им.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import presence
from .acquaint import lines

SEED = "memory/_curated/expertise.json"
LEVELS = {"none": 0, "clinical": 1, "science": 2}
# Наука Шики: молекулярная химия, биофизика, генетика — языка такого уровня в мире нет.
SCIENCE = re.compile(
    r"митохондри|комплекс\w*\s+(?:i|1|ii|iii|iv)\b|na[ᵥv]\s*1|натриев\w*\s+канал|калиев\w*\s+канал|кальциев\w*\s+канал"
    r"|(?<![\wё])атф(?![\wё])|протонн|перенос\w*\s+электрон|электрон\w*\s+(?:транспорт|перенос)|молекул|пептид"
    r"|полисахарид|липид|протеолит|сорбц|сорбир|рецептор|нейромедиатор|ацетилхолин|холинэстераз|липофил|гидрофил"
    r"|токсикодинам|токсикокинет|фармакокинет|фармакодинам|бупивака|ротено|(?<![\wё])амид|ферментн\w*\s+(?:промыв|раств)"
    r"|секвенир|хромосом|геном|(?<![\wё])днк|(?<![\wё])рнк|нуклеотид|антител|изотонич|деполяриз"
    r"|аксон|синапс|миелин", re.I)
# Клиника: язык обученного медика мира. Не медику — рано.
CLINICAL = re.compile(
    r"миокард|фасци|адвентици|диафрагм|межр[её]берн|вентиляци|нервно-мышечн|проводимост|интоксикац|аспирац|некроз"
    r"|перфузи|гемодинам|сатурац|сепсис|ишеми|гипокси|дренаж|пункци|интубац|малоберцов", re.I)
MEDIC = re.compile(r"медик|врач|ирь?[её]нин|лекар|целител|фельдшер|санитар|хирург|медсестр|ученица\s+цунаде"
                   r"|ученик\w*\s+цунаде", re.I)
LABEL_TEXT = {
    0: "не медик: о болезни и яде — симптомами, слухом, страхом",
    1: "медик своего мира: раны, яды, противоядия, чакровая диагностика — без молекулярной химии Шики",
    2: "знаток уровня Шики",
}


def load(state_dir):
    try:
        data = json.loads((Path(state_dir) / SEED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in (data.get("npc") or {}).items() if isinstance(v, dict)}


def _card_text(hot, key):
    card = (hot.get("cast") or {}).get(key) or {}
    psyche = (hot.get("_psyche") or {}).get(key) or {}
    # Только кто он, не что с ним: «лежит в госпитале, врачи…» в state медиком не делает.
    parts = [card.get("name"), card.get("role"), json.dumps(card.get("facts") or "", ensure_ascii=False),
             psyche.get("role"), psyche.get("name")]
    return " ".join(str(p) for p in parts if p)


def tier(hot, seed, key):
    """Уровень медицинской речи NPC: хранитель → карточка (медик) → не медик."""
    row = seed.get(key) or {}
    if row.get("medicine") in LEVELS:
        return LEVELS[row["medicine"]]
    return 1 if MEDIC.search(_card_text(hot, key)) else 0


SHORT = {0: "не медик", 1: "медик мира, без химии Шики", 2: "знаток уровня Шики"}


def tier_line(hot, seed, key):
    """Для NEXT.present: только медикам — что им по силам; не медик без пометки."""
    level = tier(hot, seed, key)
    note = (seed.get(key) or {}).get("note")
    if level == 0 and not note:
        return None
    return f"{SHORT[level]} ({note})" if note else SHORT[level]


def _hits(pattern, text):
    return list(dict.fromkeys(m.group(0).strip().casefold() for m in pattern.finditer(text)))


def review(hot, turn, names, seed, always=()):
    """Замечания kind=expertise: реплика NPC выше его знания."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    notes, done = [], set()
    for label, text in lines(turn.get("gm")):
        if "шики" in label.casefold():
            continue
        key = presence.resolve(label, names)
        if key in always or (key or label) in done:
            continue
        # Подпись говорит за себя: «Врач Песка» — медик, даже если код спутал его с чужой карточкой.
        level = max(tier(hot, seed, key) if key else 0, 1 if MEDIC.search(label) else 0)
        if level >= 2:
            continue
        if "?" in text and len(text) < 90:
            continue        # переспросить услышанное — не знать
        words = _hits(SCIENCE, text)
        if level == 0:
            words += _hits(CLINICAL, text)
        if not words:
            continue
        done.add(key or label)
        notes.append({"turn": turn["id"], "kind": "expertise", "text":
                      f"Знание не по уровню: {label} говорит «{', '.join(words[:4])}» — {LABEL_TEXT[level]}. "
                      "Услышанное от Шики NPC может переспросить или пересказать неточно, но не рассуждать "
                      "им как своим: пусть назовёт по-своему (симптом, яд, чакра), ошибётся или спросит."})
    return notes
