"""Вики мира: постраничная память сущностей кампании.

Единственный источник правды — сыгранные ходы. Вики полностью пересобирается
из них при каждой свёртке и потому не теряет и не искажает старое:

* ``archive/deltas/session-NNN.jsonl`` — дельты ``d`` каждого свёрнутого хода
  (последняя версия хода при ``amend``); прошлое восстанавливается из истории
  ``recovery/TURN.json`` командой ``wiki-backfill``;
* ``campaigns/shiki/roster.json`` — карточки сессий 1–3;
* ``recovery/cast/*.json`` и HOT — нынешние карточки, Шики, нити, вещи;
* ``archive/npc-memory/*.json`` — эпизоды NPC; ``archive/shiki-canon.jsonl`` —
  поправки игрока; ``recovery/GM_ONLY.json`` — тайны, психика, часы;
* ``memory/_curated/*.json`` — проверенные дополнения хранителя в формате ``d``.

Постоянные черты (``look``, ``facts``) не перезаписываются молча: новое значение
становится текущим, прежнее остаётся в истории страницы, а свёртка сообщает
о смене в CHECKS.json. ``body``/``state`` — только текущий вид и положение.
Тайное пишется лишь в файлы ``*.GM_ONLY.md``.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import fight, plot, talk

SCHEMA = "npr-wiki/1"
DIR = "memory"
DELTAS_DIR = "archive/deltas"
CURATED_DIR = "memory/_curated"
MANIFEST = "memory/_manifest.json"
TURNS_DIR = "archive/turns"
ROSTER = "campaigns/shiki/roster.json"
PLAYERS = {"shiki", "player"}
HISTORY_ON_PAGE = 8
KNOWS_ON_PAGE = 25
NOT_ALIASES = {"глава", "мастер", "охрана", "старший", "дежурная", "неизвестный",
               "неустановленный", "второй", "третий", "раненый", "реципиент"}
DUE_WINDOW = timedelta(hours=24)

CHARACTER = (("role", "роль"), ("wants", "хочет"), ("fears", "боится"),
             ("leverage", "рычаги"), ("stance", "позиция"), ("voice", "голос"))
NOW = (("state", "положение"), ("body", "вид в последней сцене"))
ROSTER_STABLE = (("appearance", "внешность"), ("village", "деревня"), ("team", "команда"),
                 ("capabilities", "способности"), ("limitations", "ограничения"))
ROSTER_CHARACTER = (("role", "role"), ("speech", "voice"), ("goals", "wants"))
SPEAKER = re.compile(r"\*\*([^*\n:]{2,48}):\*\*")
TURN_ID = re.compile(r"^s(\d+)\.")


# ----------------------------------------------------------------- утилиты

def _norm(value):
    text = str(value or "").casefold().replace("ё", "е")
    return " ".join(re.sub(r"№\s*", "№", text).split())


def _text(value, size=None):
    if isinstance(value, (list, tuple)):
        value = "; ".join(_text(v) for v in value if v)
    elif isinstance(value, dict):
        value = "; ".join(f"{k}: {_text(v)}" for k, v in value.items() if v not in (None, "", [], {}))
    text = " ".join(str(value or "").split())
    return text if size is None or len(text) <= size else text[: size - 1] + "…"


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _jsonl(path):
    rows = []
    try:
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            if raw.strip():
                try:
                    rows.append(json.loads(raw))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def _order(turn_id):
    match = re.match(r"^s(\d+)\.\w+\.(\d+)", str(turn_id or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _session(turn_id):
    match = TURN_ID.match(str(turn_id or ""))
    return int(match.group(1)) if match else 0


# ----------------------------------------------------------------- дельты ходов

def record_deltas(state_dir, turns):
    """Сохраняет дельты свёрнутых ходов; повтор того же id заменяет запись."""
    by_session = {}
    for turn in turns:
        if turn.get("id"):
            by_session.setdefault(_session(turn["id"]), []).append(turn)
    for session, batch in by_session.items():
        path = Path(state_dir) / DELTAS_DIR / f"session-{session:03}.jsonl"
        rows = {row["id"]: row for row in _jsonl(path) if row.get("id")}
        for turn in batch:
            rows[turn["id"]] = {k: turn[k] for k in ("id", "at", "place", "kind", "d") if k in turn}
        path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(rows.values(), key=lambda r: _order(r["id"]))
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ordered)
        if not path.is_file() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")


def backfill_deltas(state_dir, ref="HEAD"):
    """Разово восстанавливает дельты всех ходов из истории recovery/TURN.json."""
    state_dir = Path(state_dir)
    log = subprocess.run(["git", "-C", str(state_dir), "log", "--reverse", "--format=%H", ref,
                          "--", "recovery/TURN.json"], capture_output=True, text=True, check=True)
    latest = {}
    for sha in log.stdout.split():
        shown = subprocess.run(["git", "-C", str(state_dir), "show", f"{sha}:recovery/TURN.json"],
                               capture_output=True, text=True)
        try:
            turn = json.loads(shown.stdout)
        except ValueError:
            continue
        if isinstance(turn, dict) and turn.get("id"):
            latest[turn["id"]] = turn
    record_deltas(state_dir, list(latest.values()))
    return {"turns": len(latest), "first": min(latest, key=_order, default=None),
            "last": max(latest, key=_order, default=None)}


def _all_deltas(state_dir):
    rows = []
    folder = Path(state_dir) / DELTAS_DIR
    for path in sorted(folder.glob("session-*.jsonl")) if folder.is_dir() else ():
        rows.extend(_jsonl(path))
    rows.sort(key=lambda r: _order(r.get("id")))
    curated = Path(state_dir) / CURATED_DIR
    for path in sorted(curated.glob("*.json")) if curated.is_dir() else ():
        item = _read_json(path, {})
        for row in (item if isinstance(item, list) else [item]):
            if isinstance(row, dict) and isinstance(row.get("d"), dict):
                rows.append({"id": row.get("source") or f"curated:{path.stem}",
                             "d": row["d"], "curated": True})
    return rows


# ----------------------------------------------------------------- реестр

def _entity(store, key, kind="npc"):
    return store.setdefault(key, {
        "id": key, "kind": kind, "name": "", "aliases": [], "first": None, "last": None,
        "stable": {}, "character": {}, "now": {}, "knows": [], "history": [], "fixes": [],
        "boundary": "", "gm": {}, "sources": [], "absent_since": None, "notes": []})


def _seen(entity, turn_id):
    if not turn_id or not TURN_ID.match(str(turn_id)):
        return
    if entity["first"] is None or _order(turn_id) < _order(entity["first"]):
        entity["first"] = turn_id
    if entity["last"] is None or _order(turn_id) > _order(entity["last"]):
        entity["last"] = turn_id


def _push(series, value, source):
    """Добавляет значение в ряд поля; повтор того же значения не множит историю."""
    value = _text(value)
    if not value:
        return False
    if series and _norm(series[-1]["v"]) == _norm(value):
        return False
    series.append({"v": value, "src": source})
    return len(series) > 1


def _add_unique(items, value, source):
    value = _text(value)
    if value and all(_norm(x["v"]) != _norm(value) for x in items):
        items.append({"v": value, "src": source})


def _apply_cast(store, key, patch, turn_id, changes):
    entity = _entity(store, key)
    _seen(entity, turn_id)
    if patch is None:
        entity["absent_since"] = turn_id
        return
    if not isinstance(patch, dict):
        return
    entity["absent_since"] = None
    if patch.get("name") and not entity["name"]:
        entity["name"] = _text(patch["name"])
    elif patch.get("name") and _norm(patch["name"]) != _norm(entity["name"]):
        if _text(patch["name"]) not in entity["aliases"]:
            entity["aliases"].append(entity["name"])
        entity["name"] = _text(patch["name"])
    for alias in patch.get("aliases") or patch.get("aliases+") or []:
        if _text(alias) and _text(alias) not in entity["aliases"]:
            entity["aliases"].append(_text(alias))
    stable = {}
    if patch.get("look"):
        stable["внешность"] = patch["look"]
    for field, value in (patch.get("facts") or {}).items():
        stable[_text(field)] = value
    for field, value in stable.items():
        series = entity["stable"].setdefault(field, [])
        if _push(series, value, turn_id):
            changes.append((key, field, series[-2], series[-1]))
    for field, _label in CHARACTER:
        if patch.get(field):
            _push(entity["character"].setdefault(field, []), patch[field], turn_id)
    for field, _label in NOW:
        if patch.get(field):
            entity["now"][field] = {"v": _text(patch[field]), "src": turn_id}
    for item in patch.get("knows+") or []:
        _add_unique(entity["knows"], item, turn_id)
    for item in patch.get("knows") or []:
        _add_unique(entity["knows"], item, turn_id)
    if patch.get("mem"):
        entity["history"].append({"turn": turn_id, "what": _text(patch["mem"], 400), "ch": "память"})
    if patch.get("hides"):
        entity["gm"]["hides"] = _text(patch["hides"])
    if patch.get("intent"):
        entity["gm"]["intent"] = patch["intent"]


def _merge_card(store, key, card, source):
    entity = _entity(store, key)
    if card.get("name") and not entity["name"]:
        entity["name"] = _text(card["name"])
    for field, _label in CHARACTER:
        if card.get(field) and not entity["character"].get(field):
            entity["character"][field] = [{"v": _text(card[field]), "src": source}]
    for field, _label in NOW:
        if card.get(field) and field not in entity["now"]:
            entity["now"][field] = {"v": _text(card[field]), "src": card.get("seen") or source}
    for item in card.get("knows") or []:
        _add_unique(entity["knows"], item, card.get("seen") or source)
    _seen(entity, card.get("first"))
    _seen(entity, card.get("seen"))
    if source not in entity["sources"]:
        entity["sources"].append(source)


def _merge_roster(store, roster):
    for card in roster.get("cards") or []:
        key = card.get("id")
        if not key:
            continue
        entity = _entity(store, key)
        entity["name"] = entity["name"] or _text(card.get("name"))
        for field, label in ROSTER_STABLE:
            if card.get(field) and label not in entity["stable"]:
                entity["stable"][label] = [{"v": _text(card[field]), "src": ROSTER}]
        for field, target in ROSTER_CHARACTER:
            if card.get(field) and not entity["character"].get(target):
                entity["character"][target] = [{"v": _text(card[field]), "src": ROSTER}]
        if card.get("relations"):
            entity["stable"].setdefault("связи", [{"v": _text(card["relations"]), "src": ROSTER}])
        entity["boundary"] = entity["boundary"] or _text(card.get("knowledge_boundary"))
        if card.get("gm_only"):
            entity["gm"].setdefault("roster_secret", _text(card["gm_only"]))
        if card.get("independent_actions"):
            entity["gm"].setdefault("independent", _text(card["independent_actions"]))
        if card.get("exam_presence"):
            entity["notes"].append(f"экзамен: {_text(card['exam_presence'])}")
        entity["sessions_hint"] = "сессии 1–3 (экзамен на тюнина)"
        if ROSTER not in entity["sources"]:
            entity["sources"].append(ROSTER)


def _merge_ledger(store, ledger):
    for key, card in (ledger or {}).get("entities", {}).items():
        target = _match(store, [key] + list(card.get("aliases") or [])) or key
        entity = _entity(store, target)
        for alias in card.get("aliases") or []:
            if alias not in entity["aliases"] and _norm(alias) != _norm(entity["name"]):
                entity["aliases"].append(alias)
        for fact in card.get("facts") or []:
            if fact.get("superseded_by"):
                continue
            label = {"sex": "пол", "age_at_discovery": "возраст", "identity": "кто это"}.get(
                fact.get("field"), _text(fact.get("field")))
            series = entity["stable"].setdefault(label, [])
            _push(series, fact.get("value"), fact.get("source") or "ENTITY_LEDGER")


def _match(store, names):
    wanted = {_norm(n) for n in names if _norm(n)}
    for key, entity in store.items():
        forms = {_norm(key), _norm(entity["name"])} | {_norm(a) for a in entity["aliases"]}
        if wanted & forms:
            return key
    return None


def _merge_same_names(store):
    """Сводит записи одного человека под разными ключами (по полному имени)."""
    by_name = {}
    for key in sorted(store, key=lambda k: (store[k]["first"] is None, k)):
        name = _norm(store[key]["name"])
        if name and name in by_name and store[key]["kind"] == store[by_name[name]]["kind"]:
            main, extra = store[by_name[name]], store.pop(key)
            main["aliases"] = list(dict.fromkeys(main["aliases"] + [extra["id"]] + extra["aliases"]))
            for field in ("stable", "character"):
                for label, series in extra[field].items():
                    main[field].setdefault(label, series)
            for label, value in extra["now"].items():
                main["now"].setdefault(label, value)
            for item in extra["knows"]:
                _add_unique(main["knows"], item["v"], item["src"])
            main["history"] += extra["history"]
            for k, v in extra["gm"].items():
                main["gm"].setdefault(k, v)
            _seen(main, extra["first"])
            _seen(main, extra["last"])
            main["sources"] = list(dict.fromkeys(main["sources"] + extra["sources"]))
        elif name:
            by_name[name] = key


def _auto_aliases(store):
    """Части собственного имени (все слова с заглавной) от 4 букв, если они ничьи больше."""
    owners = {}
    for key, entity in store.items():
        for part in re.findall(r"[\w-]{4,}", entity["name"]):
            owners.setdefault(_norm(part), set()).add(key)
    for key, entity in store.items():
        words = re.findall(r"[\w-]+", entity["name"])
        if not words or not all(w[:1].isupper() for w in words):
            continue
        for part in re.findall(r"[\w-]{4,}", entity["name"]):
            if _norm(part) in NOT_ALIASES:
                continue
            if _norm(part) != _norm(entity["name"]) and _owner(store, owners[_norm(part)]) == key:
                if part not in entity["aliases"]:
                    entity["aliases"].append(part)


def _owner(store, keys):
    """Общая фамилия достаётся тому, о ком в игре написано больше всего; при равенстве — никому."""
    if len(keys) == 1:
        return next(iter(keys))
    weight = sorted(((len(store[k]["sources"]) + len(store[k]["history"]), k) for k in keys), reverse=True)
    return weight[0][1] if weight[0][0] > weight[1][0] else None


def build(state_dir, hot, source_root=None):
    """Собирает реестр сущностей из всех источников. Ничего не пишет."""
    state_dir = Path(state_dir)
    root = Path(source_root) if source_root else Path(__file__).resolve().parents[1]
    store, changes = {}, []
    roster = _read_json(root / ROSTER, {}) or {}
    _merge_roster(store, roster)
    deltas = _all_deltas(state_dir)
    things, threads = {}, {}
    for row in deltas:
        d = row.get("d") or {}
        turn_id = row.get("id")
        for key, patch in (d.get("cast") or {}).items():
            if key in PLAYERS:
                continue
            _apply_cast(store, key, patch, turn_id, changes if not row.get("curated") else [])
        for key, patch in (d.get("things") or {}).items():
            if isinstance(patch, dict):
                things.setdefault(key, []).append({"turn": turn_id, "patch": patch})
        for key, patch in (d.get("threads") or {}).items():
            threads.setdefault(key, []).append({"turn": turn_id, "patch": patch})
    for key, card in (hot.get("cast") or {}).items():
        if key not in PLAYERS and isinstance(card, dict):
            _merge_card(store, key, card, "recovery/HOT.json")
    cast_dir = state_dir / "recovery/cast"
    for path in sorted(cast_dir.glob("*.json")) if cast_dir.is_dir() else ():
        card = _read_json(path, {}) or {}
        if path.stem not in PLAYERS:
            _merge_card(store, path.stem, card, f"recovery/cast/{path.name}")
    memory_dir = state_dir / "archive/npc-memory"
    for path in sorted(memory_dir.glob("*.json")) if memory_dir.is_dir() else ():
        record = _read_json(path, {}) or {}
        if path.stem in PLAYERS:
            continue
        entity = _entity(store, path.stem)
        entity["name"] = entity["name"] or _text(record.get("name"))
        seen = {(h.get("turn"), h.get("what")) for h in entity["history"]}
        for episode in record.get("episodes") or []:
            if (episode.get("turn"), _text(episode.get("what"), 400)) not in seen:
                entity["history"].append({"turn": episode.get("turn"), "at": episode.get("at"),
                                          "ch": episode.get("ch"),
                                          "what": _text(episode.get("what"), 400)})
            _seen(entity, episode.get("turn"))
        entity["history_file"] = f"archive/npc-memory/{path.name}"
    _merge_ledger(store, _read_json(state_dir / "recovery/ENTITY_LEDGER_GM_ONLY.json", {}))
    for key, hidden in (hot.get("_hides") or {}).items():
        if key in store and hidden:
            store[key]["gm"]["hides"] = _text(hidden)
    for key, psyche in (hot.get("_psyche") or {}).items():
        if key in store and psyche:
            store[key]["gm"]["psyche"] = psyche
    _merge_same_names(store)
    _auto_aliases(store)
    conversations, unplaced = talk.collect(state_dir, store)
    for key, entries in conversations.items():
        store[key]["talk"] = entries
    for entity in store.values():
        entity["history"].sort(key=lambda h: _order(h.get("turn")))
        if not entity["name"]:
            entity["name"] = entity["id"]
    clocks = ((hot.get("offscreen") or {}).get("clocks") or {})
    for clock_id, clock in clocks.items():
        if not isinstance(clock, dict) or clock.get("closed"):
            continue
        owner = _match(store, [clock.get("owner")]) if clock.get("owner") else None
        if owner:
            store[owner]["gm"].setdefault("clocks", {})[clock_id] = clock
    fixes = _jsonl(state_dir / "archive/shiki-canon.jsonl")
    for entity in store.values():
        forms = [f for f in [entity["name"]] + entity["aliases"] if len(f) >= 4 and not f.isascii()]
        for rule in fixes:
            text = _norm(rule.get("rule"))
            if any(_norm(f) in text for f in forms):
                entity["fixes"].append({"turn": rule.get("turn"), "rule": _text(rule.get("rule"))})
    return {"schema": SCHEMA, "cursor": (hot.get("cursor") or {}).get("id"),
            "entities": store, "things": things, "threads": threads,
            "fixes": fixes, "changes": changes}


# ----------------------------------------------------------------- проверка нового хода

# Фон без имени: роль, должность, приметa. Такие голоса не получают подсказки о карточке,
# даже если звучат во многих ходах; значимого безымянного ведущий оформляет сам.
ROLE_STEMS = ("анестезиолог", "медсестр", "сестр", "врач", "санитар", "фельдшер", "хирург", "лаборант",
              "медик", "охран", "патрул", "караул", "страж", "боец", "солдат", "конвой", "задержан",
              "пленн", "ранен", "голос", "посыльн", "курьер", "гонец", "чиновник", "писар", "секретар",
              "старш", "младш", "дежурн", "неизвестн", "незнаком", "неустановлен", "мужчин", "женщин",
              "девушк", "парен", "юнош", "старик", "старух", "ребен", "мальчик", "девочк", "пациент",
              "капитан", "командир", "офицер", "инспектор", "судья", "экзаменатор", "наблюдател",
              "продав", "хозя", "официант", "трактир", "торговц", "торговец", "шиноби", "генин",
              "тюнин", "дзенин", "анбу", "толп", "люди", "все ", "второй", "третий", "первый")
ROLE_ENDINGS = re.compile(r"(лог|ник|ница|тель|тельница|ист|истка|ор|ер|ец|арь|ач|ент|ант|ый|ий|ой|ая|яя|ые|ие)$")


def _is_background(label):
    """Безымянная роль, а не имя: «Анестезиолог», «Старший патруля», «Задержанный»."""
    name = _norm(label)
    if any(stem in name for stem in ROLE_STEMS):
        return True
    words = re.findall(r"[\w-]+", name)
    return len(words) == 1 and bool(ROLE_ENDINGS.search(words[0]))


def turn_checks(registry, turns):
    """Замечания к только что свёрнутым ходам: новые говорящие без карточки и смена постоянного."""
    store = registry["entities"]
    forms = []
    for entity in store.values():
        for form in [entity["name"], entity["id"]] + entity["aliases"]:
            if form and len(_norm(form)) >= 3:
                forms.append(_norm(form))
    notes = []
    fresh = {t.get("id") for t in turns}
    for turn in turns:
        cast = (turn.get("d") or {}).get("cast") or {}
        declared = {_norm(k) for k in cast}
        declared |= {_norm(p.get("name")) for p in cast.values() if isinstance(p, dict) and p.get("name")}
        unknown = []
        for label in SPEAKER.findall(turn.get("gm") or ""):
            name = _norm(label)
            if not name or "шики" in name or name in declared or name in unknown:
                continue
            if _is_background(label):
                continue
            if any(name == f or name in f or f in name for f in forms):
                continue
            unknown.append(name)
        for name in unknown[:4]:
            notes.append({"turn": turn["id"], "kind": "new_character",
                          "text": f"В сцене говорит «{name}» с именем, а страницы нет. Если это не "
                                  f"разовый фон, добавь в следующий ход d.cast.<id> с name, look и facts."})
    for key, field, old, new in registry["changes"]:
        if new["src"] not in fresh:
            continue
        name = store.get(key, {}).get("name", key)
        notes.append({"turn": new["src"], "kind": "stable_change",
                      "text": f"{name}: изменена постоянная черта «{field}». Было «{_text(old['v'], 90)}» "
                              f"({old['src']}), стало «{_text(new['v'], 90)}». Если это ошибка, исправь "
                              f"ход amend; если перемена произошла в мире, оставь."})
    return notes


# ----------------------------------------------------------------- страницы

def _src(value):
    return f" [{value}]" if value else ""


def _line(label, item):
    return f"- {label}: {item['v']}{_src(item['src'])}"


def _npc_page(entity, present):
    lines = [f"# {entity['name']} · `{entity['id']}`", ""]
    meta = [f"вид: {entity['kind']}"]
    if entity["first"]:
        meta.append(f"впервые: {entity['first']}")
    elif entity.get("sessions_hint"):
        meta.append(entity["sessions_hint"])
    if entity["last"]:
        meta.append(f"последний раз: {entity['last']}")
    meta.append("сейчас в сцене" if entity["id"] in present else
                (f"вне сцены с {entity['absent_since']}" if entity["absent_since"] else "вне сцены"))
    if entity["aliases"]:
        lines.append("псевдонимы: " + ", ".join(dict.fromkeys(entity["aliases"])))
    lines += [" · ".join(meta), ""]
    lines.append("## Постоянное")
    if not entity["stable"]:
        lines.append("- не записано. Не выдумывай: открой первый ход персонажа или оставь черту "
                     "неопределённой; новую черту запиши в d.cast.<id>.facts или look.")
    for label, series in entity["stable"].items():
        lines.append(_line(label, series[-1]))
        for old in series[:-1]:
            lines.append(f"    - прежде: {old['v']}{_src(old['src'])}")
    if entity["character"]:
        lines += ["", "## Характер"]
        for field, label in CHARACTER:
            series = entity["character"].get(field)
            if series:
                lines.append(_line(label, series[-1]))
    if entity["now"]:
        lines += ["", "## Сейчас"]
        for field, label in NOW:
            if field in entity["now"]:
                lines.append(_line(label, entity["now"][field]))
    if entity["boundary"]:
        lines += ["", "## Граница знаний", f"- {entity['boundary']}"]
    if entity["knows"]:
        shown = entity["knows"][-KNOWS_ON_PAGE:]
        lines += ["", f"## Знает (последние {len(shown)} из {len(entity['knows'])})"]
        lines += [f"- {k['v']}{_src(k['src'])}" for k in shown]
        if len(entity["knows"]) > len(shown):
            lines.append(f"- ранее: ещё {len(entity['knows']) - len(shown)}, полностью в "
                         f"`memory/npc/{entity['id']}.knows.md`")
    if entity["fixes"]:
        lines += ["", "## Поправки игрока"]
        lines += [f"- {f['turn']}: {_text(f['rule'], 400)}" for f in entity["fixes"]]
        lines.append("- полный текст поправок: `archive/shiki-canon.jsonl`")
    if entity["history"]:
        shown = entity["history"][-HISTORY_ON_PAGE:]
        lines += ["", f"## История (последние {len(shown)} из {len(entity['history'])})"]
        for h in shown:
            lines.append(f"- {h.get('turn') or '?'}{(' ' + h['at']) if h.get('at') else ''}: "
                         f"{_text(h.get('what'), 300)}")
        if len(entity["history"]) > len(shown):
            where = entity.get("history_file") or "archive/npc-memory/"
            lines.append(f"- ранее: {len(entity['history']) - len(shown)} эпизодов, полностью в `{where}`")
    if entity.get("talk"):
        entries = entity["talk"]
        sessions = sorted({e["turn"][:3] for e in entries})
        lines += ["", f"## Разговоры с Шики: {len(entries)} ходов ({', '.join(sessions)})",
                  f"- дословно по ходам: `memory/npc/{entity['id']}.talk.md`"]
        for entry in entries[-2:]:
            said = (entry.get("said") or [""])[0]
            lines.append(f"- {entry['turn']}: Шики — «{_text(entry.get('heard'), 160)}»; "
                         f"{entity['name']} — «{_text(said, 160)}»")
    if entity["notes"]:
        lines += ["", "## Заметки"] + [f"- {n}" for n in entity["notes"]]
    lines += ["", "Ход целиком: `archive/turns/sNN/<id>.md`."]
    return "\n".join(lines) + "\n"


def _knows_page(entity):
    lines = [f"# {entity['name']} · всё, что знает ({len(entity['knows'])})", "",
             "Только то, что персонаж видел, слышал или получил по каналу. Источник — ход.", ""]
    lines += [f"- {k['v']}{_src(k['src'])}" for k in entity["knows"]]
    return "\n".join(lines) + "\n"


def _npc_gm_page(entity):
    gm = entity["gm"]
    if not gm:
        return None
    lines = [f"# {entity['name']} · тайное", "",
             "ТОЛЬКО ВЕДУЩИЙ. Не раскрывать без добытых в игре доказательств.", ""]
    if gm.get("hides"):
        lines += ["## Скрывает", f"- {gm['hides']}", ""]
    if gm.get("roster_secret"):
        lines += ["## Тайна из карточки", f"- {gm['roster_secret']}", ""]
    if gm.get("independent"):
        lines += ["## Действует сам", f"- {gm['independent']}", ""]
    if gm.get("intent"):
        lines += ["## Намерение", f"- {_text(gm['intent'], 600)}", ""]
    if gm.get("psyche"):
        lines += ["## Психика", f"- {_text(gm['psyche'], 1500)}", ""]
    for clock_id, clock in (gm.get("clocks") or {}).items():
        lines += [f"## Часы `{clock_id}`", f"- срок: {clock.get('due')}", f"- что: {_text(clock.get('what'))}",
                  f"- признаки: {_text(clock.get('signs'))}",
                  f"- если не помешать: {_text(clock.get('if_unopposed'))}", ""]
    return "\n".join(lines).rstrip() + "\n"


SHIKI_LABELS = {"sheet": "лист персонажа", "look": "внешность", "body": "тело сейчас",
                "presence": "присутствие", "genome": "Нулевой Геном", "seals": "печати",
                "arts": "техники", "pact_rule": "Договор", "secrets_from_world": "тайны от мира",
                "home": "дом", "equipment": "снаряжение", "knows": "знает",
                "medical_note": "медицина", "mental_link": "связь", "nemera_perception": "Немера"}


def _shiki_page(hot, fixes):
    shiki = hot.get("shiki") or {}
    lines = ["# Мугами Шики · `shiki`", "",
             "Персонаж игрока. Мысли, слова и добровольные действия Шики не придумывать.", ""]
    pact = hot.get("pact") or {}
    if pact.get("stage") is not None:
        lines += ["## Договор Семи замков сейчас",
                  f"- стадия {pact.get('stage')}: {_text(pact.get('label'))} (значение {pact.get('value')})", ""]
    for key, value in shiki.items():
        label = SHIKI_LABELS.get(key, key)
        lines.append(f"## {label[:1].upper() + label[1:]}")
        if isinstance(value, list):
            lines += [f"- {_text(v)}" for v in value]
        elif isinstance(value, dict):
            lines += [f"- {k}: {_text(v)}" for k, v in value.items()]
        else:
            lines.append(f"- {_text(value)}")
        lines.append("")
    own = _shiki_fixes(fixes)
    if own:
        lines += [f"## Поправки игрока: {len(own)}",
                  "- полностью в `memory/shiki-fixes.md`; при споре прав более поздний ход"]
    return "\n".join(lines).rstrip() + "\n"


def _shiki_fixes(fixes):
    return [f for f in fixes if re.search(r"шики|договор|бетон|шитон|ситон|геном|запах|печат",
                                          _norm(f.get("rule")))]


def _shiki_fixes_page(fixes):
    own = _shiki_fixes(fixes)
    lines = [f"# Шики · поправки игрока ({len(own)})", "",
             "По порядку ходов. Более поздняя поправка сильнее ранней.", ""]
    lines += [f"- {f.get('turn')}: {_text(f.get('rule'))}" for f in own]
    return "\n".join(lines) + "\n"


def _thread_page(key, value, history):
    lines = [f"# Линия `{key}`", ""]
    if isinstance(value, dict):
        for field, item in value.items():
            lines.append(f"- {field}: {_text(item)}")
    elif value:
        lines.append(f"- {_text(value)}")
    if history:
        lines += ["", "## Изменения по ходам"]
        for entry in history[-15:]:
            lines.append(f"- {entry['turn']}: {_text(entry['patch'], 400)}")
    return "\n".join(lines) + "\n"


def _item_page(key, value, history):
    lines = [f"# Вещь `{key}`", ""]
    for field, item in (value or {}).items():
        lines.append(f"- {field}: {_text(item)}")
    if history:
        lines += ["", "## Изменения по ходам"]
        lines += [f"- {e['turn']}: {_text(e['patch'], 300)}" for e in history[-15:]]
    return "\n".join(lines) + "\n"


def _clocks_page(hot, store):
    clocks = ((hot.get("offscreen") or {}).get("clocks") or {})
    lines = ["# Часы мира", "", "ТОЛЬКО ВЕДУЩИЙ. Закадровые процессы со сроками.", ""]
    for key, clock in sorted(clocks.items(), key=lambda kv: str((kv[1] or {}).get("due"))):
        if not isinstance(clock, dict) or clock.get("closed"):
            continue
        owner = _match(store, [clock.get("owner")]) if clock.get("owner") else None
        lines += [f"## `{key}` · срок {clock.get('due')}",
                  f"- кто: {_text(clock.get('owner'))}" + (f" (`npc/{owner}`)" if owner else ""),
                  f"- что: {_text(clock.get('what'))}", f"- признаки: {_text(clock.get('signs'))}",
                  f"- если не помешать: {_text(clock.get('if_unopposed'))}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _due_soon(hot):
    cursor = (hot.get("cursor") or {}).get("at")
    try:
        now = datetime.fromisoformat(cursor)
    except (TypeError, ValueError):
        return []
    soon = []
    for key, clock in ((hot.get("offscreen") or {}).get("clocks") or {}).items():
        try:
            due = datetime.fromisoformat(str((clock or {}).get("due")))
        except ValueError:
            continue
        if not clock.get("closed") and due <= now + DUE_WINDOW:
            soon.append((clock.get("due"), key))
    return sorted(soon)


def _present_card(key, entity):
    """Короткая карточка присутствующего: облик, характер, последнее знание. Полное — на его странице."""
    def last(series, limit):
        return _text(series[-1]["v"], limit) if series else ""
    lines = [f"### {entity.get('name') or key} · `npc/{key}`"]
    stable = entity.get("stable") or {}
    look = last(stable.get("внешность"), 260) or "не записан — опиши при первой возможности и внеси в карточку (`look`)"
    lines.append(f"- облик: {look}")
    for field in ("возраст", "пол"):
        if stable.get(field):
            lines.append(f"- {field}: {last(stable[field], 60)}")
    for field, label in (("role", "роль"), ("wants", "хочет"), ("fears", "боится"), ("voice", "голос")):
        value = last((entity.get("character") or {}).get(field), 200)
        if value:
            lines.append(f"- {label}: {value}")
    knows = entity.get("knows") or []
    if knows:
        lines.append(f"- знает (последнее из {len(knows)}): " + "; ".join(_text(k["v"] if isinstance(k, dict) else k, 140) for k in knows[-3:]))
    return lines


def _scene_page(hot, store):
    cursor, scene = hot.get("cursor") or {}, hot.get("scene") or {}
    lines = ["# Сцена сейчас", "",
             f"**{cursor.get('date', '')}, {cursor.get('time', '')}.** *{cursor.get('place', '')}*",
             f"Последний ход: `{cursor.get('id')}`.", ""]
    present = [p for p in scene.get("present") or [] if p not in PLAYERS]
    if present:
        lines.append("## Рядом")
        for key in present:
            lines += _present_card(key, store.get(key) or {})
        lines.append("")
    for field, label in (("situation", "Положение"), ("positions", "Кто где"),
                         ("pressure", "Давление"), ("unread", "Не прочитано Шики")):
        if scene.get(field):
            lines += [f"## {label}", _text(scene[field]), ""]
    soon = _due_soon(hot)
    if soon:
        lines += [f"## Сроки в ближайшие 24 игровых часа: {len(soon)}",
                  "- " + ", ".join(f"`{k}` ({d})" for d, k in soon),
                  "- подробности только для ведущего: `memory/threads/CLOCKS.GM_ONLY.md`", ""]
    combat = fight.scene_lines(hot)
    if combat:
        lines += ["## Бой, раны, чакра"] + combat + [""]
    events = [e for e in ((hot.get("_plot") or {}).get("events") or {}).values()
              if e.get("status") == "active" and e.get("kind") != "main"]
    if events:
        lines += [f"## События: идут {len(events)}",
                  "- вопросы, концовки и пределы только для ведущего: `memory/PLOT.GM_ONLY.md`", ""]
    last = hot.get("last") or {}
    if last.get("gm"):
        lines += ["## Конец последнего ответа", _text(last["gm"][-900:]), ""]
    return "\n".join(lines).rstrip() + "\n"


def _index_page(hot, registry):
    store = registry["entities"]
    cursor = (hot.get("cursor") or {}).get("id")
    present = set((hot.get("scene") or {}).get("present") or [])
    lines = [f"# Карта мира NPR · на ходе `{cursor}`", "",
             "Прежде чем писать о персонаже, вещи или линии, открой её страницу "
             "(`memory/<путь>.md`). Нет черты на странице — не выдумывай: открой ход по ссылке "
             "или оставь неопределённой. Тайное — в `*.GM_ONLY.md`.", "",
             "- Шики · `shiki` (поправки: `shiki-fixes`) · сцена · `SCENE` · часы мира · "
             "`threads/CLOCKS.GM_ONLY` · сюжет и события · `PLOT.GM_ONLY` · поправки к миру · `world-fixes`", ""]

    def role(entity):
        series = entity["character"].get("role")
        if series:
            return _text(series[-1]["v"], 80)
        look = entity["stable"].get("внешность")
        return _text(look[-1]["v"], 60) if look else ""

    def sort_key(entity):
        return (entity["id"] not in present, -_order(entity["last"])[0], -_order(entity["last"])[1],
                entity["name"])

    npcs = [e for e in store.values() if e["kind"] == "npc"]
    lines.append(f"## Персонажи ({len(npcs)})")
    for entity in sorted(npcs, key=sort_key):
        aliases = [a for a in entity["aliases"] if not a.isascii()][:2]
        alias = f" ({', '.join(aliases)})" if aliases else ""
        mark = " · в сцене" if entity["id"] in present else ""
        lines.append(f"- {entity['name']}{alias} — {role(entity)} · `npc/{entity['id']}`{mark}")
    threads = hot.get("threads") or {}
    if threads:
        lines += ["", f"## Линии ({len(threads)})"]
        for key, value in threads.items():
            state = _text((value or {}).get("state") if isinstance(value, dict) else value, 70)
            lines.append(f"- `{key}` — {state} · `threads/{key}`")
    things = set(hot.get("things") or {}) | set(registry["things"])
    if things:
        lines += ["", f"## Вещи ({len(things)})"]
        lines += [f"- `{key}` · `items/{key}`" for key in sorted(things)]
    return "\n".join(lines) + "\n"


CONTINUITY = re.compile(r"campaigns/shiki/continuity/[\w.-]+\.md")
TURN_REF = re.compile(r"s\d{2}\.text\.\d{3}")


def turn_notes(fixes):
    """Ходы, заменённые, отменённые или частично исправленные поправкой игрока."""
    notes = {}
    for fix in fixes:
        rule = str(fix.get("rule") or "")
        refs = TURN_REF.findall(rule)
        others = [r for r in refs if r != fix.get("turn")]
        replaced = CONTINUITY.search(rule)
        if replaced and re.search(r"замен", rule, re.I) and others:
            target, note = others[0], (f"Текст ведущего заменён поправкой игрока ({fix.get('turn')}): "
                                       f"`{replaced.group(0)}`.")
        elif re.search(r"ПРЯМАЯ ОТМЕНА|аннулирован\w* целиком", rule) and refs:
            target, note = refs[0], f"Ход отменён игроком ({fix.get('turn')}): {_text(rule, 300)}"
        elif re.search(r"ОТКАТ ОШИБКИ", rule) and refs:
            target, note = refs[0], f"Часть текста исправлена игроком ({fix.get('turn')}): {_text(rule, 300)}"
        else:
            continue
        if note not in notes.get(target, []):
            notes.setdefault(target, []).append(note)
    return notes


def _turn_files(state_dir, fixes=()):
    files = {}
    notes = turn_notes(fixes)
    for path in sorted((Path(state_dir) / "archive").glob("session-*-turns.jsonl")):
        for row in _jsonl(path):
            if not row.get("id"):
                continue
            session = _session(row["id"])
            text = [f"# Ход `{row['id']}`", "",
                    f"{row.get('at', '')} · {row.get('place', '')}", ""]
            for note in notes.get(row["id"], []):
                text += [f"> **{note}**", ""]
            meta = []
            if row.get("legacy_id"):
                legacy = row["legacy_id"]
                meta.append("прежний id: " + (", ".join(legacy) if isinstance(legacy, list) else legacy))
            if row.get("status"):
                meta.append(f"статус: {row['status']}")
            if row.get("source"):
                source = row["source"]
                meta.append("источник: " + (", ".join(source) if isinstance(source, list) else source))
            if meta:
                text += ["*" + " · ".join(meta) + "*", ""]
            if row.get("player"):
                text += ["## Игрок", row["player"], ""]
            text += ["## Ведущий", row.get("gm", ""), ""]
            files[f"{TURNS_DIR}/s{session:02}/{row['id']}.md"] = "\n".join(text)
    return files


def render(state_dir, hot, registry):
    store = registry["entities"]
    present = set((hot.get("scene") or {}).get("present") or [])
    files = {f"{DIR}/INDEX.md": _index_page(hot, registry),
             f"{DIR}/SCENE.md": _scene_page(hot, store),
             f"{DIR}/shiki.md": _shiki_page(hot, registry["fixes"]),
             f"{DIR}/shiki-fixes.md": _shiki_fixes_page(registry["fixes"]),
             f"{DIR}/threads/CLOCKS.GM_ONLY.md": _clocks_page(hot, store),
             f"{DIR}/PLOT.GM_ONLY.md": plot.page(hot)}
    for key, entity in store.items():
        folder = "npc" if entity["kind"] == "npc" else entity["kind"]
        files[f"{DIR}/{folder}/{key}.md"] = _npc_page(entity, present)
        if len(entity["knows"]) > KNOWS_ON_PAGE:
            files[f"{DIR}/{folder}/{key}.knows.md"] = _knows_page(entity)
        if entity.get("talk"):
            files.update(talk.pages(entity, entity["talk"], f"{DIR}/{folder}"))
        secret = _npc_gm_page(entity)
        if secret:
            files[f"{DIR}/{folder}/{key}.GM_ONLY.md"] = secret
    for key, value in (hot.get("threads") or {}).items():
        files[f"{DIR}/threads/{key}.md"] = _thread_page(key, value, registry["threads"].get(key, []))
    for key in set(hot.get("things") or {}) | set(registry["things"]):
        files[f"{DIR}/items/{key}.md"] = _item_page(key, (hot.get("things") or {}).get(key),
                                                   registry["things"].get(key, []))
    placed = {(f.get("turn"), f.get("rule")) for e in store.values() for f in e["fixes"]}
    placed |= {(f.get("turn"), f.get("rule")) for f in _shiki_fixes(registry["fixes"])}
    rest = [f for f in registry["fixes"] if (f.get("turn"), f.get("rule")) not in placed]
    lines = [f"# Поправки игрока к миру и ведению ({len(rest)})", "",
             "Те, что не относятся к одной странице. Более поздняя сильнее ранней.", ""]
    lines += [f"- {f.get('turn')}: {_text(f.get('rule'))}" for f in rest]
    files[f"{DIR}/world-fixes.md"] = "\n".join(lines) + "\n"
    files.update(_turn_files(state_dir, registry["fixes"]))
    return files


def write(state_dir, hot, source_root=None, new_turns=()):
    """Пересобирает вики и возвращает отчёт с замечаниями к новым ходам."""
    state_dir = Path(state_dir)
    registry = build(state_dir, hot, source_root)
    files = render(state_dir, hot, registry)
    old = set((_read_json(state_dir / MANIFEST, {}) or {}).get("files", []))
    written = 0
    for rel, body in files.items():
        path = state_dir / rel
        if not path.is_file() or path.read_text(encoding="utf-8") != body:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
            written += 1
    for rel in sorted(old - set(files)):
        stale = state_dir / rel
        if stale.is_file():
            stale.unlink()
    manifest = {"schema": SCHEMA, "cursor": registry["cursor"], "files": sorted(files)}
    (state_dir / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (state_dir / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                      encoding="utf-8")
    sizes = {rel: len(body.encode("utf-8")) for rel, body in files.items() if rel.startswith(DIR)}

    return {"entities": len(registry["entities"]), "files": len(files), "written": written,
            "index_bytes": sizes.get(f"{DIR}/INDEX.md", 0),
            "largest_page": max(sizes.items(), key=lambda kv: kv[1]) if sizes else None,
            "fixes_total": len(registry["fixes"]),
            "checks": turn_checks(registry, list(new_turns))}
