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

Запись ``"kind": "fact"`` — сведение о самом Шики (год у Орочимару, учил Саске,
донор С-17). Узнаётся так же: был рядом, когда прозвучало, или получил по каналу
(``knows+`` в карточке). У сведения вместо слов могут быть ``patterns`` —
регулярные выражения: «жил у Орочимару» не ловится одним словом. Догадка
(«возможно», «кажется», вопрос) — не утечка: спросить и предположить NPC вправе.
Сведения не выводятся в ``fences`` — их показывает ``acquaint.view`` по людям.
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


def _visual_rules():
    """Source-scoped sightlines: authored review, not inferred from roster membership.

    Lives in the code branch, outside mutable campaign memory. A stale HOT
    `lore` entry is a candidate only; it is not an eyewitness certificate.
    """
    path = Path(__file__).resolve().parents[1] / "campaigns/shiki/KNOWLEDGE_POLICY.json"
    try:
        return (json.loads(path.read_text(encoding="utf-8"))
                .get("visual_witness_scopes") or {})
    except (OSError, ValueError):
        return {}


def load(state_dir):
    path = Path(state_dir) / SEED
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rules = _visual_rules()
    entries = [e for e in data.get("entries") or [] if isinstance(e, dict) and e.get("id")
               and (e.get("terms") or e.get("patterns"))]
    for entry in entries:
        if entry["id"] in rules and entry.get("kind") == "trait":
            entry["_visual_witness"] = rules[entry["id"]]
    return entries


def is_fact(entry):
    """Сведение или черта самого Шики (не техника): в fences не выводится, показывается по людям."""
    return entry.get("kind") in ("fact", "trait")


def is_trait(entry):
    """Видимая черта Шики (чёрная чакра, холодное тело): узнаёт тот, кто был в сцене, — глазами, не ушами.

    s11.text.073: Сакура «впервые увидела» чёрную чакру, хотя оперировала Канкуро рядом с ней в s11.text.034.
    """
    return entry.get("kind") == "trait"


# Предположение и вопрос — не знание: «возможно, вы учились у змей», «вы жили у Орочимару?»
GUESS = re.compile(r"(?<![\wё])(?:может|возможно|кажется|предполож\w*|вероятно|похоже|не знаю|допустим|"
                   r"полагаю|наверное|если|слышал\w*,? будто|говорят)(?![\wё])", re.I)


def _norm(text):
    # ё не сводится к е: «бётон» — природа чакры, «бетон» — стройматериал.
    return str(text or "").casefold()


def _terms(entry):
    return [_norm(t) for t in entry.get("terms") or [] if str(t).strip()]


_COMPILED = {}


def _pattern(entry):
    # Термин — начало слова: «Бётона», «Шитоном», но не «бетонном» и не середина слова.
    key = (tuple(_terms(entry)), tuple(str(p) for p in entry.get("patterns") or []))
    if key not in _COMPILED:
        parts = [r"(?<![\wё])(?:" + re.escape(t) + ")" for t in key[0]]
        parts += [f"(?:{p})" for p in key[1] if p.strip()]
        _COMPILED[key] = re.compile("|".join(parts) or r"(?!x)x", re.I)
    return _COMPILED[key]


def mentions(entry, text):
    return bool(_terms(entry) or entry.get("patterns")) and bool(_pattern(entry).search(_norm(text)))


def _guess(line, entry):
    """Сведение звучит в вопросе или предположении: «Вы жили у Орочимару?», «возможно, сендзюцу»."""
    for match in _pattern(entry).finditer(_norm(line)):
        start = max(line.rfind(".", 0, match.start()), line.rfind("!", 0, match.start())) + 1
        ends = [i for i in (line.find(".", match.end()), line.find("!", match.end()),
                            line.find("?", match.end())) if i >= 0]
        sentence = line[start:(min(ends) + 1) if ends else len(line)]
        if "?" in sentence or GUESS.search(sentence):
            continue
        return False
    return True


def _name(hot, key):
    if key == SHIKI:
        return "Шики"
    card = (hot.get("cast") or {}).get(key) or {}
    profile = (hot.get("_psyche") or {}).get(key) or {}
    return card.get("name") or profile.get("name") or key


