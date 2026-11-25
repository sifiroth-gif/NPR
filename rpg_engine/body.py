"""Тело после раны: усталость от кровопотери, и решает её кость.

Поправка игрока 25.11.2026 по бою в Йокогаме (Bleach RPG, III S32): лучшим тот бой сделало то,
что кости решали, а усталость шла от раны, а не от убыли энергии. Капитан держал печати,
пока кровь уходила; проверка состояния (d20 = 5 — ухудшение) дала шум в ушах, тёмные края,
давление 78/46, и вторая половина сцены — удержание пленника — шла уже против тела.

Здесь так же. Рана с кровотечением (`bleed`) после каждого хода с нагрузкой — бросок, порог,
код учитывает раны и нагрузку, а без нагрузки — раз в QUIET_MINUTES игрового времени. Кость бросает
свёртка (хеш хода), не ведущий: исход не смягчить. Ступень кровопотери — поправка ко всем
броскам, пока кровь не восполнена. Остановить кровь — только заявленное действие
(`d.heal: [{"who", "where", "stop": true}]`); восполнить — `"blood": true` (снимает ступень).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

LOSS = ("норма", "слабость", "головокружение", "шок", "без сознания")
PENALTY = (0, 0.5, 1, 2, 3)              # к перевесу всех бросков; без сознания — выход из боя
LOAD = {"средняя": 0.5, "тяжёлая": 1.0, "критическая": 2.0}
BASE = 3.0                               # выносливость, с которой средняя рана в покое — ровно 50 на 50
EXERT = 0.5                              # нагрузка хода: бросок, техника, рывок
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


def stage(hot, who):
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
    return int.from_bytes(digest[:2], "big") % 100


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


def heal(hot, item, turn_id):
    """`stop` — кровь остановлена (рана остаётся); `blood` — кровь восполнена на ступень. True — обработано."""
    who = item.get("who")
    if item.get("blood"):
        record = _state(hot).get(who)
        if record and record.get("loss"):
            record["loss"] = int(record["loss"]) - 1
            record["stage"] = LOSS[record["loss"]]
            record["restored"] = turn_id
    if not item.get("stop"):
        return bool(item.get("blood"))
    where = item.get("where")
    for wound in (hot.get("injuries") or {}).get(who) or []:
        if wound.get("bleed") and (not where or wound.get("where") == where):
            wound["bleed"] = False
            wound["stopped"] = turn_id
    return True


def check(hot, turn, delta):
    """После хода: проверки состояния всех, у кого идёт кровь; спад ступени у тех, у кого не идёт."""
    from . import fight
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    now = _parse(turn.get("at"))
    if "body" not in hot and not any(w.get("bleed") and not w.get("healed")
                                     for ws in (hot.get("injuries") or {}).values() for w in ws):
        return []
    state = _state(hot)
    fresh = []
    for who, wounds in (hot.get("injuries") or {}).items():
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
            edge = round(_level(hot, who) - BASE - load - (EXERT if exert else 0) - PENALTY[loss] / 2, 2)
            p = max(5, min(95, round(50 + 15 * edge)))
            value = _die(turn.get("id"), who, n)
            grade = fight.outcome(p, value)
            if grade == "провал":
                loss += 2 if critical else 1
            elif grade == "с ценой" and exert:
                loss += 1
            elif grade == "чисто":
                for wound in open_:
                    if wound.get("severity") == "средняя":
                        wound["bleed"], wound["stopped"] = False, f"{turn.get('id')}: сгусток"
            record["loss"] = min(loss, len(LOSS) - 1)
            record["last"] = {"turn": turn.get("id"), "die": value, "p": p, "grade": grade,
                              "exert": exert, "load": load}
            if record["loss"] == len(LOSS) - 1:
                break
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
        if row["after"] == row["before"] and row["after"] < 2:
            continue      # держится на лёгкой ступени — без пункта, видно в GM_BRIEF.fight.body
        who = "Шики" if row["who"] == "shiki" else will._name(hot, row["who"])
        where = ", ".join(w for w in row["wounds"] if w) or "рана"
        if row["after"] > row["before"]:
            verdict = f"хуже — «{LOSS[row['after']]}» (−{PENALTY[row['after']]:g} ко всем броскам)"
        else:
            verdict = f"держится, но «{LOSS[row['after']]}» (−{PENALTY[row['after']]:g})"
        parts.append(f"{who}: кость состояния {row['die']} при пороге {row['p']} ({where}, кровь идёт"
                     f"{', нагрузка' if row['exert'] else ''}) — {verdict}. Видно телом: {SIGNS.get(row['after'], '')}")
    if not parts:
        return None
    return ("Тело решила кость, не запас сил: " + " ".join(parts) + ". В прозе — ощущение, без чисел. "
            "Кровь остановит только заявленное действие — d.heal с stop; восполнить — blood.")


def brief(hot):
    return {who: {k: v for k, v in record.items() if k in ("loss", "stage", "last")}
            for who, record in _view(hot).items() if who != "fresh" and isinstance(record, dict)}
