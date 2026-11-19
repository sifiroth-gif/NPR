"""Слияние двух ключей одного человека (заготовка хранителя).

Один NPC под двумя ключами раздваивает память: реакция пишется в одну карточку,
планы — в другую (s08.text.085–090: ``morioka_hideaki`` и ``morioka_khideaki``).
Хранитель пишет ``memory/_curated/merge.json``::

    {"aliases": {"лишний_ключ": "основной_ключ"}}

Свёртка сливает всё, что есть у лишнего ключа, в основной (недостающее
дополняется, ничего не теряется), удаляет его карточку, а новые записи ведущего
под лишним ключом перенаправляет на основной.
"""
from __future__ import annotations

import json
from pathlib import Path

SEED = "memory/_curated/merge.json"


def _seed(state_dir):
    try:
        return json.loads((Path(state_dir) / SEED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def aliases(state_dir):
    data = _seed(state_dir)
    return {k: v for k, v in (data.get("aliases") or {}).items() if isinstance(k, str) and isinstance(v, str) and k != v}


def names(state_dir, hot):
    """Имя ключа от хранителя («names»): карточка без имени или заготовка по подписи.

    Правит и отложенную карточку в recovery/cast, если человека сейчас нет в сцене.
    """
    for key, name in (_seed(state_dir).get("names") or {}).items():
        if not isinstance(name, str):
            continue
        card = (hot.get("cast") or {}).get(key)
        if isinstance(card, dict):
            card["name"] = name
        else:
            path = Path(state_dir) / "recovery/cast" / f"{key}.json"
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stored = None
            if isinstance(stored, dict) and stored.get("name") != name:
                stored["name"] = name
                path.write_text(json.dumps(stored, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        profile = (hot.get("_psyche") or {}).get(key)
        if isinstance(profile, dict):
            profile["name"] = name


def _fill(target, source):
    """Недостающее в основной записи — из лишней; списки склеиваются без повторов."""
    for key, value in source.items():
        if key not in target or target[key] in (None, "", [], {}):
            target[key] = value
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _fill(target[key], value)
        elif isinstance(target[key], list) and isinstance(value, list):
            target[key] = target[key] + [v for v in value if v not in target[key]]


def redirect(turn, alias):
    """Запись ведущего под лишним ключом — на основной."""
    cast = (turn.get("d") or {}).get("cast")
    if isinstance(cast, dict):
        for old, new in alias.items():
            if old in cast:
                patch = cast.pop(old)
                if isinstance(cast.get(new), dict) and isinstance(patch, dict):
                    _fill(cast[new], patch)
                else:
                    cast.setdefault(new, patch)
    scene = (turn.get("d") or {}).get("scene")
    if isinstance(scene, dict) and isinstance(scene.get("present"), list):
        scene["present"] = list(dict.fromkeys(alias.get(k, k) for k in scene["present"]))


def apply(state_dir, hot, alias):
    """Сливает состояние; возвращает слитые пары."""
    done = []
    state_dir = Path(state_dir)
    for old, new in alias.items():
        touched = False
        card_path = state_dir / "recovery/cast" / f"{old}.json"
        cast = hot.setdefault("cast", {})
        source = cast.pop(old, None)
        if source is None and card_path.is_file():
            try:
                source = json.loads(card_path.read_text(encoding="utf-8"))
            except ValueError:
                source = None
        if isinstance(source, dict):
            source = {k: v for k, v in source.items() if k not in ("key", "auto", "role")}
            target = cast.get(new)
            if target is None:
                path = state_dir / "recovery/cast" / f"{new}.json"
                target = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
                cast[new] = target
            _fill(target, source)
            touched = True
        if card_path.is_file():
            card_path.unlink()
            touched = True
        psyche = hot.get("_psyche") or {}
        if old in psyche:
            _fill(psyche.setdefault(new, {}), psyche.pop(old))
            touched = True
        react = (hot.get("_horror") or {}).get("react") or {}
        if old in react:
            react.setdefault(new, react.pop(old))
            touched = True
        scene = hot.get("scene") or {}
        if old in (scene.get("present") or []):
            scene["present"] = list(dict.fromkeys(new if k == old else k for k in scene["present"]))
            touched = True
        auto = (hot.get("fold") or {}).get("auto_present") or {}
        if old in auto:
            auto[new] = max(auto.pop(old), auto.get(new, 0))
        for book in (hot.get("lore") or {}).values():
            if old in book:
                book.setdefault(new, book.pop(old))
        for event in ((hot.get("_will") or {}).get("happened") or []):
            if event.get("npc") == old:
                event["npc"] = new
        memory_old = state_dir / "archive/npc-memory" / f"{old}.json"
        if memory_old.is_file():
            memory_new = state_dir / "archive/npc-memory" / f"{new}.json"
            try:
                merged = json.loads(memory_new.read_text(encoding="utf-8")) if memory_new.is_file() else {}
                _fill(merged, json.loads(memory_old.read_text(encoding="utf-8")))
                memory_new.write_text(json.dumps(merged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                memory_old.unlink()
                touched = True
            except ValueError:
                pass
        if touched:
            done.append((old, new))
    return done
