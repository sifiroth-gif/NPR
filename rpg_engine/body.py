"""Тело после раны: усталость от кровопотери, и решает её кость.

Поправка игрока 25.11.2026 по бою в Йокогаме (Bleach RPG, III S32): лучшим тот бой сделало то,
что кости решали, а усталость шла от раны, а не от убыли энергии. Капитан держал печати,
пока кровь уходила; проверка состояния (d20 = 5 — ухудшение) дала шум в ушах, тёмные края,
давление 78/46, и вторая половина сцены — удержание пленника — шла уже против тела.

Здесь так же. Рана с кровотечением (`bleed`) после каждого хода с нагрузкой — проверка состояния
по полосе категорий, как всякий спор (`bands.py`): A — выносливость против нормы, D — нагрузка
хода, E — кровящие раны, F — уже потерянная кровь; физиология неточна, поэтому полоса шире на
категорию в обе стороны. Категория 3 и ниже — хуже на ступень (1 при критической — на две), 6 и
выше — средняя рана свернулась. Без нагрузки — раз в QUIET_MINUTES игрового времени. Кость d20
бросает свёртка (хеш хода), не ведущий: исход не смягчить. Ступень кровопотери — ось F всех
бросков, пока кровь не восполнена. Остановить кровь — только заявленное действие
(`d.heal: [{"who", "where", "stop": true}]`); восполнить — `"blood": true` (снимает ступень).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from . import bands

LOSS = ("норма", "слабость", "головокружение", "шок", "без сознания")
PENALTY = (0, 0.5, 1, 2, 3)              # к перевесу старых бросков d100; в новых — ось F (fight.burden)
LOAD = {"средняя": 0.5, "тяжёлая": 1.0, "критическая": 2.0}
BASE = 3.0                               # выносливость, с которой средняя рана в покое — ровно 50 на 50
QUIET_MINUTES = 10                       # без нагрузки — проверка раз в столько игровых минут
QUIET_MAX = 3
RECOVER_HOURS = 6                        # кровь не идёт: ступень спадает сама за столько часов
CUT = re.compile(r"(?:рез|рассек|рассеч|разруб|руб|колот|колол|проб|прорез|скальпел|клин|лезви|осколок|"
                 r"разорв|рван|перерез|копь|стрел|кунай|сюрикен|игл|клык|когт|ранен)", re.I)
SIGNS = {1: "слабость, пот, руки тяжелеют",
         2: "шум в ушах, темнеет по краям, тяжёлые ноги, сердце частит",
         3: "холодный пот, серые губы, мысли рвутся, равновесие уходит; только простые действия",
         4: "сознание гаснет — выход из боя; без помощи смерть"}


def bleeds(wound):
    """Кровит ли новая рана: тяжёлая и критическая — да, средняя — если режущая или колотая."""
    if "bleed" in wound:
        return bool(wound["bleed"])
    severity = wound.get("severity")
    if severity in ("тяжёлая", "критическая"):
        return True
    text = f"{wound.get('what') or ''} {wound.get('lost') or ''}".replace("ё", "е").replace("Ё", "Е")
    return severity == "средняя" and bool(CUT.search(text))


def _state(hot):
    return hot.setdefault("body", {})


def _view(hot):
    return hot.get("body") or {}


def mode(hot, who):
    """Ordinary haemodynamics, terminal death, or explicitly exceptional physiology."""
    return (_view(hot).get(who) or {}).get("mode", "ordinary")


def set_status(hot, who, item, turn_id, at):
    """Source-attributed physiological state; never infer death from a loss roll."""
    status = item["mode"]
    if status == "ordinary":
        record = _state(hot).get(who)
        if record and record.get("mode") in ("dead", "exceptional"):
            record.pop("mode", None)
            record.pop("status_source", None)
            record.pop("status_note", None)
            record["at"] = at
        return
    record = _state(hot).setdefault(who, {})
    for key in ("loss", "stage", "last", "restored"):
        record.pop(key, None)
    record.update({"mode": status, "at": at, "status_source": item.get("source") or turn_id,
                   "status_note": item["reason"]})
    if status == "dead":
        # Preserve wounds as history; dead tissue is not an active patient.
        for wound in (hot.get("injuries") or {}).get(who, []):
            if wound.get("bleed"):
                wound["bleed"] = False
                wound["closed_by_death"] = record["status_source"]
    fresh = _state(hot).get("fresh")
    if isinstance(fresh, dict):
        fresh["rows"] = [row for row in fresh.get("rows", []) if row.get("who") != who]


def stage(hot, who):
    if mode(hot, who) != "ordinary":
        return 0
    return int((_view(hot).get(who) or {}).get("loss") or 0)


def penalty(hot, who):
    return PENALTY[min(stage(hot, who), len(PENALTY) - 1)]


def _parse(value):
    try:
        return datetime.fromisoformat(str(value)[:19])
    except (TypeError, ValueError):
        return None


def _die(turn_id, who, n):
    digest = hashlib.sha256(f"body|{turn_id}|{who}|{n}".encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") % 20 + 1


def _exerted(who, delta):
    rolls = list(delta.get("rolls") or [])
    fight = delta.get("fight") if isinstance(delta.get("fight"), dict) else {}
    rolls += list(fight.get("rolls") or [])
    if who == "shiki":
        chakra = delta.get("chakra") if isinstance(delta.get("chakra"), dict) else {}
        return bool(fight.get("bursts") or chakra.get("spend")
                    or any(isinstance(r, dict) and not r.get("by") for r in rolls))
    return any(isinstance(r, dict) and r.get("by") == who for r in rolls)


def _level(hot, who):
    from . import fight
    if who == "shiki":
        return fight.shiki_level(hot, "endurance") or BASE
    foe = ((((hot.get("_fight") or {}).get("active") or {}).get("foes") or {}).get(who))
    return fight.foe_level(foe, "endurance") if foe else BASE


def _band(hot, who, load, exert, loss):
    """Полоса проверки состояния: выносливость, нагрузка, кровящие раны, потерянная кровь; ±1 — физиология."""
    axes = {"A": bands.axis(_level(hot, who) - BASE), "B": (0, 0), "C": (0, 0),
            "D": (-1, -1) if exert else (0, 0),
            "E": (-2, -2) if load >= 2 else (-1, -1) if load >= 1 else (-1, 0) if load else (0, 0),
            "F": (-min(2, loss), -min(2, loss))}
    cats, _m = bands.band(axes)
    cats = list(range(max(1, cats[0] - 1), min(7, cats[-1] + 1) + 1))
    while len(cats) > 5:
        cats = cats[1:-1]
    return cats


MEDICAL_CUTOVER = "s14.text.024"


def modern(turn_id):
    from .aging import _ordinal
    here, cut = _ordinal(turn_id), _ordinal(MEDICAL_CUTOVER)
    return here is not None and cut is not None and here >= cut


def recovery_anchor(hot, who, turn_id, at):
    """Recovery time starts at haemostasis, not the last bleeding roll."""
    record = _view(hot).get(who)
    if modern(turn_id) and record and record.get("loss") and at and not any(
            w.get("bleed") and not w.get("healed") for w in (hot.get("injuries") or {}).get(who, [])):
        record["at"] = at


def heal(hot, item, turn_id, at=None):
    """`stop` — кровь остановлена (рана остаётся); `blood` — кровь восполнена на ступень. True — обработано."""
    who = item.get("who")
    if item.get("blood"):
        record = _state(hot).get(who)
        if record and record.get("loss"):
            record["loss"] = int(record["loss"]) - 1
            record["stage"] = LOSS[record["loss"]]
            record["restored"] = turn_id
            if modern(turn_id) and at:
                record["at"] = at
    if not item.get("stop"):
        return bool(item.get("blood"))
    where, source = item.get("where"), item.get("injury_turn")
    for wound in (hot.get("injuries") or {}).get(who) or []:
        if (wound.get("bleed") and (not where or wound.get("where") == where)
                and (not source or wound.get("turn") == source)):
            wound["bleed"] = False
            wound["stopped"] = turn_id
    recovery_anchor(hot, who, turn_id, at)
    return True


def check(hot, turn, delta):
    """После хода: проверки состояния всех, у кого идёт кровь; спад ступени у тех, у кого не идёт."""
    # Explicit status also applies in a sourced out-of-character correction.
    for who, item in (delta.get("body_status") or {}).items():
        set_status(hot, who, item, turn["id"], turn["at"])
    # A recorded lethal combat outcome must stop ordinary blood-loss rolls.
    for field in ("foes", "foe_state"):
        for who, item in ((delta.get("fight") or {}).get(field) or {}).items():
            if isinstance(item, dict) and item.get("status") in ("dead", "killed", "мёртв", "мертв", "убит"):
                if mode(hot, who) != "dead":
                    set_status(hot, who, {"mode": "dead", "reason": "Подтверждённый смертельный исход боя"},
                               turn["id"], turn["at"])
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    now = _parse(turn.get("at"))
    if "body" not in hot and not any(w.get("bleed") and not w.get("healed")
                                     for ws in (hot.get("injuries") or {}).values() for w in ws):
        return []
    state = _state(hot)
    fresh = []
    patients = dict(hot.get("injuries") or {})
    if modern(turn.get("id")):
        # Full anatomical repair does not erase residual systemic blood loss.
        for who, record in state.items():
            if who != "fresh" and isinstance(record, dict) and record.get("loss"):
                patients.setdefault(who, [])
    for who, wounds in patients.items():
        if mode(hot, who) != "ordinary":
            continue
        open_ = [w for w in wounds if not w.get("healed") and w.get("bleed")]
        record = state.get(who)
        if not open_:
            if record and record.get("loss") and now:   # кровь не идёт — тело медленно возвращает своё
                then = _parse(record.get("at"))
                if then and (now - then).total_seconds() >= RECOVER_HOURS * 3600:
                    record["loss"] = max(0, int(record["loss"]) - int((now - then).total_seconds() // (RECOVER_HOURS * 3600)))
                    record["stage"], record["at"] = LOSS[record["loss"]], turn.get("at")
            continue
        record = state.setdefault(who, {"loss": 0, "stage": LOSS[0], "at": min(str(w.get("at")) for w in open_)})
        exert = _exerted(who, delta)
        if exert:
            count = 1
        else:
            then = _parse(record.get("at"))
            count = min(QUIET_MAX, int((now - then).total_seconds() // (QUIET_MINUTES * 60))) if now and then else 0
        if not count:
            continue
        load = sum(LOAD.get(w.get("severity"), 0) for w in open_)
        critical = any(w.get("severity") == "критическая" for w in open_)
        before = int(record.get("loss") or 0)
        for n in range(count):
            loss = int(record.get("loss") or 0)
            cats = _band(hot, who, load, exert, loss)
            value = _die(turn.get("id"), who, n)
            cat = bands.from_die(cats, value)
            if cat <= 3:
                loss += 2 if cat == 1 and critical else 1
            elif cat >= 6:
                for wound in open_:
                    if wound.get("severity") == "средняя":
                        wound["bleed"], wound["stopped"] = False, f"{turn.get('id')}: сгусток"
            record["loss"] = min(loss, len(LOSS) - 1)
            record["last"] = {"turn": turn.get("id"), "die": value, "band": cats, "cat": cat,
                              "exert": exert, "load": load}
            if record["loss"] == len(LOSS) - 1:
                break
            if modern(turn.get("id")):
                # A clot may end bleeding before all quiet checks. Recompute
                # the load for the next check, never bleed from a closed wound.
                open_ = [w for w in open_ if w.get("bleed")]
                if not open_:
                    break
                load = sum(LOAD.get(w.get("severity"), 0) for w in open_)
                critical = any(w.get("severity") == "критическая" for w in open_)
        record["stage"], record["at"] = LOSS[record["loss"]], turn.get("at")
        fresh.append({"who": who, "before": before, "after": record["loss"], **record["last"],
                      "wounds": [w.get("where") for w in open_ if w.get("bleed")]})
    state["fresh"] = {"turn": turn.get("id"), "rows": fresh}
    return fresh


def must(hot):
    """Пункт NEXT.must: исход костей состояния последнего хода (Шики — первым)."""
    from . import will
    fresh = (_view(hot).get("fresh") or {})
    if fresh.get("turn") != (hot.get("cursor") or {}).get("id"):
        return None
    rows = sorted(fresh.get("rows") or [], key=lambda r: r["who"] != "shiki")
    parts = []
    for row in rows:
        if mode(hot, row["who"]) != "ordinary":
            continue
        if row["after"] == row["before"] and row["after"] < 2:
            continue      # держится на лёгкой ступени — без пункта, видно в GM_BRIEF.fight.body
        who = "Шики" if row["who"] == "shiki" else will._name(hot, row["who"])
        where = ", ".join(w for w in row["wounds"] if w) or "рана"
        if row["after"] > row["before"]:
            verdict = f"хуже — «{LOSS[row['after']]}» (ось F всех бросков)"
        else:
            verdict = f"держится, но «{LOSS[row['after']]}»"
        parts.append(f"{who}: проверка состояния — полоса {bands.text(row['band'])}, d20 {row['die']}, категория "
                     f"{row['cat']} ({bands.CATEGORIES[row['cat']]}; {where}, кровь идёт"
                     f"{', нагрузка' if row['exert'] else ''}) — {verdict}. Видно телом: {SIGNS.get(row['after'], '')}")
    if not parts:
        return None
    return ("Тело решила кость, не запас сил: " + " ".join(parts) + ". В прозе — ощущение, без чисел. "
            "Кровь остановит только заявленное действие — d.heal с stop; восполнить — blood.")


def brief(hot):
    return {who: {k: v for k, v in record.items() if k in ("loss", "stage", "last", "mode", "status_source", "status_note")}
            for who, record in _view(hot).items() if who != "fresh" and isinstance(record, dict)}
