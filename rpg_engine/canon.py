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
``identities`` [{a, b, source}], ``looks`` [{who, names, nouns, wrong, trait, source, near}] —
постоянный облик (23.11.2026, s11.text.077: старое фото Шики «темноволосым», хотя волосы белые с
s01.text.093): сторож ловит неверную черту в одном предложении с именем или с родовым словом
(«подросток») при имени рядом — в любом времени, и на фото, в записи, в воспоминании;
``near`` — черта однозначна (цвет волос Гаары), хватит имени рядом («его рыжих волос», s11.text.076).
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
LOOK_NEAR = 400      # знаков вокруг предложения, где ищется имя для родового слова («подросток»)
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
            "forbid": list(data.get("forbid") or []), "identities": list(data.get("identities") or []),
            "looks": [x for x in data.get("looks") or [] if isinstance(x, dict) and x.get("wrong") and x.get("names")],
            "ages": [x for x in data.get("ages") or [] if isinstance(x, dict) and x.get("born") and x.get("names")]}


def _sentence(text, start, end):
    left = max(text.rfind(c, 0, start) for c in ".!?…\n") + 1
    rights = [i for i in (text.find(c, end) for c in ".!?…\n") if i >= 0]
    return left, (min(rights) + 1 if rights else len(text))


# Между чертой и именем — другой человек или чужое «её/его»: черта не о нём («её чёрные волосы», «темноволосая женщина»).
OTHER = re.compile(r"\b(её|его|их|ей|ему)\b|женщин|мужчин|стари|девушк|девочк|ребён|ребен|человек|шиноби|незнаком|"
                   r"страж|солдат|торгов|служанк|медсестр|лекар|куноичи|охранник|дозорн|разведчик", re.I)
LOOK_TIE, LOOK_NOUN = 60, 30   # знаков от черты до имени / до родового слова


def _tied(sent, start, end, rx, reach):
    """Имя (или родовое слово) рядом с чертой [start, end) в предложении, без другого человека между ними."""
    for hit in rx.finditer(sent):
        gap = sent[min(hit.end(), end):max(hit.start(), start)]
        if abs(hit.start() - start) <= reach and not OTHER.search(gap):
            return True
    return False


