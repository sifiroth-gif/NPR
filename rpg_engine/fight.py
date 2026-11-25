"""Бой и чакра: заранее выданные кости, полоса исхода, раны, рывки, мораль, конец боя.

С 25.11.2026 — по образцу боя в Йокогаме (Bleach III S32, механика LIVE/2): соотношение сил по
шести осям даёт полосу категорий исхода 1–7, кость d20 раскладывается по полосе (`bands.py`).
Ниже, в старом описании, — прежняя модель d100 с порогом: по ней проверяются только старые ходы.

Ведущий в чате не запускает код до ответа. Поэтому свёртка после каждого хода
выдаёт в GM_BRIEF кости на следующий ход, а после записи проверяет, что ведущий
взял их по порядку и посчитал порог по формуле. Код не решает исход за
ведущего и не блокирует игру: расхождения уходят в CHECKS.json.

Порог: ``p = 50 + 15 × перевес``, в пределах 5–95; перевес = умение Шики −
умение противника + поправки. Перевес от +3 — без броска, от −3 — этим
способом невозможно. Кость меньше ``p − 25`` — чисто, меньше ``p`` — успех,
меньше ``p + 15`` — с ценой, иначе провал.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import json
import re
from datetime import datetime

from . import bands, body

DICE = 12
EXCHANGE_LIMIT = 6        # обменов в одном бою до требования развязки
BURSTS_PER_EXCHANGE = 4   # жёстких стартов шуншина до спада (лист: 3–4)
ROLLS_KEEP = 12
CLOSED_KEEP = 10

# Уровень противника по рангу, если умение не задано (шкала листа Шики, 0–5).
RANK = {"civilian": 0.5, "genin": 1.5, "chunin": 2.5, "jonin": 3.5, "elite": 4.2, "kage": 5.0,
        "beast": 3.0}
RANK_SPEED = {"civilian": 1.0, "genin": 2.0, "chunin": 2.5, "jonin": 3.5, "elite": 4.2, "kage": 5.0,
              "beast": 3.5}

# Умение боя → ключ листа Шики (HOT.shiki.sheet → stats_out_of_5).
SHIKI_SKILL = {
    "speed": "shunshin_no_jutsu", "shunshin": "shunshin_no_jutsu",
    "reaction": "reflex_and_coordination", "weapons": "reflex_and_coordination",
    "taijutsu": "taijutsu", "ninjutsu": "ninjutsu", "genjutsu": "genjutsu",
    "medical": "medical_ninjutsu", "sensory": "sensory", "stealth": "stealth",
    "strength": "physical_strength", "endurance": "physical_endurance",
    "control": "chakra_control", "suiton": "suiton", "raiton": "raiton", "futon": "futon",
    "hyoton": "hyoton", "shiton": "ninjutsu", "byoton": "ninjutsu", "genome": "zero_genome_mastery",
}
CHAKRA_SKILLS = {"ninjutsu", "genjutsu", "medical", "control", "suiton", "raiton", "futon",
                 "hyoton", "shiton", "byoton"}
PHYSICAL_SKILLS = {"speed", "shunshin", "reaction", "weapons", "taijutsu", "strength", "endurance",
                   "stealth"}

# Постоянный Режим отшельника освоен в Рьючидо 04.03.1287. Старый
# структурированный лист умений относится к 1283 году, ДО обучения.
# Добавочный боевой эффект в системе 0–5 — кампания, НЕ числовой канон манги.
SAGE_TRAINED_AT = "0010-03-04T09:44:00"
SAGE_BONUS = {
    "speed": 0.5, "shunshin": 0.5, "reaction": 0.5, "weapons": 0.5,
    "taijutsu": 0.5, "strength": 0.5, "endurance": 0.5, "sensory": 0.75,
}


from . import fusion


def sage_active(hot):
    """Устойчивая природная энергия после обучения, без второй активации."""
    cursor = str(((hot.get("cursor") or {}).get("at") or ""))
    if not cursor or cursor < SAGE_TRAINED_AT:
        return False
    chakra = (((hot.get("shiki") or {}).get("chakra") or {}).get("value"))
    return not isinstance(chakra, (int, float)) or chakra > 0


# Поправки, которые ведущий может назвать сам: имя → (минимум, максимум).
MODS = {"внезапность": (0, 1), "позиция": (-1, 1), "подготовка": (0, 1), "укрытие": (-1, 1),
        "дистанция": (-1, 1), "местность": (-1, 1), "численность": (-1, 1), "знание": (0, 0.5),
        "ужас": (0, 0.5), "помеха": (-1, 0)}
SEVERITY = {"лёгкая": 0, "средняя": 0.5, "тяжёлая": 1, "критическая": 2}
MORALE = {"steady": 0, "shaken": 0.5, "breaking": 1, "broken": 1}
ACTIVE = "active"

# Именной противник (поправка игрока 23.11.2026: «бои были лёгкие; дальше именные и сильные — честно,
# противник может прервать действие Шики и действовать между его действиями»).
ROSTER = "campaigns/shiki/ref/FOES.json"
NAMED_RANKS = ("elite", "kage")
NAMED_EDGE_CAP = 2.0       # перевес Шики против именного — не выше: p ≤ 80, всегда бросок
LONG_BONUS = 1.0           # только подлинно длинный ритуал, зарядка, лечение, НЕ быстрые печати Шики
DEFENSE = {"genjutsu": "genjutsu", "taijutsu": "reaction", "weapons": "reaction", "ninjutsu": "reaction",
           "speed": "speed", "shunshin": "speed", "reaction": "reaction", "strength": "strength",
           "sensory": "stealth", "stealth": "sensory"}
_ROSTER = None


def roster():
    """Карточки именных противников канона: {ключ: {names, skills, traits}}."""
    global _ROSTER
    if _ROSTER is None:
        try:
            path = Path(__file__).resolve().parents[1] / ROSTER
            _ROSTER = json.loads(path.read_text(encoding="utf-8")).get("foes") or {}
        except (OSError, ValueError):
            _ROSTER = {}
    return _ROSTER


def canon_card(key, foe):
    """Карточка канона по ключу, по полю canon или по имени."""
    cards = roster()
    ref = foe.get("canon") or key
    if ref in cards:
        return ref, cards[ref]
    name = str(foe.get("name") or "").casefold().strip()
    for ref, card in cards.items():
        if name and any(name == str(n).casefold() for n in card.get("names") or []):
            return ref, card
    return None, None


ARSENAL_SHOW = 4          # ещё не пущенных в ход приёмов в NEXT
NEMERA_QUIET = 10         # ходов без Немеры вне боя — напоминание о её своей воле
NEMERA = re.compile(r"немер|отрост|втор\w* чакр|за семью", re.I)
NEMERA_PROTOCOL = ("Немера по своему протоколу (ref/NEMERA.md), без просьбы игрока: в каждом обмене одно-два "
                   "действия отростков по её выбору — перехват, хватка, отвод удара, ощупывание чакры врага в 5 м; "
                   "порядок интересов: сохранить сосуд → увидеть предел противника → своё. Может отказать и "
                   "торговаться, но не исчезает из боя. Её чакра — в записи хода, в прозе без чисел.")


def _arsenal_used(card, text):
    return [name for name, key in card.get("arsenal") or [] if key and re.search(key, text, re.I)]


def allies(hot):
    """Присутствующие союзники с карточкой канона (side: ally): {ключ: (ref, карточка)}."""
    out = {}
    for key in (hot.get("scene") or {}).get("present") or []:
        card = (hot.get("cast") or {}).get(key) or {}
        ref, canon = canon_card(key, {"name": card.get("name")})
        if canon and canon.get("side") == "ally":
            out[key] = (ref, canon)
    return out


def track_arsenal(hot, turn):
    """Отмечает пущенные в ход приёмы именных противников и союзников по тексту сцены."""
    active = (hot.get("_fight") or {}).get("active")
    if not active or turn.get("kind") in ("ooc", "technical", "void"):
        return
    text = str(turn.get("gm") or "")
    used = active.setdefault("used", {})
    people = {k: canon_card(k, f)[1] for k, f in (active.get("foes") or {}).items() if named(f)}
    people.update({k: c for k, (_r, c) in allies(hot).items()})
    for key, card in people.items():
        if card:
            fresh = [n for n in _arsenal_used(card, text) if n not in (used.get(key) or [])]
            if fresh:
                used[key] = (used.get(key) or []) + fresh


def backfill_arsenal(hot, rows, recent=15):
    """Бой шёл до появления учёта: пущенное в ход по последним сыгранным ходам — один раз."""
    active = (hot.get("_fight") or {}).get("active")
    if not active or "used" in active:
        return
    active["used"] = {}
    for row in [r for r in rows if r.get("kind") not in ("ooc", "technical", "void")][-recent:]:
        track_arsenal(hot, row)


def arsenal_left(hot, key, card):
    used = set((((hot.get("_fight") or {}).get("active") or {}).get("used") or {}).get(key) or [])
    return [name for name, _k in card.get("arsenal") or [] if name not in used][:ARSENAL_SHOW]


def nemera_turn(hot, turn):
    """Немера не ждёт просьбы: в бою — в каждом обмене, вне боя — замечание после долгого молчания."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    if fusion.active(hot):
        return []
    if "nemera" not in (hot.get("cast") or {}) and not (hot.get("shiki") or {}).get("nemera_chakra"):
        return []   # до пробуждения Немеры
    text = str(turn.get("gm") or "")
    seen = bool(NEMERA.search(text))
    fold = hot.setdefault("fold", {})
    fold["nemera_quiet"] = 0 if seen else int(fold.get("nemera_quiet") or 0) + 1
    active = (hot.get("_fight") or {}).get("active")
    delta = turn.get("d") or {}
    if active and not seen and isinstance(delta.get("fight"), dict) and not delta["fight"].get("start"):
        return [{"turn": turn["id"], "kind": "fight", "text":
                 "Немера в обмене бездействовала и не видна в сцене. " + NEMERA_PROTOCOL}]
    if not active and fold["nemera_quiet"] and fold["nemera_quiet"] % NEMERA_QUIET == 0:
        return [{"turn": turn["id"], "kind": "will", "text":
                 f"Немера молчит {fold['nemera_quiet']} ходов. У неё своя воля (ref/NEMERA.md): заметила ли она то, "
                 "что Шики пропустил, назрела ли цена сделки, хочет ли что-то потрогать или выяснить в 5 м? "
                 "Действует и говорит сама, не по вызову; молчание — тоже её выбор, но видимый."}]
    return []


