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

import hashlib
import json
import re
from pathlib import Path

SEED = "memory/_curated/directives.json"
KEEP = 16
RECENT_TURNS = 15
HASH = re.compile(r"#([^#]+)#")
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
            out.append(item)
    return out


# Explicit ongoing markers, not a general natural-language intent classifier.
PERMANENT = re.compile(r"\b(?:всегда|впредь|отныне|постоянно|до отмены|"
                       r"с этого момента|в каждом ходе|в каждой сцене|во всех сценах|"
                       r"на постоянной основе|новое постоянное правило)\b", re.I)
CANCEL = re.compile(r"\b(?:отмен[яи]\w*|отмена|сними|замени|заменяю|больше не действует)\b", re.I)
ONE_SHOT = re.compile(r"\b(?:только сейчас|на этот ход|в этой сцене|"
                      r"один раз|перепиши|заново|выведи|покажи заново)\b", re.I)
QUESTION = re.compile(r"^(?:мастеру\s*[:,]?\s*)?(?:а\s+)?"
                      r"(?:как|почему|зачем|что|где|когда|кто|сколько|можешь ли)\b", re.I)
ID = re.compile(r"dir_[0-9a-f]{16}")
MIGRATION = "campaigns/shiki/ref/DIRECTIVE_MIGRATION.json"


def stable_id(text):
    return "dir_" + hashlib.sha256(_norm(text).casefold().encode()).hexdigest()[:16]


def classify(text):
    text = _norm(text)
    if CANCEL.search(text):
        return "review"  # do not infer which standing rule is cancelled
    if "?" in text or QUESTION.match(text):
        return "question"
    if ONE_SHOT.search(text):
        return "once"
    if PERMANENT.search(text):
        return "standing"
    return "review"  # ambiguity is not a durable command


def _items(turn):
    player = turn.get("player") or ""
    return [_norm(player)] if turn.get("kind") == "ooc" else extract(player)


def validate_ops(turn):
    """Operation evidence must be a verbatim part of an actual GM address."""
    ops = (turn.get("d") or {}).get("directive_ops")
    if ops is None:
        return []
    if not isinstance(ops, list) or any(not isinstance(x, dict) for x in ops):
        raise ValueError("directive_ops: expected list of objects")
    player = turn.get("player") or ""
    addressed = _items(turn)
    for op in ops:
        kind, quote = op.get("op"), op.get("quote")
        if kind not in ("add", "revoke", "replace") or not isinstance(quote, str) or not quote.strip():
            raise ValueError("directive_ops: operation and verbatim quote required")
        if quote not in player or not any(_norm(quote) in text for text in addressed):
            raise ValueError("directive_ops: quote is not in player GM address")
        if kind in ("revoke", "replace"):
            if not isinstance(op.get("id"), str) or not ID.fullmatch(op["id"]) or not CANCEL.search(quote):
                raise ValueError("directive_ops: explicit cancellation and target ID required")
        if kind in ("add", "replace"):
            text = op.get("text")
            if not isinstance(text, str) or not text.strip() or text not in quote:
                raise ValueError("directive_ops: new text must be a verbatim part of quote")
            if op.get("scope", "shiki") not in ("general", "shiki"):
                raise ValueError("directive_ops: invalid scope")
            if op.get("scope") == "general" and not re.search(
                    r"\b(?:в любой игре|для всех игр|во всех кампаниях|общ[еи]е правил\w*)\b", quote, re.I):
                raise ValueError("directive_ops: general scope must be explicit")
            if kind == "add" and classify(quote) != "standing":
                raise ValueError("directive_ops: add requires explicit ongoing instruction, not question/one-shot")
            if kind == "replace" and ("?" in text or ONE_SHOT.search(text)):
                raise ValueError("directive_ops: replacement cannot be a question/one-shot")
    return ops


