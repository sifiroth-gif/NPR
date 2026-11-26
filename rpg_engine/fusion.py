"""Source-gated post-fusion rules; historical saves and other campaigns are unchanged."""
MAX_CHAKRA = 600.0
REGEN_PER_HOUR = 10
SAGE_BONUS = {"speed": .75, "shunshin": .75, "reaction": .75, "weapons": .75,
              "taijutsu": .75, "strength": .75, "endurance": .75, "sensory": 1.0}
RETIRED_ACTORS = frozenset(("nemera", "utsuro"))
PROFILE = "dragon-fusion-v1"
SOURCE = "s14.text.046"
def active(hot):
    return ((hot.get("shiki") or {}).get("fusion_profile") or {}).get("id") == PROFILE
def retired(hot, key):
    return active(hot) and key in RETIRED_ACTORS
def max_chakra(hot, default=100.0):
    return MAX_CHAKRA if active(hot) else default
def regen_rate(hot, default=6):
    return REGEN_PER_HOUR if active(hot) else default
def always(hot, keys):
    return [key for key in keys if not retired(hot, key)]
PASSIVES = ("Постоянный режим отшельника дракона; расширенная природная и нечеловеческая сенсорика, "
            "усиленное тело, пластичность и регенерация в освоенных пределах. Единая чакра 600, "
            "цвет чёрный; риск только прямого контакта кожи либо свежей крови с раной/слизистой. "
            "Немера не отдельный NPC: отростки только по заявке Шики, из общего резерва. "
            "ВМШ лишь по заявке: красная радужка, чёрные склеры, вертикальный зрачок и узор 2900.jpg. "
            "Суми проводит освоенную чакру; не активировать за игрока. Новые техники требуют освоения. "
            "Источник: campaigns/shiki/ref/FUSION.md.")

# Explicit final profile: player correction s15.text.132, after the fusion.
FINAL_AT = "0010-04-23T14:09:00"
FINAL_EFFECTIVE = {"speed": 6.25, "shunshin": 6.25, "reaction": 5.95,
                   "sensory": 6.0, "taijutsu": 5.45, "strength": 4.85,
                   "endurance": 5.75, "ninjutsu": 5.6, "genjutsu": 4.9,
                   "medical": 5.2, "control": 5.5}

def final_profile(hot):
    return active(hot) and str((hot.get("cursor") or {}).get("at") or "") >= FINAL_AT

def passives(hot):
    if not final_profile(hot):
        return PASSIVES
    return (PASSIVES.replace("риск только прямого контакта кожи либо свежей крови с раной/слизистой.",
                             "обычное касание в покое при удержании чакры безопасно; кровь и потеря контроля остаются риском.")
            .replace("ВМШ лишь по заявке: красная радужка, чёрные склеры, вертикальный зрачок и узор 2900.jpg.",
                     "Единый Риннешаринган лишь по заявке, одномоментно: багрово-фиолетовая радужка, кольца, узор 2900.jpg, чёрные склеры и змеиный зрачок; шесть путей и прежние искусства ВМШ. Генетическое развитие завершено."))
