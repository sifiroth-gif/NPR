"""HOT v2: единое оперативное состояние текстовой кампании.

Мастер в чате записывает только короткий файл хода (``recovery/TURN.json``):
дословную заявку игрока, дословный видимый ответ, одну строку хроники и
необязательную дельту состояния. Всё остальное делает эта свёртка в CI:

* ``recovery/HOT.json``            — курсор, сцена, состав с мотивами и знанием,
                                     нити, закадровые часы, тело Шики, договор,
                                     хроника сессии, последний ответ дословно;
* ``archive/session-NNN-turns.jsonl`` — дословный журнал ходов, только дозапись;
* ``archive/session-NNN-chronicle.md`` — хроника сессии строкой на ход;
* ``recovery/cast/<ключ>.json``     — живая карточка каждого затронутого NPC;
* ``recovery/GM_ONLY.json``         — скрытая правда, закадровые часы и тайны NPC.
                                     В HOT этого нет: игрок может открыть HOT без спойлеров.

Мастер никогда не переписывает HOT целиком и не дублирует сцену пересказами.
Свёртка детерминирована и идемпотентна: ход с номером не больше курсора
пропускается, кроме явной правки последнего хода (``"amend": true``).
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import re
import subprocess
import zlib
from pathlib import Path

from . import continuity_guard, dread, npc_memory, prose_lint

SCHEMA = "npr-hot/2"
TURN_SCHEMA = "npr-turn/2"
HOT_PATH = "recovery/HOT.json"
TURN_PATH = "recovery/TURN.json"
LEGACY_PATH = "recovery/FAST_SCENE.json"
CAST_DIR = "recovery/cast"
SECRET_PATH = "recovery/GM_ONLY.json"   # скрытая правда, закадровые часы, тайны NPC: игроку не открывать
SECRET_SCHEMA = "npr-gm-only/2"
CHECKS_PATH = "recovery/CHECKS.json"   # замечания свёртки: ведущий читает перед новой сценой
CHECKS_KEEP = 16

TURN_ID = re.compile(r"s(\d{2})\.text\.(\d{3,4})")
WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря")

LINE_LIMIT = 320
KNOWS_LIMIT = 14
CHRONICLE_IN_HOT = 4
CANON_IN_HOT = 6
CANON_ARCHIVE = "archive/shiki-canon.jsonl"
LINT_KEEP = 3
RESOLVED_KEEP = 6
CALENDAR_AHEAD = 14
HOT_LIMIT = 72000   # байт; HOT читается один раз за чат, но расти без конца не должен

# Договор семи запоров: ленивый расчёт по игровому времени (PROGRESSION_RULES.json).
PACT_STAGES = (
    (0, "Спящий", "Только семь прерывистых колец и корни на внутренней стороне правого предплечья."),
    (8, "Первые сосудистые тени", "Потемневшие сосуды у правого запястья, новые корни; "
        "неясная тревога и эпизодическая слабая нота затхлой больницы только при близком контакте."),
    (22, "Бледность", "Стойкая бледность, тени под глазами; предчувствие мучительной смерти, "
         "при разговоре вблизи слабый реальный запах больничной затхлости и влажной земли."),
    (40, "Истощённый силуэт", "Заметная худоба без потери силы, тёмные вены, корни до плеча; "
         "в осознанной близости тихий ужас агонии, ощутимый но сдержанный запах старой больницы, земли и тления. "
         "НЕТ шлейфа или штрафа скрытности. Стадия 3: постепенные тёмные чакрочувствительные микросети в крови Шики от самого договора, независимо от чужих биоптатов; обычное свёртывание и сила сохранены."),
    (60, "Чёрные сосуды", "Тёмные сосуды на шее и корпусе, корни до шеи; кровь при ранах постепенно тёмно-бордовая, пока НЕ заразна; "
         "предчувствие болезненной смерти и запах только вблизи."),
    (78, "Активный контактный синдром", "Болезнь передаётся потенциально голой кожей к голой коже или свежей кровью прямо в открытую рану/на слизистую; "
         "кровь темнеет, нет воздушного/опосредованного пути и шлейфа."),
    (95, "Устойчивая тяжёлая форма", "Худоба, тёмные сосуды и корни на шее/корпусе; густо-тёмная кровь в глубине сгустка, инфекционна только при свежем прямом попадании в открытые ткани/слизистые, "
         "вблизи предчувствие агонии, без ауры-маяка."),
)
PACT_EVENT_POINTS = {
    "attempt": {"shiton": 0.6, "byoton": 0.9},
    "practice": {"shiton": 1.5, "byoton": 2.25},
    "mastery": {"shiton": 3.0, "byoton": 4.5},
    "cast_D_C": {"shiton": 1.0, "byoton": 1.5},
    "cast_B": {"shiton": 2.0, "byoton": 3.0},
    "cast_A": {"shiton": 3.5, "byoton": 5.25},
    "cast_S": {"shiton": 6.0, "byoton": 9.0},
    "overload": {"shiton": 1.5, "byoton": 2.25},
}


class TurnError(ValueError):
    """Файл хода не годится для свёртки."""


# --------------------------------------------------------------------------- время

def parse_at(value):
    try:
        return _dt.datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TurnError(f"некорректное игровое время: {value!r}") from exc


def heading(at, *, year_offset=1277):
    """Дата и день недели для шапки сцены считаются кодом, а не мастером."""
    moment = parse_at(at)
    return {
        "date": f"{WEEKDAYS[moment.weekday()]}, {moment.day} {MONTHS[moment.month - 1]} "
                f"{moment.year + year_offset} года",
        "time": f"{moment:%H:%M}",
    }


def turn_number(turn_id):
    match = TURN_ID.fullmatch(turn_id or "")
    if not match:
        raise TurnError(f"некорректный идентификатор хода: {turn_id!r}")
    return int(match[1]), int(match[2])


# --------------------------------------------------------------------------- договор

def pact_state(pact, at):
    """Пассивный рост 0.75/сутки от связывания плюс очки событий запретных школ."""
    pact = dict(pact or {})
    bound = pact.get("bound_at")
    if not bound:
        return pact
    days = max((parse_at(at) - parse_at(bound)).total_seconds(), 0) / 86400
    passive = round(float(pact.get("rate_per_day", 0.75)) * days, 4)
    events = round(sum(float(e.get("points", 0)) for e in pact.get("events", [])), 4)
    value = min(round(passive + events, 4), 100.0)
    stage = 0
    for index, (threshold, _label, _signs) in enumerate(PACT_STAGES):
        if value >= threshold:
            stage = index
    pact.update({"value": value, "stage": stage, "label": PACT_STAGES[stage][1],
                 "signs": PACT_STAGES[stage][2], "contact_hazard": stage >= 5})
    if stage + 1 < len(PACT_STAGES):
        pact["next_stage_at"] = PACT_STAGES[stage + 1][0]
    else:
        pact.pop("next_stage_at", None)
    return pact


def _pact_event(pact, event, at):
    event_id = event.get("id")
    kind, school = event.get("type"), event.get("school")
    if not event_id or kind not in PACT_EVENT_POINTS or school not in PACT_EVENT_POINTS[kind]:
        raise TurnError(f"некорректное событие договора: {event!r}")
    events = pact.setdefault("events", [])
    if any(e.get("id") == event_id for e in events):
        return
    events.append({"id": event_id, "at": at, "type": kind, "school": school,
                   "points": PACT_EVENT_POINTS[kind][school]})


# --------------------------------------------------------------------------- ход

def validate_turn(turn):
    if not isinstance(turn, dict) or turn.get("schema") != TURN_SCHEMA:
        raise TurnError(f"ожидается схема {TURN_SCHEMA}")
    turn_number(turn.get("id"))
    parse_at(turn.get("at"))
    ooc = turn.get("kind") == "ooc"
    for field in ("player",) + (() if ooc else ("gm", "place")):
        if not isinstance(turn.get(field), str) or not turn[field].strip():
            raise TurnError(f"пустое обязательное поле {field!r}")
    line = turn.get("line", "")
    if not isinstance(line, str) or len(line) > LINE_LIMIT:
        raise TurnError(f"строка хроники должна быть строкой до {LINE_LIMIT} знаков")
    if "d" in turn and not isinstance(turn["d"], dict):
        raise TurnError("дельта 'd' должна быть объектом")
    if isinstance(turn.get("d"), dict) and "scene" in turn["d"] and not isinstance(
            turn["d"]["scene"], dict):
        raise TurnError("дельта 'd.scene' должна быть объектом")
    return turn


def _shorten(text, limit=LINE_LIMIT):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    stop = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:stop + 1] if stop > limit // 2 else cut.rstrip(" ,;:") + "…")


def turn_from_legacy(fast):
    """Адаптер старого ``FAST_SCENE.json`` (npr-shiki-fast-scene/1)."""
    if not isinstance(fast, dict) or not isinstance(fast.get("latest_turn") or {}, dict) \
            or not isinstance(fast.get("cursor") or {}, dict):
        raise TurnError("старый файл сцены имеет неизвестное строение")
    latest = fast.get("latest_turn") or {}
    turn_id = latest.get("id") or (fast.get("cursor") or {}).get("last_turn_id")
    history = fast.get("history") if isinstance(fast.get("history"), list) else []
    summary = next((str(h.get("summary") or "") for h in reversed(history)
                    if isinstance(h, dict) and h.get("turn_id") == turn_id), "")
    if not summary and isinstance(latest.get("facts"), dict):
        summary = "; ".join(str(v) for v in latest["facts"].values())
    turn = {
        "schema": TURN_SCHEMA, "id": turn_id,
        "at": latest.get("at") or (fast.get("cursor") or {}).get("at"),
        "place": latest.get("location") or (fast.get("cursor") or {}).get("location", ""),
        "player": latest.get("player_input") or "",
        "gm": latest.get("exact_visible_gm_response") or "",
        "line": _shorten(summary), "legacy": True,
    }
    if latest.get("turn_kind") in ("ooc", "technical"):
        turn["kind"] = "ooc"
    return turn


# --------------------------------------------------------------------------- свёртка

def _merge(target, patch):
    """Словари сливаются рекурсивно, ``null`` удаляет ключ, ``ключ+`` дополняет список."""
    for key, value in patch.items():
        if key.endswith("+"):
            base = target.setdefault(key[:-1], [])
            for item in (value if isinstance(value, list) else [value]):
                if item not in base:
                    base.append(item)
        elif value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value
    return target


def _apply_delta(hot, delta, turn):
    turn_id, at = turn["id"], turn["at"]
    if "scene" in delta:
        _merge(hot.setdefault("scene", {}), delta["scene"])
    present = set(hot.get("scene", {}).get("present", []))
    for key, patch in (delta.get("cast") or {}).items():
        if patch is None:
            hot.get("cast", {}).pop(key, None)
            continue
        card = hot.setdefault("cast", {}).setdefault(key, {})
        _merge(card, patch)
        if len(card.get("knows", [])) > KNOWS_LIMIT:
            card["knows"] = card["knows"][-KNOWS_LIMIT:]
        card["seen"] = turn_id
    for key in present:
        if key in hot.get("cast", {}):
            hot["cast"][key]["seen"] = turn_id
    # Actor-scoped knowledge is source-attributed and has an explicit access
    # channel. Secret beliefs never enter player-readable HOT or cast cards.
    for evidence in delta.get("knowledge_events") or []:
        if not isinstance(evidence, dict):
            raise TurnError("knowledge_events must contain objects")
        actor = evidence.get("actor")
        if actor not in (hot.get("cast") or {}):
            raise TurnError(f"knowledge_events: unknown actor {actor!r}")
        if (not isinstance(evidence.get("claim"), str) or not evidence["claim"].strip()
                or not isinstance(evidence.get("source"), str) or not evidence["source"]
                or evidence.get("channel") not in
                ("message", "witness", "document", "inference", "told_in_scene")):
            raise TurnError("knowledge_events require claim, source and authentic channel")
        visibility = evidence.get("visibility", "player_known")
        if visibility not in ("player_known", "gm_only"):
            raise TurnError("knowledge_events visibility must be player_known or gm_only")
        entry = {k: evidence[k] for k in
                 ("actor", "claim", "source", "channel", "certainty") if k in evidence}
        entry["turn"] = turn_id
        entry["visibility"] = visibility
        if visibility == "gm_only":
            records = hot.setdefault("_npc_knowledge", {}).setdefault(actor, [])
        else:
            records = hot["cast"][actor].setdefault("knowledge_provenance", [])
        if not any(v.get("source") == entry["source"] and
                   v.get("claim") == entry["claim"] for v in records):
            records.append(entry)
        if len(records) > 32:
            del records[:-32]
    for key, patch in (delta.get("threads") or {}).items():
        threads = hot.setdefault("threads", {})
        if patch is None or (isinstance(patch, dict) and patch.get("closed")):
            closed = threads.pop(key, {})
            if isinstance(patch, dict):
                closed.update(patch)
            hot.setdefault("offscreen", {}).setdefault("resolved", []).append(
                {"id": f"thread:{key}", "at": at, "turn": turn_id,
                 "outcome": closed.get("closed") or closed.get("state", "")})
        else:
            _merge(threads.setdefault(key, {}), patch if isinstance(patch, dict) else {"state": patch})
            threads[key]["turn"] = turn_id
    offscreen = hot.setdefault("offscreen", {})
    for key, patch in (delta.get("clocks") or {}).items():
        clocks = offscreen.setdefault("clocks", {})
        if patch is None or (isinstance(patch, dict) and ("done" in patch or patch.get("closed"))):
            done = clocks.pop(key, {})
            offscreen.setdefault("resolved", []).append(
                {"id": f"clock:{key}", "at": at, "turn": turn_id,
                 "outcome": (patch or {}).get("done", "снято")
                 if isinstance(patch, dict) else "снято", "what": done.get("what", "")})
        else:
            if "due" in patch:
                parse_at(patch["due"])
            _merge(clocks.setdefault(key, {}), patch)
    if "truth" in delta:
        _merge(offscreen.setdefault("truth", {}), delta["truth"])
    if "shiki" in delta:
        _merge(hot.setdefault("shiki", {}), delta["shiki"])
    for event in delta.get("pact_events") or []:
        _pact_event(hot.setdefault("pact", {}), event, at)
    for item in delta.get("canon+") or []:
        entry = {"turn": turn_id, "rule": item}
        if entry not in hot.setdefault("canon", []):
            hot["canon"].append(entry)
    offscreen["resolved"] = offscreen.get("resolved", [])[-RESOLVED_KEEP:]


def apply_turn(hot, turn):
    """Применяет один ход. Возвращает ``True``, если состояние изменилось."""
    validate_turn(turn)
    session, number = turn_number(turn["id"])
    cursor = hot.setdefault("cursor", {})
    current = (int(hot.get("session", 0)), int(cursor.get("seq", 0)))
    amend = bool(turn.get("amend")) and (session, number) == current
    if (session, number) <= current and not amend:
        return False
    if amend:
        # Undo the previous version fully before applying its replacement.
        undo = hot.get("_undo") or {}
        if undo.get("id") != turn["id"] or not undo.get("snapshot"):
            raise TurnError(f"{turn['id']}: no verified pre-turn state for safe amendment")
        try:
            previous = json.loads(zlib.decompress(
                base64.b64decode(undo["snapshot"], validate=True)).decode("utf-8"))
        except (ValueError, zlib.error, UnicodeDecodeError, TypeError) as exc:
            raise TurnError(f"{turn['id']}: invalid amendment rollback snapshot") from exc
        candidate = previous
    else:
        candidate = hot

    # Validate against the *predecessor* before mutating anything. A gap or
    # backwards timestamp must never be represented as a merely logged warning.
    before_cursor = candidate.get("cursor", {})
    before_session = int(candidate.get("session", 0))
    before_seq = int(before_cursor.get("seq", 0))
    if (session != before_session or number != before_seq + 1) and not (
            session == before_session + 1 and number == 1):
        raise TurnError(f"{turn['id']}: sequence gap after {before_cursor.get('id')}")
    if before_cursor.get("at") and parse_at(turn["at"]) < parse_at(before_cursor["at"]):
        raise TurnError(f"{turn['id']}: game time precedes {before_cursor['at']}")

    # Once a scene has a verified anchor, no new in-world turn may silently
    # inherit the positions and present NPCs from an unrelated old scene.
    # A small scene patch is cheaper than debugging contradictory HOT memory.
    scene_anchor = (candidate.get("scene") or {}).get("as_of")
    if turn.get("kind") != "ooc" and scene_anchor and not isinstance(
            (turn.get("d") or {}).get("scene"), dict):
        raise TurnError(f"{turn['id']}: d.scene is required after {scene_anchor}")

    if amend:
        hot.clear()
        hot.update(previous)
        hot["_undo"] = undo
    else:
        # A compact private snapshot preserves exact pre-turn state for one
        # amendment. Older turns require a separate, sourced correction.
        prior = {k: v for k, v in hot.items() if k != "_undo"}
        raw = json.dumps(prior, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        hot["_undo"] = {"id": turn["id"], "snapshot":
                        base64.b64encode(zlib.compress(raw, level=6)).decode("ascii")}

    cursor = hot.setdefault("cursor", {})
    current = (int(hot.get("session", 0)), int(cursor.get("seq", 0)))

    ooc = turn.get("kind") == "ooc"
    if session != current[0]:
        hot.setdefault("sessions_closed", []).append(
            {"session": current[0], "last": cursor.get("id"),
             "chronicle": f"archive/session-{current[0]:03}-chronicle.md"})
        hot["chronicle"] = []
        hot["session"] = session
        # The verified intersession marker belongs only to the cold-start
        # boundary. Remove it after the FIRST exact new-session turn.
        hot.pop("handoff", None)

    line = turn.get("line") or _shorten("Игрок: " + turn["player"], 200)
    entry = {"id": turn["id"], "at": turn["at"], "line": line}
    if ooc:
        entry["ooc"] = True
    chronicle = hot.setdefault("chronicle", [])
    if amend and chronicle and chronicle[-1]["id"] == turn["id"]:
        chronicle[-1] = entry
    else:
        chronicle.append(entry)

    if not ooc:
        cursor.update({"id": turn["id"], "seq": number, "at": turn["at"], "place": turn["place"],
                       "awaiting": turn.get("awaiting", "player")})
        cursor.update(heading(turn["at"], year_offset=hot.get("calendar", {}).get("year_offset", 1277)))
        hot["last"] = {"id": turn["id"], "player": turn["player"], "gm": turn["gm"]}
        report = prose_lint.lint(turn["gm"])
        lint = hot.setdefault("lint", {})
        recent = [r for r in lint.get("recent", []) if r["id"] != turn["id"]]
        recent.append({"id": turn["id"], "score": report["score"], "verdict": report["verdict"],
                       "hits": [{"rule": h["rule"], "quote": h["quote"]} for h in report["hits"][:6]]})
        lint["recent"] = recent[-LINT_KEEP:]
        totals = lint.setdefault("session_totals", {})
        for rule, count in report["by_rule"].items():
            totals[rule] = totals.get(rule, 0) + count
        lint["note"] = prose_lint.GM_NOTE
    else:
        cursor["seq"] = number
        cursor["id"] = turn["id"]

    delta = turn.get("d") or {}
    # Психика, отношения, чувство и намерения NPC не попадают в открытую карточку.
    public_delta, inner = npc_memory.split_patch(hot, delta, turn["id"])
    _apply_delta(hot, public_delta, turn)
    if not ooc and isinstance(delta.get("scene"), dict):
        hot.setdefault("scene", {})["as_of"] = turn["id"]
    continuity_guard.apply_things(hot, delta, turn["id"], turn["at"])
    npc_memory.apply(hot, turn, inner)
    dread.after_turn(hot, turn, inner, delta)
    continuity_guard.check_turn(hot, turn)
    refresh(hot)
    return True


def refresh(hot):
    """Производные поля: договор, просроченные закадровые часы."""
    at = hot.get("cursor", {}).get("at")
    if not at:
        return hot
    if hot.get("pact"):
        hot["pact"] = pact_state(hot["pact"], at)
    # Календарь на две недели вперёд: мастер не считает дни недели в уме.
    calendar = hot.setdefault("calendar", {})
    offset = calendar.get("year_offset", 1277)
    start = parse_at(at)
    calendar["ahead"] = [heading((start + _dt.timedelta(days=shift)).isoformat(), year_offset=offset)["date"]
                         for shift in range(1, CALENDAR_AHEAD + 1)]
    offscreen = hot.setdefault("offscreen", {})
    now = parse_at(at)
    due = sorted((key for key, clock in offscreen.get("clocks", {}).items()
                  if clock.get("due") and not clock.get("closed") and parse_at(clock["due"]) <= now),
                 key=lambda key: offscreen["clocks"][key]["due"])
    offscreen["due_now"] = due
    npc_memory.refresh(hot)
    if len(hot.get("_checks") or []) > CHECKS_KEEP:
        hot["_checks"] = hot["_checks"][-CHECKS_KEEP:]
    return hot


# --------------------------------------------------------------------------- файлы

def _git(state_dir, *args):
    result = subprocess.run(["git", "-C", str(state_dir), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git failed")
    return result.stdout


def _history(state_dir, path, since):
    """Версии файла от старых к новым после коммита ``since`` (или все)."""
    try:
        _git(state_dir, "rev-parse", "--git-dir")
    except (RuntimeError, FileNotFoundError):
        return []
    span = ["HEAD"]
    if since:
        try:
            _git(state_dir, "cat-file", "-e", f"{since}^{{commit}}")
            span = [f"{since}..HEAD"]
        except RuntimeError:
            pass
    try:
        commits = _git(state_dir, "log", "--format=%H", "--reverse", *span, "--", path).split()
    except RuntimeError:
        return []
    versions = []
    for commit in commits:
        try:
            versions.append((commit, json.loads(_git(state_dir, "show", f"{commit}:{path}"))))
        except (RuntimeError, json.JSONDecodeError):
            continue
    return versions


def _head(state_dir):
    try:
        return _git(state_dir, "rev-parse", "HEAD").strip()
    except (RuntimeError, FileNotFoundError):
        return ""


def _normalize_recorded_turn(raw):
    """Backwards-compatible projection for previously published turn metadata.

    Full player/GM prose remains verbatim. Only the derived chronicle summary
    may be shortened. An opening scene without a player message uses an
    explicit *non-quotation* marker rather than inventing a player's words.
    Direct validate_turn() stays strict for new producers.
    """
    if not isinstance(raw, dict):
        return raw
    turn = dict(raw)
    line = turn.get("line")
    if isinstance(line, str) and len(line) > LINE_LIMIT:
        turn["line"] = _shorten(line)
    ident = turn.get("id")
    if (isinstance(ident, str) and ident.endswith(".text.001")
            and turn.get("player") == "" and isinstance(turn.get("gm"), str)
            and turn["gm"].strip()):
        turn["player"] = "[Начальная сцена ведущего; сообщения игрока не было]"
    return turn


def collect_turns(state_dir, hot):
    """Новые ходы из истории TURN.json, рабочей копии и старого FAST_SCENE.json."""
    state_dir = Path(state_dir)
    since = hot.get("fold", {}).get("commit", "")
    found, errors = {}, []

    def take(raw, origin, legacy=False):
        try:
            turn = _normalize_recorded_turn(turn_from_legacy(raw) if legacy else raw)
            validate_turn(turn)
        except TurnError as exc:
            errors.append(f"{origin}: {exc}")
            return
        found[turn_number(turn["id"])] = turn

    for commit, raw in _history(state_dir, LEGACY_PATH, since):
        if raw.get("format", "").startswith("npr-shiki-fast-scene"):
            take(raw, f"{LEGACY_PATH}@{commit[:7]}", legacy=True)
    legacy = state_dir / LEGACY_PATH
    if legacy.is_file():
        take(json.loads(legacy.read_text(encoding="utf-8")), LEGACY_PATH, legacy=True)
    for commit, raw in _history(state_dir, TURN_PATH, since):
        take(raw, f"{TURN_PATH}@{commit[:7]}")
    current = state_dir / TURN_PATH
    if current.is_file():
        try:
            take(json.loads(current.read_text(encoding="utf-8")), TURN_PATH)
        except json.JSONDecodeError as exc:
            errors.append(f"{TURN_PATH}: не JSON ({exc})")
    return [found[key] for key in sorted(found)], errors


def _turn_digest(turn):
    """Content identity independent of formatting and Git commit."""
    encoded = json.dumps(turn, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dump(data):
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def _append_log(state_dir, session, turns):
    path = Path(state_dir) / f"archive/session-{session:03}-turns.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                rows[row["id"]] = row
    for turn in turns:
        rows[turn["id"]] = {k: turn[k] for k in ("id", "at", "place", "player", "gm", "line", "kind", "roll")
                            if k in turn}
    ordered = sorted(rows.values(), key=lambda row: turn_number(row["id"]))
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ordered),
                    encoding="utf-8")
    return path


def _write_chronicle(state_dir, hot, full):
    session = int(hot["session"])
    path = Path(state_dir) / f"archive/session-{session:03}-chronicle.md"
    lines = [f"# Сессия {session}: хроника по ходам", ""]
    lines += [f"- `{e['id']}` {e['at']} — {e['line']}" for e in full]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _full_chronicle(state_dir, hot):
    """Полная хроника сессии: архивный файл плюс хвост из HOT."""
    session = int(hot["session"])
    path = Path(state_dir) / f"archive/session-{session:03}-chronicle.md"
    entries = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"- `([^`]+)` (\S+) — (.*)", raw)
            if match:
                entries[match[1]] = {"id": match[1], "at": match[2], "line": match[3]}
    for entry in hot.get("chronicle", []):
        entries[entry["id"]] = entry
    return sorted(entries.values(), key=lambda e: turn_number(e["id"]))


def _archive_canon(state_dir, hot):
    """Keep the full, source-attributed canon outside the small live packet.

    This retains every approved correction in a durable JSONL archive. HOT
    carries only recent corrections plus a source path; older rules remain
    retrievable and are never inferred from summaries.
    """
    path = Path(state_dir) / CANON_ARCHIVE
    collected = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                collected[(entry["turn"], entry["rule"])] = entry
    for entry in hot.get("canon", []):
        collected[(entry["turn"], entry["rule"])] = entry
    if collected:
        path.parent.mkdir(parents=True, exist_ok=True)
        contents = "".join(json.dumps(item, ensure_ascii=False) + "\n"
                           for item in collected.values())
        if not path.exists() or path.read_text(encoding="utf-8") != contents:
            path.write_text(contents, encoding="utf-8")
    if len(hot.get("canon", [])) > CANON_IN_HOT:
        hot["canon"] = hot["canon"][-CANON_IN_HOT:]
    if path.exists() and collected:
        hot["canon_older"] = CANON_ARCHIVE


def _trim_inactive_cast(hot):
    """Cards are already persisted in recovery/cast, so keep only scene actors."""
    present = set((hot.get("scene") or {}).get("present") or [])
    cast = hot.get("cast") or {}
    if len(cast) > len(present):
        hot["cast"] = {key: value for key, value in cast.items() if key in present}
        hot["cast_older"] = CAST_DIR


def _read_state(state_dir):
    """HOT и закрытая половина сливаются в одно состояние в памяти."""
    state_dir = Path(state_dir)
    hot_path = state_dir / HOT_PATH
    if not hot_path.is_file():
        raise ValueError(f"нет {HOT_PATH}: сначала выполните миграцию")
    raw_hot = hot_path.read_text(encoding="utf-8")
    hot = json.loads(raw_hot)
    if hot.get("schema") != SCHEMA:
        raise ValueError(f"{HOT_PATH}: ожидается схема {SCHEMA}")
    secret_path = state_dir / SECRET_PATH
    raw_secret = secret_path.read_text(encoding="utf-8") if secret_path.is_file() else ""
    secret = json.loads(raw_secret) if raw_secret else {}
    offscreen = secret.get("offscreen") or {"truth": {}, "clocks": {}, "resolved": []}
    if "truth_zlib64" in offscreen:
        # Rehydrate complete hidden facts; the readable preview is not a
        # second source of truth. Verify it against the complete payload.
        encoded = offscreen["truth_zlib64"]
        payload = zlib.decompress(base64.b64decode(encoded, validate=True))
        loaded = json.loads(payload.decode("utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("compressed GM_ONLY truth is not an object")
        preview = offscreen.get("truth_preview", {})
        if not isinstance(preview, dict) or any(loaded.get(k) != v for k, v in preview.items()):
            raise ValueError("GM_ONLY truth preview does not match full archive")
        if offscreen.get("truth_index") and list(loaded) != offscreen["truth_index"]:
            raise ValueError("GM_ONLY truth index differs from full archive")
        offscreen = dict(offscreen)
        offscreen.pop("truth_zlib64")
        offscreen.pop("truth_preview", None)
        offscreen.pop("truth_index", None)
        offscreen["truth"] = loaded
    hot["offscreen"] = offscreen
    hot["_hides"] = dict(secret.get("cast") or {})
    hot["_npc_knowledge"] = dict(secret.get("npc_knowledge") or {})
    hot["_undo"] = secret.get("undo")
    hot["_psyche"] = dict(secret.get("psyche") or {})
    hot["_checks"] = list(secret.get("checks") or [])
    hot["_horror"] = dict(secret.get("horror") or {})
    for key, hidden in hot["_hides"].items():
        if key in hot.get("cast", {}):
            hot["cast"][key]["hides"] = hidden
    return hot, (raw_hot, raw_secret)


def _split_state(hot):
    """Открытая часть для HOT.json и закрытая для GM_ONLY.json."""
    public = json.loads(json.dumps(hot))
    hides = public.pop("_hides", {})
    npc_knowledge = public.pop("_npc_knowledge", {})
    undo = public.pop("_undo", None)
    psyche = public.pop("_psyche", {})
    checks = public.pop("_checks", [])
    horror = public.pop("_horror", {})
    public.pop("_mem_log", None)
    for key, card in public.get("cast", {}).items():
        if "hides" in card:
            hides[key] = card.pop("hides")
    offscreen = public.pop("offscreen", {})
    # The secret facts grow across dozens of sessions. Store them losslessly
    # compressed, not by dropping older hidden evidence or NPC motives.
    truth = offscreen.get("truth")
    if isinstance(truth, dict):
        truth_bytes = json.dumps(truth, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
        if len(truth_bytes) > 4096:
            # Recent entries are kept readable to GitHub-only GMs; all older
            # material remains compressed in the same authenticated file.
            recent = {}
            budget = 6800
            for k, v in reversed(list(truth.items())):
                entry_bytes = len(json.dumps({k: v}, ensure_ascii=False,
                                separators=(",", ":")).encode("utf-8"))
                if entry_bytes > budget:
                    continue
                recent[k] = v
                budget -= entry_bytes
                if len(recent) >= 8:
                    break
            offscreen = dict(offscreen)
            offscreen.pop("truth")
            offscreen["truth_zlib64"] = base64.b64encode(
                zlib.compress(truth_bytes, level=9)).decode("ascii")
            offscreen["truth_preview"] = dict(reversed(list(recent.items())))
            offscreen["truth_index"] = list(truth)
    public["offscreen"] = {"see": SECRET_PATH, "due": len(offscreen.get("due_now", [])),
                           "clocks": len(offscreen.get("clocks", {}))}
    secret = {"schema": SECRET_SCHEMA,
              "access": "ТОЛЬКО МАСТЕР. Игроку не показывать и не пересказывать, пока это не сыграно в сцене.",
              "cursor": public.get("cursor", {}).get("id"), "offscreen": offscreen,
              "cast": hides, "npc_knowledge": npc_knowledge, "psyche": psyche,
              "checks": checks, "horror": horror, "undo": undo}
    return public, secret


def _write_state(state_dir, hot, before=("", "")):
    state_dir = Path(state_dir)
    public, secret = _split_state(hot)
    texts = (_dump(public), _dump(secret))
    for path, text, old in zip((HOT_PATH, SECRET_PATH), texts, before):
        if text != old:
            (state_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (state_dir / path).write_text(text, encoding="utf-8")
    return texts, texts != tuple(before)


def _write_cast(state_dir, hot):
    """Карточка каждого NPC из состава. Убранный из HOT персонаж остаётся в файле."""
    cast_dir = Path(state_dir) / CAST_DIR
    cast_dir.mkdir(parents=True, exist_ok=True)
    for key, card in hot.get("cast", {}).items():
        target = cast_dir / f"{key}.json"
        desired = _dump({"schema": "npr-cast-card/2", "key": key,
                         **{k: v for k, v in card.items() if k != "hides"}})
        if not target.is_file() or target.read_text(encoding="utf-8") != desired:
            target.write_text(desired, encoding="utf-8")


def _recall_cast(state_dir, hot, turn):
    """Возвращает в состав отложенную карточку, если ход снова трогает этого NPC."""
    delta = turn.get("d") or {}
    wanted = set(delta.get("cast") or {}) | set((delta.get("scene") or {}).get("present") or [])
    for key in wanted:
        target = Path(state_dir) / CAST_DIR / f"{key}.json"
        if key not in hot.get("cast", {}) and target.is_file():
            card = json.loads(target.read_text(encoding="utf-8"))
            card.pop("schema", None)
            card.pop("key", None)
            if key in hot.get("_hides", {}):
                card["hides"] = hot["_hides"][key]
            hot.setdefault("cast", {})[key] = card


def _amend_base(hot, turn):
    """Состояние до исправляемого хода: правку сверяют с ним, а не с ней самой."""
    undo = hot.get("_undo") or {}
    if undo.get("id") != turn.get("id") or not undo.get("snapshot"):
        return hot
    try:
        return json.loads(zlib.decompress(
            base64.b64decode(undo["snapshot"], validate=True)).decode("utf-8"))
    except (ValueError, zlib.error, UnicodeDecodeError, TypeError):
        return hot


def _archived_cards(state_dir):
    cast_dir = Path(state_dir) / CAST_DIR
    out = []
    for path in sorted(cast_dir.glob("*.json")) if cast_dir.is_dir() else ():
        try:
            out.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    return out


def _write_checks(state_dir, hot):
    """Короткий открытый файл замечаний: читается за один запрос перед новой сценой."""
    payload = {"schema": "npr-checks/1", "cursor": (hot.get("cursor") or {}).get("id"),
               "fold_errors": (hot.get("fold") or {}).get("errors", []),
               "checks": hot.get("_checks", []),
               "rule": "Сцена уже показана игроку. Расхождение исправляется следующим ходом "
                       "(d.things, d.cast, amend), а не молчаливым переписыванием сыгранного."}
    path = Path(state_dir) / CHECKS_PATH
    body = _dump(payload)
    if not path.is_file() or path.read_text(encoding="utf-8") != body:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def ensure_npc_memory(state_dir, hot):
    """Разовое восстановление памяти NPC из сыгранного и наполнение карточек.

    Запускается из каждого ``fold``: если архива эпизодов ещё нет, он строится
    по дословным журналам; карточка без ``memory_total`` получает ранние и
    последние эпизоды. Повторный вызов ничего не меняет.
    """
    state_dir = Path(state_dir)
    report = {}
    if not (state_dir / npc_memory.MEMORY_DIR).is_dir() and (state_dir / "archive").is_dir():
        report = npc_memory.backfill(state_dir)
    for key, card in (hot.get("cast") or {}).items():
        if "memory_total" not in card:
            npc_memory.hydrate_card(card, npc_memory.load_episodes(state_dir, key))
    if "motifs" not in (hot.get("_horror") or {}):
        rows = []
        for path in sorted((state_dir / "archive").glob("session-*-turns.jsonl"))[-2:]:
            rows += [json.loads(r) for r in path.read_text(encoding="utf-8").splitlines() if r.strip()]
        rows.sort(key=lambda r: npc_memory._order(r.get("id", "")))
        dread.seed_motifs(hot, rows)
    cast_dir = state_dir / CAST_DIR
    for path in sorted(cast_dir.glob("*.json")) if cast_dir.is_dir() else ():
        if path.stem in (hot.get("cast") or {}):
            continue
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if "memory_total" not in card:
            episodes = npc_memory.load_episodes(state_dir, path.stem)
            if episodes:
                npc_memory.hydrate_card(card, episodes)
                path.write_text(_dump(card), encoding="utf-8")
    return report


def fold(state_dir):
    """Сворачивает новые ходы в HOT и архивы. Возвращает краткий отчёт."""
    state_dir = Path(state_dir)
    hot, before = _read_state(state_dir)
    ensure_npc_memory(state_dir, hot)
    turns, errors = collect_turns(state_dir, hot)
    applied = []
    amended = []
    archived = None
    for turn in turns:
        try:
            # Idempotency must precede *any* cast rehydration or file writes.
            # A replayed TURN.json may contain cast: {npc: null}: restoring
            # the NPC before apply_turn skips the old ID resurrects stale cast.
            incoming = turn_number(turn["id"])
            current = (int(hot.get("session", 0)), int(hot.get("cursor", {}).get("seq", 0)))
            fingerprint = _turn_digest(turn)
            amendment = bool(turn.get("amend")) and incoming == current
            if incoming <= current and (
                    not amendment or hot.get("fold", {}).get("turn_digest") == fingerprint):
                continue
            # Проверка источников, знаний NPC и наступивших сроков. Сцена уже
            # показана игроку: отказ свернуть её лишь разорвал бы память. Поэтому
            # содержательные замечания записываются, а не останавливают свёртку.
            # Блокируют только структурные ошибки apply_turn (разрыв, время назад).
            from . import live_scene, scene_gate
            policy_path = state_dir / live_scene.POLICY_PATH
            policy = (json.loads(policy_path.read_text(encoding="utf-8"))
                      if policy_path.is_file() else {"enabled": False})
            _, primary = scene_gate.played_turns(state_dir)
            sources = [x for x in primary if x["id"] != turn["id"]]
            sources += [x for x in applied if x["id"] != turn["id"]]
            base = _amend_base(hot, turn) if amendment else hot
            review_notes = live_scene.validate_admission(
                base, turn, policy=policy, accepted=sources)
            if archived is None:
                archived = _archived_cards(state_dir)
            name_notes = npc_memory.check_names(base, turn.get("d") or {}, turn["id"], archived)
            _recall_cast(state_dir, hot, turn)
            if any(v is None for v in ((turn.get("d") or {}).get("cast") or {}).values()):
                # Перед тем как убрать NPC из состава, сохраняем его карточку и тайну.
                _write_cast(state_dir, hot)
                for key, card in hot.get("cast", {}).items():
                    if "hides" in card:
                        hot.setdefault("_hides", {})[key] = card["hides"]
            if apply_turn(hot, turn):
                hot.setdefault("fold", {})["turn_digest"] = fingerprint
                applied.append(turn)
                if amendment:
                    amended.append(turn["id"])
                checks = hot.setdefault("_checks", [])
                for note in review_notes:
                    checks.append({"turn": turn["id"], "kind": "review", "text": note})
                for note in name_notes:
                    checks.append({"turn": turn["id"], "kind": "name", "text": note})
        except TurnError as exc:
            errors.append(f"{turn.get('id')}: {exc}")
    # Never publish a partly accepted batch. All turns remain in Git history
    # and will be replayed after the rejected entry is corrected. The refusal
    # itself IS published, so the GM sees it instead of a silently stale HOT.
    if errors:
        stale, stale_before = _read_state(state_dir)
        stale.setdefault("fold", {})["errors"] = errors
        _write_state(state_dir, stale, stale_before)
        _write_checks(state_dir, stale)
        return {"status": "rejected", "cursor": stale["cursor"].get("id"),
                "applied": [], "errors": errors, "due": 0}
    refresh(hot)
    fold_info = hot.setdefault("fold", {})
    fold_info["errors"] = []
    if applied:
        fold_info["commit"] = _head(state_dir) or fold_info.get("commit", "")
        fold_info["turns"] = fold_info.get("turns", 0) + len(applied)
    by_session = {}
    for turn in applied:
        by_session.setdefault(turn_number(turn["id"])[0], []).append(turn)
    for session, batch in by_session.items():
        _append_log(state_dir, session, batch)
    full = _full_chronicle(state_dir, hot)
    _write_chronicle(state_dir, hot, full)
    if len(hot.get("chronicle", [])) > CHRONICLE_IN_HOT:
        hot["chronicle"] = hot["chronicle"][-CHRONICLE_IN_HOT:]
        hot["chronicle_older"] = f"archive/session-{int(hot['session']):03}-chronicle.md"
    npc_memory.flush(state_dir, hot, amended=amended)
    npc_memory.sync_cards(state_dir, hot)
    _write_cast(state_dir, hot)
    _archive_canon(state_dir, hot)
    _trim_inactive_cast(hot)
    _write_checks(state_dir, hot)
    if applied or ((state_dir / "recovery/GATE_POLICY.json").is_file() and
                   not (hot.get("scene") or {}).get("npc_disclosures_to_player")):
        # Cache source pointers even for an existing folded campaign when
        # activating the integrated pre-scene gate.
        from . import scene_gate
        _, accepted = scene_gate.played_turns(state_dir)
        hot.setdefault("scene", {})["npc_disclosures_to_player"] = (
            scene_gate.compact_disclosure_cues(hot, accepted))
    texts, changed = _write_state(state_dir, hot, before)
    # Build even for an older already-folded checkpoint when a new policy
    # activates the cache. No revision, source canon or game clock is changed.
    from . import live_scene, scene_gate
    brief_path = state_dir / live_scene.PATH
    # Rebuild the bounded derived packet on every fold, including policy and
    # engine-only updates. write() avoids changing bytes when it is identical.
    _, accepted = scene_gate.played_turns(state_dir)
    # Canonical entity cards are derived from reviewed seed + played turn deltas.
    # They include patients/places/items omitted from scene.present NPC roster.
    from . import entity_memory
    entity_memory.rebuild(state_dir)
    live_scene.write(brief_path, live_scene.build_for_state(hot, accepted, state_dir))
    # Historical recall is refreshed by the *same* existing fold and committed
    # in archive/ with the verbatim journal; no detached optional sidecar job.
    heuristic = None
    if (state_dir / "archive").is_dir():
        from . import heuristic_history, recall_audit
        heuristic = heuristic_history.save(
            Path(__file__).resolve().parents[1], state_dir)
        # Validate coverage of every existing session, source-shard checksums,
        # and warm search timings in the same ordinary gameplay fold.
        report = recall_audit.audit(state_dir)
        stats_path = state_dir / "archive/heuristic-recall/audit.json"
        stats_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
    return {"status": "updated" if changed else "current",
            "cursor": hot["cursor"].get("id"), "applied": [t["id"] for t in applied],
            "errors": errors, "due": len(hot["offscreen"].get("due_now", [])),
            "bytes": {"hot": len(texts[0].encode("utf-8")), "gm_only": len(texts[1].encode("utf-8"))},
            "heuristic_history": heuristic,
            "lint": [(r["id"], r["verdict"]) for r in hot.get("lint", {}).get("recent", [])]}


def bootstrap(state_dir, seed):
    """Разовая миграция со старого ``FAST_SCENE.json``: без потерь и без правки старых файлов.

    ``seed`` — заготовка HOT (состав, нити, закадровое, тело, договор, канон и строки хроники,
    написанные по сыгранному). Курсор, последний ответ и дословный журнал берутся из истории git.
    """
    state_dir = Path(state_dir)
    hot = json.loads(json.dumps(seed))
    hot["schema"] = SCHEMA
    session = int(hot["session"])
    versions = [raw for _commit, raw in _history(state_dir, LEGACY_PATH, "")]
    legacy_file = state_dir / LEGACY_PATH
    if legacy_file.is_file():
        versions.append(json.loads(legacy_file.read_text(encoding="utf-8")))
    turns = {}
    for raw in versions:
        try:
            turn = validate_turn(turn_from_legacy(raw))
        except TurnError:
            continue
        if turn_number(turn["id"])[0] == session:
            turns[turn["id"]] = turn
    if not turns:
        raise ValueError(f"в истории {LEGACY_PATH} нет ходов сессии {session}")
    ordered = sorted(turns.values(), key=lambda t: turn_number(t["id"]))
    lines = {e["id"]: e for e in hot.get("chronicle", [])}
    for turn in ordered:
        if turn["id"] in lines:
            turn["line"] = lines[turn["id"]]["line"]
        else:
            lines[turn["id"]] = {"id": turn["id"], "at": turn["at"], "line": turn["line"]}
    hot["chronicle"] = sorted(lines.values(), key=lambda e: turn_number(e["id"]))
    latest = ordered[-1]
    number = turn_number(latest["id"])[1]
    offset = hot.setdefault("calendar", {}).setdefault("year_offset", 1277)
    hot["cursor"] = {"id": latest["id"], "seq": number, "at": latest["at"], "place": latest["place"],
                     "awaiting": "player", **heading(latest["at"], year_offset=offset)}
    hot["last"] = {"id": latest["id"], "player": latest["player"], "gm": latest["gm"]}
    totals, recent = {}, []
    for turn in ordered:
        report = prose_lint.lint(turn["gm"])
        for rule, count in report["by_rule"].items():
            totals[rule] = totals.get(rule, 0) + count
        recent.append({"id": turn["id"], "score": report["score"], "verdict": report["verdict"],
                       "hits": [{"rule": h["rule"], "quote": h["quote"]} for h in report["hits"][:6]]})
    hot["lint"] = {"recent": recent[-LINT_KEEP:], "session_totals": totals, "note": prose_lint.GM_NOTE}
    present = hot.get("scene", {}).get("present", [])
    for key in present:
        if key in hot.get("cast", {}):
            hot["cast"][key]["seen"] = latest["id"]
    hot["fold"] = {"commit": _head(state_dir), "turns": len(hot["chronicle"]), "errors": [], "warnings": [],
                   "migrated_from": LEGACY_PATH, "verbatim_turns": len(ordered)}
    refresh(hot)
    _append_log(state_dir, session, ordered)
    _write_chronicle(state_dir, hot, hot["chronicle"])
    if len(hot["chronicle"]) > CHRONICLE_IN_HOT:
        hot["chronicle"] = hot["chronicle"][-CHRONICLE_IN_HOT:]
        hot["chronicle_older"] = f"archive/session-{session:03}-chronicle.md"
    _write_cast(state_dir, hot)
    _archive_canon(state_dir, hot)
    _trim_inactive_cast(hot)
    texts, _changed = _write_state(state_dir, hot)
    return {"status": "bootstrapped", "cursor": hot["cursor"]["id"], "verbatim_turns": len(ordered),
            "chronicle": len(hot["chronicle"]),
            "bytes": {"hot": len(texts[0].encode("utf-8")), "gm_only": len(texts[1].encode("utf-8"))},
            "pact": {k: hot["pact"].get(k) for k in ("value", "stage", "label")} if hot.get("pact") else None,
            "due": len(hot["offscreen"].get("due_now", []))}


def check(state_dir):
    """Проверка согласованности без записи: для CI и ручного аудита."""
    state_dir = Path(state_dir)
    hot, raw = _read_state(state_dir)
    problems = []
    if '"truth"' in raw[0] or '"hides"' in raw[0]:
        problems.append(f"скрытая правда попала в {HOT_PATH}")
    cursor = hot.get("cursor", {})
    for field in ("id", "seq", "at", "place", "date", "time"):
        if field not in cursor:
            problems.append(f"в курсоре нет {field}")
    if hot.get("last", {}).get("id") not in (cursor.get("id"), None) and \
            not (hot.get("chronicle") and hot["chronicle"][-1].get("ooc")):
        problems.append("последний ответ не совпадает с курсором")
    # The optional live GM brief is a derived file: it must refer to exactly
    # the same accepted turn. Never allow a stale actor/source packet.
    from . import live_scene
    brief_path = state_dir / live_scene.PATH
    if brief_path.is_file():
        try:
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            signature = brief.get("digest")
            payload = {k: v for k, v in brief.items() if k != "digest"}
            if brief.get("format") != live_scene.FORMAT or signature != live_scene._digest(payload):
                problems.append("GM_BRIEF has invalid format or digest")
            elif brief.get("cursor", {}).get("id") != cursor.get("id"):
                problems.append("GM_BRIEF cursor is stale")
        except (OSError, ValueError, TypeError):
            problems.append("GM_BRIEF cannot be read")
    scene_anchor = (hot.get("scene") or {}).get("as_of")
    if scene_anchor and scene_anchor != (hot.get("last") or {}).get("id"):
        problems.append("сцена отстаёт от последнего игрового хода")
    if cursor.get("at") and heading(cursor["at"])["date"] != cursor.get("date"):
        problems.append("дата курсора не соответствует игровому времени")
    # kage_guard_team denotes an aggregate of eight guards in scene.positions;
    # it is not one real NPC with an independently attributable memory.
    aggregate_keys = {"kage_guard_team"}
    for key in hot.get("scene", {}).get("present", []):
        if key not in hot.get("cast", {}) and key != "shiki" and key not in aggregate_keys:
            problems.append(f"в сцене присутствует {key}, но карточки в составе нет")
    for key, clock in hot.get("offscreen", {}).get("clocks", {}).items():
        for field in ("owner", "what", "due", "if_unopposed"):
            if not clock.get(field):
                problems.append(f"часы {key}: нет поля {field}")
    size = len(raw[0].encode("utf-8"))
    if size > HOT_LIMIT:
        problems.append(f"HOT разросся до {size} байт")
    return {"ok": not problems, "problems": problems, "bytes": size, "cursor": cursor.get("id")}
