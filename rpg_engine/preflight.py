"""Read-only draft preflight. No extra connector call, no semantic proof.

The optional CLI uses an already available NEXT and draft TURN. Ordinary
chat follows the same compact contract mentally before publishing. A passed
mechanical check does NOT certify plot, perception, medicine or player agency.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

from . import cognition, continuity_guard, fight, hot, overlay, prose_lint

# Player's binding instruction s17.text.110; do not retroactively judge old turns.
DESCRIPTION_FROM = (17, 111)
DESCRIPTION_180_FROM = (17, 114)
DESCRIPTION_RULE = (
    "Описания: каждый игровой ход, даже диалог — предметная среда, доступная Шики "
    "сенсорика, конкретное действие, видимые реакции NPC. Минимум 3 абзаца "
    "повествования по ≥8 слов, всего ≥180 слов вне реплик и шапок; нижний порог, не цель. "
    "Не набивай объём повторами или чужими мыслями. Если не выполнено, допиши весь "
    "черновик до публикации даже первой строки. OOC/технические записи исключены."
)


def description_errors(turn):
    """Structural floor only: meaningful detail still requires GM judgement."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    if hot.turn_number(turn["id"]) < DESCRIPTION_FROM:
        return []
    text = turn.get("gm", "")
    text = re.sub(r"<(?:box|svg|caption|text)\b[^>]*>.*?</(?:box|svg|caption|text)>", "", text, flags=re.S)
    text = re.sub(r"\x60\x60\x60.*?\x60\x60\x60", "", text, flags=re.S)
    lengths = []
    for block in re.split(r"\n\s*\n", text):
        lines = []
        for raw in block.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "**", "—", "–", "-", ">", "[", "<", "***")):
                continue
            lines.append(line)
        words = re.findall(r"[А-Яа-яЁёA-Za-z]+(?:-[А-Яа-яЁёA-Za-z]+)*", " ".join(lines))
        if len(words) >= 8:
            lengths.append(len(words))
    minimum = 180 if hot.turn_number(turn["id"]) >= DESCRIPTION_180_FROM else 60
    if len(lengths) < 3 or sum(lengths) < minimum:
        return [f"Описания: только {len(lengths)} содержательных абзацев, {sum(lengths)} слов; "
                f"нужно ≥3 абзацев по ≥8 слов и ≥{minimum} слов повествования. Допиши сцену до публикации."]
    return []


CHECKLIST = (
    "Заявка: все действия/слова, попытка ≠ исход; не решай за Шики.",
    "Источник: поздняя поправка, состояние/умение, неизвестное не факт.",
    "Перспектива: доступ Шики и канал NPC; никаких скрытых мыслей/GM_ONLY.",
    "Цена: время, ресурс, кости, техника, функция и остаточный дефицит.",
    "Мир: срок, доступная весть, самостоятельный мотив и реакция NPC.",
    "Дельта: только случившееся, точные ID, место/состав, цена; затем сцена и запись.",
    DESCRIPTION_RULE,
)
RULE = "До публикации: " + " ".join(CHECKLIST)
LABEL = re.compile(r"^\*\*Мугами Шики:\*\*\s*[—–]\s*(.*)$", re.I | re.M)


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _normal(value):
    return " ".join(str(value or "").split())


def _cost(packet, turn, errors, cues):
    delta = turn.get("d") or {}
    patch = delta.get("chakra") or {}
    bursts = (delta.get("fight") or {}).get("bursts", 0)
    if type(bursts) is not int or bursts < 0:
        errors.append("Некорректное число рывков.")
        return None
    total = bursts * fight.BURST_COST
    for item in patch.get("spend") or []:
        raw = item.get("cost", fight.COST.get(str(item.get("rank", "")).upper()))
        if not _number(raw) or raw < 0:
            errors.append("Расход чакры: неверная цена/ранг.")
            continue
        total += raw * fight.TECHNIQUE_COST_SCALE
    if not total and "set" not in patch:
        return None
    if "set" in patch:
        if not _number(patch["set"]) or not 0 <= patch["set"] <= fight.MAX_CHAKRA:
            errors.append("Установка чакры вне диапазона.")
            return None
        if not str(patch.get("why") or "").strip():
            errors.append("Установка чакры без источника/причины.")
        cues.append("Проверь основание d.chakra.set; это не способ обойти расход.")
    source = packet.get("chakra") or {}
    stamp = (packet.get("lag") or {}).get("chakra_at")
    if not _number(source.get("value")) or not stamp:
        cues.append("Нет проверенного исходного запаса; не утверждай полную силу.")
        return {"spend": total, "available": None}
    ledger = {"shiki": {"chakra": {"value": source["value"], "at": stamp,
                                 **({"locked_until": source["locked_until"]} if source.get("locked_until") else {})}}}
    fight._regen(ledger, turn["at"])
    available = patch.get("set", ledger["shiki"]["chakra"]["value"])
    if total > available + 0.01:
        cues.append("Расход выше запаса: проверь срыв/ослабление, внешний источник и потерю сознания; полная сила не гарантирована.")
    if total > 0 and total >= available and not patch.get("collapse"):
        errors.append("Исчерпанная чакра без d.chakra.collapse; проверь исход.")
    return {"spend": round(total, 2), "available_at_end": available,
            "note": "Верхняя оценка на конец хода; в момент действия запас может быть меньше."}


