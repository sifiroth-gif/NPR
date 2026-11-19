"""Редкое знание мира: кто владеет, кто слышал, чего не знает никто.

Некоторые вещи в мире почти никому не известны: природы чакры Шики, устройство
Договора. Ведущий легко «протекает»: персонаж рассуждает о Бётоне, как о
знакомой школе, или собирается сам его «испытать». Хранитель ведёт реестр
``memory/_curated/lore.json``:

    {"entries": [{"id": "byoton_shiton", "title": "Бётон и Шитон",
                  "terms": ["бётон", "шитон"], "wield": ["shiki", "nemera", "utsuro"],
                  "world": "что о нём знает мир", "heard": {"seina": "s05.text.030"}}]}

Код делает три вещи:

* ``track`` — после каждого хода отмечает, кто был рядом, когда термин прозвучал
  (в заявке игрока или в сцене): этот человек слышал слово, но не стал знатоком;
* ``fences`` — перед сценой для присутствующих: кто владеет, кто слышал (с ходом),
  кто не слышал; это читается до реплик, а не после;
* ``review`` — после хода смотрит реплики NPC с термином: говорит тот, кто не
  слышал, или не владеющий собирается применить, испытать, измерить.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SEED = "memory/_curated/lore.json"
# Не владеющий ставит себя действующим лицом природы: «испытывать на тебе Бётон»,
# «я применю», «мне придётся использовать». Рассказ о чужом применении
# («последнее применение Бётона») — не умение и не замечание.
CAN = re.compile(
    r"(?:испыт|примен|использ|вызв|направ|насыл|навод)\w*\s+(?:на\s+|к\s+)?"
    r"(?:тебе|тебя|тобой|нём|нем|ней|неё|нее|вас|вами|нас|себе|себя|него|пациент\w*)"
    r"|(?<![\wё])(?:испытаю|применю|использую|вызову|направлю|воспроизведу|измерю|освою|овладею|научусь)"
    r"|(?<![\wё])я\s+(?:могу|смогу|умею|буду|стану|попробую|хочу)\s+(?:\w+\s+){0,2}?"
    r"(?:испыт|примен|использ|вызв|воспроизв|измер|освои|овлад)\w*"
    r"|(?<![\wё])мне\s+(?:права|можно|позволено|придётся|предстоит)\s+"
    r"(?:испыт|примен|использ|вызв|воспроизв|измер)\w*", re.I)
NEAR = 60          # знаков от термина до оборота умения
SHIKI = "shiki"


def load(state_dir):
    path = Path(state_dir) / SEED
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data.get("entries") or [] if isinstance(e, dict) and e.get("id") and e.get("terms")]


def _norm(text):
    # ё не сводится к е: «бётон» — природа чакры, «бетон» — стройматериал.
    return str(text or "").casefold()


def _terms(entry):
    return [_norm(t) for t in entry.get("terms") or [] if str(t).strip()]


def _pattern(entry):
    # Термин — начало слова: «Бётона», «Шитоном», но не «бетонном» и не середина слова.
    return re.compile(r"(?<![\wё])(" + "|".join(re.escape(t) for t in _terms(entry)) + ")", re.I)


def mentions(entry, text):
    return bool(_terms(entry)) and bool(_pattern(entry).search(_norm(text)))


def _name(hot, key):
    if key == SHIKI:
        return "Шики"
    card = (hot.get("cast") or {}).get(key) or {}
    profile = (hot.get("_psyche") or {}).get(key) or {}
    return card.get("name") or profile.get("name") or key


def heard(hot, entry):
    """Кто слышал: заготовка хранителя + отмеченное кодом по ходам."""
    out = dict(entry.get("heard") or {})
    for key, turn in ((hot.get("lore") or {}).get(entry["id"]) or {}).items():
        out.setdefault(key, turn)
    return out


def track(hot, turn, entries, present):
    """Отмечает присутствующих не-владельцев рядом с прозвучавшим термином."""
    if turn.get("kind") == "ooc":
        return []
    text = f"{turn.get('player', '')}\n{turn.get('gm', '')}"
    added = []
    for entry in entries:
        if not mentions(entry, text):
            continue
        wield = set(entry.get("wield") or [])
        known = heard(hot, entry)
        book = hot.setdefault("lore", {}).setdefault(entry["id"], {})
        for key in present:
            if key == SHIKI or key in wield or key in known:
                continue
            book[key] = turn["id"]
            added.append((entry["id"], key))
    return added


def _stems(text):
    return {w[:5] for w in re.findall(r"[\wё]{4,}", _norm(text))}


def _speech_words(player):
    from .talk import speech
    return _stems(speech(player))


def _speaker(hot, label, people):
    """Метка реплики → ключ присутствующего по слову имени; иначе None."""
    words = {w for w in re.findall(r"[\wё]{3,}", _norm(label))}
    found = [k for k in people if words & {w for w in re.findall(r"[\wё]{3,}", _norm(_name(hot, k)))}]
    return found[0] if len(found) == 1 else None


def _npc_lines(turn, skip_names=(), hot=None, people=()):
    """(говорящий или None, реплика) — реплики сцены, кроме речи Шики из заявки.

    Подписанные именем владеющего (Немера, Уцуро) и мысленная речь курсивом
    пропускаются: говорит тот, кто вправе знать.
    """
    shiki = _speech_words(turn.get("player"))
    skip = ["шики"] + [_norm(n).split()[0] for n in skip_names if str(n).strip()]
    lines = []
    for raw in str(turn.get("gm") or "").split("\n"):
        line = raw.strip()
        who = None
        label = re.match(r"\*\*([^*]{2,48}?):\*\*\s*", line)
        if label:
            if any(n in _norm(label.group(1)) for n in skip):
                continue
            who = _speaker(hot or {}, label.group(1), people)
            line = line[label.end():]
        elif line.startswith("*"):
            continue
        if not line.startswith(("—", "–")):
            continue
        words = _stems(line)
        if words and shiki and len(words & shiki) / len(words) >= 0.6:
            continue        # это Шики: реплика пересказывает его заявку
        lines.append((who, line.lstrip("—– ").strip()))
    return lines


def _wielders(hot, entry):
    return ", ".join(_name(hot, k) for k in entry.get("wield") or [])


def review(hot, turn, entries, present_before, present_after):
    """Замечания kind=lore по репликам NPC этого хода."""
    if turn.get("kind") == "ooc":
        return []
    notes = []
    people = [k for k in dict.fromkeys(list(present_before) + list(present_after)) if k != SHIKI]
    for entry in entries:
        wield = set(entry.get("wield") or [])
        lines = _npc_lines(turn, [_name(hot, k) for k in wield], hot, people)
        hits = [(who, ln) for who, ln in lines if mentions(entry, ln) and who not in wield]
        others = [k for k in people if k not in wield]
        if not hits or not others:
            continue
        # Слышал ли кто-то раньше этого хода или от самого Шики в этой заявке.
        before = heard(hot, entry)
        from_shiki = mentions(entry, turn.get("player"))
        title = entry.get("title") or entry["id"]
        flagged = None
        for who, line in hits:
            candidates = [who] if who else others
            unheard = [k for k in candidates if k not in before and not from_shiki]
            if unheard and len(unheard) == len(candidates):
                names = ", ".join(_name(hot, k) for k in unheard)
                notes.append({"turn": turn["id"], "kind": "lore", "text":
                              f"Утечка знания: в реплике «{line[:140]}» звучит {title}, но {names} "
                              f"об этом не слышал(а). Откуда знание? Владеют только {_wielders(hot, entry)}; "
                              f"слышали — по ходам в fences."})
                flagged = line
                break
        for who, line in hits:
            if line == flagged:
                continue
            candidates = [who] if who else others
            low = _norm(line)
            spots = [m.start() for m in _pattern(entry).finditer(low)]
            found = [(m.start(), s) for m in CAN.finditer(low) for s in spots if abs(m.start() - s) <= NEAR]
            if found:
                start = max(0, min(found[0]) - 60)
                quote = ("…" if start else "") + line[start:max(found[0]) + 40].strip() + "…"
                names = ", ".join(_name(hot, k) for k in candidates)
                verb = "знает" if len(candidates) == 1 else "знают"
                notes.append({"turn": turn["id"], "kind": "lore", "text":
                              f"Проверь реплику «{quote}»: {title} — владеют только "
                              f"{_wielders(hot, entry)}. {names} {verb} лишь пересказ Шики: сами не применят, "
                              f"не испытают, не измерят — могут только просить, спрашивать или бояться."})
                break
    return notes


def fences(hot, entries, present):
    """Строки для присутствующих: кто владеет, кто слышал, кто нет."""
    out = []
    people = [k for k in present if k != SHIKI]
    if not people:
        return out
    for entry in entries:
        wield = set(entry.get("wield") or [])
        known = heard(hot, entry)
        parts = []
        for key in people:
            if key in wield:
                continue
            turn = known.get(key)
            parts.append(f"{_name(hot, key)} — {'слышал(а) с ' + turn if turn else 'не слышал(а)'}")
        if not parts:
            continue
        out.append(f"{entry.get('title') or entry['id']}: владеют только {_wielders(hot, entry)}. "
                   f"{entry.get('world', '').strip()} Рядом: {'; '.join(parts)}.".replace("  ", " "))
    return out
