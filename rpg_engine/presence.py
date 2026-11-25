"""Кто в сцене: по подписям реплик, если ведущий не обновил ``d.scene.present``.

С s08.047 ведущий перестал вести ``present``: в сохранении до s08.065 рядом с Шики
числился только он сам, хотя говорили Такаги, Мориока, Сэкидо, служащая архива.
На присутствии держится всё, что напоминает ведущему о людях: реакции на Шики,
воля NPC, границы знания, карточки. Пустой состав выключил это молча.

Теперь каждая реплика подписана (``**Имя:** — …``), и код восстанавливает
состав сам: подписи → ключи карточек. Ведущий, написавший ``present``,
остаётся главным — догадка кода применяется только без его записи.
Безымянный, но повторяющийся голос (не фоновая роль) на втором ходу получает
карточку-заготовку: без карточки человек выпадает из всех напоминаний.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .places import slug
from .wiki import ROLE_STEMS, _is_background

SHIKI = "shiki"
TITLE_WORDS = {"мастер", "господин", "госпожа", "доктор", "глава", "сама", "сан", "кун", "тян", "сенсей"}
# «Связной Песка», «Офицер Травы»: деревня с заглавной — не имя. Иначе «Врач Песка» находил связного,
# а каждое «Песка» в повествовании числилось встречей с ним (acquaint).
GEO_STEMS = ("песк", "трав", "лист", "туман", "облак", "камн", "камен", "звук", "дожд", "водопад", "кусагакур",
             "сунагакур", "конох", "киригакур", "кумогакур", "ивагакур", "отогакур", "амэгакур")
AUTO_AFTER = 2       # ходов с подписью без карточки — и карточка-заготовка
FORGET = 3           # ходов без реплики — и добавленный кодом уходит из состава


def _norm(text):
    return " ".join(re.findall(r"[\wё-]+", str(text or "").casefold()))


def _clean(label):
    text = re.sub(r"\(.*?\)", " ", str(label or ""))
    text = text.split(",")[0]
    return _norm(text)


def word_is_first(name, word):
    """Первое слово имени («Песков Ёсио») — имя; после должности («Связной Песка») — деревня."""
    words = re.findall(r"[\wё-]+", name)
    return bool(words) and words[0] == word


def _entry(names):
    names = [str(n) for n in names if n and str(n).strip()]
    proper = set()
    for name in names:
        for word in re.findall(r"[\wё-]+", name):
            low = word.casefold()
            # Часть имени — только собственное имя: с заглавной и не должность/роль.
            if word[:1].isupper() and len(low) >= 3 and low not in TITLE_WORDS \
                    and not any(stem in low for stem in ROLE_STEMS) \
                    and not (low.startswith(GEO_STEMS) and not word_is_first(name, word)):
                proper.add(low)
    return {"display": names[0] if names else "", "forms": [_norm(n) for n in names], "parts": proper}


def _wiki_names(path):
    try:
        head = path.read_text(encoding="utf-8")[:600]
    except OSError:
        return []
    title = re.match(r"#\s*(.+?)\s*·", head)
    names = [title.group(1)] if title else []
    alias = re.search(r"^псевдонимы:\s*(.+)$", head, re.M)
    if alias:
        names += [a.strip() for a in alias.group(1).split(",")]
    return names


def display(names, key):
    return (names.get(key) or {}).get("display") or key


def roster(state_dir, hot):
    """Ключ → имя и формы: карточки HOT, отложенные в recovery/cast, страницы вики."""
    raw = {}
    for key, card in (hot.get("cast") or {}).items():
        raw.setdefault(key, []).extend([card.get("name")] + list(card.get("aliases") or []))
    cast_dir = Path(state_dir) / "recovery/cast"
    for path in sorted(cast_dir.glob("*.json")) if cast_dir.is_dir() else ():
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        raw.setdefault(path.stem, []).extend([card.get("name")] + list(card.get("aliases") or []))
    for key, profile in (hot.get("_psyche") or {}).items():
        if isinstance(profile, dict):
            raw.setdefault(key, []).append(profile.get("name"))
    npc_dir = Path(state_dir) / "memory/npc"
    for path in sorted(npc_dir.glob("*.md")) if npc_dir.is_dir() else ():
        if "." not in path.stem:
            raw.setdefault(path.stem, []).extend(_wiki_names(path))
    # Ключ без имени тоже в списке: подпись найдёт его по транслитерации ключа.
    return {k: _entry(v) for k, v in raw.items()}


def resolve(label, names, scope=()):
    """Подпись → ключ: полное имя или псевдоним, затем единственное совпадение по собственному имени."""
    clean = _clean(label)
    if not clean:
        return None
    exact = [k for k, e in names.items() if clean in e["forms"]]
    scoped = [k for k in exact if k in scope]
    if len(scoped) == 1:
        return scoped[0]
    if len(scoped) > 1 or len(exact) > 1:
        return None
    if len(exact) == 1:
        # A generic mask/cloak is not a permanent name; an unrelated person
        # outside a known scene cannot be reintroduced by that label.
        if scope and clean in ("человек в маске", "человек в плаще"):
            return None
        return exact[0]
    # Должность и деревня — не имя: «Разведчик Суны» не «Разведчик Звука» (s11.text.074).
    plain = [w for w in clean.split() if not any(stem in w for stem in ROLE_STEMS) and not w.startswith(GEO_STEMS)]
    words = {w for w in plain if len(w) >= 3}
    partial = [k for k, e in names.items() if words and words <= e["parts"]]
    scoped = [k for k in partial if k in scope]
    if len(scoped) == 1:
        return scoped[0]
    if len(partial) == 1:
        return partial[0]
    # Карточка без имени (ведущий завёл только ключ): «Мориока Хидэаки» → morioka_hideaki.
    def soft(part):
        return part.replace("kh", "h").replace("-", "_")
    slugs = {soft(slug(w)) for w in plain if len(w) >= 4}
    scored = sorted(((len(slugs & {soft(p) for p in k.split("_")}), k) for k in names), reverse=True)
    if scored and scored[0][0] and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1]
    return None


VOICE = re.compile(r"\*\*([^*\n:]{2,48}):\*\*\s*[*_]*\s*[—–]")
NOT_VOICES = ("мастеру", "мастер", "ooc", "примечание")


def speakers(gm):
    """Подписи реплик: «**Имя:** — …». Поля документа («**Объект:** …») без тире — не голоса."""
    return [m.group(1).strip() for m in VOICE.finditer(str(gm or ""))]


def voices(turn, names, always_present=(), scope=()):
    """Подписи хода → (ключи карточек, метки без карточки)."""
    if turn.get("kind") == "ooc":
        return [], []
    keys, unknown = [], []
    for label in speakers(turn.get("gm")):
        if "шики" in _norm(label) or _clean(label) in NOT_VOICES or re.search(r"[—–-]\s*(?:воспоминание|цитата|из письма)\s*$", label, re.I):
            continue
        key = resolve(label, names, scope=scope)
        if key:
            if key not in always_present and key not in keys:
                keys.append(key)
        else:
            shown = " ".join(re.sub(r"\(.*?\)", " ", label).split(",")[0].split())
            if not _is_background(_clean(label)) and shown and shown not in unknown:
                unknown.append(shown)
    return keys, unknown


def update(hot, turn, seq, declared):
    """Состав после хода: ведущий записал present — его слово; иначе добавить говоривших.

    Добавленный кодом, кто молчит FORGET ходов, уходит из состава: так сцена не
    копит людей, оставшихся в другом месте. Записанных ведущим код не убирает.
    """
    auto = hot.setdefault("fold", {}).setdefault("auto_present", {})
    if declared:
        auto.clear()
        return
    scene = hot.setdefault("scene", {})
    present = list(scene.get("present") or [SHIKI])
    for key in turn.get("_spoke") or []:
        auto[key] = seq
        if key not in present:
            present.append(key)
    for key, last in list(auto.items()):
        if seq - int(last) >= FORGET:
            auto.pop(key)
            if key in present:
                present.remove(key)
    scene["present"] = present


def auto_cards(hot, turn, unknown, names):
    """Повторяющийся безымянный голос → {ключ: карточка-заготовка}."""
    seen = hot.setdefault("fold", {}).setdefault("voices", {})
    made = {}
    for shown in unknown:
        label = _clean(shown)
        row = seen.setdefault(label, {"turns": [], "first": turn["id"]})
        if turn["id"] not in row["turns"]:
            row["turns"] = (row["turns"] + [turn["id"]])[-4:]
        if len(row["turns"]) < AUTO_AFTER:
            continue
        key = slug(label).replace("-", "_")[:40]
        if key in names:
            continue
        made[key] = {"name": shown[:1].upper() + shown[1:], "role": "по подписи в сцене",
                     "first": row["first"], "auto": True}
        names[key] = _entry([made[key]["name"]])
        seen.pop(label, None)
    return made


SILENT = 5           # ходов без реплики и без имени в тексте — человек ушёл из сцены


def _named(entry, gm):
    words = set(re.findall(r"[а-яёa-z]{4,}", str(gm or "").casefold()))
    for part in entry.get("parts") or ():
        stem = part if len(part) <= 5 else part[:-1]
        if len(stem) >= 4 and any(w.startswith(stem) for w in words):
            return True
    return False


def prune_silent(hot, turn, seq, names, always_present=()):
    """Убирает из состава того, кого SILENT ходов нет ни в репликах, ни в тексте сцены.

    s11.text.039: на тракте за Суной в составе оставались Баки, Канкуро и связной,
    оставшиеся в госпитале. Состав питает реакции, волю и знание NPC в NEXT —
    лишние люди начинают «присутствовать» и говорить. Возвращает замечание.
    """
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    scene = hot.setdefault("scene", {})
    present = list(scene.get("present") or [])
    seen = hot.setdefault("fold", {}).setdefault("present_seen", {})
    spoke = set(turn.get("_spoke") or [])
    gm = turn.get("gm")
    for key in present:
        if key == SHIKI or key in always_present:
            continue
        if key in spoke or _named(names.get(key) or {}, gm) or key not in seen:
            seen[key] = seq
    gone = [k for k in present if k not in (SHIKI, *always_present) and seq - int(seen.get(k, seq)) >= SILENT]
    for key in list(seen):
        if key not in present or key in gone:
            seen.pop(key, None)
    if not gone:
        return []
    scene["present"] = [k for k in present if k not in gone]
    shown = ", ".join(display(names, k) for k in gone)
    return [{"turn": turn.get("id"), "kind": "presence", "text":
             f"Из состава сцены убраны {shown}: {SILENT} ходов ни реплики, ни упоминания. Если кто-то рядом — "
             "впиши его в d.scene.present и дай ему действовать."}]
