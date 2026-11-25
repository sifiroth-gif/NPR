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
