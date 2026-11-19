"""Где Шики был: проверка утверждений сцены «в доме X» по сыгранным местам.

s08.text.090: «Он вспомнил Шики в доме Аракавы» — Шики там не был, Аракаву лечили
в кабинете Центрального управления. Игрок заметил, ведущий переписал сцену.
Код сверяет такие утверждения (предложение с Шики + «в доме / кабинете / клинике
<Имя>») с местами всех сыгранных ходов и даёт замечание до следующей сцены.
"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

ROOMS = {"дом": "дом", "квартир": "квартир", "кабинет": "кабинет", "клиник": "клиник",
         "лаборатори": "лаборатор", "комнат": "комнат", "палат": "палат"}
CLAIM = re.compile(r"(?:\bв|\bу|\bиз|\bвозле|\bоколо)\s+(дом[аеу]?|квартир[аеыу]|кабинет[аеу]?|клиник[аеиу]|"
                   r"лаборатори[июя]|комнат[аеыу]|палат[аеыу])\s+(?:[а-яё]+\s+)?([А-ЯЁ][а-яё]{2,})")


HERE = re.compile(r"\b(находится|стоит|сидит|входит|вош[её]л|заш[её]л|оказал\w*|прош[её]л в)\b", re.I)


def places(state_dir):
    out = set()
    for path in glob.glob(str(Path(state_dir) / "archive/session-*-turns.jsonl")):
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    place = json.loads(line).get("place")
                    if place:
                        out.add(str(place).casefold())
        except (OSError, ValueError):
            continue
    return out


def _sentences(text):
    return re.split(r"(?<=[.!?…])\s+|\n+", str(text or ""))


def review(turn, visited):
    """Замечания kind=continuity: утверждение о месте, где Шики не был ни в одном ходе."""
    notes = []
    for sentence in _sentences(turn.get("gm")):
        if "Шики" not in sentence or HERE.search(sentence):
            continue   # «Шики находится в кабинете X» — место этой сцены, а не воспоминание
        for room, name in CLAIM.findall(sentence):
            room_stem = next((v for k, v in ROOMS.items() if room.casefold().startswith(k)), room[:4])
            name_stem = name.casefold()[:max(4, len(name) - 2)]
            if name_stem in ("шики", "муга"):
                continue
            if any(room_stem in p and name_stem in p for p in visited):
                continue
            seen = sorted(p for p in visited if name_stem in p)[:3]
            where = f" Места с «{name}»: {'; '.join(seen)}." if seen else ""
            notes.append({"turn": turn["id"], "kind": "continuity", "text":
                          f"«{sentence.strip()[:160]}» — Шики ни в одном ходе не был «{room} {name}».{where} "
                          "Если это ошибка — исправь в следующем ходе или amend; если новое место — оно должно "
                          "быть сыграно."})
            break
    return notes
