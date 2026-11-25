"""Кто знает Шики и откуда: знакомство до этой сессии и сведения о нём.

s11.text.033–037: Сакура «помнит участников того экзамена», Какаши — «давно не
виделись», Баки «видел вас в Конохе». До s11 ни один из них с Шики не говорил:
Сакура мелькнула в одной сцене экзамена, Какаши и Баки — ни в одной. Ведущий
выдумал им общее прошлое, чтобы разговор сразу пошёл как между знакомыми.

Код делает три вещи:

* ``index`` — по всем сыгранным ходам: в каких ходах NPC говорил рядом с Шики
  (подпись реплики) и где лишь назван в повествовании;
* ``review`` — после хода ищет в репликах NPC воспоминание о прошлом с Шики
  («давно не виделись», «помню вас», «с экзамена») и сверяет со знакомством;
* ``view`` — перед сценой по каждому присутствующему одна строка: знакомы ли до
  этой сессии, что о Шики слышал (сведения из lore.json) и уровень речи медика.
"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from . import history_access, identity, lore, presence

LABEL = re.compile(r"^\s*\*\*([^*\n]{2,48}?)(?:\\)?:\*\*\s*[*_]*\s*[—–-]\s*(.*)$")
KNOWN = 3            # общих ходов с репликой NPC — знакомы; меньше — мельком
KNOWN_ANY = 5        # или столько ходов с ним в сцене вообще: до s08 реплики не подписывались
SHOW_TURNS = 2       # ходов-примеров в строке знакомства
# Воспоминание NPC о прошлом с Шики. «Вы изменились» — тоже: сравнивать можно только с виденным.
MEMORY = re.compile(
    r"давно не видел|давненько|(?<![\wё])(?:тебя|вас)\s+(?:я\s+)?(?:помн|видел|встречал|запомнил|узнал)"
    r"|(?<![\wё])(?:помню|запомнил\w*|видел\w*|встречал\w*)\s+(?:тебя|вас)(?![\wё])"
    r"|(?<![\wё])мы\s+(?:уже\s+)?(?:встречались|виделись|знакомы)"
    r"|(?<![\wё])(?:с|на|после|до|со времён)\s+(?:того\s+|прошлого\s+)?экзамен"
    r"|(?:ваше|тво[её])\s+имя\s+(?:мне\s+)?(?:попадал|встречал|знаком|слышал)"
    r"|(?<![\wё])в\s+прошл\w+\s+раз|(?<![\wё])как\s+тогда(?![\wё])"
    r"|(?<![\wё])(?:ты|вы)\s+(?:совсем\s+|сильно\s+|очень\s+|не\s+)?изменил", re.I)


def _session(turn_id):
    match = re.match(r"s(\d+)\.", str(turn_id or ""))
    return int(match.group(1)) if match else 0


def turns(state_dir):
    """Сыгранные ходы всех сессий по порядку (OOC пропущены)."""
    out = []
    if state_dir is None:
        return []
    for path in sorted(glob.glob(str(Path(state_dir) / "archive/session-*-turns.jsonl"))):
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if row.get("id") and row.get("kind") != "ooc":
                out.append(row)
    return out


def _stems(names, generic=()):
    """Первые 4 буквы → [(основа имени, ключ)]: «Сакуры» находит «сакура», «Какаши» — себя.

    Общая основа у нескольких карточек («Аракава Дзюн», «Аракава Мэй», «Охранник Аракавы»)
    отходит главной карточке — ключу без суффикса (arakawa); иначе основа ничего не доказывает.
    """
    owners = {}
    for key, entry in names.items():
        if key in generic:
            continue        # «Связной», «Дозорный»: заготовка по подписи — имя не имя, считается лишь речь
        for part in entry.get("parts") or ():
            if len(part) >= 4:
                owners.setdefault(part if len(part) <= 5 else part[:-1], set()).add(key)
    out = {}
    for stem, keys in owners.items():
        if len(keys) > 1:
            continue
        out.setdefault(stem[:4], []).append((stem, next(iter(keys))))
    return out


def generic(state_dir, hot):
    """Ключи карточек-заготовок по подписи (auto): их «имя» — описание, а не имя."""
    out = {k for k, c in (hot.get("cast") or {}).items() if isinstance(c, dict) and c.get("auto")}
    folder = Path(state_dir) / "recovery/cast"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else ():
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("auto"):
                out.add(path.stem)
        except (OSError, ValueError, AttributeError):
            continue
    return out


def lines(gm):
    """(подпись, реплика) — подписанные реплики сцены."""
    out = []
    for raw in str(gm or "").split("\n"):
        match = LABEL.match(raw)
        if match:
            out.append((match.group(1).strip(), match.group(2).strip()))
    return out


def index(state_dir, names, rows=None, generic=()):
    """Spoke/seen are access evidence; mentioned is search only."""
    stems = _stems(names, generic)
    rows = list(rows if rows is not None else turns(state_dir))
    scopes = history_access.scopes(rows, history_access.deltas(state_dir))
    rules = identity.active(state_dir) if state_dir is not None else []
    out = {}
    def record(key, field, turn):
        out.setdefault(key, {"spoke": [], "seen": [], "mentioned": []})[field].append(turn)
    for row in rows:
        if row.get("kind") in ("ooc", "technical", "void"):
            continue
        turn = row["id"]
        scope = scopes.get(turn)
        if scope is not None:
            scope = history_access.corrected_scope(rules, turn, scope)
        gm = str(row.get("gm") or "")
        spoke = {history_access.speaker(rules, turn, label, names, scope)
                 for label, _ in lines(gm)} - {None, lore.SHIKI}
        seen = (set(scope or ()) & set(names)) - spoke - {lore.SHIKI, "player"}
        mentioned = set()
        for word in set(re.findall(r"[а-яёa-z]{4,}", gm.casefold())):
            for base, key in stems.get(word[:4], ()):
                if word.startswith(base):
                    mentioned.add(key)
        for key in spoke:
            record(key, "spoke", turn)
        for key in seen:
            record(key, "seen", turn)
        for key in mentioned - spoke - seen:
            record(key, "mentioned", turn)
    return out


def level(met, key, session):
    """(уровень, ходы) до сессии: 2 — знакомы, 1 — мельком, 0 — не встречались."""
    row = met.get(key) or {}
    spoke = [t for t in row.get("spoke") or [] if _session(t) < session]
    seen = [t for t in row.get("seen") or [] if _session(t) < session]
    both = sorted(set(spoke) | set(seen), key=_order)
    if len(spoke) >= KNOWN or len(both) >= KNOWN_ANY:
        return 2, both
    if both:
        return 1, spoke or seen
    return 0, []


def _order(turn_id):
    match = re.match(r"s(\d+)\.\w+\.(\d+)", str(turn_id or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def describe(met, key, session):
    grade, where = level(met, key, session)
    if grade == 2:
        return f"до s{session:02d} знакомы ({where[0]}–{where[-1]}, {len(where)} ходов вместе)"
    if grade == 1:
        shown = ", ".join(where[:SHOW_TURNS])
        return f"до s{session:02d} лишь мельком ({shown}) — разве что смутно в лицо, без имени и общего прошлого"
    return f"до s{session:02d} встреча с Шики не подтверждена — общее прошлое не приписывать"


SURPRISE = re.compile(r"(?<![а-яё])(?:немыслим\w*|не может быть|невозможн\w*|впервые|растерян\w*|ошеломл\w*|"
                      r"потрясен\w*|потрясён\w*|изумл\w*|не верил\w*|остолбен\w*|оцепене\w*|у него тоже|"
                      r"что это (?:за|было))", re.I)
AGAIN_SCENE = 12     # виденное в нынешней сцене — та же встреча, не «раньше»


def again(hot, turn, names, entries, always=()):
    """NPC поражён тем, что уже видел в прошлой сцене (s12.text.043: «Шаринган…» Наруто после s11.text.046)."""
    from . import aging
    text = str(turn.get("gm") or "")
    visible = [e for e in entries or [] if lore.is_trait(e) and shown(e, text)]
    if not visible:
        return []
    now = aging._ordinal(turn.get("id")) or 0
    seen_book = hot.get("lore") or {}
    notes, done = [], set()
    blocks = re.split(r"\n\s*\n", text)
    for i, block in enumerate(blocks):
        if not SURPRISE.search(block):
            continue
        near = "\n".join(blocks[max(0, i - 1):i + 1])
        keys = {presence.resolve(label, names) for label, _ in lines(near)}
        keys |= {k for k in (hot.get("scene") or {}).get("present") or []
                 if (((hot.get("cast") or {}).get(k) or {}).get("name") or "§")[:5] in near}
        for key in keys - {None, lore.SHIKI} - set(always) - done:
            for entry in visible:
                turn_seen = (seen_book.get(entry["id"]) or {}).get(key)
                ordinal = aging._ordinal(turn_seen)
                if turn_seen and ordinal is not None and now - ordinal >= AGAIN_SCENE:
                    done.add(key)
                    notes.append({"turn": turn["id"], "kind": "acquaint", "text":
                                  f"Виденное не удивляет заново: {lore._name(hot, key)} уже видел «{entry.get('title')}» "
                                  f"({turn_seen}). Реакция — от этой памяти: узнал, сравнил, насторожился, — не открытие."})
                    break
    return notes


def review(hot, turn, met, names, always=(), entries=None):
    """Замечания kind=acquaint: NPC вспоминает прошлое с Шики, которого не было, — или поражён виденным."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    session = _session(turn.get("id"))
    notes, done = again(hot, turn, names, entries, always), set()
    for label, text in lines(turn.get("gm")):
        if "шики" in label.casefold():
            continue
        key = presence.resolve(label, names)
        if not key or key in always or key in done:
            continue
        match = MEMORY.search(text)
        if not match:
            continue
        grade, _ = level(met, key, session)
        if grade == 2:
            continue
        done.add(key)
        start = max(0, match.start() - 50)
        quote = ("…" if start else "") + text[start:match.end() + 40].strip()
        name = lore._name(hot, key) if key in (hot.get("cast") or {}) else presence.display(names, key)
        notes.append({"turn": turn["id"], "kind": "acquaint", "text":
                      f"Память без источника: {name} — «{quote}». {describe(met, key, session).capitalize()}. "
                      "Не расширяй это прошлое: узнаёт по Книге Бинго, слуху или документу — и говорит откуда, — "
                      "либо не помнит."})
    return notes


