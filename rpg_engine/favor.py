"""Мир не подыгрывает: похвала, уступки без цены и отсутствие «нет» у NPC.

s11.text.030–038: Баки впускает в Суну нукенина класса S, жившего год у убийцы
Четвёртого Кадзекагэ; затем «Вы спасли его. Я этого не забуду», отказ от
48 миллионов и свободный выход. Сакура: «Его возможности впечатляют», «после
экзамена ты времени не терял». Какаши берёт неизвестного в отряд за одну
реплику. Ни одного отказа, торга или проверки за восемь ходов: мир подстроился
под желание игрока. Каждая уступка по отдельности объяснима — вместе это
подыгрывание.

Код считает в репликах NPC похвалу Шики, уступки (с ценой или без) и трение —
отказ, условие, торг, недоверие. С 24.11.2026 (s12.text.054–062: Орочимару называет
имя змеи по просьбе, Сиранами «уходит по собственной воле» после лечения, Ямато
«освобождает тропу», змея-разведчик «так и не добралась» — игрок: «подыгрывает,
сглаживает конфликты») — и в повествовании: угроза или противник отступает, соглашается,
не ищет дальше без цены в том же абзаце (``smooth``). Замечание к ходу — при явной похвале и уступке
без цены; пункт ``must`` — когда окно последних ходов перекошено. Исход код не
решает: напоминает, что у NPC своя воля.
"""
from __future__ import annotations

import re

from . import lore, presence
from .acquaint import lines

WINDOW = 8          # ходов в окне
MIN_TURNS = 5       # раньше окна не судить
PRAISE_MAX = 4      # похвал за окно — уже перекос
FREE_MAX = 1        # уступок без цены за окно — больше одной перекос
COOLDOWN = 4        # ходов между пунктами must
SMOOTH_MAX = 3      # сглаживаний без цены за окно — перекос

PRAISE = re.compile(
    r"впечатл|восхищ|поразительн|невероятн|блестящ|великолепн|гениальн|мастерск|превосходн|безупречн"
    r"|(?<![\wё])(?:вы|ты)\s+(?:\w+\s+)?спасл?и?(?![\wё])|спасли\s+(?:его|её|ее|их|нас)|не\s+забуду|в\s+долгу"
    r"|(?<![\wё])спасибо|благодар|никто\s+(?:\w+\s+){0,2}не\s+(?:смог|мог|сумел)"
    r"|чего\s+(?:они|мы|наши\s+\w+)\s+не\s+(?:смогли|могли|сумели)|лучше\s+(?:всех|любого|нас)"
    r"|времени\s+не\s+терял|слишком\s+хорошо|впервые\s+вижу|за\s+несколько\s+минут", re.I)
GRANT = re.compile(
    r"(?<![\wё])(?:вы|ты)\s+свобод|не\s+(?:станем|будем|стану|станет)\s+(?:\w+\s+)?задерж|принимаю\s+(?:ваше|тво[её])\s+"
    r"предложени|добро\s+пожаловать|не\s+возражаю|(?<![\wё])разрешаю|можете\s+идти|как\s+скажете"
    r"|(?:вам|тебе)\s+доверя|отказаться\s+от"
    r"|свободн\w+\s+(?:проход|выход)|запрет\s+на\s+задержание", re.I)
COST = re.compile(
    r"(?<![\wё])(?:но|однако|если|взамен|пока)(?![\wё])|при\s+условии|цен[аеуы](?![\wё])|услови|не\s+более"
    r"|под\s+(?:надзор|охран|присмотр|ответственность)|сопровожд|залог|поклян|отвечаете|не\s+снимаю|не\s+прощ", re.I)
FRICTION = re.compile(
    r"(?<![\wё])нет[.!]|не\s+позволю|не\s+пущу|отказ|откажу|запрещ|(?<![\wё])нельзя|не\s+дам|не\s+отдам"
    r"|не\s+скажу|ни\s+шагу|арест|задержать|под\s+стражу|(?<![\wё])сначала\s+(?:вы|ты)|взамен|услови"
    r"|не\s+верю|лж[её]т|врёт|врете|докажите|докажи|чем\s+(?:вы|ты)\s+заплат|что\s+(?:вы|ты)\s+(?:дадите|дашь)"
    r"|сдайте|сдай\s+оруж|руки\s+на\s+виду|не\s+двигаться", re.I)


