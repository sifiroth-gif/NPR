"""Исход спора по образцу боя в Йокогаме (Bleach RPG, III S32; механика LIVE/2, `02_CORE.md`).

Игрок, 25.11.2026: «Бой в Йокогаме целиком и полностью должен стать отправной точкой для
переработки боевой системы с учётом соотношения сил противников».

Как там: до броска выбирается полоса допустимых категорий исхода по шести осям против
конкретного противника, затем d20 раскладывается по полосе. Курохицуги: полоса 4–6, выпало 4 —
встречный срыв (противник вырвался наполовину и распорол капитану бок). Фумяку на измотанном
капитане: полоса 5–7, выпало 3 — всё равно 5, ограниченный успех: худшее, чем полоса,
не выпадает, лучшее — тоже.

Оси (каждая −2…+2, неуверенность — два соседних значения «a:b»):
  A — мастерство и инструмент: умение действующего против защиты цели (код, по листу и карточке);
  B — мощь, запас, рычаг: запас чакры и ранг (код) + рычаг ведущего (численность, призыв, оружие);
  C — знание и подготовка (ведущий): знает ли приём, читал ли движения, готовил ли место;
  D — геометрия, время, среда (ведущий): дистанция, укрытие, вода, окно; длинное действие — −1 (код);
  E — действительное сопротивление цели (ведущий) + её раны, кровопотеря, мораль (код);
  F — состояние и нагрузка действующего (код): раны, кровопотеря, чакра на исходе.
Сумма M: −12…−8 → 1; −7…−4 → 2; −3…−1 → 3; 0 → 4; 1…3 → 5; 4…7 → 6; 8…12 → 7.
"""

from __future__ import annotations

CATEGORIES = {1: "решительный провал", 2: "провал", 3: "ограниченный провал", 4: "встречный срыв",
              5: "ограниченный успех", 6: "чистый успех", 7: "решительный успех"}
MEANING = {1: "цель сорвана, случился названный до броска серьёзный ущерб",
           2: "цель не достигнута",
           3: "цель не достигнута, но есть полезный частичный результат",
           4: "обмен: обе стороны платят, удержание или взаимный срыв",
           5: "часть цели, меньший охват или известная цена",
           6: "цель достигнута",
           7: "цель достигнута и открыто дополнительное преимущество"}
D20 = {2: (10, 20), 3: (6, 14, 20), 4: (5, 10, 15, 20), 5: (4, 8, 12, 16, 20)}
AXES = "ABCDEF"
GM_AXES = "CDE"          # пишет ведущий
STEP = (0.5, 1.5)        # разница уровней → ось: <0,5 — 0; <1,5 — 1; дальше 2
EDGE = 0.15              # у границы шага — неуверенность «a:b»


def category(m):
    for upper, result in ((-8, 1), (-4, 2), (-1, 3), (0, 4), (3, 5), (7, 6)):
        if m <= upper:
            return result
    return 7


def from_die(band, value):
    """d20 по полосе от худшей категории к лучшей (`02_CORE.md`, раздел 2)."""
    band = list(band)
    if len(band) == 1:
        return band[0]
    value = max(1, min(20, int(value)))
    return next(cat for cat, upper in zip(band, D20[len(band)]) if value <= upper)


def axis(diff):
    """Разница уровней (0–5) → значение оси, у границы — пара соседних."""
    sign = 1 if diff >= 0 else -1
    size = abs(diff)
    value = 0 if size < STEP[0] else 1 if size < STEP[1] else 2
    near = next((s for s in STEP if abs(size - s) < EDGE), None)
    if near is None:
        return (sign * value, sign * value)
    low = 1 if near == STEP[1] else 0
    pair = sorted((sign * low, sign * (low + 1)))
    return (pair[0], pair[1])


def parse(value):
    """Ось ведущего: число или «a:b» (соседние) → (низ, верх) в пределах −2…+2."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        low, high = value
    elif isinstance(value, str) and ":" in value:
        low, high = value.split(":", 1)
    else:
        low = high = value
    try:
        low, high = int(float(low)), int(float(high))
    except (TypeError, ValueError):
        return None
    low, high = max(-2, min(2, low)), max(-2, min(2, high))
    if high < low:
        low, high = high, low
    if high - low > 1:
        low = high - 1
    return (low, high)


def clamp(pair, shift=0):
    return (max(-2, min(2, pair[0] + shift)), max(-2, min(2, pair[1] + shift)))


def band(axes, honest=False):
    """Оси {A..F: (низ, верх)} → полоса категорий. honest — против именного всегда бросок."""
    low = sum(axes[a][0] for a in AXES)
    high = sum(axes[a][1] for a in AXES)
    cats = list(range(category(low), category(high) + 1))
    if honest and len(cats) == 1:   # к середине: сильному — риск, слабому — шанс
        cat = cats[0]
        cats = [cat - 1, cat] if cat > 4 else [cat, cat + 1] if cat < 4 else [3, 4, 5]
    while len(cats) > 5:
        cats = cats[1:-1] if len(cats) - 2 >= 5 else cats[1:]
    return cats, (low, high)


def text(cats):
    return f"{cats[0]}–{cats[-1]}" if len(cats) > 1 else str(cats[0])