def verified_visual_witness(hot, entry, actor, source):
    """A trait is *seen* only with an authenticated actor/event sightline.

    Historical curator rows audit specific episodes. New episodes require a
    current-turn `knowledge_events` witness route, not a narrative mention or
    a previous, possibly erroneous `lore` cache entry.
    """
    rule = entry.get("_visual_witness")
    if not rule:
        return True
    source = str(source or "")
    episode = (rule.get("episodes") or {}).get(source)
    if episode is not None:
        return (actor in (episode.get("witnessed_by") or [])
                and actor not in (episode.get("known_non_witnesses") or []))
    card = ((hot.get("cast") or {}).get(actor) or {})
    return any(isinstance(e, dict) and e.get("claim") == entry["id"]
               and e.get("channel") == "witness"
               and e.get("source") == source and e.get("turn") == source
               for e in card.get("knowledge_provenance") or [])


def heard(hot, entry):
    """Actor-specific knowledge; high-impact visual feats require proof.

    A previously auto-filled "seen" record is not itself evidence. For strict
    sightline traits only named verified witnesses survive in NEXT.present.
    """
    candidates = dict(entry.get("heard") or {})
    for key, turn in ((hot.get("lore") or {}).get(entry["id"]) or {}).items():
        candidates.setdefault(key, turn)
    if entry.get("_visual_witness"):
        out = {key: source for key, source in candidates.items()
               if verified_visual_witness(hot, entry, key, source)}
        for key, card in (hot.get("cast") or {}).items():
            for event in (card or {}).get("knowledge_provenance") or []:
                if (isinstance(event, dict) and event.get("claim") == entry["id"]
                        and event.get("channel") == "witness"
                        and verified_visual_witness(hot, entry, key, event.get("source"))):
                    out.setdefault(key, event["source"])
        return out
    # Oral knowledge through a sourced report is different from witnessing a
    # visual feat. It never silently becomes "видел".
    out = candidates
    for key, card in (hot.get("cast") or {}).items():
        if key not in out and any(mentions(entry, k) for k in (card or {}).get("knows") or []):
            out[key] = "knows+"
    return out


def reconcile_visual_memory(hot, entries):
    """Remove derivative, unsupported eyewitness rows; never edit play history.

    The operation is idempotent. Verified scenes remain in the provenance
    index; missing evidence is not converted into an invented eyewitness.
    """
    removed = {}
    store = hot.get("lore") or {}
    for entry in entries:
        if not entry.get("_visual_witness"):
            continue
        current = store.get(entry["id"]) or {}
        invalid = {actor: source for actor, source in current.items()
                   if not verified_visual_witness(hot, entry, actor, source)}
        if invalid:
            for actor in invalid:
                current.pop(actor, None)
            removed[entry["id"]] = invalid
    return removed


INNER = ("немера", "уцуро")   # голоса внутри Шики: их слышит только он


def heard_text(turn, names=None):
    """Что прозвучало вслух: речь Шики из заявки и подписанные реплики, кроме голосов внутри Шики.

    Сведение о Шики узнают ушами: повествование («змеиные черты режима отшельника») и
    мысли курсивом свидетелю недоступны — в отличие от увиденной техники.
    """
    player = re.sub(r"#[^#]*#", " ", str(turn.get("player") or ""))
    player = re.sub(r"(?is)\bмастеру\b.*?(?=\bпо игре\b|$)", " ", player)
    player = re.sub(r"(?i)\bпо игре\s*:", " ", player)
    parts = [player]
    for raw in str(turn.get("gm") or "").split("\n"):
        label = re.match(r"^\s*\*\*([^*\n]{2,48}?)(?:\\)?:\*\*\s*[*_]*\s*[—–-]\s*(.*)$", raw)
        if label and not any(n in _norm(label.group(1)) for n in INNER):
            parts.append(label.group(2))
    return "\n".join(parts)


