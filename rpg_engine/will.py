"""Своя воля NPC: у значимого человека есть цель, следующий шаг со сроком и слабость.

Мир мрачный: люди выбирают себя, лгут, торгуются, ошибаются по своей
слабости и идут против Шики, когда это им выгодно или когда их загнали.
Код ничего не решает за ведущего. Он:

* переносит в скрытую психику NPC заготовку хранителя
  (``memory/_curated/will.GM_ONLY.json``): грани характера и планы, не
  затирая того, что ведущий уже записал сам;
* после каждого хода спрашивает о планах, срок которых прошёл: NPC не ждёт
  Шики и делает свой шаг за кадром — удачно, неудачно или иначе;
* замечает, когда значимый NPC несколько ходов рядом с Шики только отвечает,
  и спрашивает, есть ли у него повод сделать свой ход (повода нет — ожидание
  тоже решение);
* напоминает дать волю NPC, который стал значимым, а её у него нет.

План записывается в ``d.cast.<id>.intent.<ключ>``: ``goal`` (чего хочет),
``next`` (следующий шаг), ``due`` (игровое время шага), ``flaw`` (на чём
ошибётся), ``against_shiki`` (чем это бьёт по Шики), ``if_unopposed`` (что
будет, если никто не помешает), ``status``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from . import npc_memory

SEED = "memory/_curated/will.GM_ONLY.json"
PASSIVE_TURNS = 4          # ходов рядом с Шики без собственного шага
SOON_HOURS = 24
AGENDA_FIELDS = ("goal", "next", "due", "flaw", "against_shiki", "if_unopposed", "status")
DARK = ("Мир мрачный: NPC выбирает себя, лжёт, торгуется, ошибается по своей слабости и идёт против "
        "Шики, когда ему это выгодно или его загнали. Помощь без выгоды редка и имеет причину.")


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _state(hot):
    return hot.setdefault("_will", {})


def _name(hot, key):
    card = (hot.get("cast") or {}).get(key) or {}
    profile = (hot.get("_psyche") or {}).get(key) or {}
    return card.get("name") or profile.get("name") or key


# ---------------------------------------------------------------- заготовка хранителя

def seed(state_dir, hot):
    """Переносит заготовку хранителя в скрытую психику, если файл изменился."""
    path = Path(state_dir) / SEED
    if not path.is_file():
        return []
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    state = _state(hot)
    if state.get("seed") == digest:
        return []
    data = json.loads(raw.decode("utf-8"))
    changed = []
    for key, will in data.items():
        if key.startswith("_") or not isinstance(will, dict):
            continue
        profile = hot.setdefault("_psyche", {}).setdefault(key, {})
        if not (hot.get("cast") or {}).get(key) and not profile.get("name"):
            card = Path(state_dir) / "recovery/cast" / f"{key}.json"
            try:
                profile["name"] = json.loads(card.read_text(encoding="utf-8")).get("name") or key
            except (OSError, ValueError):
                pass
        facets = profile.setdefault("psyche", {})
        for facet, value in (will.get("psyche") or {}).items():
            if facet in npc_memory.FACETS and value and not facets.get(facet):
                facets[facet] = str(value)[:300]
                changed.append(key)
        active = profile.setdefault("intent", {})
        done = {d.get("key") for d in profile.get("intent_done") or []}
        for name, agenda in (will.get("intent") or {}).items():
            if not isinstance(agenda, dict) or name in done:
                continue
            entry = active.setdefault(name, {"status": "committed", "turn": "keeper"})
            for field in AGENDA_FIELDS:
                if agenda.get(field) and not entry.get(field):
                    entry[field] = str(agenda[field])[:300]
            changed.append(key)
    state["seed"] = digest
    return sorted(set(changed))


# ---------------------------------------------------------------- после хода

def track(hot, turn, inner):
    """Сколько ходов подряд NPC рядом с Шики и когда он последний раз менялся изнутри."""
    if turn.get("kind") == "ooc":
        return
    state = _state(hot)
    streak, touched = state.setdefault("streak", {}), state.setdefault("touched", {})
    present = [k for k in (hot.get("scene") or {}).get("present") or [] if k not in ("shiki", "player")]
    for key in list(streak):
        if key not in present:
            streak.pop(key)
    for key in present:
        streak[key] = int(streak.get(key, 0)) + 1
    for key, own in (inner or {}).items():
        if any(own.get(k) for k in ("intent", "rel", "affect", "psyche", "mem")):
            touched[key] = turn["id"]
            streak[key] = 0


def agendas(hot):
    """Все активные планы NPC со сроком: [(npc, ключ, план, срок)] по времени."""
    found = []
    for key, profile in (hot.get("_psyche") or {}).items():
        if not isinstance(profile, dict):
            continue
        for name, agenda in (profile.get("intent") or {}).items():
            if isinstance(agenda, dict) and agenda.get("status", "committed") == "committed":
                found.append((key, name, agenda, _parse(agenda.get("due"))))
    found.sort(key=lambda x: (x[3] is None, x[3] or datetime.max))
    return found


def review(hot, turns):
    """Замечания kind=will: просроченный план, пассивный значимый NPC, NPC без воли."""
    cursor = hot.get("cursor") or {}
    now, turn_id = _parse(cursor.get("at")), cursor.get("id")
    notes = []

    def note(text):
        notes.append({"turn": turn_id, "kind": "will", "text": text})

    if now:
        for key, name, agenda, due in agendas(hot):
            if due and due <= now:
                flaw = f" Его слабость: {agenda['flaw']}." if agenda.get("flaw") else ""
                note(f"За кадром: «{_name(hot, key)}» собирался «{agenda.get('next') or agenda.get('goal')}» "
                     f"к {agenda.get('due')}. Он не ждёт Шики: реши, что вышло — удалось, сорвалось, "
                     f"сменил тактику.{flaw} Запиши d.cast.{key}.intent.{name} (status, next, due) и след в мире.")
    if turns:
        streak = _state(hot).get("streak") or {}
        for key, count in streak.items():
            card = (hot.get("cast") or {}).get(key) or {}
            profile = (hot.get("_psyche") or {}).get(key) or {}
            significant = card.get("tier") in ("developed", "deep") or profile.get("intent")
            if count >= PASSIVE_TURNS and significant:
                goal = next((a.get("goal") for a in (profile.get("intent") or {}).values()
                             if isinstance(a, dict) and a.get("goal")), card.get("wants"))
                note(f"«{_name(hot, key)}» {count} ход(ов) рядом с Шики только отвечает. Его цель: "
                     f"«{goal or 'не записана'}». Есть ли у него повод сделать свой ход — надавить, солгать, "
                     "сторговаться, ошибиться, пойти против Шики? Повода нет — ждать тоже его решение.")
        for key in (hot.get("scene") or {}).get("present") or []:
            card = (hot.get("cast") or {}).get(key) or {}
            profile = (hot.get("_psyche") or {}).get(key) or {}
            if card.get("tier") in ("developed", "deep") and not profile.get("psyche") and not profile.get("intent"):
                note(f"«{_name(hot, key)}» появляется уже {card.get('memory_total', 0)} раз, а воли у него нет: "
                     f"задай d.cast.{key}.psyche (decides, deep_fear, boundary) и intent с next, due, flaw.")
    return notes


# ---------------------------------------------------------------- выдача ведущему

def view(hot, key):
    """Воля одного NPC для GM_BRIEF: как решает и какие у него планы."""
    profile = (hot.get("_psyche") or {}).get(key) or {}
    psyche = profile.get("psyche") or {}
    out = {f: psyche[f] for f in ("decides", "deep_fear", "boundary", "contradiction", "mask") if psyche.get(f)}
    plans = {name: {f: a[f] for f in AGENDA_FIELDS if a.get(f)}
             for name, a in (profile.get("intent") or {}).items() if isinstance(a, dict)}
    if plans:
        out["plans"] = plans
    return out


def soon(hot, hours=SOON_HOURS):
    """Чужие планы, чей срок наступит в ближайшие сутки или уже прошёл."""
    now = _parse((hot.get("cursor") or {}).get("at"))
    if not now:
        return []
    horizon = now + timedelta(hours=hours)
    present = set((hot.get("scene") or {}).get("present") or [])
    return [{"npc": key, "name": _name(hot, key), "plan": name,
             **{f: str(a[f])[:170] for f in ("next", "due", "if_unopposed", "against_shiki") if a.get(f)},
             "overdue": due <= now}
            for key, name, a, due in agendas(hot)
            if due and due <= horizon and key not in present][:6]   # присутствующие — в present_npcs
