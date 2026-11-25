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
import time
import traceback
import zlib
from pathlib import Path

from . import acquaint, aging, arcs, body, canon, claims, drama, echo, expertise, favor, limits, style, continuity_guard, directives, dread, fight, inquiry, keeper, lore, merge, nextpack, npc_memory, plot, presence, prose_lint, will

SCHEMA = "npr-hot/2"
TURN_SCHEMA = "npr-turn/2"
HOT_PATH = "recovery/HOT.json"
TURN_PATH = "recovery/TURN.json"
LEGACY_PATH = "recovery/FAST_SCENE.json"
CAST_DIR = "recovery/cast"
SECRET_PATH = "recovery/GM_ONLY.json"   # скрытая правда, закадровые часы, тайны NPC: игроку не открывать
UNDO_PATH = "recovery/UNDO.GM_ONLY.json"   # снимок для amend последнего хода: отдельно, чтобы GM_ONLY читался целиком
SECRET_SCHEMA = "npr-gm-only/2"
CHECKS_PATH = "recovery/CHECKS.json"   # замечания свёртки: ведущий читает перед новой сценой
# Запись хода — не пересказ сцены: длинная бухгалтерия задерживает ответ ведущего.
SITUATION_NORM = 600
SITUATION_MAX = 900
STATE_MAX = 400
BOOKKEEPING_NORM = 4000    # весь ход сверх gm и player: сейчас медиана около 8000, больше самой сцены
CHECKS_KEEP = 16

TURN_ID = re.compile(r"s(\d{2})\.text\.(\d{3,4})")
WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря")

LINE_LIMIT = 320
KNOWS_LIMIT = 8       # в HOT — последние; все знания на странице memory/npc/<id>.md
CHRONICLE_IN_HOT = 10   # 23.11.2026: было 4 — ведущий терял ход сессии; полная хроника — archive/session-NNN-chronicle.md
CANON_IN_HOT = 6
CANON_ARCHIVE = "archive/shiki-canon.jsonl"
LINT_KEEP = 3
RESOLVED_KEEP = 6
CALENDAR_AHEAD = 14
# байт. Единый норматив (19.11.2026): выше HOT_LIMIT свёртка блокируется и пишет
# сбой в CHECKS.json; рабочая норма — до HOT_NORM. Полная память NPC живёт в вики,
# HOT держит только текущую сцену, свежие знания и эпизоды.
HOT_LIMIT = limits.HOT_LIMIT   # пределы — в rpg_engine/limits.py
HOT_NORM = limits.HOT_NORM
LAST_GM_TAIL = 1500  # знаков последнего ответа в HOT; полный текст — в TURN.json и archive/

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
    (95, "Устойчивая тяжёлая форма", "Максимум Договора: холодная цельная мертвенно-серая кожа без шелушения, распада поверхности, некротических пятен или лоскутов при сохранной работе органов, практически чёрная функциональная кровь и чакросети внутренней среды; корни и сосуды шеи и туловища, но не лица. "
         "Заражение только прямым голым контактом живых кож либо свежей кровью в открытую рану/слизистую, не через воздух, запах и предметы."),
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
    # A player-authored narrative breakthrough may explicitly advance the
    # irreversible pact stage without fabricating technique casts or points.
    stage = max(stage, int(pact.get("stage_floor", 0)))
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

def _last_entry(turn):
    """Хвост последнего ответа для продолжения сцены.

    Полный опубликованный текст хранится в TURN.json и дословном журнале
    archive/session-NNN-turns.jsonl; копия длинной сцены в HOT переполняла предел.
    """
    gm = turn["gm"]
    entry = {"id": turn["id"], "player": turn["player"], "gm": gm}
    if len(gm) > LAST_GM_TAIL:
        entry["gm"] = "…" + gm[-LAST_GM_TAIL:]
        entry["gm_chars"] = len(gm)
    return entry



def _merge(target, patch):
    """Словари сливаются рекурсивно, ``null`` удаляет ключ, ``ключ+`` дополняет список."""
    for key, value in patch.items():
        if key.endswith("+"):
            base = target.get(key[:-1])
            if not isinstance(base, list):   # «knows» строкой (s13.text.021): дополнение не роняет свёртку
                base = [] if base in (None, "", {}) else [base]
                target[key[:-1]] = base
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
        # С какого хода стоит отношение и вид: давнее не выдаётся за нынешнее.
        for field in ("stance", "body", "state"):
            if field in patch and patch[field] is not None:
                card.setdefault("since", {})[field] = turn_id
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
    plot.apply(hot, delta.get("plot"), turn_id, at)
    plot.touch_threads(hot, delta.get("threads"), turn_id)
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
    if "pact_stage_floor" in delta:
        floor = delta["pact_stage_floor"]
        if type(floor) is not int or not 0 <= floor < len(PACT_STAGES):
            raise TurnError("pact_stage_floor must be an integer stage in range")
        pact = hot.setdefault("pact", {})
        pact["stage_floor"] = max(int(pact.get("stage_floor", 0)), floor)
    additions = delta.get("canon+") or []
    if isinstance(additions, str):
        # s11.text.073: строка вместо списка разошлась по буквам — 54 «поправки» из одной буквы в каноне.
        additions = [additions]
    for item in additions:
        if not str(item).strip() or len(str(item).strip()) < 8:
            continue
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
    pool = fight.dice(hot)   # кости, выданные ведущему до этого хода

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
        hot["last"] = _last_entry(turn)
        report = _lint(turn["gm"], _npc_names(hot))
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
    if not ooc:
        # Состав и карточки-заготовки, восстановленные свёрткой по подписям реплик.
        for key, card in (turn.get("_cards_auto") or {}).items():
            have = hot.setdefault("cast", {}).setdefault(key, dict(card))
            if not have.get("name") and card.get("name"):
                have["name"] = card["name"]
        scene_patch = delta.get("scene") if isinstance(delta.get("scene"), dict) else {}
        presence.update(hot, turn, number, "present" in scene_patch)
        for key in (hot.get("scene") or {}).get("present") or []:
            if key in hot.get("cast", {}):
                hot["cast"][key]["seen"] = turn["id"]
    hot.setdefault("_checks", []).extend(fight.apply(hot, delta, turn, pool))
    body.check(hot, turn, delta)      # кровь идёт — кость состояния решает тело (body.py)
    if not ooc and isinstance(delta.get("scene"), dict):
        hot.setdefault("scene", {})["as_of"] = turn["id"]
    continuity_guard.apply_things(hot, delta, turn["id"], turn["at"])
    npc_memory.apply(hot, turn, inner)
    will.track(hot, turn, inner)
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