def named(foe):
    return bool(foe.get("named") or foe.get("canon") or foe.get("rank") in NAMED_RANKS)

# Чакра Шики: шкала 0–100. Цена ЛЮБОЙ техники, включая явный cost, снижена в 4 раза.
MAX_CHAKRA = 100.0
TECHNIQUE_COST_SCALE = 0.25
COST = {"E": 2, "D": 4, "C": 8, "B": 15, "A": 25, "S": 40}  # базовая цена до множителя
BURST_COST = 3 * TECHNIQUE_COST_SCALE
REGEN_PER_HOUR = 6
LOCKOUT_HOURS = 2         # после обморока чакра не восполняется сразу (канон s08.text.021)
CHAKRA_STATES = ((60, "готов", 0), (30, "напряжение", -0.5), (10, "на исходе", -1), (0, "пусто", -2))


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _note(notes, turn_id, text):
    notes.append({"turn": turn_id, "kind": "fight", "text": text})


# ---------- кости ----------

def _digest(hot):
    cursor = hot.get("cursor") or {}
    seed = json.dumps([cursor.get("id"), cursor.get("at"), hot.get("last")], ensure_ascii=False,
                      sort_keys=True)
    return hashlib.sha256(seed.encode("utf-8")).digest()


def dice(hot):
    """Кости d20 на следующий ход. Зависят только от уже свёрнутого состояния."""
    digest = hashlib.sha256(b"d20|" + _digest(hot)).digest()
    return [int.from_bytes(digest[2 * i:2 * i + 2], "big") % 20 + 1 for i in range(DICE)]


def dice100(hot):
    """Прежние кости d100 — только для проверки старых ходов (p, value, result)."""
    digest = _digest(hot)
    return [int.from_bytes(digest[2 * i:2 * i + 2], "big") % 100 for i in range(DICE)]


GRADE = ("чисто", "успех", "с ценой", "провал")   # от лучшего к худшему


def outcome(p, value):
    if value < p - 25:
        return "чисто"
    if value < p:
        return "успех"
    if value < p + 15:
        return "с ценой"
    return "провал"


# ---------- умения и перевес ----------