def _hot_for_age(state_dir, turn):
    """Дата проверяемого хода — из него самого (at), смещение года — из HOT."""
    try:
        hot = json.loads((Path(state_dir) / "recovery/HOT.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hot = {}
    return {"cursor": {"at": turn.get("at")}, "calendar": hot.get("calendar") or {}}


def look_hits(cur, text):
    """Неверная постоянная черта облика: [(look, отрывок)]. Черта привязана к имени в том же
    предложении — или к родовому слову («подросток», «юноша») при имени рядом (LOOK_NEAR):
    так ловится и старое фото, запись, воспоминание."""
    out = []
    for look in cur.get("looks") or []:
        try:
            wrong, names = re.compile(look["wrong"], re.I), re.compile(look["names"])
            nouns = re.compile(look["nouns"], re.I) if look.get("nouns") else None
        except (re.error, TypeError):
            continue
        for match in wrong.finditer(text):
            left, right = _sentence(text, match.start(), match.end())
            sent, start, end = text[left:right], match.start() - left, match.end() - left
            head = OTHER.search(sent[end:end + LOOK_NOUN])   # «темноволосая женщина» — черта другого человека
            own = [m.start() for m in (names.search(sent[end:end + LOOK_NOUN]),
                                       nouns and nouns.search(sent[end:end + LOOK_NOUN])) if m]
            if head and not (own and min(own) < head.start()):
                continue
            near = text[max(0, left - LOOK_NEAR):right + LOOK_NEAR]
            if (look.get("near") and names.search(near)) or _tied(sent, start, end, names, LOOK_TIE) or \
                    (nouns and _tied(sent, start, end, nouns, LOOK_NOUN) and names.search(near)):
                out.append((look, " ".join(sent.split())))
                break
    return out


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
    if cur["ages"]:
        lines += ["## Возраст — от дня рождения", "",
                  "Возраст считается на дату хода по календарю игры и растёт сам (`NEXT.ages`); оригинальные возрасты "
                  "Naruto здесь не действуют.", ""]
        for x in cur["ages"]:
            span = f" — {x['born_before']}" if x.get("born_before") else ""
            mark = " · выведено из канона, ждёт подтверждения" if x.get("derived") else ""
            lines.append(f"- **{x.get('who')}**: род. {x.get('born')}{span} ({x.get('source')}){mark}")
        lines.append("")
    if cur["looks"]:
        lines += ["## Облик — постоянные черты", "",
                  "Во всех временах — и на старом фото, в записи, в воспоминании, — пока сцена не показала перемену.", ""]
        lines += [f"- **{x.get('who')}**: {x.get('trait')} ({x.get('source')})" for x in cur["looks"]]
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
    for look in cur["looks"]:              # облик присутствующего — одна строка за ход, не чаще раза в REPEAT
        key = f"look:{look.get('who')}"
        if key not in shown and (look.get("who") == "Мугами Шики" or any(re.search(look["names"], n) for n in names)):
            picked.append((key, f"Облик {look.get('who')}: {look.get('trait')} ({look.get('source')})"))
            break
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
    for entry, said, age, sent in age_hits(state_dir, _hot_for_age(state_dir, turn), text):
        notes.append({"turn": turn["id"], "kind": "canon",
                      "text": f"Возраст: «{_short(sent, 110)}» — {entry.get('who')}: {age} на дату хода "
                              f"(род. {entry.get('born')}, {entry.get('source')}), не {said}. Возраст считается от дня "
                              "рождения по календарю игры (NEXT.ages); исправь следующим ходом."})
    for look, sent in look_hits(curated(state_dir), text):
        notes.append({"turn": turn["id"], "kind": "canon",
                      "text": f"Облик: «{_short(sent, 120)}» — {look.get('who')}: {look.get('trait')} "
                              f"({look.get('source')}). Облик постоянен и в прошлом (фото, запись, воспоминание), "
                              "пока сцена не показала перемену; NPC не подтверждает неверное. Сверь с первоисточником "
                              "и исправь следующим ходом."})
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


# --- Возраст: день рождения и отсчёт по игровому календарю (24.11.2026) ------------------------------
# Игрок: «Возраст персонажей слетает. Его как-то нужно зафиксировать, но отсчёт должен быть сохранён»
# (s12.text.059: Наруто — 15, как в оригинальном Shippuden, хотя здесь ему 20). Хранится день рождения
# (``canon.json`` ``ages``: who, names, born "ГГГГ-ММ-ДД", source; born_before — день не задан, известен
# промежуток; derived — выведено из канона разницей с Наруто, ждёт подтверждения игрока), возраст код
# считает на дату хода: растёт сам, когда игровой календарь переходит день рождения.
import datetime as _dt

YEAR_OFFSET = 1277     # внутренний год 0010 — 1287 (hot.calendar.year_offset)
_TENS = {"двадцат": 20, "тридцат": 30, "сорок": 40, "пятьдесят": 50, "пятидесят": 50, "шестьдесят": 60,
         "шестидесят": 60, "семьдесят": 70, "семидесят": 70, "восемьдесят": 80, "восьмидесят": 80,
         "девяност": 90}
_TEENS = {"десят": 10, "одиннадцат": 11, "двенадцат": 12, "тринадцат": 13, "четырнадцат": 14, "пятнадцат": 15,
          "шестнадцат": 16, "семнадцат": 17, "восемнадцат": 18, "девятнадцат": 19}
_UNITS = {"один": 1, "одна": 1, "одного": 1, "два": 2, "две": 2, "двух": 2, "три": 3, "трёх": 3, "трех": 3,
          "четыре": 4, "четырёх": 4, "четырех": 4, "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7,
          "семи": 7, "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9}
AGE = re.compile(
    r"(?<![\wё])((?:двадцат|тридцат|сорок|пятьдесят|пятидесят|шестьдесят|шестидесят|семьдесят|семидесят|"
    r"восемьдесят|восьмидесят|девяност)\w*(?:[\s-]+(?:один|одна|одного|два|две|двух|три|трёх|трех|четыре|"
    r"четырёх|четырех|пять|пяти|шесть|шести|семь|семи|восемь|восьми|девять|девяти))?|(?:одиннадцат|двенадцат|"
    r"тринадцат|четырнадцат|пятнадцат|шестнадцат|семнадцат|восемнадцат|девятнадцат|десят)\w*|\d{1,3})"
    r"(?:[\s-]*(?:лет|года|год)(?![\wё])|-?летн\w*)", re.I)


def _number(text):
    text = str(text).casefold()
    digits = re.match(r"\d+", text)
    if digits:
        return int(digits.group())
    for base, value in _TEENS.items():
        if text.startswith(base):
            return value
    for base, value in _TENS.items():
        if text.startswith(base):
            rest = re.split(r"[\s-]+", text, maxsplit=1)
            return value + (_UNITS.get(rest[1], 0) if len(rest) > 1 else 0)
    return None


def _date(text):
    try:
        return _dt.date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def today(hot):
    """Календарная дата хода: внутренний год + смещение кампании (0010 → 1287)."""
    at = str((hot.get("cursor") or {}).get("at") or "")
    offset = int((hot.get("calendar") or {}).get("year_offset") or YEAR_OFFSET)
    try:
        moment = _dt.date.fromisoformat(at[:10])
    except ValueError:
        return None
    return moment.replace(year=moment.year + offset)


def age_on(born, day):
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def age_of(entry, day):
    """(возраст, строка): по дню рождения; при неизвестном дне — промежуток, пока он не пройден."""
    born, before = _date(entry.get("born")), _date(entry.get("born_before"))
    if not born or not day:
        return None, ""
    low = age_on(born, day)
    birth = f"род. {born:%d.%m}.{born.year}"
    if before and before > born:
        high = age_on(before, day)
        if high != low:
            return low, (f"{low} или {high} — день рождения не задан (между {born:%d.%m} и {before:%d.%m}), "
                         f"спроси игрока")
        return low, (f"{low} (род. между {born:%d.%m} и {before:%d.%m}.{born.year}; {low + 1} — в этот промежуток, "
                     f"день не задан — спроси игрока)")
    turn = born.replace(year=day.year)
    if turn < day:
        turn = born.replace(year=day.year + 1)
    note = f"{low} ({birth}; {low + 1} — с {turn:%d.%m}.{turn.year})"
    if entry.get("derived"):
        note += "; выведено из канона, ждёт подтверждения игрока"
    return low, note


def ages_now(state_dir, hot, keys=None):
    """{who: строка возраста} — для присутствующих (по именам карточек) и Шики."""
    cur, day = curated(state_dir), today(hot)
    if not cur["ages"] or not day:
        return {}
    cast = hot.get("cast") or {}
    present = list(keys if keys is not None else (hot.get("scene") or {}).get("present") or [])
    names = [str((cast.get(k) or {}).get("name") or k) for k in present]
    out = {}
    for entry in cur["ages"]:
        try:
            rx = re.compile(entry["names"])
        except re.error:
            continue
        if entry.get("who") == "Мугами Шики" or any(rx.search(n) for n in names):
            age, note = age_of(entry, day)
            if age is not None:
                out[entry.get("who")] = note
    return out


def age_hits(state_dir, hot, text):
    """[(entry, сказано, верно, отрывок)]: возраст в одном предложении с именем расходится с отсчётом."""
    cur, day = curated(state_dir), today(hot)
    out = []
    if not cur["ages"] or not day:
        return out
    for match in AGE.finditer(text or ""):
        said = _number(match.group(1))
        left, right = _sentence(text, match.start(), match.end())
        sent = text[left:right]
        for entry in cur["ages"]:
            try:
                rx = re.compile(entry["names"])
            except re.error:
                continue
            if not _tied(sent, match.start() - left, match.end() - left, rx, LOOK_TIE):
                continue
            age, _ = age_of(entry, day)
            before = _date(entry.get("born_before"))
            allowed = {age} | ({age_on(before, day)} if before else set()) | ({age - 1, age + 1} if entry.get("derived") else set())
            if said is not None and age is not None and said not in allowed:
                out.append((entry, said, age, " ".join(sent.split())))
            break
    return out