def standing(state_dir):
    try:
        data = json.loads((Path(state_dir) / SEED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [dict(d, id=d.get("id") or stable_id(d["text"])) for d in data.get("standing") or []
            if isinstance(d, dict) and d.get("text")]


def _migration(state_dir):
    """Only exact, verified player passages from the existing campaign."""
    if state_dir is None:
        return []
    root = Path(__file__).resolve().parents[1]
    try:
        data = json.loads((root / MIGRATION).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if data.get("schema") != "npr-directive-migration/1":
        return []
    result = []
    for row in data.get("entries", []):
        path = Path(state_dir) / "archive/turns" / row["turn"].split(".")[0] / (row["turn"] + ".md")
        try:
            player = path.read_text(encoding="utf-8").split("## Игрок\n", 1)[1].split("\n## ", 1)[0].strip()
        except (OSError, IndexError):
            continue
        if row["quote"] in player:
            result.append({"id": stable_id(row["text"]), "text": row["text"], "since": row["turn"],
                           "from": [row["turn"]], "scope": "shiki"})
    return result


def active(state_dir, hot):
    """Replay durable ops over the legacy curated list; no seed rewrite."""
    base = standing(state_dir) + _migration(state_dir) if state_dir is not None else []
    rows = {row["id"]: dict(row) for row in base}
    for event in hot.get("directive_events") or []:
        kind = event["op"]
        if kind in ("revoke", "replace"):
            rows.pop(event["id"], None)
        if kind in ("add", "replace"):
            rows[event["new_id"]] = {"id": event["new_id"], "text": event["text"],
                                      "scope": event.get("scope", "shiki"), "since": event["turn"],
                                      "from": [event["turn"], event["quote"]]}
    return list(rows.values())


def capture(hot, turn, state_dir=None):
    """Keep permanent events unbounded by recent-turn limits; all source text is in TURN."""
    ops = validate_ops(turn)
    live = {row["id"]: row for row in active(state_dir, hot)}
    events = hot.setdefault("directive_events", [])
    book = hot.setdefault("directives", [])
    known = {(d.get("turn"), d.get("text")) for d in book}
    added = []
    # Explicit ops win over heuristic capture of the same passage.
    for item in _items(turn):
        kind = classify(item)
        if kind == "standing" and not any(_norm(op["quote"]) in item for op in ops):
            ops = [*ops, {"op": "add", "text": item, "quote": item, "scope": "shiki"}]
        elif kind != "standing" and (turn["id"], item) not in known:
            book.append({"turn": turn["id"], "text": item, "kind": kind})
            added.append(item)
    for op in ops:
        event = dict(op, turn=turn["id"])
        kind = event["op"]
        if kind in ("add", "replace"):
            event["new_id"] = stable_id(event["text"])
        if event in events:
            continue
        if kind in ("revoke", "replace"):
            if event["id"] not in live:
                raise ValueError("directive_ops: unknown/inactive target ID")
            old = live.pop(event["id"])
            if kind == "replace" and "scope" not in event:
                event["scope"] = old.get("scope", "shiki")
        if kind in ("add", "replace"):
            if kind == "add" and event["new_id"] in live:
                continue
            live[event["new_id"]] = event
        events.append(event)
        added.append(event.get("text", event["id"] if "id" in event else ""))
    del book[:-KEEP]
    return added


def _seq(turn_id):
    try:
        session, _, number = str(turn_id).split(".")
        return int(session.lstrip("s")) * 10000 + int(number)
    except ValueError:
        return 0


def view(state_dir, hot):
    """Only standing instructions are binding; recent ambiguity is a review cue."""
    keep = active(state_dir, hot)
    cursor = _seq((hot.get("cursor") or {}).get("id"))
    covered = set()
    for d in keep:
        sources = d.get("from") or []
        covered |= {_norm(x) for x in ([sources] if isinstance(sources, str) else sources)}
    recent = [d for d in hot.get("directives") or []
              if 0 <= cursor - _seq(d.get("turn")) <= RECENT_TURNS
              and _norm(d["text"]) not in covered and _norm(d.get("turn") or "") not in covered
              and d.get("kind", classify(d["text"])) == "review"]
    out = {}
    if keep:
        out["standing"] = [f"{d['text']} ({d.get('since', '')})".replace(" ()", "") for d in keep]
        out["standing_ids"] = [d["id"] for d in keep]
    if recent:
        out["recent"] = [f"{d['text'][:220]} ({d['turn']})" for d in recent[-6:]]
    return out


def project_view(view, turn):
    """Apply confirmed pending OOC operations to NEXT without reading the seed."""
    if not view or not view.get("standing_ids"):
        return view
    # Use the existing stable IDs, not a hash of formatted display strings.
    texts = dict(zip(view["standing_ids"], view.get("standing", [])))
    ops = validate_ops(turn)
    for item in _items(turn):
        if classify(item) == "standing" and not any(_norm(op["quote"]) in item for op in ops):
            ops = [*ops, {"op": "add", "text": item}]
    for op in ops:
        if op["op"] in ("revoke", "replace"):
            if op["id"] not in texts:
                raise ValueError("directive_ops: unknown/inactive target ID")
            texts.pop(op["id"])
        if op["op"] in ("add", "replace"):
            texts.setdefault(stable_id(op["text"]), f"{op['text']} ({turn['id']})")
    return dict(view, standing=list(texts.values()), standing_ids=list(texts))