# Повествование: противник, угроза или упрямый NPC сам сдаёт позицию.
SMOOTH = re.compile(
    r"(?<![\wё])(?<!не\s)(?:отступил\w*[^.!?\n]{0,30}(?:освобожда|пропуска|уступа)\w*|"
    r"освобожда\w*\s+(?:тропу|дорогу|проход|путь)|"
    r"опустил\w*\s+(?:руку|ладонь)[^.!?\n]{0,40}(?:так\s+и\s+не|не\s+заверш|не\s+слож|не\s+законч)\w*|"
    r"опустил\w*\s+(?:оружие|кунай|меч|барьер|заграждени\w*)|убрал\w*\s+(?:барьер|оружие|кунай|заграждени\w*)|"
    r"не\s+стал\w*\s+(?:преследовать|спорить|настаивать|возражать|мешать|задерживать|искать|проверять)|"
    r"не\s+захотел\w*\s+(?:искать|преследовать|спорить|проверять)|без\s+возражений|"
    r"ограничил\w*\s+(?:коротким|жестом|кивком)|не\s+пытал\w*\s+(?:воздейств|помешать|остановить|задержать)\w*|"
    r"так\s+и\s+не\s+(?:добрал|наш|замети|обнаружи|увиде)\w*|оставил\w*\s+(?:попытк|сенбон|иглу)\w*)", re.I)
SELF = re.compile(r"Немер|отрост|нить|Шики", re.I)   # свои: Немера, сам Шики — не «сглаживание мира»
# Цена в том же абзаце: кровь, рана, потеря, долг, подозрение, обида, условие, слежка.
SMOOTH_COST = re.compile(
    r"кров|ран[аыуе]|бол[ьи]|сломал|потерял|не\s+простит|запомн|счёт|долг|подозр|недовер|обид|условие|взамен|"
    r"цен[аыу](?![\wё])|следит|следил|метк|запеча|не\s+отпуст|позже|ещё\s+вернётся|вернусь", re.I)


# Тревога рядом с Шики — видимым поведением (24.11.2026, игрок: «больше описаний в стиле Лавкрафта
# реакции на Шики без описаний внешности, а именно описание реакций и тревога»). s12.text.062–067 —
# шесть ходов подряд без единого признака: люди рядом с ним ведут себя как рядом с кем угодно.
UNEASE = re.compile(
    r"(?<![\wё])(?:отшатн\w*|попятил\w*|отступил\w*|шаг\w* назад|сглотн\w*|побледн\w*|вздрогн\w*|передёрн\w*|"
    r"передерн\w*|содрогн\w*|осёкся|осекся|запнул\w*|не договорил\w*|оборвал\w*|замолчал\w*|умолк\w*|"
    r"голос\w* (?:сел|дрогнул|сорвался|стал тише|упал)|отв[её]л\w* (?:взгляд|глаза)|смотрел\w* мимо|"
    r"не (?:смотрел|поднимал)\w* глаз|избегал\w*|держал\w*(?: \w+)? (?:дистанц|расстояни)\w*|"
    r"не (?:подходил|приближал)\w*|не поворачивал\w* спин\w*|рук\w*[^.!?\n]{0,20}(?:к оружи|к кунаю|к рукояти|"
    r"к подсумк|на рукоять)|пальц\w*[^.!?\n]{0,20}(?:побелел|сжал|дрогнул|дрожал)\w*|костяшк\w*|перекрест\w*|"
    r"молитв\w*|поспешн\w*|слишком (?:быстро|громко|тихо|ровно)|натянут\w*|нервн\w*|тревог\w*|беспоко\w*|"
    r"страх\w*|боял\w*|боится|опаск\w*|ужас\w*|жут\w*|не по себе|(?:затаил|задержал)\w* дыхани\w*|"
    r"(?:по)?медлил\w*|не решал\w*|переспросил\w*|ещё раз (?:проверил|пересчитал)\w*)", re.I)
CALM_MAX = 2        # ходов подряд с людьми рядом и без тревоги — пункт must
# Приёмы по кругу, чтобы тревога не стала одной дрожью (references/horror-masters.md «Реакция на Шики»).
UNEASE_WAYS = (
    "тревога раньше причины: человек меняется до того, как поймёт почему — понижает голос, кладёт вещи ровнее",
    "пустое место: вокруг Шики расстояние, которого никто не выбирал; спиной к нему не встают",
    "речь ломается: фраза о нём обрывается и продолжается о другом, его имя произносят с паузой или не произносят",
    "лишнее движение: вытирает чистые руки, второй раз проверяет застёжку, держит чашку обеими руками",
    "счёт не сходится: переспрашивает очевидное, повторяет услышанное, будто проверяет, что расслышал",
    "длинная фраза наблюдения — и короткий поступок, который уже не отменить: ушёл, взялся за оружие, отдал",
    "отложенный след: после встречи — бессонница, донос, перенос смены, оберег у двери; Шики узнаёт по следу",
    "тишина группы: при нём разговор короче, смех гаснет, кто-то находит дело в другом конце",
    "честнее взрослых: ребёнок смотрит, пока его не уведут; собака не идёт за порог от запаха змеи",
)