def _rolls(packet, turn, errors, cues):
    d = turn.get("d") or {}
    rows = (d.get("rolls") or []) + ((d.get("fight") or {}).get("rolls") or [])
    if not rows:
        return
    pool = packet.get("dice")
    if not pool or (packet.get("lag") or {}).get("dice_for") != turn["id"]:
        errors.append("Нет костей для этого хода; старый пул не использовать.")
        return
    for index, row in enumerate(rows):
        if index >= len(pool) or row.get("value") != pool[index]:
            errors.append("Бросок %d: не та кость/лишний бросок." % (index + 1))
            continue
        band = fight._declared(row.get("band"))
        if band:
            cat = fight.from_band(band, pool[index])
            if type(row.get("cat")) is int and row["cat"] > cat:
                errors.append("Бросок %d: категория выше результата заявленной полосы." % (index + 1))
            if band[0] == 1 and not row.get("risk"):
                cues.append("Бросок %d: серьёзный риск не назван." % (index + 1))
        else:
            cues.append("Бросок %d: сверь полосу и оси по COMBAT." % (index + 1))


def _clinical(packet, turn, errors, cues):
    d = turn.get("d") or {}
    rows = (d.get("heal") or []) + ((d.get("fight") or {}).get("heal") or [])
    if not rows:
        return
    med = packet.get("medical") or {}
    for item in rows:
        who, where, source = (item.get(k) for k in ("who", "where", "injury_turn"))
        if item.get("all") and (where or source):
            errors.append("Лечение: all конфликтует с локализацией/источником.")
        if not where and not item.get("all") and not item.get("blood") and not source:
            errors.append("Лечение: нет однозначного селектора раны.")
        patient = (med.get("patients") or {}).get(who)
        if not patient:
            cues.append("Пациент %s: нет клинического снимка; проверь источник/доступ." % who)
            continue
        wounds = [dict(zip(med.get("fields") or [], row)) for row in patient.get("w") or []]
        matches = [row for row in wounds if (not where or row.get("where") == where)
                   and (not source or row.get("turn") == source)]
        if where and len(matches) > 1 and not source:
            errors.append("Лечение: несколько ран %s; нужен injury_turn." % who)
        if where and not matches:
            cues.append("Лечение: рана не совпала со снимком; сверить исходный ход.")
    cues.append("Лечение: проверить морфологию, функцию, кровь, комплект, время; закрытие не равно полной функции.")