def shiki_level(hot, skill):
    stats = ((hot.get("shiki") or {}).get("stats") or {})
    key = SHIKI_SKILL.get(skill, skill)
    value = stats.get(key)
    if not isinstance(value, (int, float)):
        return None
    return float(value) + ((fusion.SAGE_BONUS if fusion.active(hot) else SAGE_BONUS).get(skill, 0.0) if sage_active(hot) else 0.0)


def foe_level(foe, skill):
    skills = foe.get("skills") or {}
    if isinstance(skills.get(skill), (int, float)):
        return float(skills[skill])
    rank = foe.get("rank") or "chunin"
    if skill in ("speed", "shunshin", "reaction"):
        return RANK_SPEED.get(rank, 2.5)
    return RANK.get(rank, 2.5)


def _wound_penalty(wounds):
    return max((SEVERITY.get(w.get("severity"), 0) for w in wounds or [] if not w.get("healed")),
               default=0)


def chakra_state(value):
    for floor, label, penalty in CHAKRA_STATES:
        if value >= floor:
            return label, penalty
    return "пусто", -2


def edge(hot, roll):
    """Перевес и порог для броска Шики. Возвращает (перевес, порог, разбор) или None."""
    skill = roll.get("skill")
    mine = roll.get("level") if isinstance(roll.get("level"), (int, float)) else shiki_level(hot, skill)
    if mine is None:
        return None
    vs = roll.get("vs")
    if isinstance(vs, (int, float)):
        theirs, foe = float(vs), {}
    else:
        foe = (((hot.get("_fight") or {}).get("active") or {}).get("foes") or {}).get(vs) or {}
        if not foe and not isinstance(roll.get("vs_level"), (int, float)):
            return None
        theirs = (float(roll["vs_level"]) if isinstance(roll.get("vs_level"), (int, float))
                  else foe_level(foe, roll.get("vs_skill") or skill))
    parts = {"умение": mine, "противник": -theirs}
    for name, value in (roll.get("mods") or {}).items():
        low, high = MODS.get(name, (0, 0))
        parts[name] = max(low, min(high, float(value)))
    fight = (hot.get("_fight") or {}).get("active") or {}
    if skill in PHYSICAL_SKILLS:
        penalty = _wound_penalty((hot.get("injuries") or {}).get("shiki"))
        if penalty:
            parts["раны Шики"] = -penalty
    if skill in CHAKRA_SKILLS:
        _label, penalty = chakra_state(((hot.get("shiki") or {}).get("chakra") or {}).get("value", 100))
        if penalty:
            parts["чакра"] = penalty
    if body.penalty(hot, "shiki"):        # кровопотеря бьёт по всему: и по рывку, и по печатям
        parts["кровопотеря"] = -body.penalty(hot, "shiki")
    if foe:
        hurt = _wound_penalty((hot.get("injuries") or {}).get(vs))
        if hurt:
            parts["раны противника"] = min(hurt, 1)
        if body.penalty(hot, vs):
            parts["кровопотеря противника"] = min(body.penalty(hot, vs), 2)
        morale = MORALE.get(foe.get("morale"), 0)
        if morale:
            parts["мораль противника"] = morale
    total = round(sum(parts.values()), 2)
    if foe and named(foe) and total > NAMED_EDGE_CAP:
        parts["именной противник"] = round(NAMED_EDGE_CAP - total, 2)
        total = NAMED_EDGE_CAP
    p = max(5, min(95, round(50 + 15 * total)))
    return total, p, parts


def foe_edge(hot, roll):
    """Бросок противника (``by``): его умение − защита Шики + поправки; ``long`` — Шики в длинном действии."""
    foe = (((hot.get("_fight") or {}).get("active") or {}).get("foes") or {}).get(roll.get("by")) or {}
    if not foe:
        return None
    skill = roll.get("skill") or ("speed" if roll.get("interrupt") else "taijutsu")
    theirs = foe_level(foe, skill)
    defend = roll.get("vs_skill") or ("speed" if roll.get("interrupt") else DEFENSE.get(skill, "reaction"))
    mine = shiki_level(hot, defend)
    if mine is None:
        mine = shiki_level(hot, "reaction") or 5.0
    parts = {"умение противника": theirs, "защита Шики": -mine}
    # Максимально быстрые освоенные печати Шики — не длительная подготовка.
    # Подлинно долгие действия (лечение, ритуал, зарядка) сохраняют окно врагу.
    handseals = str(roll.get("long_kind") or "").casefold() in ("handseals", "seals", "печати")
    if roll.get("long") and not handseals:
        parts["длинное действие Шики"] = LONG_BONUS
    for name, value in (roll.get("mods") or {}).items():
        low, high = MODS.get(name, (0, 0))
        parts[name] = max(low, min(high, float(value)))
    penalty = _wound_penalty((hot.get("injuries") or {}).get("shiki"))
    if penalty and defend in PHYSICAL_SKILLS:
        parts["раны Шики"] = penalty
    hurt = _wound_penalty((hot.get("injuries") or {}).get(roll.get("by")))
    if hurt:
        parts["раны противника"] = -min(hurt, 1)
    if body.penalty(hot, "shiki"):
        parts["кровопотеря Шики"] = body.penalty(hot, "shiki")
    if body.penalty(hot, roll.get("by")):
        parts["кровопотеря противника"] = -min(body.penalty(hot, roll.get("by")), 2)
    morale = MORALE.get(foe.get("morale"), 0)
    if morale:
        parts["мораль противника"] = -morale
    total = round(sum(parts.values()), 2)
    return total, max(5, min(95, round(50 + 15 * total))), parts


# ---------- применение хода ----------

