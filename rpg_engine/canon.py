"""Канон перед глазами ведущего: свод, строки к сцене, сторож отменённого, сверка раз в 20 ходов.

Прямые поправки игрока копятся в ``archive/shiki-canon.jsonl`` (``d.canon+``), а файлы
правил (SHIKI.md, TECHNIQUES.md) хранитель сводит изредка — они отставали от игры на
целую сессию («Суми» и «Смертный надрез» потерялись с s09.text.068). Ведущий читает
правила при старте чата; в длинном чате они вымываются. Здесь:

* **свод** ``memory/CANON.md`` каждой свёрткой: тождества людей, отменённое, поправки
  после последнего сведения в правила — по разделам, заменённое помечено;
* **канон к сцене** ``NEXT.canon``: до трёх строк по присутствующим, заявке игрока и
  смыслу сцены, плюс строка техники из TECHNIQUES.md, если игрок её назвал; одна и та
  же строка не повторяется чаще раза в ``REPEAT`` ходов;
* **сторож**: отменённое в тексте хода — замечание (только при нарушении: ход не
  дольше и пакет не толще, пока всё чисто);
* **сверка** раз в ``REVIEW`` ходов: перечитать свод.

Заготовка хранителя — ``memory/_curated/canon.json``: ``consolidated`` (до какого хода
поправки уже в правилах), ``superseded`` {ход: каким заменён}, ``forbid`` [{pattern, why}],
``identities`` [{a, b, source}].
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import aging
from .heuristic_history import stem, tokens

SOURCE = "archive/shiki-canon.jsonl"
CURATED = "memory/_curated/canon.json"
DIGEST = "memory/CANON.md"
TECHNIQUES = "campaigns/shiki/ref/TECHNIQUES.md"
REVIEW = 20          # ходов между сверками канона
REPEAT = 10          # строка канона к сцене не чаще раза в столько ходов
ITEMS, ITEM_CHARS = 3, 180
TOPICS = (
    ("Облик, возраст, статус", r"облик|внешн|волос|роба|одежд|маск|возраст|лет\b|года\b|нукенин|дзёнин|статус|награ"),
    ("Техники, печати, сендзюцу, глаза", r"техник|печат|сенчакр|сендзюцу|отшельник|шаринган|мангекё|шитон|бётон|хётон|"
                                         r"суйтон|райтон|катон|шуншин|гендзюцу|яд|клон|суми|меч|чакр"),
    ("Договоры, призыв, вещи", r"договор|контракт|призыв|хагуро|журнал|документ|флакон|образец|убежищ"),
    ("Люди и связи", r"[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+|тождеств|та же|тот же|оказал|встреч"),
)
RULE = ("Канон к сцене — держи эти строки; при расхождении с памятью чата прав канон. Полный свод — "
        "memory/CANON.md.")
REVIEW_RULE = ("Сверка канона (раз в {n} ходов): перечитай memory/CANON.md — тождества, отменённое и поправки "
               "после сведения ({k} шт.) — и веди сцену в согласии с ними.")


def load(state_dir):
    path = Path(state_dir) / SOURCE
    if not path.is_file():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            # Обрывки из одной буквы (s11.text.073: d.canon+ строкой разошёлся по символам) — не поправки.
            if row.get("rule") and len(str(row["rule"]).strip()) >= 8:
                rows.append({"turn": str(row.get("turn") or ""), "rule": str(row["rule"]), "line": number})
    seen = {}
    for row in rows:                       # ключ записи: ход#номер (за один ход бывает несколько поправок)
        seen[row["turn"]] = seen.get(row["turn"], 0) + 1
        row["key"] = f"{row['turn']}#{seen[row['turn']]}"
    return rows


def curated(state_dir):
    path = Path(state_dir) / CURATED
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        data = {}
    return {"consolidated": data.get("consolidated"), "superseded": dict(data.get("superseded") or {}),
            "forbid": list(data.get("forbid") or []), "identities": list(data.get("identities") or [])}


def _names(text):
    return {m.group(1) for m in re.finditer(r"«([^»]{3,40})»", text or "")}


def superseded(rows, cur):
    """{ключ записи: чем заменена}: заготовка хранителя (ход или ход#n) + поздняя поправка,
    которая «отменяет» имя в кавычках из ранней."""
    given = dict(cur.get("superseded") or {})
    out = {}
    for row in rows:
        if row["key"] in given or row["turn"] in given:
            out[row["key"]] = given.get(row["key"]) or given.get(row["turn"])
    for i, early in enumerate(rows):
        names = _names(early["rule"])
        if not names or early["key"] in out:
            continue
        for late in rows[i + 1:]:
            if late["turn"] != early["turn"] and any(_cancels(late["rule"], n) for n in names):
                out[early["key"]] = late["key"]
                break
    return out


def _cancels(text, name):
    """Поздняя поправка отменяет «имя»: «отмен…» в том же предложении рядом с ним, без «остаётся»."""
    for match in re.finditer(re.escape(f"«{name}»"), text):
        start = max(text.rfind(".", 0, match.start()), text.rfind(";", 0, match.start())) + 1
        stops = [i for i in (text.find(".", match.end()), text.find(";", match.end())) if i >= 0]
        clause = text[start:min(stops) if stops else len(text)]
        if re.search(r"отмен|упраздн|больше не", clause, re.I) and not re.search(r"оста[её]тся|сохраня|не отмен", clause, re.I):
            return True
    return False


def _after(turn, mark):
    a, b = aging._ordinal(turn), aging._ordinal(mark)
    return b is None or (a is not None and a > b)


def _short(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def digest(state_dir):
    """memory/CANON.md: тождества, отменённое, поправки после последнего сведения в правила."""
    rows, cur = load(state_dir), curated(state_dir)
    gone = superseded(rows, cur)
    lines = ["# Канон: живой свод", "",
             "Собирает код каждой свёрткой из прямых поправок игрока (`archive/shiki-canon.jsonl`). Основа — "
             "`campaigns/shiki/SHIKI.md` и `ref/TECHNIQUES.md` (main); здесь — что важно не потерять и что "
             f"появилось после их сведения (сведено по {cur.get('consolidated') or '—'}). Поздняя поправка сильнее "
             "ранней.", ""]
    if cur["identities"]:
        lines += ["## Тождества людей", ""]
        lines += [f"- **{i.get('a')}** = {i.get('b')} ({i.get('source')})" for i in cur["identities"]]
        lines.append("")
    lines += ["## Отменено — не использовать", ""]
    lines += [f"- {f.get('label') or f.get('pattern')}: {f.get('why')}" for f in cur["forbid"]]
    by_key = {r["key"]: r for r in rows}
    for old, new in sorted(gone.items(), key=lambda kv: aging._ordinal(kv[0].split("#")[0]) or 0):
        if old in by_key:
            lines.append(f"- ~~{old}~~ заменено {new}: {_short(by_key[old]['rule'], 140)}")
    lines.append("")
    fresh = [r for r in rows if _after(r["turn"], cur.get("consolidated")) and r["key"] not in gone]
    lines += [f"## Поправки после сведения ({len(fresh)})", ""]
    placed = set()
    for title, rx in TOPICS:
        group = [r for r in fresh if r["key"] not in placed and re.search(rx, r["rule"], re.I)]
        if group:
            lines += [f"### {title}", ""]
            for r in group:
                placed.add(r["key"])
                lines.append(f"- {r['turn']}: {_short(r['rule'], 420)}")
            lines.append("")
    rest = [r for r in fresh if r["key"] not in placed]
    if rest:
        lines += ["### Прочее", ""] + [f"- {r['turn']}: {_short(r['rule'], 420)}" for r in rest] + [""]
    body = "\n".join(lines).rstrip() + "\n"
    path = Path(state_dir) / DIGEST
    if not path.is_file() or path.read_text(encoding="utf-8") != body:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return {"entries": len(rows), "fresh": len(fresh), "superseded": len(gone)}


# ── к сцене ──────────────────────────────────────────────────────────────────

def _stems(text):
    return {stem(w) for w in tokens(text or "") if len(w) >= 4}


def techniques(engine_root, request):
    """Строки TECHNIQUES.md, чьё название игрок упомянул в заявке."""
    path = Path(engine_root) / TECHNIQUES
    if not path.is_file() or not request:
        return []
    asked = _stems(request)
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"- \*\*(.+?)\*\*", line)
        if not match:
            continue
        name = re.sub(r"[«»:]", " ", match.group(1))
        key = {s for s in _stems(name) if s not in {stem("техника"), stem("печати")}}
        hit = key & asked
        if hit:
            found.append((len(hit), line[2:]))
    found.sort(key=lambda x: -x[0])
    return [_short(line.replace("**", ""), 300) for _, line in found[:2]]


def for_scene(state_dir, hot, applied, engine_root):
    """Строки канона к сцене для NEXT.canon (или None)."""
    rows, cur = load(state_dir), curated(state_dir)
    gone = superseded(rows, cur)
    live = [r for r in rows if r["key"] not in gone]
    cursor = (hot.get("cursor") or {}).get("id")
    now = aging._ordinal(cursor) or 0
    shown = hot.setdefault("fold", {}).setdefault("canon_shown", {})
    for key in [k for k, at in shown.items() if now - at >= REPEAT]:
        shown.pop(key)
    played = [t for t in applied or [] if t.get("kind") not in ("ooc", "technical", "void")]
    request = " ".join(str(t.get("player") or "") for t in played[-1:])
    scene = hot.get("scene") or {}
    names = set()
    for key in scene.get("present") or []:
        if key in ("shiki", "player", "nemera"):   # Немера — свой модуль NEMERA.md, читается при старте
            continue
        card = (hot.get("cast") or {}).get(key) or {}
        names |= {w for w in re.findall(r"[А-ЯЁ][а-яё]{3,}", str(card.get("name") or ""))}
    picked = []
    for ident in cur["identities"]:
        text = f"{ident.get('a')} = {ident.get('b')} ({ident.get('source')})"
        if any(n in text for n in names) and f"id:{ident.get('a')}" not in shown:
            picked.append((f"id:{ident.get('a')}", text))
    for row in reversed(live):              # поздние поправки о присутствующих — первыми
        if len(picked) >= ITEMS:
            break
        if any(n in row["rule"] for n in names) and row["key"] not in shown:
            picked.append((row["key"], f"{row['turn']}: {row['rule']}"))
    context = " ".join(x for x in (request, str(scene.get("situation") or ""), str(cursor and
                                   (hot.get("cursor") or {}).get("place") or "")) if x)
    if len(picked) < ITEMS and context.strip():
        ids = {r["key"]: r for r in live}
        try:
            from . import meaning
            index = _canon_ids(state_dir, rows)
            near = meaning.search(state_dir, context, limit=6,
                                  allow=lambda rid: index.get(rid) in ids) or []
            near_keys = [index[rid] for rid, _ in near]
        except Exception:
            near_keys = []
        if not near_keys:                   # без модели — по общим редким словам
            want = _stems(context)
            scored = sorted(((len(_stems(r["rule"]) & want), r["key"]) for r in live), reverse=True)
            near_keys = [k for n, k in scored[:4] if n >= 3]
        for key in near_keys:
            if len(picked) >= ITEMS:
                break
            if key not in shown and key not in {p[0] for p in picked}:
                picked.append((key, f"{ids[key]['turn']}: {ids[key]['rule']}"))
    lines = techniques(engine_root, request)
    for key, _ in picked:
        shown[key] = now
    items = [_short(text, ITEM_CHARS) for _, text in picked] + lines
    return {"rule": RULE, "items": items} if items else None


def _canon_ids(state_dir, rows):
    """{id записи исторического индекса: ключ поправки}: запись индекса указывает строку в shiki-canon.jsonl."""
    from . import heuristic_history
    manifest = Path(state_dir) / "archive/heuristic-recall/manifest.json"
    if not manifest.is_file():
        return {}
    by_line = {r["line"]: r["key"] for r in rows}
    out = {}
    for row in heuristic_history.load(manifest)["records"]:
        if row.get("kind") == "correction" and row.get("source") == SOURCE:
            key = by_line.get(int(row.get("start") or 0))
            if key:
                out[row["id"]] = key
    return out


# ── сторож и сверка ──────────────────────────────────────────────────────────

def guard(state_dir, turn):
    """Отменённое в тексте хода — замечания kind=canon (OOC не проверяется)."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    text = f"{turn.get('gm') or ''}\n{turn.get('line') or ''}"
    notes = []
    for rule in curated(state_dir)["forbid"]:
        try:
            match = re.search(rule["pattern"], text, re.I)
        except (re.error, KeyError, TypeError):
            continue
        if match:
            start = max(0, match.start() - 50)
            notes.append({"turn": turn["id"], "kind": "canon",
                          "text": f"Канон: «…{_short(text[start:match.end() + 40], 110)}» — {rule.get('why')}. "
                                  "Исправь следующим ходом, не переписывая показанное молча."})
    return notes[:3]


def review(state_dir, hot):
    """Раз в REVIEW ходов — сверка со сводом (строка для NEXT или None)."""
    cursor = (hot.get("cursor") or {}).get("id") or ""
    seq = int(cursor.rsplit(".", 1)[-1]) if cursor[-3:].isdigit() else 0
    if not seq or seq % REVIEW:
        return None
    rows, cur = load(state_dir), curated(state_dir)
    gone = superseded(rows, cur)
    fresh = [r for r in rows if _after(r["turn"], cur.get("consolidated")) and r["key"] not in gone]
    return REVIEW_RULE.format(n=REVIEW, k=len(fresh))