def _dump_hot(data):
    """Compact the live machine-state losslessly; full human-readable context is in wiki/archives.

    The former indentation pushed a valid HOT above the 90 KB fold gate.
    Preserve all values, ordering and Ukrainian/Russian text; no semantic trimming.
    """
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


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


KEEPER_FILL = "memory/_curated/state_fill.json"


def _lint(text, names=()):
    """Линтер прозы и вёрстка реплик кампании: реплика без подписи сразу делает вердикт «переписать».

    Мысли и память NPC в повествовании (``interior``) — замечание к следующему ходу, не переписывание:
    переписанный трижды ход — тоже жалоба игрока (s11)."""
    report = prose_lint.lint(text)
    for hit in (prose_lint.speakers(text), prose_lint.mechanics(text)):
        if hit:   # реплика без подписи, числа механики в прозе — сразу «переписать»
            report["hits"].insert(0, hit)
            report["by_rule"][hit["rule"]] = report["by_rule"].get(hit["rule"], 0) + 1
            report["advice"][hit["rule"]] = prose_lint.ADVICE[hit["rule"]]
            report["verdict"] = "переписать"
    for hit in (prose_lint.interior(text, names) if names else None, prose_lint.menu(text)):
        if hit:   # мысли NPC, меню в конце хода — замечание, не переписывание
            report["hits"].insert(0, hit)
            report["by_rule"][hit["rule"]] = report["by_rule"].get(hit["rule"], 0) + 1
            report["advice"][hit["rule"]] = prose_lint.ADVICE[hit["rule"]]
            if report.get("verdict") == "чисто":
                report["verdict"] = "шум"
    return report


def _npc_names(hot):
    """Имена карточек для сторожа перспективы: все, кроме Шики и Немеры."""
    return [str(c.get("name")) for k, c in (hot.get("cast") or {}).items()
            if isinstance(c, dict) and c.get("name") and k not in ("shiki", "nemera")]


def _apply_fix(hot, fix):
    """{"path": ["cast", "sakura", "facts"], "match": "встречала", "value": "…"}: нет пути — ничего."""
    *head, last = fix["path"]
    node = hot
    for step in head:
        node = node.get(step) if isinstance(node, dict) else None
        if node is None:
            return False
    if not isinstance(node, dict) or last not in node:
        return False
    target, match, value = node[last], fix.get("match"), fix.get("value")
    if isinstance(target, list) and match:
        hits = [i for i, item in enumerate(target) if match in str(item)]
        for i in reversed(hits):
            if value is None:
                target.pop(i)
            else:
                target[i] = value
        return bool(hits)
    if isinstance(target, str) and match:
        if match not in target:
            return False
        node[last] = target.replace(match, value or "")
        return True
    node[last] = value
    return True


RESYNC_TURNS = 2   # ходов, пока висит просьба перечитать (снимается раньше, если ведущий перечитал)
RESYNC_READ = ("GM_CORE.md", "SHIKI.md", "ref/TECHNIQUES.md", "ref/NEMERA.md", "ref/COMBAT.md",
               "memory/CANON.md", "recovery/GM_BRIEF.json")


def resync_item(hot):
    """Пункт must: перечитать стартовый пакет в нынешнем чате, как в новом."""
    pending = (hot.get("fold") or {}).get("resync")
    if not pending:
        return None
    session = int(hot.get("session") or 0)
    files = ", ".join(RESYNC_READ) + f", archive/session-{session:03d}-chronicle.md"
    note = f" {pending['note']}" if pending.get("note") else ""
    return ("Обнови картину в этом чате, как в новом (игрок не может открыть новый чат): одним пакетом перечитай "
            f"{files}. Сыгранное, канон и память NPC — по этим файлам, а не по памяти чата.{note} В TURN этого хода — "
            "\"chat\": \"new\" и read с прочитанным.")


def _resync_seen(hot, turn):
    """Ведущий перечитал (chat: new или GM_CORE в read) или прошло RESYNC_TURNS ходов — просьба снята."""
    pending = (hot.get("fold") or {}).get("resync")
    if not pending or turn.get("kind") in ("ooc", "technical", "void"):
        return
    fresh = turn.get("chat") == "new" or any(str(r).endswith("GM_CORE.md") for r in turn.get("read") or [])
    pending["left"] = int(pending.get("left") or 0) - 1
    if fresh or pending["left"] <= 0:
        hot["fold"].pop("resync", None)