def apply(hot, delta, turn, pool, legacy=None):
    """Бой, броски, раны, чакра одного хода. Возвращает замечания для CHECKS."""
    notes = []
    turn_id, at = turn["id"], turn["at"]
    _regen(hot, at)
    state = hot.setdefault("_fight", {})
    data = delta.get("fight") if isinstance(delta.get("fight"), dict) else {}
    if data.get("start"):
        start = data["start"] if isinstance(data["start"], dict) else {"id": str(data["start"])}
        if state.get("active"):
            _close(state, "прерван новым боем", turn_id, at)
        state["active"] = {"id": start.get("id") or turn_id, "place": start.get("place") or turn.get("place"),
                           "opened": turn_id, "opened_at": at, "exchanges": 0, "foes": {}, "rolls": [],
                           "bursts": {}}
    active = state.get("active")
    if active and data:
        if active.get("last") != turn_id:
            active["exchanges"] = int(active.get("exchanges") or 0) + 1
            active["last"] = turn_id
        for key, foe in (data.get("foes") or {}).items():
            if isinstance(foe, dict):
                card = active["foes"].setdefault(key, {"status": ACTIVE, "morale": "steady"})
                card.update({k: v for k, v in foe.items() if v is not None})
                _canonize(key, card, turn_id, notes)
    elif data and not data.get("end") and (data.get("foes") or data.get("wounds")):
        _note(notes, turn_id, "d.fight без открытого боя: начни его полем start.")
    if active and not data and turn.get("kind") not in ("ooc", "technical", "void") and _named_active(active):
        _note(notes, turn_id, "Бой с именным противником идёт, а d.fight в ходе нет: обмен не записан — "
              "его ход, кости и раны пропали.")

    rolled = _rolls(hot, delta, turn_id, pool, notes, legacy)
    if active and data and not data.get("end"):
        _review_exchange(active, rolled, data, turn_id, notes)
    _wounds(hot, data.get("wounds"), turn_id, at)
    for item in delta.get("heal") or data.get("heal") or []:
        _heal(hot, item, turn_id, notes, at=at)
    if active:
        for key, patch in (data.get("foe_state") or {}).items():
            if isinstance(patch, dict) and key in active["foes"]:
                active["foes"][key].update(patch)
    bursts = data.get("bursts", 0)
    if type(bursts) is not int or bursts < 0:
        _note(notes, turn_id, "d.fight.bursts: требуется неотрицательное целое число; расход не применён.")
        bursts = 0
    if bursts:
        if active is not None:
            active["bursts"][turn_id] = bursts
        if bursts > BURSTS_PER_EXCHANGE:
            _note(notes, turn_id, f"{bursts} жёстких рывков шуншина за обмен, предел листа 3–4: "
                  "следующие рывки — с помехой −1 и риском ошибки опоры, пока Шики не переведёт дух.")
    _chakra(hot, delta.get("chakra"), bursts, turn_id, at, notes)
    if active and data.get("end"):
        _close(state, data["end"], turn_id, at)
    elif active:
        _review_active(hot, active, turn_id, notes)
    return notes


def _canonize(key, card, turn_id, notes):
    """Именной противник по карточке канона: умения не ниже FOES.json."""
    ref, canon = canon_card(key, card)
    if not canon:
        return
    card["canon"], card["named"] = ref, True
    skills = dict(card.get("skills") or {})
    raised = []
    for skill, value in (canon.get("skills") or {}).items():
        mine = skills.get(skill)
        if not isinstance(mine, (int, float)) or mine < value:
            if isinstance(mine, (int, float)):
                raised.append(f"{skill} {mine:g}→{value:g}")
            skills[skill] = value
    card["skills"] = skills
    card.setdefault("traits", canon.get("traits") or [])
    if raised:
        _note(notes, turn_id, f"«{card.get('name') or key}»: умения ниже канона подняты ({', '.join(raised)}) — "
              f"карточка ref/FOES.json «{ref}».")


def _named_active(active):
    return [k for k, f in (active.get("foes") or {}).items() if f.get("status", ACTIVE) == ACTIVE and named(f)]


def _review_exchange(active, rolled, data, turn_id, notes):
    """Именной противник в каждом обмене действует сам; против него — бросок, а не авто-успех."""
    for key in _named_active(active):
        foe = active["foes"][key]
        name = foe.get("name") or key
        own = [r for r in rolled if r.get("by") == key]
        acted = own or key in (data.get("foe_actions") or {})
        if not acted:
            _note(notes, turn_id, f"Именной противник «{name}» в этом обмене не действовал: у него свой ход "
                  f"каждый обмен — прервать длинное действие Шики, ударить между его шагами, сменить позицию, "
                  f"уйти. Кость d.rolls с \"by\": \"{key}\" (interrupt, long) или d.fight.foe_actions.{key}.")
        if not [r for r in rolled if r.get("vs") == key and not r.get("by")] and not own:
            _note(notes, turn_id, f"Против «{name}» в обмене нет ни одной кости: с именным противником исход "
                  "решает бросок, перевес Шики не выше +2.")


CUTOVER = "s13.text.061"   # первый ход после перехода на полосы (25.11.2026)


def _new(roll, turn_id):
    """Новая запись — полоса и категория; старая — порог p и result. Голая кость (только value) до
    CUTOVER — старая (s11.text.063–065), после — новая."""
    if any(k in roll for k in ("band", "axes", "cat")):
        return True
    if "p" in roll or "result" in roll:
        return False
    from .aging import _ordinal
    here, cut = _ordinal(turn_id), _ordinal(CUTOVER)
    return here is None or cut is None or here >= cut


def _rolls(hot, delta, turn_id, pool, notes, legacy=None):
    rolls = list(delta.get("rolls") or []) + list(((delta.get("fight") or {}) if isinstance(delta.get("fight"), dict) else {}).get("rolls") or [])
    active = (hot.get("_fight") or {}).get("active")
    done = []
    for index, roll in enumerate(rolls):
        if not isinstance(roll, dict):
            continue
        label = f"Бросок «{roll.get('what', index + 1)}»"
        if not _new(roll, turn_id):
            if _legacy_roll(hot, roll, index, label, turn_id, pool, legacy, notes, active):
                done.append(roll)
            continue
        if index >= len(pool):
            _note(notes, turn_id, f"{label}: костей на ход {len(pool)}; лишний бросок не проверен.")
            continue
        value = pool[index]
        if roll.get("value") != value:
            _note(notes, turn_id, f"{label}: взята кость {roll.get('value')}, по порядку была {value} "
                  f"(dice[{index}]). Кости берут подряд, не выбирают.")
        done.append(roll)
        got = resolve(hot, roll)
        if got is None:
            _note(notes, turn_id, f"{label}: не найден участник ({roll.get('by') or 'shiki'} → {roll.get('vs')}) — "
                  "vs: id противника из d.fight.foes или число-уровень.")
            continue
        computed = got["band"]
        use = _declared(roll.get("band"))
        if use:
            above = use[-1] > computed[-1] and not (roll.get("random") and use[-1] == computed[-1] + 1)
            below = use[0] < computed[0] and not (roll.get("random") and use[0] == computed[0] - 1)
            if above or below:
                _note(notes, turn_id, f"{label}: полоса {bands.text(use)}, по осям {bands.text(computed)} "
                      f"({got['why']}). Сузить можно свойствами техники и пределами; расширить на соседнюю — "
                      "только с random (причина). Исход считаю по расчётной.")
                use = computed
        else:
            use = computed
        expected = bands.from_die(use, value)
        cat = roll.get("cat")
        if isinstance(cat, int) and cat > expected:
            _note(notes, turn_id, f"{label}: записана категория {cat}, по кости {value} и полосе "
                  f"{bands.text(use)} — {expected} ({bands.CATEGORIES[expected]}). Исход не смягчается.")
        if use[0] == 1 and not roll.get("risk"):
            _note(notes, turn_id, f"{label}: в полосе есть 1 — серьёзный ущерб называют до броска (risk).")
        record = {"turn": turn_id, "what": roll.get("what"), "band": use, "value": value, "cat": expected}
        if roll.get("by"):
            record["by"] = roll["by"]
        if active is not None:
            active["rolls"] = (active.get("rolls") or []) + [record]
            del active["rolls"][:-ROLLS_KEEP]
    return done