def _narration(text):
    """Абзацы повествования без реплик, шапки и служебного."""
    out = []
    for para in re.split(r"\n\s*\n", str(text or "")):
        lines = [l for l in para.split("\n") if l.strip() and not l.lstrip("*_ ").startswith(("—", "–", "<text", "###"))
                 and not re.match(r"^\s*\*\*[^*]{2,60}:\*\*", l)]
        if lines:
            out.append(" ".join(lines))
    return out


def smoothing(turn):
    """[отрывок]: в повествовании угроза или противник сдаёт позицию без цены в том же абзаце."""
    out = []
    for para in _narration(turn.get("gm")):
        match = SMOOTH.search(para)
        if match:
            left = max(para.rfind(c, 0, match.start()) for c in ".!?") + 1
            if SELF.search(para[left:match.start()]):
                continue
        if match and not SMOOTH_COST.search(para):
            start = max(0, match.start() - 60)
            out.append(" ".join(para[start:match.end() + 40].split()))
    return out


def _state(hot):
    return hot.setdefault("fold", {}).setdefault("favor", {"recent": []})


def measure(turn, names, always=()):
    """Счёт хода: похвала, уступки, уступки без цены, трение; и примеры."""
    rows = []
    for label, text in lines(turn.get("gm")):
        if "шики" in label.casefold():
            continue
        key = presence.resolve(label, names)
        if key in always:
            continue
        rows.append((key, label, text))
    out = {"id": turn.get("id"), "praise": 0, "grant": 0, "free": 0, "friction": 0, "quotes": []}
    smooth = smoothing(turn)
    out["smooth"] = len(smooth)
    if smooth:
        out["sq"] = f"{turn.get('id')}: «{smooth[0][-90:]}»"
    out["quotes"] += [f"«{q[:100]}» — сглажено" for q in smooth[:1]]
    for i, (key, label, text) in enumerate(rows):
        if PRAISE.search(text):
            out["praise"] += 1
            if len(out["quotes"]) < 2:
                out["quotes"].append(f"{label}: «{text[:90]}»")
        if FRICTION.search(text):
            out["friction"] += 1
        if GRANT.search(text):
            out["grant"] += 1
            # Цена — в той же реплике или в двух следующих репликах того же говорящего.
            same = [t for k, lb, t in rows[i + 1:i + 6] if (k and k == key) or lb == label][:2]
            if not any(COST.search(t) for t in [text] + same):
                out["free"] += 1
                if len(out["quotes"]) < 3:
                    out["quotes"].append(f"{label}: «{text[:90]}» — без цены")
    return out