def _keeper_fill(state_dir, hot):
    """Поля состояния, которые хранитель задаёт один раз (чакра после обморока и т. п.).

    Заполняет только отсутствующее: что ведущий уже записал ходом, не трогает.
    """
    path = Path(state_dir) / KEEPER_FILL
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return
    # «replace»: поправка хранителя, которая заменяет устаревшее поле один раз на версию
    # файла (например, HOT.shiki.presence после поправки игрока). Дальнейшие записи
    # ведущего ходом сильнее: повторно заменяется только при новой версии файла.
    replace = data.get("replace") if isinstance(data.get("replace"), dict) else {}
    digest = hashlib.sha256(json.dumps(replace, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    fold = hot.setdefault("fold", {})
    if replace and fold.get("keeper_replace") != digest:
        for section, fields in replace.items():
            if isinstance(fields, dict):
                hot.setdefault(section, {}).update(fields)
        fold["keeper_replace"] = digest
    # «fix»: точечная поправка хранителя по пути (строка факта в карточке NPC), один раз на версию.
    # s11.text.033: в facts Сакуры ведущий записал «встречала Шики на экзамене» — встреч не было.
    # «restore_aged»: вернуть из archive/aged ушедшее во время сессии (игрок, 23.11.2026: продолжить сессию,
    # а не закрывать; разгрузки увели её же память). Один раз на id.
    restore = data.get("restore_aged") if isinstance(data.get("restore_aged"), dict) else None
    if restore and restore.get("session") and fold.get("restored_aged") != restore.get("id"):
        from . import aging
        fold["restored_aged"] = restore.get("id")
        fold["restored_report"] = aging.restore(state_dir, hot, restore["session"])
    # «resync»: игрок не может открыть новый чат (23.11.2026) — ведущий перечитывает стартовый пакет в нынешнем.
    resync = data.get("resync") if isinstance(data.get("resync"), dict) else None
    if resync and resync.get("id") and fold.get("resync_done") != resync["id"]:
        fold["resync_done"] = resync["id"]
        fold["resync"] = {"id": resync["id"], "since": (hot.get("cursor") or {}).get("id"), "left": RESYNC_TURNS,
                          "note": str(resync.get("note") or "")[:400]}
    fixes = [f for f in data.get("fix") or [] if isinstance(f, dict) and isinstance(f.get("path"), list)]
    fix_digest = hashlib.sha256(json.dumps(fixes, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    if fixes and fold.get("keeper_fix") != fix_digest:
        for fix in fixes:
            _apply_fix(hot, fix)
        fold["keeper_fix"] = fix_digest
        _recall_cast(state_dir, hot, {})   # поправленный состав — с карточками из recovery/cast
    for section, fields in data.items():
        if section.startswith("_") or section in ("replace", "fix") or not isinstance(fields, dict):
            continue
        target = hot.setdefault(section, {})
        for key, value in fields.items():
            if key not in target:
                target[key] = value


def _load_stats(state_dir, hot):
    """Уровни умений из листа Шики (HOT.shiki.sheet) — для порогов боя."""
    shiki = hot.get("shiki") or {}
    sheet = shiki.get("sheet")
    if not isinstance(sheet, str) or not (Path(state_dir) / sheet).is_file():
        return
    try:
        stats = json.loads((Path(state_dir) / sheet).read_text(encoding="utf-8")).get("stats_out_of_5")
    except (ValueError, OSError):
        return
    if isinstance(stats, dict) and stats != shiki.get("stats"):
        shiki["stats"] = stats


CHECK_TURNS = 3        # замечания живут три хода: дальше они история, а не задание
OBSOLETE_CHECKS = ("review.sources must refer", "missing earliest/latest prior NPC disclosure",
                   "source-sensitive scene must provide review.sources", "requires source-bound novelty_contrast")


def _tidy_checks(hot):
    """Повтор одного замечания — одна строка; старше трёх ходов — уходит из CHECKS."""
    cursor = hot.get("cursor") or {}
    session, seq = int(hot.get("session") or 0), int(cursor.get("seq") or 0)
    fresh, seen = [], {}
    present = set((hot.get("scene") or {}).get("present") or [])
    for check in hot.get("_checks") or []:
        text = str(check.get("text") or "")
        if any(rule in text for rule in OBSOLETE_CHECKS):
            continue
        if re.match(r"намерение \S+: статус \S+$", text):
            continue   # старая форма: «done»/«in_progress» теперь понимаются, план не откатывается
        if "offscreen knows+" in text and text.split(":")[0] in present:
            continue   # NPC уже в сцене: замечание о «заочном» знании снято
        match = re.match(r"s(\d+)\.text\.(\d+)", str(check.get("turn") or ""))
        if match and (int(match.group(1)) != session or seq - int(match.group(2)) >= CHECK_TURNS) \
                and check.get("kind") not in ("plot", "fight", "memory", "will"):
            continue
        key = (check.get("kind"), re.sub(r"s\d+\.text\.\d+", "#", str(check.get("text"))))
        if key in seen:
            first = seen[key]
            check = dict(check, since=first.get("since") or first.get("turn"))
            fresh.remove(first)
        seen[key] = check
        fresh.append(check)
    hot["_checks"] = fresh


def _cap_cards(hot):
    """HOT держит в карточке только свежие знания и эпизоды; полные — в вики и archive/npc-memory."""
    keep = npc_memory.CARD_FIRST + npc_memory.CARD_RECENT
    for card in (hot.get("cast") or {}).values():
        if len(card.get("knows") or []) > KNOWS_LIMIT:
            card["knows"] = card["knows"][-KNOWS_LIMIT:]
        memory = card.get("memory")
        if isinstance(memory, list) and len(memory) > keep:
            card["memory"] = memory[:npc_memory.CARD_FIRST] + memory[-npc_memory.CARD_RECENT:]


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
        # Порядок ключей не важен: архив сжимается с sort_keys, а индекс раньше
        # писался в порядке вставки — новый ключ «не в конец алфавита» ломал свёртку (s08.065).
        if offscreen.get("truth_index") and set(loaded) != set(offscreen["truth_index"]):
            raise ValueError("GM_ONLY truth index differs from full archive")
        offscreen = dict(offscreen)
        offscreen.pop("truth_zlib64")
        offscreen.pop("truth_preview", None)
        offscreen.pop("truth_index", None)
        offscreen["truth"] = loaded
    hot["offscreen"] = offscreen
    hot["_hides"] = dict(secret.get("cast") or {})
    hot["_npc_knowledge"] = dict(secret.get("npc_knowledge") or {})
    undo_path = state_dir / UNDO_PATH
    hot["_undo"] = (json.loads(undo_path.read_text(encoding="utf-8")).get("undo")
                    if undo_path.is_file() else secret.get("undo"))
    hot["_psyche"] = dict(secret.get("psyche") or {})
    hot["_checks"] = list(secret.get("checks") or [])
    hot["_horror"] = dict(secret.get("horror") or {})
    hot["_plot"] = dict(secret.get("plot") or {})
    hot["_fight"] = dict(secret.get("fight") or {})
    hot["_will"] = dict(secret.get("will") or {})
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
    plot_state = public.pop("_plot", {})
    fight_state = public.pop("_fight", {})
    will_state = public.pop("_will", {})
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
            offscreen["truth_index"] = sorted(truth)
    public["offscreen"] = {"see": SECRET_PATH, "due": len(offscreen.get("due_now", [])),
                           "clocks": len(offscreen.get("clocks", {}))}
    secret = {"schema": SECRET_SCHEMA,
              "access": "ТОЛЬКО МАСТЕР. Игроку не показывать и не пересказывать, пока это не сыграно в сцене.",
              "cursor": public.get("cursor", {}).get("id"), "offscreen": offscreen,
              "cast": hides, "npc_knowledge": npc_knowledge, "psyche": psyche,
              "checks": checks, "horror": horror, "plot": plot_state, "fight": fight_state, "will": will_state,
              "undo": undo}
    return public, secret


def _write_state(state_dir, hot, before=("", "")):
    state_dir = Path(state_dir)
    public, secret = _split_state(hot)
    undo_text = _dump({"schema": "npr-undo/1", "access": secret["access"], "undo": secret.pop("undo")})
    texts = (_dump_hot(public), _dump(secret))
    for path, text, old in zip((HOT_PATH, SECRET_PATH), texts, before):
        if text != old:
            (state_dir / path).parent.mkdir(parents=True, exist_ok=True)
            (state_dir / path).write_text(text, encoding="utf-8")
    undo_path = state_dir / UNDO_PATH
    undo_changed = not undo_path.is_file() or undo_path.read_text(encoding="utf-8") != undo_text
    if undo_changed:
        undo_path.parent.mkdir(parents=True, exist_ok=True)
        undo_path.write_text(undo_text, encoding="utf-8")
    return texts, texts != tuple(before) or undo_changed


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
    wanted = set(delta.get("cast") or {}) | set((delta.get("scene") or {}).get("present") or []) | \
        set(turn.get("_spoke") or []) | set((hot.get("scene") or {}).get("present") or [])
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


def _always_present(state_dir):
    try:
        policy = json.loads((Path(state_dir) / "recovery/GATE_POLICY.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [k for k in policy.get("always_present") or [] if isinstance(k, str)]


def _checks_payload(hot):
    return {"schema": "npr-checks/1", "cursor": (hot.get("cursor") or {}).get("id"),
            "fold_errors": (hot.get("fold") or {}).get("errors", []),
            "checks": hot.get("_checks", []),
            "rule": "Сцена уже показана игроку. Расхождение исправляется следующим ходом "
                    "(d.things, d.cast, amend), а не молчаливым переписыванием сыгранного."}


def _write_notes(hot, turn):
    """Замечание kind=write: запись хода длиннее нужного или страницы читаются повторно."""
    if turn.get("kind") == "ooc":
        return []
    notes = []
    extra = sorted(set(turn.get("review") or {}) - {"predecessor"})
    if extra:
        notes.append(f"review: нужен только predecessor, {', '.join(extra)} не пиши")
    delta = turn.get("d") or {}
    situation = str(((delta.get("scene") or {}) if isinstance(delta.get("scene"), dict) else {}).get("situation") or "")
    if len(situation) > SITUATION_MAX:
        notes.append(f"d.scene.situation {len(situation)} знаков: только перемена и то, что нужно "
                     f"следующему ходу, до {SITUATION_NORM}; знания NPC — в knows+, не в situation")
    extra_chars = len(json.dumps({k: v for k, v in turn.items() if k not in ("gm", "player")},
                                 ensure_ascii=False))
    if extra_chars > BOOKKEEPING_NORM:
        parts = sorted(((len(json.dumps(v, ensure_ascii=False)), k) for k, v in delta.items()), reverse=True)[:3]
        notes.append(f"вся запись сверх сцены {extra_chars} знаков (норма до {BOOKKEEPING_NORM}); крупнее всего: "
                     + ", ".join(f"{k} {n}" for n, k in parts))
    long_state = [k for k, v in (delta.get("cast") or {}).items()
                  if isinstance(v, dict) and len(str(v.get("state") or "")) > STATE_MAX]
    if long_state:
        notes.append(f"state у {', '.join(long_state)} длиннее {STATE_MAX} знаков: одна-две фразы о перемене")
    read = [str(r) for r in turn.get("read") or []]
    fold_info = hot.setdefault("fold", {})
    again = [r for r in read if r in (fold_info.get("last_read") or [])]
    fold_info["last_read"] = read
    if again:
        notes.append(f"повторно открыты {', '.join(again)}: страница уже в этом чате, перед ходом "
                     "достаточно recovery/NEXT.json")
    if not notes:
        return []
    return [{"turn": turn["id"], "kind": "write", "text": "Запись хода: " + "; ".join(notes) + "."}]


def _write_checks(state_dir, hot):
    """Короткий открытый файл замечаний: читается за один запрос перед новой сценой."""
    payload = _checks_payload(hot)
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
    _clock = [time.monotonic()]
    timings = {}

    def mark(name):   # где уходит время свёртки (24.11.2026: в CI 66 с при 16 с локально)
        now = time.monotonic()
        timings[name] = round(now - _clock[0], 2)
        _clock[0] = now

    state_dir = Path(state_dir)
    hot, before = _read_state(state_dir)
    ensure_npc_memory(state_dir, hot)
    _load_stats(state_dir, hot)
    alias = merge.aliases(state_dir)
    merge.apply(state_dir, hot, alias)
    turns, errors = collect_turns(state_dir, hot)
    lore_entries = lore.load(state_dir)
    always = _always_present(state_dir)
    names = presence.roster(state_dir, hot)
    lore_new = []
    # Знакомство с Шики по всем сессиям и уровень речи медиков — для проверок реплик NPC.
    archive_rows = acquaint.turns(state_dir)
    met = acquaint.index(state_dir, names, archive_rows, acquaint.generic(state_dir, hot))
    acquaint.backfill(state_dir, hot, lore_entries, names, archive_rows)
    # Derived "saw it" cache may include actors from a different simultaneous
    # fight. Audit the *source episode*, not roster membership, before reuse.
    lore.reconcile_visual_memory(hot, lore_entries)
    skill_seed = expertise.load(state_dir)
    canon_arcs = arcs.load(state_dir)
    favor.seed(hot, archive_rows, names, always)   # окно подыгрывания сразу, не с нуля
    fight.backfill_arsenal(hot, archive_rows)        # бой шёл до учёта арсенала
    mark("setup")
    visited = None
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
            merge.redirect(turn, alias)   # после отпечатка: идемпотентность не зависит от слияний
            # Проверка источников, знаний NPC и наступивших сроков. Сцена уже
            # показана игроку: отказ свернуть её лишь разорвал бы память. Поэтому
            # содержательные замечания записываются, а не останавливают свёртку.
            # Блокируют только структурные ошибки apply_turn (разрыв, время назад).
            from . import live_scene, scene_gate, memory_guard, entity_memory
            policy_path = state_dir / live_scene.POLICY_PATH
            policy = (json.loads(policy_path.read_text(encoding="utf-8"))
                      if policy_path.is_file() else {"enabled": False})
            _, primary = scene_gate.played_turns(state_dir)
            sources = [x for x in primary if x["id"] != turn["id"]]
            sources += [x for x in applied if x["id"] != turn["id"]]
            base = _amend_base(hot, turn) if amendment else hot
            present_before = list((base.get("scene") or {}).get("present") or []) + \
                [k for k in policy.get("always_present") or [] if isinstance(k, str)]
            review_notes = live_scene.validate_admission(
                base, turn, policy=policy, accepted=sources)
            # Already published prose cannot be retracted by an asynchronous fold;
            # record any source discrepancy for the very next scene.
            review_notes.extend(memory_guard.review(entity_memory.load(state_dir), turn)["errors"])
            if archived is None:
                archived = _archived_cards(state_dir)
            name_notes = npc_memory.check_names(base, turn.get("d") or {}, turn["id"], archived)
            if turn.get("kind") not in ("ooc", "technical", "void"):
                name_notes += npc_memory.check_marks(base, turn, archived, _always_present(state_dir))
                # Сторож канона и уход манеры — только при нарушении: чистый ход ничего не добавляет.
                hot.setdefault("_checks", []).extend(canon.guard(state_dir, turn) + style.drift(state_dir, turn))
            # Состав по подписям реплик, если ведущий не записал present (код не правит его запись).
            spoke, unnamed = presence.voices(turn, names, always)
            cards = presence.auto_cards(hot, turn, unnamed, names)
            # Говорящий без имени в карточке или вовсе без карточки (есть только страница вики).
            for key in spoke:
                cards.setdefault(key, {"name": presence.display(names, key), "auto": True})
            turn["_spoke"], turn["_cards_auto"], turn["_always"] = spoke + [k for k in cards if k not in spoke], cards, always
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
                present_after = list((hot.get("scene") or {}).get("present") or [])
                checks.extend(lore.review(hot, turn, lore_entries, present_before, present_after))
                # Кто откуда знает Шики, речь не по уровню, мир не подыгрывает.
                checks.extend(acquaint.review(hot, turn, met, names, always, lore_entries))
                checks.extend(expertise.review(hot, turn, names, skill_seed, always))
                checks.extend(favor.track(hot, turn, names, always))
                checks.extend(arcs.track(hot, turn, canon_arcs))   # канон арки: сыграно, пропущено
                fight.track_arsenal(hot, turn)                      # пущенные в ход приёмы именных
                checks.extend(fight.nemera_turn(hot, turn))         # Немера не ждёт просьбы
                drama.track(hot, turn, always)                      # ритм, почин, разговор NPC между собой
                _resync_seen(hot, turn)                             # перечитал ли ведущий стартовый пакет
                checks.extend(presence.prune_silent(hot, turn, int(hot.get("cursor", {}).get("seq", 0)), names, always))
                # Всегда присутствующая Немера живёт в чувствах Шики: «впервые увидеть» его стихию не может.
                witnesses = [k for k in dict.fromkeys(present_before + present_after) if k not in always]
                lore_new.extend({"entry": e, "key": k, "turn": turn["id"]} for e, k in
                                lore.track(hot, turn, lore_entries, witnesses, names))
                checks.extend(_write_notes(hot, turn))
                checks.extend(inquiry.track(hot, turn))
                if visited is None:
                    visited = claims.places(state_dir)
                if turn.get("place"):
                    visited.add(str(turn["place"]).casefold())
                checks.extend(claims.review(turn, visited))
                directives.capture(hot, turn)
        except TurnError as exc:
            errors.append(f"{turn.get('id')}: {exc}")
        except Exception as exc:   # сбой кода — отказ с причиной в NEXT, а не молча упавшая свёртка (s13.text.021)
            where = traceback.extract_tb(exc.__traceback__)[-1]
            errors.append(f"{turn.get('id')}: сбой свёртки {type(exc).__name__}: {exc} "
                          f"({Path(where.filename).name}:{where.lineno}); ход в истории, свернётся после исправления кода")
            break
    # Never publish a partly accepted batch. All turns remain in Git history
    # and will be replayed after the rejected entry is corrected. The refusal
    # itself IS published, so the GM sees it instead of a silently stale HOT.
    if errors:
        stale, stale_before = _read_state(state_dir)
        stale.setdefault("fold", {})["errors"] = errors
        _write_state(state_dir, stale, stale_before)
        _write_checks(state_dir, stale)
        nextpack.write_blocked(state_dir, stale["cursor"].get("id"), errors, rule=(
            "Ход не свёрнут: исправь его тем же id с amend по fold_errors. До этого память стоит."))
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
    from . import wiki
    if not (state_dir / wiki.DELTAS_DIR).is_dir():
        try:  # разово: прошлые дельты из истории TURN.json (в Actions история полная)
            wiki.backfill_deltas(state_dir)
        except (OSError, subprocess.CalledProcessError):
            pass
    wiki.record_deltas(state_dir, applied)
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
    # Замечания плана — текущее состояние, не события: каждая свёртка заменяет прежние.
    hot["_checks"] = [c for c in hot.get("_checks", []) if c.get("kind") not in ("plot", "will")]
    hot["_checks"].extend(plot.review(hot, applied))
    hot["_checks"].extend(will.review(hot, applied))
    # Заготовки хранителя — после ходов: исправление хода (amend) восстанавливает
    # снимок без них, и прямую правку HOT оно бы стёрло.
    if applied:
        hot.setdefault("fold", {})["lore_new"] = lore_new   # впервые увидевшие — для NEXT.must
    merge.apply(state_dir, hot, alias)   # снимок amend мог вернуть лишний ключ
    merge.names(state_dir, hot)
    keeper.apply(state_dir, hot)   # поправка хранителя к сюжету и линиям, раз на версию
    will.seed(state_dir, hot)
    will.fill_names(state_dir, hot)
    _keeper_fill(state_dir, hot)
    # Мир не ждёт ведущего: просроченные планы отсутствующих исполняются сами
    # (GM_ONLY will.happened → NEXT.offscreen).
    will.advance(hot, _always_present(state_dir))
    hot["_checks"].extend(plot.schedule(hot))   # главы со сроком мира открываются сами
    _cap_cards(hot)
    # Давнее — из оперативной памяти в archive/aged/ (и на страницы вики); у предела — строже.
    measure = lambda h: len(_dump_hot(_split_state(h)[0]).encode("utf-8"))
    aged = aging.apply(state_dir, hot, size=measure, limit=HOT_NORM)
    if limits.trim_requested(state_dir):   # игрок выбрал строгую разгрузку сейчас
        extra = aging._pass(state_dir, hot, aging.TIGHT)
        aged = {k: aged.get(k, 0) + extra.get(k, 0) for k in set(aged) | set(extra) if aged.get(k, 0) + extra.get(k, 0)}
        aged["by_request"] = 1
    if aged:
        hot.setdefault("fold", {})["aged"] = aged
    _tidy_checks(hot)
    mark("turns")
    wiki_report = _update_wiki(state_dir, hot, applied)
    mark("wiki")
    _write_checks(state_dir, hot)
    if applied or ((state_dir / "recovery/GATE_POLICY.json").is_file() and
                   not (hot.get("scene") or {}).get("npc_disclosures_to_player")):
        # Cache source pointers even for an existing folded campaign when
        # activating the integrated pre-scene gate.
        from . import scene_gate
        _, accepted = scene_gate.played_turns(state_dir)
        hot.setdefault("scene", {})["npc_disclosures_to_player"] = _disclosures(
            scene_gate.compact_disclosure_cues(hot, accepted, per_actor=4), _always_present(state_dir))
    # Вести мира и темп сцены: весомое за кадром доходит до Шики пунктом must.
    from . import news
    try:
        news_item = news.step(hot, applied)
    except Exception as exc:   # производное: сбой не останавливает свёртку
        news_item = None
        hot.setdefault("_checks", []).append({"turn": hot.get("cursor", {}).get("id"), "kind": "will",
                                              "text": f"вести мира не собраны: {type(exc).__name__}"})
    # Счётчик показа новостей мира растёт до записи состояния, иначе событие висит в NEXT вечно.
    happened = will.offscreen(hot)
    favor_item = favor.must(hot)   # до записи: пауза между напоминаниями хранится в HOT
    arcs.advance(hot, canon_arcs)   # канон без Шики по сроку случается сам
    hot.setdefault("_checks", []).extend(arcs.revalidate(hot, canon_arcs))   # отмена маршрутом — не отмена
    try:   # эхо прошлого: давняя нить, созвучная сцене, — изредка
        played = [t for t in applied if t.get("kind") not in ("ooc", "technical", "void")]
        echoed = echo.pick(state_dir, hot, texts=[f"{t.get('gm') or ''} {t.get('line') or ''}" for t in played],
                           turns=played)
    except Exception as exc:   # эхо — украшение, не повод сорвать свёртку
        echoed = None
        hot.setdefault("_checks", []).append({"turn": (hot.get("cursor") or {}).get("id"), "kind": "echo",
                                              "text": f"эхо прошлого не собрано: {type(exc).__name__}"})
    lore.reconcile_visual_memory(hot, lore_entries)
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
    from . import entity_memory, general_memory, memory_quality
    ledger = entity_memory.rebuild(state_dir)
    memory_index = general_memory.save(state_dir, hot, ledger=ledger)
    memory_quality.save(state_dir, hot, memory_index)
    mark("general_memory")
    brief = live_scene.build_for_state(hot, accepted, state_dir)
    live_scene.write(brief_path, brief)
    # Historical recall is refreshed by the *same* existing fold and committed
    # in archive/ with the verbatim journal; no detached optional sidecar job.
    heuristic = meaning_report = None
    if (state_dir / "archive").is_dir():
        from . import heuristic_history, recall_audit
        engine_root = Path(__file__).resolve().parents[1]
        mark("brief")
        heuristic = heuristic_history.save(engine_root, state_dir)
        mark("heuristic_index")
        # Validate coverage of every existing session, source-shard checksums,
        # and warm search timings in the same ordinary gameplay fold.
        report = recall_audit.audit(state_dir)
        stats_path = state_dir / "archive/heuristic-recall/audit.json"
        stats_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        mark("recall_audit")
        meaning_report = _recall_layer(engine_root, state_dir, hot)
        mark("meaning")
    from . import recall_request
    extra = _canon_and_style(state_dir, hot, applied)
    extra["knows"] = _knows(state_dir, hot, met, lore_entries, skill_seed)
    if favor_item:
        extra["favor"] = favor_item
    unease_item = favor.unease_must(hot, set(_always_present(state_dir)))
    if unease_item:
        extra["unease"] = unease_item
    try:   # возраст по дню рождения на дату хода — растёт сам (canon.json ages)
        extra["ages"] = canon.ages_now(state_dir, hot)
    except Exception as exc:
        hot.setdefault("_checks", []).append({"turn": (hot.get("cursor") or {}).get("id"), "kind": "canon",
                                              "text": f"возраст: {type(exc).__name__}: {exc}"})
    extra["resync"] = resync_item(hot)
    try:   # историческая память присутствующих по теме сцены (слова + смысл): NEXT.present.<id>.past
        extra["past"] = echo.present_past(state_dir, hot, skip=set(_always_present(state_dir)))
    except Exception as exc:   # производное: сбой не останавливает свёртку
        hot.setdefault("_checks", []).append({"turn": (hot.get("cursor") or {}).get("id"), "kind": "memory",
                                              "text": f"past: {type(exc).__name__}: {exc}"})
    extra["body"] = body.must(hot)
    try:   # давний знакомый после разлуки, один пункт драмы в must (drama.py)
        extra["gap"] = drama.gaps(state_dir, hot, set(_always_present(state_dir)))
        extra["drama"] = drama.must(hot, set(_always_present(state_dir)))
    except Exception as exc:
        hot.setdefault("_checks", []).append({"turn": (hot.get("cursor") or {}).get("id"), "kind": "will",
                                              "text": f"drama: {type(exc).__name__}: {exc}"})
    mark("extras")
    extra["arc"] = arcs.view(hot, canon_arcs)
    extra["arc_must"] = arcs.must(hot, canon_arcs)
    near = limits.check(state_dir, hot, hot_bytes=len(texts[0].encode("utf-8")),
                        secret_bytes=len(texts[1].encode("utf-8")))
    nextpack.write(state_dir, hot, brief, _checks_payload(hot), _always_present(state_dir), happened=happened,
                   echoed=echoed, near=near, recall=recall_request.view(state_dir, hot), news=news_item,
                   extra=extra)
    news.write_pace(state_dir, hot)
    mark("next")
    return {"status": "updated" if changed else "current", "timings": timings,
            "cursor": hot["cursor"].get("id"), "applied": [t["id"] for t in applied],
            "errors": errors, "due": len(hot["offscreen"].get("due_now", [])),
            "bytes": {"hot": len(texts[0].encode("utf-8")), "gm_only": len(texts[1].encode("utf-8"))},
            "heuristic_history": heuristic, "meaning": meaning_report, "wiki": wiki_report,
            # Историческая память присутствующих: работал ли смысловой слой и сколько эпизодов у кого.
            "past": {"meaning": ((hot.get("fold") or {}).get("past") or {}).get("meaning"),
                     "episodes": {k: len(v) for k, v in (extra.get("past") or {}).items()}},
            "lint": [(r["id"], r["verdict"]) for r in hot.get("lint", {}).get("recent", [])]}


def _canon_and_style(state_dir, hot, applied):
    """Свод канона, канон к сцене, сверка раз в 20 ходов, эталоны и образец манеры; длина чата."""
    engine_root = Path(__file__).resolve().parents[1]
    extra = {}
    _count_chat(hot, applied)
    for name, step in (("canon_digest", lambda: canon.digest(state_dir)),
                       ("canon", lambda: canon.for_scene(state_dir, hot, applied, engine_root)),
                       ("canon_review", lambda: canon.review(state_dir, hot)),
                       ("style_tagged", lambda: style.tag(state_dir, applied)),
                       ("style", lambda: style.sample(state_dir, hot, applied))):
        try:
            value = step()
        except Exception as exc:   # производное: сбой не останавливает свёртку
            hot.setdefault("_checks", []).append({"turn": (hot.get("cursor") or {}).get("id"), "kind": "memory",
                                                  "text": f"{name}: {type(exc).__name__}: {exc}"})
            continue
        if value and name in ("canon", "canon_review", "style"):
            extra[name] = value
    return extra


DISCLOSURE_BYTES = 6000   # указатели источников в HOT.scene: при девяти NPC в сцене было 21 869 байт


def _disclosures(cues, always=()):
    """Кэш указателей «что NPC уже раскрыл» в пределах DISCLOSURE_BYTES; полный — в GM_BRIEF.

    s11.text.040: девять человек в составе по восемь указателей — HOT перевалил 110 КБ,
    свёртка встала. Всегда присутствующая Немера — не собеседник; строки короче;
    сверх бюджета уходят те, чьё последнее раскрытие давнее.
    """
    out = {}
    for actor, rows in (cues or {}).items():
        if actor in always:
            continue
        out[actor] = [{**row, "line": str(row.get("line") or "")[:160]} for row in rows]
    def size():
        return len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    from .aging import _ordinal
    while out and size() > DISCLOSURE_BYTES:
        oldest = min(out, key=lambda a: max((_ordinal(r.get("turn")) or 0) for r in out[a]))
        out.pop(oldest)
    return out


def _knows(state_dir, hot, met, entries, seed):
    """NEXT.present.<id>.knows: знакомство до сессии, сведения о Шики, уровень речи медика."""
    always = set(_always_present(state_dir))
    session = int(hot.get("session") or 0)
    out = {}
    for key in (hot.get("scene") or {}).get("present") or []:
        if key == lore.SHIKI or key in always:
            continue
        try:
            out[key] = acquaint.view(hot, key, met, entries, session, expertise.tier_line(hot, seed, key))
            _ref, card = fight.canon_card(key, {"name": ((hot.get("cast") or {}).get(key) or {}).get("name")})
            if card and (card.get("now") or card.get("character")):
                # Канонический человек 1287 года, а не карточка экзамена 1283 (s11.text.073: Наруто ждал приглашения).
                out[f"{key}#who"] = "; ".join(x for x in (card.get("now") and f"сейчас: {card['now']}",
                                                          card.get("character") and f"характер: {card['character']}") if x)
        except Exception as exc:   # производное: сбой не останавливает свёртку
            hot.setdefault("_checks", []).append({"turn": (hot.get("cursor") or {}).get("id"), "kind": "memory",
                                                  "text": f"knows {key}: {type(exc).__name__}: {exc}"})
    return out


def _count_chat(hot, applied):
    """Ходов в нынешнем чате ведущего: метка "chat": "new" в TURN, чтение GM_CORE или новая сессия — с нуля."""
    chat = hot.setdefault("fold", {}).setdefault("chat", {"turns": 0})
    for turn in applied or []:
        session = str(turn.get("id") or "").split(".", 1)[0]
        fresh = turn.get("chat") == "new" or any(str(r).endswith("GM_CORE.md") for r in turn.get("read") or [])
        if fresh or chat.get("session") != session:
            chat.update(since=turn.get("id"), turns=0, session=session)
        chat["turns"] = int(chat.get("turns") or 0) + 1
    return chat


def _recall_layer(engine_root, state_dir, hot):
    """Смысловые векторы и ответ на запрос памяти ведущего; сбой не останавливает свёртку."""
    from . import meaning, recall_request
    report = {}
    try:
        report = meaning.update(engine_root, state_dir)
    except Exception as exc:  # производный слой
        report = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    try:
        answered = recall_request.handle(engine_root, state_dir, hot)
        if answered is not None:
            report["recall"] = answered.get("error") or answered.get("mode")
    except Exception as exc:
        report["recall"] = f"error: {type(exc).__name__}: {exc}"
    return report


def _update_wiki(state_dir, hot, applied):
    """Пересобирает memory/ и добавляет замечания о новых персонажах и смене постоянного."""
    from . import wiki
    try:
        report = wiki.write(state_dir, hot, new_turns=applied)
    except Exception as exc:  # вики производна: её сбой не останавливает свёртку
        hot.setdefault("_checks", []).append(
            {"turn": (hot.get("cursor") or {}).get("id"), "kind": "wiki",
             "text": f"Вики не обновилась: {type(exc).__name__}: {exc}"})
        return {"error": str(exc)}
    checks = hot.setdefault("_checks", [])
    checks[:] = [c for c in checks if not str(c.get("text", "")).startswith("Вики не обновилась")]
    for note in report.pop("checks", []):
        if note not in checks:
            checks.append(note)
    from . import wiki_eval
    try:
        evaluation = wiki_eval.run(state_dir)
    except Exception as exc:  # проверка памяти не останавливает свёртку
        evaluation = {"error": str(exc)}
    report["eval"] = {k: evaluation.get(k) for k in ("total", "passed", "score", "error")}
    checks[:] = [c for c in checks if c.get("kind") != "memory"]
    if evaluation.get("score") is not None and evaluation["score"] < wiki_eval.TARGET:
        failed = "; ".join(f"{f['q']} — {f['why']}" for f in evaluation["failed"][:3])
        checks.append({"turn": (hot.get("cursor") or {}).get("id"), "kind": "memory",
                       "text": f"Контрольные вопросы памяти: {evaluation['passed']} из {evaluation['total']}. "
                               f"Не найдено: {failed}"})
    if len(checks) > CHECKS_KEEP:
        hot["_checks"] = checks[-CHECKS_KEEP:]
    return report


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
    hot["last"] = _last_entry(latest)
    totals, recent = {}, []
    for turn in ordered:
        report = _lint(turn["gm"])
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


def record_blocked(state_dir, problems, attempted=None):
    """Публикует в CHECKS.json сбой проверки состояния, когда свёртка не записана.

    Без этого ведущий видит прежний чистый CHECKS.json и не знает, что память
    перестала обновляться. Ходы не теряются: следующая успешная свёртка берёт
    их из истории git.
    """
    path = Path(state_dir) / CHECKS_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {"schema": "npr-checks/1", "fold_errors": [], "checks": []}
    payload["state_errors"] = list(problems)
    payload["state_errors_turn"] = attempted
    payload["state_errors_rule"] = (
        "Ход сохранён в истории, но память (HOT, GM_BRIEF, карточки) не обновилась. "
        "Это неисправность движка, а не ошибка хода: не исправляй ход, сообщи игроку, "
        "что нужна починка. После исправления свёртка догонит все ходы сама.")
    body = _dump(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    nextpack.write_blocked(state_dir, payload.get("cursor"), problems, kind="state_errors",
                           rule=payload["state_errors_rule"])
    return payload