def _short_turn(turn_id, session):
    match = re.match(r"s(\d+)\.\w+\.(\d+)", str(turn_id or ""))
    if not match:
        return str(turn_id)
    return match.group(2) if int(match.group(1)) == session else f"s{match.group(1)}.{match.group(2)}"


def brief(met, key, session):
    """Коротко для NEXT: «до s11 мельком (s01.011, s01.092), без имени и прошлого»."""
    grade, where = level(met, key, session)
    head = f"до s{session:02d}"
    if grade == 2:
        return f"{head} знакомы ({_short_turn(where[0], 0)}–{_short_turn(where[-1], 0)})"
    if grade == 1:
        return f"{head} мельком ({', '.join(_short_turn(t, 0) for t in where[:SHOW_TURNS])}), без имени и прошлого"
    return f"{head} встреча не подтверждена"


def facts_known(hot, entries, key, traits=False):
    """[(заголовок, ход)] сведений о Шики, которые NPC слышал (или черт, которые видел — traits=True)."""
    out = []
    for entry in entries:
        if not lore.is_fact(entry) or lore.is_trait(entry) != traits:
            continue
        if key in (entry.get("wield") or []):
            out.append((entry.get("short") or entry.get("title") or entry["id"], "свидетель"))
            continue
        turn = lore.heard(hot, entry).get(key)
        if turn:
            out.append((entry.get("short") or entry.get("title") or entry["id"], turn))
    return out