def from_band(band, value):
    return bands.from_die(band, value)


def _declared(value):
    if isinstance(value, str):
        parts = [x for x in value.replace("–", "-").split("-") if x.strip()]
        value = [int(x) for x in parts if x.strip().isdigit()]
    if not isinstance(value, (list, tuple)) or not value:
        return None
    nums = sorted({int(x) for x in value if isinstance(x, (int, float)) and 1 <= int(x) <= 7})
    if not nums:
        return None
    return list(range(nums[0], nums[-1] + 1))[:5]


def _legacy_roll(hot, roll, index, label, turn_id, pool, legacy, notes, active):
    """Старая запись p/value/result (до 25.11.2026): проверка по костям d100 того хода."""
    old = legacy if legacy is not None else []
    if index >= len(old):
        if roll.get("value") in pool:
            _note(notes, turn_id, f"{label}: запись по старой форме (p, result) — теперь полоса и категория: "
                  "band, value, cat (ref/COMBAT.md).")
        return False
    value = old[index]
    if roll.get("value") != value:
        if roll.get("value") == pool[index] if index < len(pool) else False:
            _note(notes, turn_id, f"{label}: кость d20 записана по старой форме — нужна полоса: axes C/D/E, "
                  "band, value, cat (ref/COMBAT.md).")
            return False
        _note(notes, turn_id, f"{label}: взята кость {roll.get('value')}, по порядку была {value} "
              f"(GM_BRIEF.fight.dice[{index}]). Кости берут подряд, не выбирают.")
    computed = foe_edge(hot, roll) if roll.get("by") else edge(hot, roll)
    if computed is None:
        if roll.get("skill") and not isinstance(roll.get("level"), (int, float)):
            _note(notes, turn_id, f"{label}: умения «{roll.get('skill')}» нет в листе — укажи level.")
        p = roll.get("p")
    else:
        total, p, _parts = computed
        if isinstance(roll.get("p"), (int, float)) and abs(roll["p"] - p) > 3:
            _note(notes, turn_id, f"{label}: порог {roll['p']}, по формуле {p} (перевес {total:+}).")
        if total >= 3 and not roll.get("by"):
            _note(notes, turn_id, f"{label}: перевес {total:+} — исход решается без броска.")
    if isinstance(p, (int, float)):
        expected = outcome(p, value)
        if roll.get("result") in GRADE and GRADE.index(roll["result"]) < GRADE.index(expected):
            _note(notes, turn_id, f"{label}: записан исход «{roll['result']}», по кости {value} "
                  f"и порогу {p} — «{expected}». Результат не смягчается и не перебрасывается.")
        record = {"turn": turn_id, "what": roll.get("what"), "p": p, "value": value, "result": expected}
        if roll.get("by"):
            record["by"] = roll["by"]
        if active is not None:
            active["rolls"] = (active.get("rolls") or []) + [record]
            del active["rolls"][:-ROLLS_KEEP]
    return True


# ---------- соотношение сил: оси и полоса (bands.py) ----------

def _foe(hot, who):
    return ((((hot.get("_fight") or {}).get("active") or {}).get("foes") or {}).get(who))


def _ally(hot, who):
    found = allies(hot).get(who)
    return found[1] if found else None


def level(hot, who, skill):
    """Уровень участника в умении (шкала листа 0–5+): Шики, противник, союзник или число."""
    if isinstance(who, (int, float)):
        return float(who)
    if who in (None, "shiki"):
        found = shiki_level(hot, skill)
        if found is None and skill in DEFENSE:
            found = shiki_level(hot, DEFENSE[skill])
        return found if found is not None else (shiki_level(hot, "reaction") or 4.5)
    foe = _foe(hot, who)
    if foe is not None:
        return foe_level(foe, skill)
    card = _ally(hot, who)
    if card:
        skills = card.get("skills") or {}
        if isinstance(skills.get(skill), (int, float)):
            return float(skills[skill])
        values = [v for v in skills.values() if isinstance(v, (int, float))]
        return sum(values) / len(values) if values else RANK["jonin"]
    return None


