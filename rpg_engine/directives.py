"""Поправки игрока к манере ведения — перед глазами ведущего каждый ход.

Игрок пишет мастеру прямо в заявке: «Мастеру: усиль влияние Шики», «#прошу тебя не
потеряй напряжение#», «Перепиши сцену. Это важно». В длинном чате такие слова тонут
через десяток ходов, а в новом чате их нет вовсе: ведущий «игнорирует указания»,
потому что их больше не видит.

Код вынимает их из заявок сам (``capture``) и держит в ``HOT.directives``.
Хранитель переносит постоянные в ``memory/_curated/directives.json``. ``NEXT.directives``
показывает постоянные всегда и свежие — ``RECENT_TURNS`` ходов.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SEED = "memory/_curated/directives.json"
KEEP = 16
RECENT_TURNS = 15
HASH = re.compile(r"#([^#]{3,700})#")
TO_GM = re.compile(r"^\s*(мастер\w*|прошу\b|перед тем как|учти\b|не забы\w+|сделай\b|перепиши\b|заново\b|важно\b|"
                   r"обязательно\b|ooc\b)", re.I)
ADDRESS = re.compile(r"(мастеру|прошу тебя|прошу|перепиши|сделай|учти)", re.I)
LINE = re.compile(r"(мастеру\s*:|перепиши|заново\b|это важно|сделай так|оцени все|не теряй|не потеряй)", re.I)


def _norm(text):
    return " ".join(str(text or "").split())


def extract(player):
    """Обращения к ведущему в заявке: #…# с адресом мастеру и строки с прямой поправкой."""
    text = str(player or "")
    found = [m.group(1) for m in HASH.finditer(text) if TO_GM.match(m.group(1))]
    bare = HASH.sub(" ", text)
    for line in re.split(r"[\n\r]+", bare):
        hit = LINE.search(line)
        if not hit:
            continue
        if "#" in line:
            # Непарная решётка: обращение — от слова-адреса («прошу», «мастеру»)
            # или ближайшей # слева до ближайшей # справа.
            left = line.rfind("#", 0, hit.start()) + 1
            words = list(ADDRESS.finditer(line[:hit.end()]))
            if words:
                left = max(left, words[-1].start())
            right = line.find("#", hit.start())
            line = line[left:right if right != -1 else None]
        # «Мастеру: … По игре: …» — берётся только обращение мастеру.
        found.append(re.split(r"по игре\s*:", line, flags=re.I)[0])
    out = []
    for item in found:
        item = re.sub(r"^\s*мастеру\s*[:,]?\s*", "", _norm(item), flags=re.I)
        if len(item) >= 8 and item not in out:
            out.append(item[:400])
    return out


def capture(hot, turn):
    """Новые поправки хода в HOT.directives; OOC-ход — целиком."""
    items = [_norm(turn.get("player"))[:400]] if turn.get("kind") == "ooc" else extract(turn.get("player"))
    book = hot.setdefault("directives", [])
    known = {d.get("text") for d in book}
    added = []
    for text in items:
        if text and text not in known:
            book.append({"turn": turn["id"], "text": text})
            added.append(text)
    del book[:-KEEP]
    return added


def standing(state_dir):
    try:
        data = json.loads((Path(state_dir) / SEED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d for d in data.get("standing") or [] if isinstance(d, dict) and d.get("text")]


def _seq(turn_id):
    try:
        session, _, number = str(turn_id).split(".")
        return int(session.lstrip("s")) * 10000 + int(number)
    except ValueError:
        return 0


def view(state_dir, hot):
    """Для NEXT: постоянные всегда, свежие — RECENT_TURNS ходов."""
    keep = standing(state_dir)
    cursor = _seq((hot.get("cursor") or {}).get("id"))
    covered = set()
    for d in keep:
        sources = d.get("from") or []
        covered |= {_norm(x) for x in ([sources] if isinstance(sources, str) else sources)}
    recent = [d for d in hot.get("directives") or []
              if cursor - _seq(d.get("turn")) <= RECENT_TURNS and _norm(d["text"]) not in covered
              and _norm(d.get("turn") or "") not in covered]   # поправка уже стала постоянной (from: ход)
    out = {}
    if keep:
        out["standing"] = [f"{d['text']} ({d.get('since', '')})".replace(" ()", "") for d in keep]
    if recent:
        out["recent"] = [f"{d['text'][:220]} ({d['turn']})" for d in recent[-6:]]
    return out