def track(hot, turn, entries, present, names=None):
    """Отмечает присутствующих не-владельцев рядом с прозвучавшим термином (сведение — только вслух).

    Сведение о Шики слышит лишь тот, кто в этом ходе говорит или назван в сцене: в составе
    s11.text.040 на тракте числились Баки и Канкуро, оставшиеся в Суне, — тайну они не слышали.
    """
    if turn.get("kind") == "ooc":
        return []
    from .presence import _named
    in_scene = set(turn.get("_spoke") or [])
    if names is not None:
        in_scene |= {k for k in present if _named(names.get(k) or {}, turn.get("gm"))}
    text = f"{turn.get('player', '')}\n{turn.get('gm', '')}"
    spoken = None
    added = []
    for entry in entries:
        if is_trait(entry):
            if not mentions(entry, text):
                continue
        elif is_fact(entry):
            spoken = heard_text(turn) if spoken is None else spoken
            if not mentions(entry, spoken):
                continue
        elif not mentions(entry, text):
            continue
        wield = set(entry.get("wield") or [])
        known = heard(hot, entry)
        book = hot.setdefault("lore", {}).setdefault(entry["id"], {})
        for key in present:
            if key == SHIKI or key in wield or key in known:
                continue
            # A global roster is not a sightline: a character may be chasing
            # another enemy elsewhere while still listed as "present".
            if is_fact(entry) and names is not None and key not in in_scene:
                continue
            if (entry.get("_visual_witness") and
                    not verified_visual_witness(hot, entry, key, turn["id"])):
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
            if is_fact(entry) and _guess(line, entry):
                continue        # спросить или предположить вправе каждый
            candidates = [who] if who else others
            unheard = [k for k in candidates if k not in before and not from_shiki]
            if unheard and len(unheard) == len(candidates):
                names = ", ".join(_name(hot, k) for k in unheard)
                if is_fact(entry):
                    text = (f"Знание о Шики без источника: в реплике «{line[:140]}» — «{title}», но {names} "
                            "этого от Шики не слышал(а) и по каналу не получал(а). Покажи канал в сцене "
                            "(донесение, слух, документ — и knows+ в карточке) или пусть не знает: спросит, "
                            "ошибётся, промолчит.")
                else:
                    text = (f"Утечка знания: в реплике «{line[:140]}» звучит {title}, но {names} "
                            f"об этом не слышал(а). Откуда знание? Владеют только {_wielders(hot, entry)}; "
                            f"слышали — по ходам в fences.")
                notes.append({"turn": turn["id"], "kind": "lore", "text": text})
                flagged = line
                break
        if is_fact(entry):
            continue            # «применить» сведение нельзя — проверка умения только для техник
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


def first_sight(hot, entries):
    """Впервые увидевшие/услышавшие в последнем свёрнутом ходе: [(запись, [имена], ход)]."""
    fresh = (hot.get("fold") or {}).get("lore_new") or []
    out = []
    for entry in entries:
        if is_fact(entry):
            continue        # услышанное о Шики — не «увиденное впервые»
        rows = [r for r in fresh if r.get("entry") == entry["id"]]
        if rows:
            out.append((entry, [_name(hot, r["key"]) for r in rows], rows[-1]["turn"]))
    return out


def fences(hot, entries, present):
    """Строки для присутствующих: кто владеет, кто слышал, кто нет."""
    out = []
    people = [k for k in present if k != SHIKI]
    if not people:
        return out
    for entry in entries:
        if entry.get("_visual_witness"):
            known = heard(hot, entry)
            people_seen = [_name(hot, key) + " (" + str(known[key]) + ")" for key in people if key in known]
            people_unknown = [_name(hot, key) for key in people if key not in known]
            out.append(f"{entry.get('title') or entry['id']}: непосредственно видели — "
                       f"{', '.join(people_seen) or 'нет подтверждённых свидетелей'}; "
                       f"нет подтверждения наблюдения — {', '.join(people_unknown) or 'никого'}. "
                       "Присутствие в отряде и запись lore без источника не доказывают видимость.")
            continue
        if is_fact(entry):
            continue        # сведения о Шики — построчно по людям (acquaint.view)
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