def view(hot, key, met, entries, session, tier_line=None):
    """Одна строка о NPC для NEXT.present: знакомство, услышанное о Шики, уровень речи медика."""
    parts = [brief(met, key, session)]
    known = facts_known(hot, entries, key)
    if known:
        parts.append("слышал: " + ", ".join(f"{t} {_short_turn(w, session)}" for t, w in known[:4]))
    elif any(lore.is_fact(e) and not lore.is_trait(e) for e in entries):
        parts.append("тайн Шики не слышал")
    seen = facts_known(hot, entries, key, traits=True)
    if seen:   # уже видел — не «впервые», не удивляется заново
        parts.append("видел: " + ", ".join(f"{t} {_short_turn(w, session)}" for t, w in seen[:5]))
    # A reviewed absence from a specific episode is evidence too. Do not
    # confuse it with "never saw this technique" in any other encounter.
    excluded = []
    for entry in entries:
        rule = entry.get("_visual_witness") or {}
        for episode, audit in (rule.get("episodes") or {}).items():
            if _session(episode) <= session and key in (audit.get("known_non_witnesses") or []):
                label = entry.get("short") or entry.get("title") or entry["id"]
                excluded.append(f"{label} в {episode}")
    if excluded:
        parts.append("не видел в данном эпизоде: " + ", ".join(excluded[:2]))
    together = _together(met, key, session)
    if together:   # где NPC был рядом с Шики в этой сессии — помнит это (память карточки хранит лишь края)
        parts.append("в сессии вместе: " + ", ".join(together))
    if tier_line:
        parts.append(tier_line)
    return "; ".join(parts)


def _together(met, key, session, limit=4):
    """Ходы этой сессии рядом с Шики свёрнуты в отрезки: «033–036, 054–065».

    s11.text.073: карточка Сакуры хранила лишь 033 и 071–073 — совместную операцию 034 и бой
    с Сасори ведущий не видел и «показал» ей чёрную чакру впервые.
    """
    row = met.get(key) or {}
    turns = sorted({int(t.rsplit(".", 1)[-1]) for t in (row.get("spoke") or []) + (row.get("seen") or [])
                    if _session(t) == session and t.rsplit(".", 1)[-1].isdigit()})
    spans = []
    for n in turns:
        if spans and n - spans[-1][1] <= 2:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    out = [f"{a:03d}" if a == b else f"{a:03d}–{b:03d}" for a, b in spans]
    return out[-limit:] if len(out) <= limit else [out[0], "…"] + out[-(limit - 1):]


