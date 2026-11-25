"""Непротиворечивость сыгранному: учёт вещей и состояний, охват заявки игрока.

Перенос контракта Bleach ``gm_turn_guard``: переходы ``было → стало``
проверяются последовательно, так что нельзя дважды потратить последнюю
единицу, второй раз снять вещь с прежнего места или вылечить уже
зашитую рану заново. Новый предмет вводится явно и с причиной.

Ведущий пишет в ``d.things`` только то, что изменилось:

    "things": {
      "scalpel":  {"was": {"holder": "shiki"}, "now": {"holder": "kadziura_satoru"}},
      "ampoules": {"was": {"count": 3}, "now": {"count": 2}},
      "letter_v7": {"new": {"name": "письмо с печатью В-7", "holder": "shiki"},
                    "why": "передал курьер у ворот"},
      "old_note": {"gone": "сожжено в печи"}
    }

Код ничего не блокирует: сцена уже показана игроку, и отказ записать её
только разорвал бы память. Расхождения попадают в ``recovery/CHECKS.json``
и в пакет сцены, где ведущий видит их перед следующим ответом.
"""
from __future__ import annotations

import re

FIELDS = ("name", "holder", "where", "state", "count")
THINGS_KEEP = 60
GONE_KEEP = 12
WORD = re.compile(r"[а-яёa-z0-9]+", re.I)
OOC = re.compile(r"(?im)^\s*(?:мастеру|ooc|вне игры)\s*:.*$")
ACTION = re.compile(r"#[^#]*#|\([^)]*\)|\*[^*]*\*")
SPEECH_MIN_WORDS = 4
SPEECH_MIN_COVERAGE = 0.5


def _norm(value):
    return " ".join(str(value).casefold().split()) if value is not None else None


def _check(hot, turn_id, kind, text):
    checks = hot.setdefault("_checks", [])
    entry = {"turn": turn_id, "kind": kind, "text": text}
    if entry not in checks:
        checks.append(entry)


def apply_things(hot, delta, turn_id, at):
    things = hot.setdefault("things", {})
    for key, patch in ((delta or {}).get("things") or {}).items():
        if not isinstance(patch, dict):
            _check(hot, turn_id, "things", f"{key}: запись должна быть объектом")
            continue
        # Bare holder/state/count formerly disappeared without a warning.
        unknown = set(patch) - {"was", "now", "new", "why", "gone"}
        if unknown:
            _check(hot, turn_id, "things", f"{key}: неизвестные поля {', '.join(sorted(unknown))}; используй was/now или new/gone")
            continue
        modes = sum(k in patch for k in ("now", "new", "gone"))
        if modes != 1 or ("was" in patch and "now" not in patch):
            _check(hot, turn_id, "things", f"{key}: нужен один переход now/new/gone")
            continue
        malformed = [k for k in ("was", "now", "new") if k in patch and
                     (not isinstance(patch[k], dict) or set(patch[k]) - set(FIELDS))]
        if malformed:
            _check(hot, turn_id, "things", f"{key}: неверная структура {', '.join(malformed)}")
            continue
        if "gone" in patch:
            item = things.pop(key, None)
            if item is None:
                _check(hot, turn_id, "things", f"{key}: убран предмет, которого не было в учёте")
                item = {}
            gone = hot.setdefault("things_gone", [])
            gone.append({"key": key, "name": item.get("name", key), "turn": turn_id,
                         "at": at, "how": str(patch["gone"])[:200]})
            del gone[:-GONE_KEEP]
            continue
        if "new" in patch:
            if key in things:
                _check(hot, turn_id, "things",
                       f"{key}: введён как новый, но уже есть в учёте ({_brief(things[key])})")
            if not patch.get("why"):
                _check(hot, turn_id, "things", f"{key}: новый предмет без why — откуда он взялся")
            item = {k: v for k, v in (patch.get("new") or {}).items() if k in FIELDS}
            item.update({"since": turn_id, "why": str(patch.get("why", ""))[:200]})
            things[key] = _valid(hot, turn_id, key, item)
            continue
        item = things.get(key)
        if item is None:
            _check(hot, turn_id, "things",
                   f"{key}: изменён предмет вне учёта; введён без проверки прежнего состояния")
            item = {"since": turn_id}
            things[key] = item
        for field, expected in (patch.get("was") or {}).items():
            if field in item and _norm(item[field]) != _norm(expected):
                _check(hot, turn_id, "contradiction",
                       f"{key}.{field}: в сыгранном «{item[field]}», ход исходит из «{expected}» "
                       f"(с {item.get('turn', item.get('since'))})")
        for field, value in (patch.get("now") or {}).items():
            if field in FIELDS:
                item[field] = value
        item["turn"] = turn_id
        _valid(hot, turn_id, key, item)
    if len(things) > THINGS_KEEP:
        for key in list(things)[: len(things) - THINGS_KEEP]:
            gone = hot.setdefault("things_gone", [])
            gone.append({"key": key, "name": things[key].get("name", key), "turn": turn_id,
                         "at": at, "how": "вытеснено из оперативного учёта"})
            del gone[:-GONE_KEEP]
            things.pop(key)


def _valid(hot, turn_id, key, item):
    if "count" in item:
        try:
            count = int(item["count"])
        except (TypeError, ValueError):
            _check(hot, turn_id, "things", f"{key}: count не число")
            return item
        if count < 0:
            _check(hot, turn_id, "contradiction", f"{key}: потрачено больше, чем было (count {count})")
        item["count"] = count
    return item


def _brief(item):
    return ", ".join(f"{k}={item[k]}" for k in FIELDS if k in item)


def _stem(word):
    return word[:5] if len(word) > 5 else word


def player_speech(text):
    """Прямая речь игрока без действий в #…#, скобок и обращений «Мастеру:»."""
    text = OOC.sub(" ", text or "")
    return ACTION.sub(" ", text)


def coverage(player, gm):
    words = [w.casefold() for w in WORD.findall(player_speech(player)) if len(w) >= 4]
    if len(words) < SPEECH_MIN_WORDS:
        return None
    pool = {_stem(w.casefold()) for w in WORD.findall(gm or "")}
    hit = sum(1 for w in words if _stem(w) in pool)
    return hit / len(words)


def check_turn(hot, turn):
    """Проверки после свёртки хода; только предупреждения."""
    if turn.get("kind") == "ooc":
        return
    share = coverage(turn.get("player", ""), turn.get("gm", ""))
    if share is not None and share < SPEECH_MIN_COVERAGE:
        _check(hot, turn["id"], "player",
               f"реплика игрока вошла в сцену на {round(share * 100)}%: слова Шики "
               "переносятся в прозу полностью и в заданном порядке")


def scene_things(hot, keys):
    """Вещи у присутствующих, у Шики и на месте действия."""
    holders = set(keys) | {"shiki"}
    place = _norm((hot.get("cursor") or {}).get("place", "")) or ""
    out = {}
    for key, item in (hot.get("things") or {}).items():
        where = _norm(item.get("where")) or ""
        if item.get("holder") in holders or (where and where in place):
            out[key] = item
    return out