def power(hot, who):
    """Мощь и запас (ось B): запас чакры и его остаток у Шики, у прочих — запас, ранг или средний уровень."""
    if isinstance(who, (int, float)):
        return float(who)
    if who in (None, "shiki"):
        base = shiki_level(hot, "reserves")
        if base is None:
            base = (((hot.get("shiki") or {}).get("stats") or {}).get("chakra_reserves")) or 4.5
        _label, penalty = chakra_state(((hot.get("shiki") or {}).get("chakra") or {}).get("value", 100))
        return float(base) + penalty
    foe = _foe(hot, who) or _ally(hot, who)
    if foe is None:
        return None
    skills = foe.get("skills") or {}
    if isinstance(skills.get("reserves"), (int, float)):
        return float(skills["reserves"])
    if foe.get("rank") in RANK:
        return RANK[foe["rank"]]
    values = [v for v in skills.values() if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else RANK["chunin"]


def burden(hot, who):
    """Ось F действующего: раны и кровопотеря (0 … −2). Остаток чакры — в B, не здесь: один фактор — один раз,
    и усталость — от раны, не от запаса (игрок, 25.11.2026)."""
    if isinstance(who, (int, float)):
        return (0, 0)
    who = who or "shiki"
    wound = _wound_penalty((hot.get("injuries") or {}).get(who))
    loss = body.stage(hot, who)
    if wound >= 2 or loss >= 3:
        return (-2, -2)
    if wound >= 1 or loss >= 2:
        return (-1, -1)
    if wound >= 0.5 or loss >= 1:
        return (-1, 0)
    return (0, 0)


def exposed(hot, who):
    """Прибавка к оси E против цели: её раны, кровопотеря, мораль (0 … +2)."""
    if isinstance(who, (int, float)):
        return 0
    low, high = burden(hot, who if who else "shiki")
    shift = -low if low == high else 0
    foe = _foe(hot, who)
    if foe:
        shift = max(shift, {"shaken": 0, "breaking": 1, "broken": 2}.get(foe.get("morale"), 0))
    return shift


def honest(hot, actor, target):
    """Против именного, элиты и каге — всегда бросок: полоса не сводится к одной категории."""
    for who in (actor, target):
        foe = _foe(hot, who) if isinstance(who, str) else None
        if foe and named(foe):
            return True
    return False


def resolve(hot, roll):
    """Оси A–F и полоса для броска. Действующий — by (или Шики), цель — vs (или Шики)."""
    actor = roll.get("by") or "shiki"
    target = roll.get("vs") if roll.get("vs") is not None else ("shiki" if actor != "shiki" else None)
    if target is None:
        return None
    skill = roll.get("skill") or ("speed" if roll.get("interrupt") else "taijutsu")
    if actor == "shiki":
        defense = roll.get("vs_skill") or skill
    else:
        defense = roll.get("vs_skill") or ("speed" if roll.get("interrupt") else DEFENSE.get(skill, "reaction"))
    mine, theirs = level(hot, actor, skill), level(hot, target, defense)
    strong, weak = power(hot, actor), power(hot, target)
    if None in (mine, theirs, strong, weak):
        return None
    given = roll.get("axes") if isinstance(roll.get("axes"), dict) else {}
    axes = {"A": bands.axis(mine - theirs), "B": bands.axis(strong - weak)}
    lever = bands.parse(given.get("B", 0)) or (0, 0)
    axes["B"] = bands.clamp(axes["B"], max(-1, min(1, lever[0])))
    for name in bands.GM_AXES:
        axes[name] = bands.parse(given.get(name, 0)) or (0, 0)
    handseals = str(roll.get("long_kind") or "").casefold() in ("handseals", "seals", "печати")
    if roll.get("long") and actor != "shiki" and not handseals:
        axes["D"] = bands.clamp(axes["D"], 1)          # Шики в длинном действии — окно противнику
    axes["E"] = bands.clamp(axes["E"], exposed(hot, target))
    extra = bands.parse(given.get("F", 0)) or (0, 0)
    axes["F"] = bands.clamp(burden(hot, actor), max(0, min(1, extra[0])))
    cats, (low, high) = bands.band(axes, honest(hot, actor, target))
    why = ", ".join(f"{k} {v[0]:+d}" if v[0] == v[1] else f"{k} {v[0]:+d}:{v[1]:+d}" for k, v in axes.items())
    return {"axes": axes, "band": cats, "M": (low, high), "why": why}


def matchup(hot, actor, target, skills=("speed", "taijutsu", "ninjutsu", "genjutsu")):
    """Подсказка ведущему: оси, которые считает код (A по умениям, B, F, прибавка к E)."""
    def fmt(pair):
        return f"{pair[0]:+d}" if pair[0] == pair[1] else f"{pair[0]:+d}:{pair[1]:+d}"
    out = {}
    for skill in skills:
        roll = {"by": actor if actor != "shiki" else None, "vs": target, "skill": skill}
        got = resolve(hot, roll)
        if got:
            out[skill] = fmt(got["axes"]["A"])
    probe = resolve(hot, {"by": actor if actor != "shiki" else None, "vs": target, "skill": skills[0]})
    if not probe:
        return {}
    return {"A": out, "B": fmt(probe["axes"]["B"]), "F": fmt(probe["axes"]["F"]),
            "E+": exposed(hot, target), "always_roll": honest(hot, actor, target)}


def _wounds(hot, wounds, turn_id, at):
    for wound in wounds or []:
        if not isinstance(wound, dict) or not wound.get("who"):
            continue
        record = {k: wound[k] for k in ("where", "what", "severity", "lost") if wound.get(k)}
        record.update({"turn": turn_id, "at": at})
        if body.bleeds(wound):       # кровь идёт: дальше тело решает кость состояния (body.py)
            record["bleed"] = True
        hot.setdefault("injuries", {}).setdefault(wound["who"], []).append(record)


def _heal(hot, item, turn_id, notes=None, *, at=None):
    if not isinstance(item, dict) or not item.get("who"):
        return
    who = item["who"]
    wounds = (hot.get("injuries") or {}).get(who) or []
    where, source = item.get("where"), item.get("injury_turn")
    modern = _new({}, turn_id)

    def warn(message):
        if notes is not None:
            _note(notes, turn_id, f"Лечение {who}: {message}")

    if body.modern(turn_id) and item.get("all") and (where or source):
        warn("all=true несовместимо с выбором отдельной раны.")
        return
    if modern and any(key in item and type(item[key]) is not bool for key in ("stop", "blood", "all")):
        warn("stop/blood/all должны быть логическими значениями; лечение не применено.")
        return
    if source is not None and (not isinstance(source, str) or not source):
        warn("injury_turn должен быть ID исходного повреждения.")
        return
    matches = [w for w in wounds if not w.get("healed") and
               (not where or w.get("where") == where) and
               (not source or w.get("turn") == source)]
    if "residual" in item:
        residual = item["residual"]
        valid = (isinstance(residual, dict) and not (set(residual) - {"lost", "severity"})
                 and isinstance(residual.get("lost"), str) and bool(residual["lost"].strip())
                 and ("severity" not in residual or isinstance(residual["severity"], str) and residual["severity"] in SEVERITY)
                 and where and len(matches) == 1 and not item.get("all"))
        if not valid:
            warn("частичное лечение требует единственную рану where (при совпадении — injury_turn) и residual.lost; severity — из шкалы ран.")
            return
        if item.get("stop") or item.get("blood"):
            body.heal(hot, item, turn_id, at)
        matches[0].update(residual)
        matches[0]["recovered_at"] = turn_id
        return
    # An explicit false haemostasis/transfusion flag is not a request for
    # complete recovery. Keep this distinct from old unqualified full heal.
    if modern and ("stop" in item or "blood" in item) and not (item.get("stop") or item.get("blood")):
        warn("stop/blood=false не означает полного исцеления.")
        return
    if modern and (item.get("stop") or not item.get("blood")):
        if not where and not item.get("all"):
            warn("укажи точную рану where либо явное all=true.")
            return
        if where and (not matches or len(matches) > 1) and not item.get("all"):
            warn("рана не найдена или неоднозначна; укажи injury_turn.")
            return
    if (item.get("stop") or item.get("blood")) and body.heal(hot, item, turn_id, at):
        return      # кровь остановлена или восполнена; рана остаётся до лечения
    was_bleeding = any(w.get("bleed") for w in matches)
    for wound in matches:
        wound["healed"] = turn_id
    if was_bleeding:
        body.recovery_anchor(hot, who, turn_id, at)
    remaining = [w for w in wounds if not w.get("healed")]
    if remaining:
        hot["injuries"][who] = remaining
    else:
        hot.get("injuries", {}).pop(who, None)


# ---------- чакра ----------

def _regen(hot, at):
    chakra = (hot.get("shiki") or {}).get("chakra")
    if not isinstance(chakra, dict) or "value" not in chakra:
        return
    now, then = _parse(at), _parse(chakra.get("at"))
    if not now or not then or now <= then:
        return
    start = then
    locked = _parse(chakra.get("locked_until"))
    if locked and locked > start:
        start = min(locked, now)
    hours = max(0.0, (now - start).total_seconds() / 3600)
    chakra["value"] = round(min(fusion.max_chakra(hot), float(chakra["value"]) + fusion.regen_rate(hot) * hours), 2)
    chakra["max"] = fusion.max_chakra(hot)
    chakra["at"] = at
    chakra["state"] = chakra_state(chakra["value"])[0]
    if locked and now >= locked:
        chakra.pop("locked_until", None)


def _chakra(hot, patch, bursts, turn_id, at, notes):
    shiki = hot.setdefault("shiki", {})
    chakra = shiki.get("chakra")
    if not isinstance(patch, dict) and not bursts:
        return
    patch = patch if isinstance(patch, dict) else {}
    if "set" in patch:
        value = patch["set"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= fusion.max_chakra(hot):
            _note(notes, turn_id, "d.chakra.set: требуется конечное число в пределах запаса; установка не применена.")
        else:
            chakra = shiki["chakra"] = {**(chakra if isinstance(chakra, dict) else {}),
                                       "value": float(value), "max": fusion.max_chakra(hot),
                                       "at": at, "why": patch.get("why", "")}
    if not isinstance(chakra, dict):
        return
    chakra["max"] = fusion.max_chakra(hot)
    chakra["value"] = max(0.0, min(fusion.max_chakra(hot), float(chakra["value"])))
    spent, used = 0.0, []
    for item in patch.get("spend") or []:
        if not isinstance(item, dict):
            continue
        if "cost" in item:
            base_cost = item["cost"]
            if type(base_cost) not in (int, float) or not math.isfinite(base_cost) or base_cost < 0:
                _note(notes, turn_id, "d.chakra.spend.cost: требуется конечная неотрицательная цена; расход не применён.")
                continue
        else:
            base_cost = COST.get(str(item.get("rank", "")).upper())
        if base_cost is None:
            _note(notes, turn_id, f"Чакра: у «{item.get('what')}» нет ранга (E–S) или cost.")
            continue
        cost = float(base_cost) * TECHNIQUE_COST_SCALE
        left = float(chakra["value"]) - spent
        if cost > left + 0.01:
            _note(notes, turn_id, f"«{item.get('what')}» стоит {cost:g}, а у Шики {max(left, 0):.0f} из {fusion.max_chakra(hot):g}: "
                  "техника срывается, выходит ослабленной или Шики теряет сознание на ней (d.chakra.collapse). "
                  "Без внешнего источника чакры полной силы не будет.")
        spent += float(cost)
        used.append(item.get("what"))
    spent += BURST_COST * bursts
    before = float(chakra["value"])
    chakra["value"] = round(before - spent, 2)
    chakra["at"] = at
    if used or bursts:
        chakra["last"] = {"turn": turn_id, "spent": spent, "what": [u for u in used if u] +
                          ([f"рывки ×{bursts}"] if bursts else [])}
    if chakra["value"] <= 0:
        chakra["value"] = 0.0
        if not patch.get("collapse") and before > 0:
            _note(notes, turn_id, f"Чакра Шики исчерпана ({before:.0f} → 0 после «{', '.join(map(str, used)) or 'рывков'}»): "
                  "по канону он теряет сознание; отметь d.chakra.collapse и последствия.")
    if patch.get("collapse"):
        chakra["value"] = 0.0
        moment = _parse(at)
        if moment:
            from datetime import timedelta
            chakra["locked_until"] = (moment + timedelta(hours=LOCKOUT_HOURS)).isoformat()
    chakra["state"] = chakra_state(chakra["value"])[0]


# ---------- ход боя ----------

def _review_active(hot, active, turn_id, notes):
    foes = active.get("foes") or {}
    injuries = hot.get("injuries") or {}
    for key, foe in foes.items():
        if foe.get("status", ACTIVE) == ACTIVE and any(
                w.get("severity") == "критическая" for w in injuries.get(key, []) if not w.get("healed")):
            _note(notes, turn_id, f"Противник «{foe.get('name') or key}» с критической раной всё ещё действует: "
                  "объясни (техника, стимулятор) или выведи его из боя (foe_state.status).")
    if foes and all(f.get("status", ACTIVE) != ACTIVE for f in foes.values()):
        _note(notes, turn_id, "Ни одного действующего противника: закрой бой полем d.fight.end с исходом.")
    if int(active.get("exchanges") or 0) >= EXCHANGE_LIMIT:
        _note(notes, turn_id, f"Бой «{active['id']}» идёт {active['exchanges']} обменов: следующей сценой "
              "сведи к исходу — смерть, увечье, плен, бегство, сдача или разрыв дистанции. Новых волн не вводи.")


def _close(state, outcome_text, turn_id, at):
    active = state.pop("active", None)
    if not active:
        return
    summary = {"id": active["id"], "place": active.get("place"), "opened": active.get("opened"),
               "closed": turn_id, "at": at, "exchanges": active.get("exchanges"), "outcome": str(outcome_text),
               "foes": {k: {"name": v.get("name"), "status": v.get("status")} for k, v in active["foes"].items()}}
    closed = state.setdefault("closed", [])
    closed.append(summary)
    del closed[:-CLOSED_KEEP]
    state["aftermath"] = {"turn": turn_id, "id": active["id"], "outcome": str(outcome_text)[:160]}


def aftermath(hot):
    """Пункт must сразу после конца боя: в Йокогаме победа открыла вторую половину сцены — счёт."""
    state = (hot.get("_fight") or {}).get("aftermath") or {}
    if not state or state.get("turn") != (hot.get("cursor") or {}).get("id"):
        return None
    return (f"Бой «{state['id']}» кончился ({state['outcome']}), сцена — нет: счёт за бой. Что держит Шики и чем "
            "платит (печати, пленник, отростки), кровь и раны (кость состояния), кто пришёл на шум и что видел, "
            "что осталось на месте. Союзники решают своё сами; стоп-кадр положения — в конце хода.")


# ---------- выдача ведущему ----------

ARSENAL_RULE = ("Именной противник и союзник бьются всем арсеналом по своей цели, не разговором вместо действия: "
                "arsenal_left — ещё не пущенное в ход; меняй приёмы, ставь ловушки, бей по прикрываемому.")
RULES = ("Как в Йокогаме: соотношение сил даёт полосу исхода, d20 — категорию. Кость — подряд из dice, по одной на "
         "каждую настоящую неопределённость; против именного — всегда. Оси −2…+2 (неуверенность «a:b»): A мастерство, "
         "B мощь и запас, F состояние — считает код (fight.active.foes.<id>.vs: «shiki→» — бросок Шики, «→shiki» — "
         "противника); C знание и подготовка, D дистанция/время/среда, E сопротивление цели — ты. Рычаг (численность, "
         "призыв) — B ±1. Сумма M: ≤−8 → 1; ≤−4 → 2; ≤−1 → 3; 0 → 4; ≤3 → 5; ≤7 → 6; больше → 7; сумма низов и верхов "
         "даёт полосу. Сузь её свойствами техники и пределами; соседнюю добавь только с random. d20 по полосе от худшей: "
         "2 кат. 1–10/11–20; 3 — 1–6/7–14/15–20; 4 — по 5; 5 — по 4. Категории: " +
         "; ".join(f"{k} {v}" for k, v in bands.CATEGORIES.items()) +
         ". Есть 1 в полосе — risk (ущерб) до броска. d.rolls: what, skill, vs, by (бросок противника), interrupt, "
         "long, axes {C,D,E}, band, value, cat, risk. Игроку числа не показывай.")


def brief(hot):
    """GM_BRIEF.fight: кости на следующий ход, бой, чакра, раны."""
    state = hot.get("_fight") or {}
    active = state.get("active")
    chakra = (hot.get("shiki") or {}).get("chakra") or {}
    result = {"dice": dice(hot), "rules": RULES, "ref": "campaigns/shiki/ref/COMBAT.md"}
    if chakra:
        result["chakra"] = {k: chakra[k] for k in ("value", "max", "state", "locked_until", "last") if k in chakra}
    if fusion.active(hot):
        result.setdefault("chakra", {})["regen_per_hour"] = fusion.REGEN_PER_HOUR
    injuries = {k: [w for w in v if not w.get("healed")] for k, v in (hot.get("injuries") or {}).items()}
    if injuries:
        result["injuries"] = injuries
    if body.brief(hot):
        result["body"] = body.brief(hot)
    if active:
        result["active"] = {
            "id": active["id"], "place": active.get("place"), "exchanges": active.get("exchanges", 0),
            "exchanges_left": max(0, EXCHANGE_LIMIT - int(active.get("exchanges") or 0)),
            "foes": {k: {**v,
                         "levels": {s: foe_level(v, s) for s in ("speed", "taijutsu", "ninjutsu", "genjutsu", "sensory")},
                         "vs": {"shiki→": matchup(hot, "shiki", k), "→shiki": matchup(hot, k, "shiki")},
                         **({"tempo": tempo(hot, v)} if named(v) else {}),
                         **({"arsenal_left": arsenal_left(hot, k, canon_card(k, v)[1])}
                            if named(v) and canon_card(k, v)[1] else {})}
                     for k, v in active["foes"].items()},
            "allies": {k: {"name": c.get("names", [k])[0], "arsenal_left": arsenal_left(hot, k, c)}
                       for k, (_r, c) in allies(hot).items()},
            "nemera": ("Отдельной Немеры нет. Отростки — действия Шики из единого запаса." if fusion.active(hot) else NEMERA_PROTOCOL), "arsenal_rule": ARSENAL_RULE,
            "recent_rolls": active.get("rolls", [])[-4:]}
    if state.get("closed"):
        result["last_fight"] = state["closed"][-1]
    return result


def tempo(hot, foe):
    """Окна именного противника против Шики: свой ход каждый обмен, лишний — если быстр."""
    mine = shiki_level(hot, "speed") or 5.0
    extra = " и ещё один между шагами длинной заявки" if foe_level(foe, "speed") >= mine - 0.5 else ""
    return f"свой ход каждый обмен{extra}; длинное действие Шики — D +1 противнику; против него бросок всегда"


def scene_lines(hot):
    """Строки для memory/SCENE.md: только видимое — раны и что идёт бой."""
    lines = []
    active = (hot.get("_fight") or {}).get("active")
    if active:
        lines.append(f"- идёт бой `{active['id']}`, обмен {active.get('exchanges', 0)}")
    for who, wounds in (hot.get("injuries") or {}).items():
        for wound in wounds:
            if not wound.get("healed"):
                lines.append(f"- рана: {who} — {wound.get('where', '')}, {wound.get('severity', '')}"
                             f"{'; ' + wound['lost'] if wound.get('lost') else ''} [{wound.get('turn')}]")
    chakra = (hot.get("shiki") or {}).get("chakra")
    if isinstance(chakra, dict) and "value" in chakra:
        lines.append(f"- чакра Шики: {chakra.get('state') or chakra_state(chakra['value'])[0]}")
    return lines