def inspect(packet, turn, *, receipts=(), used=()):
    """Return mechanical errors and manual review cues. Never mutate arguments.

    used is an optional LOCAL list of explicit capability IDs, not a field of
    TURN, and not a lexical guess that mentioning a technique activates it.
    """
    errors, cues = [], []
    try:
        hot.validate_turn(turn)
    except (ValueError, TypeError, OverflowError) as exc:
        return {"mechanical_ok": False, "errors": [str(exc)], "review": [], "checklist": list(CHECKLIST)}
    if not isinstance(packet, dict):
        return {"mechanical_ok": False, "errors": ["NEXT не объект."], "review": [], "checklist": list(CHECKLIST)}
    try:
        packet = overlay.project(packet, receipts)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return {"mechanical_ok": False, "errors": ["Не удалось проверить курсор: " + str(exc)],
                "review": [], "checklist": list(CHECKLIST)}
    if packet.get("fold_errors") or packet.get("state_errors") or (packet.get("lag_status") or {}).get("write_allowed") is False:
        errors.append("Состояние заблокировано/не подтверждено; не публикуй новый ход.")
    review = turn.get("review")
    if (turn.get("id") != packet.get("next_id") or not isinstance(review, dict)
            or review.get("predecessor") != packet.get("cursor")):
        errors.append("ID или predecessor не совпадает с проверенным курсором.")
    start = (packet.get("lag") or {}).get("at")
    if start and hot.parse_at(turn["at"]) < hot.parse_at(start):
        errors.append("Время хода раньше курсора.")
    if turn.get("kind") == "ooc":
        if start and hot.parse_at(turn["at"]) != hot.parse_at(start):
            errors.append("OOC не двигает игровые часы.")
        return {"mechanical_ok": not errors, "errors": errors, "review": cues, "checklist": ["Ответь на вопрос или поправку; не играй сцену."]}
    d = turn.get("d") or {}
    if turn.get("kind") in ("technical", "void"):
        cues.append("Технический ход: не выдумывай игровое событие.")
        return {"mechanical_ok": not errors, "errors": errors, "review": cues, "checklist": list(CHECKLIST)}
    if not isinstance(d.get("scene"), dict) or not isinstance(d["scene"].get("present"), list):
        errors.append("Запиши актуальный состав d.scene.present, не наследуй молча.")
    gm = turn["gm"]
    errors.extend(description_errors(turn))
    errors.extend(cognition.inspect(packet, turn))
    speech = _normal(continuity_guard.player_speech(turn["player"]))
    lines = _normal(" ".join(LABEL.findall(gm)))
    if speech and speech not in lines:
        cues.append("Сверь дословную речь Шики: возможен пропуск или другая атрибуция.")
    actions = re.findall(r"#([^#]+)#", turn["player"])
    if actions:
        cues.append("Сверь все %d фрагментов действий; прерванное — попытка, не добавляй решения." % len(actions))
    for hit in (prose_lint.speakers(gm), prose_lint.mechanics(gm)):
        if hit:
            errors.append(hit["quote"])
    names = [str(row.get("name") or key) for key, row in (d.get("cast") or {}).items() if isinstance(row, dict)]
    for key, row in (packet.get("present") or {}).items():
        if isinstance(row, dict):
            names.append(str(row.get("name") or key))
    prior_present = set(packet.get("present") or {})
    after_present = set((d.get("scene") or {}).get("present") or [])
    local_present = prior_present | after_present | {"shiki", "nemera"}
    for actor, patch in (d.get("cast") or {}).items():
        if isinstance(patch, dict) and patch.get("knows+") and actor not in local_present:
            if not any(ev.get("actor") == actor and ev.get("channel") in ("message", "document", "witness", "inference")
                       for ev in d.get("knowledge_events") or []):
                cues.append("NPC %s вне известного состава: знания без подтверждённого канала." % actor)
    for ev in d.get("knowledge_events") or []:
        if ev.get("visibility") == "gm_only":
            patch = (d.get("cast") or {}).get(ev["actor"]) or {}
            if ev["claim"] in (patch.get("knows+") or []):
                errors.append("Закрытое знание продублировано в открытом knows+.")
    if packet.get("news_id") in (d.get("must_done") or []) and not any(
            ev.get("id") == packet["news_id"] for ev in d.get("news_delivered") or []):
        errors.append("Весть нельзя закрыть только must_done: требуется news_delivered.")
    interior = prose_lint.interior(gm, names)
    if interior:
        cues.append(interior["quote"])
    cap = packet.get("capabilities") or {}
    abilities = {row["id"]: row for field in ("items", "catalog")
                 for row in cap.get(field, [])}
    unavailable = set(cap.get("unavailable_ids") or [])
    indexed = set(cap.get("catalog_ids") or [])
    for ident in used:
        row = abilities.get(ident)
        if ident in unavailable or (row and row.get("activation") == "unavailable"):
            errors.append("Недоступная способность: " + ident)
        elif not row and ident not in indexed:
            cues.append("Неизвестная способность %s: проверь реестр." % ident)
        else:
            cues.append("Проверь активацию, условия и цену: " + ident)
    try:
        _rolls(packet, turn, errors, cues)
        cost = _cost(packet, turn, errors, cues)
        _clinical(packet, turn, errors, cues)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        errors.append("Некорректный расчёт/клиническая запись: " + str(exc))
        cost = None
    if packet.get("source_review"):
        cues.append("Есть конфликт источников: проверь указанные первичные ходы.")
    if packet.get("must") or (packet.get("lag_status") or {}).get("unresolved_must"):
        cues.append("Сверь актуальные обязательства; подтверждай только выполненное, не повторяй старое.")
    if packet.get("due") or packet.get("offscreen"):
        cues.append("Сроки и вести: факт мира не означает знание Шики; проверь канал.")
    if (packet.get("lag_status") or {}).get("status") == "projected":
        cues.append("Локальная проекция не обновляет знания NPC/мир/раны; проверь неизвестное адресно.")
    return {"mechanical_ok": not errors, "errors": errors, "review": cues,
            "cost": cost, "checklist": list(CHECKLIST)}


def postcheck(turn):
    """Already published: report only, never reject a semantic discrepancy."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    speech = _normal(continuity_guard.player_speech(turn.get("player", "")))
    lines = _normal(" ".join(LABEL.findall(turn.get("gm", ""))))
    errors = description_errors(turn)
    if speech and len(speech.split()) >= 4 and speech not in lines:
        errors.append("Предпроверка: дословная речь Шики не найдена под его подписью; сверить исходный текст.")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("next", type=Path)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--used", action="append", default=[])
    args = parser.parse_args(argv)
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = inspect(read(args.next), read(args.draft),
                     receipts=read(args.receipts) if args.receipts else (), used=args.used)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["mechanical_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