TRAIT_BACKFILL = 2   # 24.11.2026: черту видел тот, кто был в сцене (говорил или в составе), а не назван в тексте


UNSEEN = re.compile(r"(?<![а-яё])(?:не\s+(?:узнал\w*|видел\w*|заметил\w*|знал\w*|увидел\w*)|ни\s+\w+,\s+ни\s)", re.I)


def shown(entry, text):
    """Черта показана в тексте, а не названа в отрицании («ни Кабуто, ни Орочимару не узнали о Шарингане», s09.text.034)."""
    norm = lore._norm(text)
    for match in lore._pattern(entry).finditer(norm):
        start = max(norm.rfind(c, 0, match.start()) for c in ".!?\n") + 1
        ends = [i for i in (norm.find(c, match.end()) for c in ".!?\n") if i >= 0]
        if not UNSEEN.search(norm[start:min(ends) if ends else len(norm)]):
            return True
    return False


def _rebackfill_traits(hot, entries):
    """Прежний разбор считал свидетелем любого названного (s09.text.024: «Орочимару захочет…» — и Орочимару
    «видел» Шаринган Шики, хотя s09.text.034: «ни Кабуто, ни Орочимару не узнали»). Пересчёт один раз."""
    fold = hot.setdefault("fold", {})
    if fold.get("trait_backfill") == TRAIT_BACKFILL:
        return
    traits = {e["id"] for e in entries if lore.is_trait(e) and not e.get("_visual_witness")}
    for trait in traits:
        (hot.get("lore") or {}).pop(trait, None)
    fold["lore_backfill"] = [x for x in fold.get("lore_backfill") or [] if x not in traits]
    fold["trait_backfill"] = TRAIT_BACKFILL


def backfill(state_dir, hot, entries, names, rows=None):
    """Разово для новых сведений lore.json: кто из говоривших рядом слышал их в прошлых ходах;
    черту Шики видел тот, кто был в сцене — говорил или стоял в её составе (d.scene.present)."""
    _rebackfill_traits(hot, entries)
    done = set(hot.setdefault("fold", {}).get("lore_backfill") or [])
    fresh = [e for e in entries if lore.is_fact(e) and e["id"] not in done]
    if not fresh:
        return {}
    rows = rows if rows is not None else turns(state_dir)
    added = {}
    rows = list(rows)
    scopes = history_access.scopes(rows, history_access.deltas(state_dir))
    rules = identity.active(state_dir) if state_dir is not None else []
    for row in rows:
        scope = scopes.get(row["id"])
        if scope is not None:
            scope = history_access.corrected_scope(rules, row["id"], scope)
        scene = (scope or set()) - {"shiki", "player"}
        text = lore.heard_text(row, names)
        full = f"{row.get('player') or ''}\n{row.get('gm') or ''}"
        hits = [e for e in fresh if (shown(e, full) if lore.is_trait(e) else lore.mentions(e, text))]
        if not hits:
            continue
        people = set()
        for label, _ in lines(row.get("gm")):
            if "шики" in label.casefold() or any(n in label.casefold() for n in lore.INNER):
                continue
            key = history_access.speaker(rules, row["id"], label, names, scope)
            if key:
                people.add(key)
        for entry in hits:
            if lore.is_trait(entry):     # видел тот, кто был в сцене: говорил или в её составе
                witnesses = people | scene
            else:
                witnesses = people
            wield = set(entry.get("wield") or [])
            known = lore.heard(hot, entry)
            book = hot.setdefault("lore", {}).setdefault(entry["id"], {})
            candidates = witnesses
            if entry.get("_visual_witness"):
                # Historical high-impact feats must be independently audited.
                # A named NPC in a transcript can be miles from the technique.
                audit = ((entry["_visual_witness"].get("episodes") or {})
                         .get(row["id"]) or {})
                candidates = set(audit.get("witnessed_by") or [])
            for key in sorted(candidates):
                if (key in wield or key in known or key in book or
                        (entry.get("_visual_witness") and
                         not lore.verified_visual_witness(hot, entry, key, row["id"]))):
                    continue
                book[key] = row["id"]
                added.setdefault(entry["id"], []).append(key)
    hot["fold"]["lore_backfill"] = sorted(done | {e["id"] for e in fresh})
    return added