def track(hot, turn, names, always=()):
    """Записывает счёт хода в окно; замечание kind=favor — при явном подыгрывании в этом ходе."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    row = measure(turn, names, always)
    state = _state(hot)
    people = [k for k in (hot.get("scene") or {}).get("present") or [] if k not in ("shiki", "player") and k not in always]
    if people:   # рядом есть люди: тревога видна в их поведении — или счёт спокойных ходов растёт
        calm = not UNEASE.search("\n".join(_narration(turn.get("gm"))))
        state["calm"] = int(state.get("calm") or 0) + 1 if calm else 0
        state["calm_at"] = turn.get("id")
    recent = [r for r in state.get("recent") or [] if r.get("id") != row["id"]]
    state["recent"] = (recent + [{k: v for k, v in row.items() if k != "quotes"}])[-WINDOW:]
    if row["free"] or row["praise"] >= 3 or row["smooth"] >= 2:
        what = []
        if row["praise"] >= 3:
            what.append(f"похвала Шики в {row['praise']} репликах")
        if row["free"]:
            what.append(f"уступок без цены — {row['free']}")
        if row["smooth"] >= 2:
            what.append(f"конфликт гаснет сам — {row['smooth']} раза")
        return [{"turn": row["id"], "kind": "favor", "text":
                 f"Подыгрывание: {', '.join(what)} ({'; '.join(row['quotes'][:2])}). NPC уступает по своей "
                 "причине и за свою цену — условие, надзор, услуга, риск для него самого; восхищение — "
                 "редкость, не реакция по умолчанию; противник не бросает цель без причины, угроза не исчезает сама."}]
    return []


VERSION = 3          # 24.11.2026: сглаживание в повествовании и спокойные ходы — пересчитать по архиву


def seed(hot, rows, names, always=()):
    """Пустое окно — из последних сыгранных ходов архива, один раз (и заново при смене счёта)."""
    state = _state(hot)
    if state.get("v") != VERSION:
        state.update(recent=[], seeded=False, v=VERSION)
    if state.get("recent") or state.get("seeded"):
        return
    played = [r for r in rows if r.get("kind") not in ("ooc", "technical", "void")][-WINDOW:]
    state["recent"] = [{k: v for k, v in measure(r, names, always).items() if k != "quotes"} for r in played]
    state["seeded"] = True
    calm = 0     # спокойные ходы подряд с конца: люди говорили рядом, тревоги в повествовании нет
    for row in reversed(played):
        voices = [label for label, _ in lines(row.get("gm")) if "шики" not in label.casefold()
                  and presence.resolve(label, names) not in always and "немер" not in label.casefold()]
        if not voices or UNEASE.search("\n".join(_narration(row.get("gm")))):
            break
        calm += 1
    if played:
        state.update(calm=calm, calm_at=played[-1].get("id"))


def totals(hot):
    recent = _state(hot).get("recent") or []
    return {"turns": len(recent), **{k: sum(int(r.get(k) or 0) for r in recent)
                                    for k in ("praise", "grant", "free", "friction", "smooth")}}


def must(hot):
    """Пункт must, если окно перекошено: похвала, уступки без цены, ни одного «нет»."""
    state = _state(hot)
    recent = state.get("recent") or []
    if len(recent) < MIN_TURNS:
        return None
    total = totals(hot)
    skew = (total["praise"] >= PRAISE_MAX or total["free"] > FREE_MAX or total["smooth"] >= SMOOTH_MAX
            or (total["friction"] == 0 and total["grant"] + total["praise"] >= 2))
    if not skew:
        return None
    from .aging import _ordinal
    now = _ordinal(recent[-1].get("id")) or 0
    if now - int(state.get("must_at") or -COOLDOWN) < COOLDOWN and state.get("must_turn") != recent[-1].get("id"):
        return None
    state["must_at"], state["must_turn"] = now, recent[-1].get("id")
    why = []
    if total["praise"] >= PRAISE_MAX:
        why.append(f"похвал и благодарностей Шики — {total['praise']}")
    if total["free"] > FREE_MAX:
        why.append(f"уступок без цены — {total['free']}")
    if total["smooth"] >= SMOOTH_MAX:
        seen = [r["sq"] for r in recent if r.get("sq")][-2:]
        why.append(f"конфликт гаснет сам без цены — {total['smooth']} раз" + (f" ({'; '.join(seen)})" if seen else ""))
    if total["friction"] == 0:
        why.append("ни одного отказа, условия или недоверия")
    return (f"Мир подыгрывает Шики: за {total['turns']} ходов {', '.join(why)}. "
            "В этом ходе у присутствующего — своё против желания Шики по его карточке (служба, страх, выгода): "
            "условие, торг, проверка, отказ или ложь; противник и угроза держат цель, пока их не снимет цена, поступок "
            "или кость. Заявка игрока — намерение, исход решает мир; спорное — костью. "
            "Не наказание и не враждебность ради неё — его воля.")


def unease_must(hot, always=()):
    """Пункт must: CALM_MAX ходов подряд с людьми рядом и без тревоги — три приёма по кругу."""
    people = [k for k in (hot.get("scene") or {}).get("present") or [] if k not in ("shiki", "player") and k not in always]
    if not people:   # рядом никого (25.11.2026: змеи ушли, а пункт о тревоге людей остался)
        return None
    state = _state(hot)
    calm = int(state.get("calm") or 0)
    if calm < CALM_MAX:
        return None
    from .aging import _ordinal
    start = (_ordinal(state.get("calm_at")) or 0) % len(UNEASE_WAYS)
    ways = [UNEASE_WAYS[(start + i * 3) % len(UNEASE_WAYS)] for i in range(3)]
    word = "ход" if calm % 10 == 1 and calm % 100 != 11 else (
        "хода" if calm % 10 in (2, 3, 4) and calm % 100 not in (12, 13, 14) else "ходов")
    return (f"Тревога рядом с Шики пропала: {calm} {word} подряд люди рядом ведут себя как с кем угодно. "
            "В этом ходе — их тревога по-лавкрафтовски, только видимым (перспектива Шики) и без описания его облика: "
            + "; ".join(ways) + ". Не дрожь и не пульс по умолчанию, не прошлый приём; страх не обязан быть назван.")
