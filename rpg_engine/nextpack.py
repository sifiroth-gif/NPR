"""Пакет следующего хода: одно маленькое чтение вместо пяти больших.

Ведущий в длинном чате уже держит в контексте правила, сцену и сказанное.
Перед каждым ходом ему нужно только то, что код посчитал после его записи:
замечания свёртки, вердикт прозы, sha ``TURN.json``, кости, чужие сроки, волю
присутствующих и границы редкого знания. Раньше за этим перечитывались
``GM_BRIEF`` (42 КБ), ``HOT`` (65 КБ, ради ``lint``), ``CHECKS`` и страницы
присутствующих NPC (25–55 КБ каждый ход) — каждое чтение раздувало чат и
задерживало сцену. ``recovery/NEXT.json`` держится в нескольких килобайтах.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import directives, dread, inquiry, lore, will

PATH = "recovery/NEXT.json"
from .limits import NEXT_HARD as HARD_BYTES, NEXT_TARGET as TARGET_BYTES  # noqa: E402
DUE_HOURS = 6
FIELD = 170
RULE = ("Это оперативный пакет, не замена сыгранному первоисточнику. Страницу memory/ "
        "открывай только для NPC, впервые входящего в сцену в этом чате, или по вопросу о прошлом; "
        "прочитанное в этом чате не перечитывай. cursor не равен твоему последнему ходу — свёртка "
        "отстала: открой CHECKS.json. Игрок просит закрыть сессию — запиши recovery/SESSION_CLOSE.json "
        "{\"close\": true}; отчёт — recovery/SESSION_CLOSE_REPORT.md. echo — давняя нить, созвучная сцене: верни событием, доступным Шики (человек, весть, вещь, последствие), не упоминанием. "
        "Давнее прошлое: если ID хода известен — адресно читай архив; иначе recovery/RECALL.json {\"q\": [\"вопрос\"]}: поиск по всем "
        "сессиям, ответ — NEXT.recall к следующему ходу.")


SOURCE_PRIORITY = ("Приоритет фактов: прямая поправка игрока > сыгранный первоисточник с последующими "
                   "изменениями > неизменённый канон > проверенная карточка > HOT/NEXT/GM_BRIEF. "
                   "Перед значимым изменяемым фактом адресно проверь последний меняющий ход; "
                   "сводка и поиск лишь указатели, отсутствие записи не означает исчезновения.")


def _short(text, limit=FIELD):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _parse(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _clock(value):
    at = _parse(value)
    return at.strftime("%d.%m %H:%M") if at else str(value or "")


def next_id(hot):
    cursor = hot.get("cursor") or {}
    handoff = hot.get("handoff") or {}
    # The cold session is already closed while the last accepted narrative
    # cursor still belongs to the preceding session. Do not advertise
    # s08.text.107 at a verified S08 -> S09 handoff.
    # Сессия может кончаться внеигровым ходом (s11.text.079 после сыгранного 077): закрыта та же сессия — новый номер.
    closed_here = str(handoff.get("closed")) == str(hot.get("session")) and handoff.get("closed") is not None
    if (handoff.get("last_turn") == cursor.get("id") or closed_here) and handoff.get("first_turn"):
        return handoff["first_turn"]
    try:
        return f"s{int(hot.get('session', 0)):02d}.text.{int(cursor.get('seq', 0)) + 1:03d}"
    except (TypeError, ValueError):
        return None


def _will_line(hot, key):
    profile = (hot.get("_psyche") or {}).get(key) or {}
    plans = [a for a in (profile.get("intent") or {}).values()
             if isinstance(a, dict) and a.get("status", "committed") in ("committed", "active")]
    lines = []
    for plan in plans[:2]:
        bits = [_short(plan.get("goal"), 120)]
        if plan.get("next"):
            bits.append("шаг: " + _short(plan["next"]) + (f" (к {_clock(plan['due'])})" if plan.get("due") else ""))
        if plan.get("flaw"):
            bits.append("слабость: " + _short(plan["flaw"], 120))
        if plan.get("against_shiki"):
            bits.append("против Шики: " + _short(plan["against_shiki"], 120))
        lines.append("; ".join(b for b in bits if b))
    decides = (profile.get("psyche") or {}).get("decides")
    if decides:
        lines.append("решает: " + _short(decides, 120))
    return lines


def _due(hot, brief, always_present=()):
    now = _parse((hot.get("cursor") or {}).get("at"))
    horizon = now + timedelta(hours=DUE_HOURS) if now else None
    out = []
    for clock in ((brief.get("world") or {}).get("clocks") or []):
        due = _parse(clock.get("due"))
        if due and horizon and due <= horizon:
            out.append((due, f"{_clock(clock['due'])} {clock.get('owner') or ''}: {_short(clock.get('what'))}"
                             f" → если никто не помешает: {_short(clock.get('if_unopposed'), 140)}"))
    for item in will.soon(hot, hours=DUE_HOURS, always_present=always_present):
        due = _parse(item.get("due"))
        decide = (due + timedelta(hours=will.GRACE_HOURS)).strftime("%H:%M") if due else "?"
        out.append((due or datetime.max, f"{_clock(item.get('due'))} {item['name']}: {_short(item.get('next'))}"
                                         f" → {_short(item.get('if_unopposed'), 140)} (не решишь до {decide} — "
                                         "решит бросок)"))
    out.sort(key=lambda x: x[0])
    return [text for _, text in out][:8]


# Вёрстка и литература — каждый ход, а не только из стартовых чтений: в длинном чате GM_CORE и
# навык уходят далеко назад, и ведущий их теряет (s10.text.093–099: реплики без подписи, Шики
# без присутствия — «ты вечно теряешь эту инструкцию»). Сверка ответа с этой карточкой — до записи.
FORM = ("Перед записью сверь ответ: 1) шапка <text color=\"secondary\" size=\"xs\">дата · время</text>, затем "
        "### место; 2) каждая реплика — **Полное имя:** — Текст, без голого тире и без «— …, — сказал он»; слова "
        "Шики из заявки — дословно, с подписью **Мугами Шики:**; 3) присутствие Шики в каждой сцене с людьми — их "
        "тревога по-лавкрафтовски, только видимым: дистанция, голос, оборванная фраза, лишнее движение, взгляд мимо, "
        "поступок, след после встречи (horror-masters «Реакция на Шики»); его облик не описывать (лишь при первой "
        "встрече или перемене), ауры нет; Шики — участник мира, не пользователь: заявка — попытка, исход решают мир и чужая воля, мир его "
        "не ждёт и не обслуживает; конфликт не гаснет сам: противник и угроза держат цель, пока их не снимут цена, поступок "
        "или кость; влияние — не одобрение: похвала редка, уступка — по своей причине и с ценой; Немера живёт "
        "по своему протоколу (ref/NEMERA.md) и не ждёт вызова; событие мира, которое Шики может увидеть или "
        "почувствовать, — в этой же сцене: игрок отвечает только на показанное; 4) NPC — тот, кто он в 1287 (present.<id>.who: "
        "характер канона), и говорит только своё (present.<id>.knows: что слышал и видел, где был рядом): "
        "нет общего прошлого с Шики, которого не было, нет его тайн без канала; его память — present.<id>.past и knows: "
        "виденное там не удивляет заново; мысли, память и выводы NPC в прозу не идут — только слово, поступок, лицо; "
        "медик говорит языком своего мира, а не словами Шики; 5) проза по навыку и horror-masters: факт, ритм, "
        "необратимость, без толкований и словаря заботы; не протокол: телесная деталь, запах, звук, у слова — цена; "
        "числа механики (единицы чакры, пороги, кости) — только в записи хода, в прозе — ощущение; "
        "6) перед последней строкой — давление мира (движение, угроза, вопрос в лицо), не перечень путей; "
        "последняя строка — **Ход Шики.**")


RECENT_TURNS = 40   # план просят у тех, кто был в игре не дальше стольких ходов назад


def must(hot, always_present=(), entries=None):
    """Обязательное на этот ход: короткий список вместо десятка советов.

    Замечания ведущий пропускал по 12 ходов подряд (s08.053–062); здесь не больше
    трёх пунктов, каждый — одно конкретное действие в этой сцене или записи.
    """
    out = inquiry.must(hot)
    for entry, names, turn in lore.first_sight(hot, entries or [])[:1]:
        out.append(f"След увиденного: {', '.join(names[:5])} впервые столкнулись с «{entry.get('title') or entry['id']}» "
                   f"({turn}). Это не проходит за ход: {entry.get('first_sight') or 'вопросы, отрицание, страх, слух'} "
                   "— в этой сцене и за кадром; запиши в их dread.")
    present = (hot.get("scene") or {}).get("present") or []
    for key, row in dread.pending(hot, present, always_present)[:2]:
        name = will._name(hot, key)
        known = ((hot.get("_psyche") or {}).get(key) or {}).get("dread")
        if row.get("need") and known:
            out.append(f"Реакция на Шики: {name} рядом с {row.get('since')}, в этой встрече реакции нет. "
                       f"Прежде — {dread.summary(hot, key)}. Что изменилось после увиденного сегодня и что "
                       f"из этого сделано здесь: сдвиг, hold или down — d.cast.{key}.dread.")
        elif row.get("need"):
            out.append(f"Реакция на Шики: {name} рядом с {row.get('since')}, отношение к Шики не сыграно. "
                       "Что видит и знает о Шики, как объясняет это по своему опыту и что из этого делает "
                       "здесь — решение, оговорка, проверка, расчёт; равнодушие тоже выбор. Запиши "
                       f"d.cast.{key}.dread: saw, reads, does, stage ({dread.summary(hot, key)}).")
        else:
            out.append(f"{name} {row.get('quiet')} хода рядом с Шики без новой реакции: увидел новое — сдвиг "
                       f"в поступке и d.cast.{key}.dread; нет — dread.hold с тем, что подтвердилось.")
    for key, name, plan in will.due_here(hot, always_present)[:1]:
        out.append(f"{will._name(hot, key)} рядом, а срок плана «{_short(plan.get('next') or plan.get('goal'), 120)}» "
                   f"прошёл — сыграй этот ход в сцене: сделано, сорвалось по слабости или смена тактики; "
                   f"запиши d.cast.{key}.intent.{name}: status и outcome.")
    # Новый план — только тем, кто значим для Шики (встречал его, имеет характер или шёл против него)
    # и был в игре недавно: давно ушедшему пациенту план не нужен.
    from .aging import _ordinal
    now = _ordinal((hot.get("cursor") or {}).get("id")) or 0
    touched = (will._state(hot).get("touched") or {})
    significant = [(k, l) for k, l in will.without_plan(hot)
                   if (l.get("against_shiki") or any(((hot.get("_psyche") or {}).get(k) or {}).get(f)
                                                     for f in ("dread", "psyche")))
                   and now - (_ordinal(touched.get(k)) or 0) <= RECENT_TURNS]
    for key, last in significant[:1]:
        plan = last.get("goal") or last.get("outcome") or last.get("next") or last.get("key")
        out.append(f"У {will._name(hot, key)} закрыт план «{_short(plan, 80)}» "
                   f"({last.get('status')}), нового нет: впиши d.cast.{key}.intent.<ключ> — goal, next, due, "
                   "flaw, if_unopposed. Исход — видимая перемена мира, что может дойти до Шики (приезд, сделка, "
                   "исчезновение, слух, смерть), не «получит перечень, но не…».")
    return out[:3]


def build(hot, brief, checks, entries, always_present=(), rules=None, happened=None, echoed=None, near=None,
          recall=None, news=None, extra=None):
    cursor = brief.get("cursor") or {}
    present = [k for k in (hot.get("scene") or {}).get("present") or [] if k != lore.SHIKI]
    pack = {
        "schema": "npr-next/1",
        "cursor": cursor.get("id"),
        "next_id": next_id(hot),
        "header": " ".join(x for x in (cursor.get("date"), cursor.get("time")) if x),
        "place": cursor.get("place"),
        "turn_file": (brief.get("turn_file") or {}).get("sha"),
        # 24.11.2026: ведущий ждал свёртку («ход сыгран, но пока не записан») — свёртка идёт около минуты,
        # а игрок отвечает быстрее; ходы из истории git код сворачивает по порядку сам.
        "write": ("Запись не ждёт свёртку. sha — из ответа твоей последней записи TURN.json (turn_file — лишь "
                  "для первой записи в чате). Если cursor старше твоего последнего хода — свёртка ещё идёт: пиши "
                  "следующий ход от своего (ID +1, predecessor — твой последний), NEXT прошлого хода в силе. "
                  "Конфликт sha — одно чтение TURN.json и запись того же хода."),
        "fold_errors": checks.get("fold_errors") or [],
    }
    if rules:
        pack["directives"] = dict(rules, rule="Поправки игрока к ведению: выше GM_CORE и навыка, исполнять до отмены.")
    if (extra or {}).get("ages"):   # возраст на дату хода от дня рождения (canon.ages_now)
        pack["ages"] = dict(extra["ages"], rule="Возраст на дату хода от дня рождения: не менять и не брать "
                                                "из оригинала Naruto; растёт сам с календарём.")
    duty = must(hot, always_present, entries)
    if (extra or {}).get("unease"):  # тревога рядом с Шики пропала (favor.unease_must)
        duty = [extra["unease"]] + duty[:2]
    if (extra or {}).get("favor"):   # мир не подыгрывает: окно похвал и уступок перекошено (favor.must)
        duty = [extra["favor"]] + duty[:2]
    if news and news.get("must"):   # мир доходит до Шики: весть или чужой шаг в затянувшейся сцене
        duty = [news["must"]] + duty[:2]
    if (extra or {}).get("arc_must"):   # канон арки просрочен рядом с Шики (arcs.must)
        duty = [extra["arc_must"]] + duty[:2]
    if (extra or {}).get("resync"):     # перечитать стартовый пакет в нынешнем чате (hot.resync_item)
        duty = [extra["resync"]] + duty[:2]
    if duty:
        pack["must"] = duty
    if happened is None:
        happened = will.offscreen(hot)
    if happened:
        pack["offscreen"] = {"rule": "Случилось без Шики, он этого не знает. Это факт мира: последствия "
                                     "встречаются там, куда дойдёт канал — слух, документ, человек, перемена; "
                                     "исход, требующий встречи с Шики, случается в ближайшей возможной сцене.",
                             "events": happened}
    pack["form"] = FORM
    for key in ("arc", "canon_review", "canon", "style"):   # события арки, канон к сцене, образец манеры
        if (extra or {}).get(key):
            pack[key] = extra[key]
    if echoed:
        pack["echo"] = echoed
    if recall:
        pack["recall"] = recall
    if near:
        pack["limits"] = near
    # Замечания к прошлым ходам ведущий уже видел: в пакет — к последнему ходу и о текущем состоянии.
    state_kinds = ("plot", "fight", "memory", "will", "echo")
    pack["checks"] = [f"[{c.get('kind')}] {c.get('text')}" for c in checks.get("checks") or []
                      if c.get("turn") in (None, cursor.get("id")) or c.get("kind") in state_kinds and
                      c.get("turn") == cursor.get("id")]
    source_conflicts = [c for c in checks.get("checks") or []
                        if c.get("turn") in (None, cursor.get("id"))
                        and c.get("kind") in ("contradiction", "historical", "source", "entity_claim")]
    if source_conflicts:
        pack["source_review"] = {
            "required": True,
            "rule": ("Перед следующим фактическим выводом найди исходный и последний меняющий архивный "
                     "ход. HOT не определяет победителя конфликта; пустота сводки не значит неизвестность."),
            "items": [_short(c.get("text"), 220) for c in source_conflicts[:2]],
        }
    last = ((hot.get("lint") or {}).get("recent") or [])[-1:]
    if last and last[0].get("id") == cursor.get("id") and last[0].get("verdict") != "чисто":
        pack["prose"] = {"verdict": last[0].get("verdict"),
                         "hits": [f"{h.get('rule')}: «{_short(h.get('quote'), 90)}»" for h in last[0].get("hits", [])[:3]]}
    people = {}
    for key in present:
        if key in always_present:
            continue
        entry = {"name": lore._name(hot, key), "reaction": dread.summary(hot, key)}
        knows = ((extra or {}).get("knows") or {}).get(key)
        if knows:   # знакомство до сессии и сведения о Шики (acquaint.view): чего NPC не знает — не говорит
            entry["knows"] = knows
        who = ((extra or {}).get("knows") or {}).get(f"{key}#who")
        if who:     # канонический человек 1287 года: кто он сейчас и каков (ref/FOES.json)
            entry["who"] = who
        past = ((extra or {}).get("past") or {}).get(key)
        if past:    # прошлые сессии с Шики по теме сцены (echo.present_past): его память — отсюда и из knows
            entry["past"] = past
        lines = _will_line(hot, key)
        if lines:
            entry["will"] = lines
        people[key] = entry
    if people:
        pack["present"] = people
    fences = lore.fences(hot, entries, present)
    if fences:
        pack["fences"] = fences
    due = _due(hot, brief, always_present)
    if due:
        pack["due"] = due
    fight = brief.get("fight") or {}
    if fight.get("dice"):
        pack["dice"] = fight["dice"]
    if fight.get("chakra"):
        pack["chakra"] = fight["chakra"]
    nemera = ((hot.get("shiki") or {}).get("nemera_chakra"))
    if isinstance(nemera, dict) and nemera.get("value") is not None:   # её запас отдельно от сотни Шики
        pack.setdefault("chakra", {})["nemera"] = {k: nemera[k] for k in ("value", "max", "range_meters") if k in nemera}
    if fight.get("active"):
        pack["fight"] = {k: v for k, v in fight.items() if k not in ("dice", "rules", "ref")}
    pack["source_priority"] = SOURCE_PRIORITY
    pack["rule"] = RULE
    _fit(pack)
    return pack


def _size(pack):
    return len(json.dumps(pack, ensure_ascii=False).encode("utf-8"))


def _cut(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _squeeze_fight(pack):
    fight = pack.get("fight") or {}
    fight.pop("last_fight", None)
    injuries = {}
    for who, wounds in (fight.get("injuries") or {}).items():
        serious = [f"{w.get('where')}: {w.get('severity')}" for w in wounds
                   if w.get("severity") in ("средняя", "тяжёлая", "критическая")]
        light = len(wounds) - len(serious)
        if serious or light:
            injuries[who] = serious + ([f"лёгких: {light}"] if light else [])
    if injuries:
        fight["injuries"] = injuries
    active = fight.get("active") or {}
    if active:
        active["recent_rolls"] = [{k: (_cut(v, 80) if k == "what" else v) for k, v in r.items()}
                                  for r in (active.get("recent_rolls") or [])[-2:]]


def _squeeze_arc(pack):
    arc = pack.get("arc") or {}
    if arc.get("next"):
        arc["next"] = [_cut(x, 260) for x in arc["next"][:2]]
    if arc.get("happened"):
        arc["happened"] = [_cut(x, 200) for x in arc["happened"][:2]]


PRESENT_KEEP = {"knows": 420, "who": 360}   # знание и характер — суть жалоб игрока, режутся последними


def _squeeze_present(pack):
    for person in (pack.get("present") or {}).values():
        for key, value in list(person.items()):
            if key != "name" and isinstance(value, str):
                person[key] = _cut(value, PRESENT_KEEP.get(key, 160))
            elif isinstance(value, list):
                person[key] = [_cut(v, 140) if isinstance(v, str) else v for v in value[:2]]


def _squeeze_minor(pack):
    pack.pop("echo", None)
    if pack.get("fences"):
        pack["fences"] = [_cut(f, 300) if isinstance(f, str) else f for f in pack["fences"][:2]]
    if pack.get("due"):
        pack["due"] = pack["due"][:2]


def _squeeze_checks(pack):
    if pack.get("checks"):
        pack["checks"] = [_cut(c, 220) for c in pack["checks"][-3:]]


_SQUEEZE = (_squeeze_fight, _squeeze_minor, _squeeze_arc, _squeeze_checks, _squeeze_present)


def _fit(pack):
    """Сначала укорачиваются сроки, затем замечания; ошибки свёртки не трогаются."""
    while _size(pack) > TARGET_BYTES and len(pack.get("due") or []) > 3:
        pack["due"].pop()
    while _size(pack) > HARD_BYTES and len(pack.get("checks") or []) > 3:
        pack["checks"].pop(0)
    if _size(pack) > HARD_BYTES:            # образец манеры — первым из необязательного
        pack.pop("style", None)

    while _size(pack) > HARD_BYTES and len((pack.get("canon") or {}).get("items") or []) > 1:
        pack["canon"]["items"].pop()
    for answer in (pack.get("recall") or {}).get("answers") or []:   # полный ответ — в файле
        if _size(pack) > HARD_BYTES and len(answer.get("top") or []) > 1:
            answer["top"] = answer["top"][:1]
    # s11.text.062: NEXT 33 КБ при пределе 17 — бой, сроки, канон и присутствующие разрослись. Полное — в
    # GM_BRIEF; здесь — ужатое по порядку, пока не влезет.
    for step in _SQUEEZE:
        if _size(pack) <= HARD_BYTES:
            break
        step(pack)
    if _size(pack) > HARD_BYTES:   # сам пакет за пределом и ужать нечего — сказать игроку
        from . import limits
        item = {"key": "next", "what": f"пакет хода NEXT (байт): {_size(pack)} из {HARD_BYTES}",
                "acts": [limits.ACT_CLOSE, limits.ACT_TRIM, limits.ACT_RAISE], "over": True}
        near = pack.setdefault("limits", {"items": [], "tell": True,
                                          "rule": "Предел близко. Скажи игроку ОДНОЙ короткой строкой вне игры после "
                                                  "сцены: что подходит к пределу и варианты из acts; выбор игрока исполни."})
        near["items"].append(item)
    return pack


def _write(state_dir, pack):
    path = Path(state_dir) / PATH
    body = json.dumps(pack, ensure_ascii=False, indent=1) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == body:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return True


def write(state_dir, hot, brief, checks, always_present=(), happened=None, echoed=None, near=None, recall=None,
          news=None, extra=None):
    pack = build(hot, brief, checks, lore.load(state_dir), always_present, directives.view(state_dir, hot),
                 happened=happened, echoed=echoed, near=near, recall=recall, news=news, extra=extra)
    _write(state_dir, pack)
    return pack


def write_blocked(state_dir, cursor, errors, *, kind="fold_errors", rule):
    """Свёртка не прошла: вместо старого пакета — сама ошибка."""
    path = Path(state_dir) / PATH
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pack = {"schema": "npr-next/1"}
    pack["cursor"] = cursor
    pack[kind] = list(errors)
    pack["blocked_rule"] = rule
    if any("разросся" in str(e) for e in errors):   # предел памяти: игрок должен знать сразу
        from . import limits
        pack["limits"] = {"items": [{"key": "hot", "what": "оперативная память переполнена — память после ходов "
                                                           "не обновляется", "acts": [limits.ACT_TRIM, limits.ACT_CLOSE,
                                                                                     limits.ACT_RAISE], "over": True}],
                          "tell": True,
                          "rule": "Скажи игроку сейчас одной строкой вне игры: память не обновляется из-за предела, "
                                  "и предложи варианты из acts; выбор игрока исполни. Ходы не потеряны."}
    _write(state_dir, pack)
    return pack
