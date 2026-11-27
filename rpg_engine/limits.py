"""Пределы памяти и правил — в одном месте, и предупреждение игроку, когда к ним подходим.

Поправка игрока 21.11.2026: пределы подняты примерно на пятую часть; когда
что-то подходит к пределу, ведущий одной строкой вне игры говорит игроку и
предлагает действия (закрыть сессию, строгая разгрузка, поднять предел).
"""
from __future__ import annotations

from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]

# 23.11.2026, игрок: «нужно было не разгружать, а увеличивать лимиты». Пять разгрузок за сессию 11 увели в
# архив её же страхи, планы и линию В-7; пределы подняты, а текущая сессия не стареет (aging.py).
HOT_NORM = 150_000           # рабочая норма HOT, байт; выше — строгое старение (но не текущей сессии)
HOT_LIMIT = 180_000          # выше — свёртка блокируется
GM_ONLY_NORM = 1_000_000       # скрытая часть: только предупреждение
BRIEF_TARGET = 60_000        # GM_BRIEF: цель (читается раз в начале чата)
BRIEF_HARD = 96_000          # GM_BRIEF: предел
# Пакет хода: 23.11.2026 в нём появились канон арки, знание присутствующих, арсенал боя, а поправок
# игрока стало 19 (7,6 КБ) — прежние 14/17 КБ не держались ни одним ходом. Сверх цели код ужимает сам.
NEXT_TARGET = 40_000         # пакет хода: цель (24.11.2026: + историческая память присутствующих)
NEXT_HARD = 50_000           # пакет хода: предел
SESSION_TURNS = 120          # длинная сессия: пора закрыть
WARN = 0.9                   # с какой доли предела предупреждать
REMIND_TURNS = 15            # одно и то же предупреждение — не чаще раза в 15 ходов

# Файлы правил (знаков). Тест tests/test_instruction_budget.py берёт пределы отсюда.
RULES = {
    "AGENTS.md": 7000,
    "campaigns/shiki/GM_CORE.md": 18000,
    "skills/dark-literary-gm/SKILL.md": 9000,
    "campaigns/shiki/SHIKI.md": 15000,
    "campaigns/shiki/ref/TECHNIQUES.md": 30000,
    "campaigns/shiki/ref/NEMERA.md": 8000,
    "skills/dark-literary-gm/references/horror-masters.md": 6000,
    "campaigns/shiki/BOOT.md": 2400,
    "campaigns/shiki/ref/MEDICINE.md": 7000,
    "campaigns/shiki/ref/COMBAT.md": 16000,
    "campaigns/shiki/ref/KNOWLEDGE.md": 7000,
    "campaigns/shiki/ref/MEMORY.md": 7000,
    "campaigns/shiki/ref/HORROR.md": 7000,
}

TRIM_REQUEST = "recovery/MEMORY_TRIM.json"
STATE = "recovery/LIMITS.json"   # когда что сказано игроку: отдельно от HOT, пишется после всех замеров
ACT_CLOSE = "закрыть сессию (SESSION_CLOSE.json)"
ACT_TRIM = "разгрузить прошлые сессии (MEMORY_TRIM.json; текущая сессия остаётся)"
ACT_RAISE = "поднять предел (хранитель)"
ACT_CHAT = "открыть новый чат: сессия продолжится с NEXT.next_id, правила перечитаются"
CHAT_TURNS = 50          # ходов в одном чате ведущего, после которых правила вымываются (s10.text.093)
ACT_RULE = "заменить правило или перенести в ref/"


def _ordinal(turn_id):
    from .aging import _ordinal as order
    return order(turn_id)


def check(state_dir, hot, *, hot_bytes, secret_bytes, engine=ENGINE):
    """Что подходит к пределу. Возвращает пакет для NEXT.limits или None; память показа — recovery/LIMITS.json."""
    import json
    state_dir, engine = Path(state_dir), Path(engine)
    items = []

    def add(key, what, size, limit, acts):
        if size >= limit * WARN:
            share = round(100 * size / limit)
            items.append({"key": key, "what": f"{what}: {size} из {limit} ({share}%)", "acts": acts,
                          "over": size > limit})

    add("hot", "оперативная память (HOT, байт)", hot_bytes, HOT_LIMIT, [ACT_CLOSE, ACT_TRIM, ACT_RAISE])
    add("gm_only", "скрытая память (GM_ONLY, байт)", secret_bytes, GM_ONLY_NORM, [ACT_CLOSE, ACT_TRIM])
    brief = state_dir / "recovery/GM_BRIEF.json"   # к этому моменту свёртка уже записала свежую сводку
    if brief.is_file():
        add("brief", "сводка ведущего GM_BRIEF (байт)", brief.stat().st_size, BRIEF_HARD, [ACT_CLOSE, ACT_TRIM, ACT_RAISE])
    for name, limit in RULES.items():   # правила держат тесты; игроку — только перерасход
        path = engine / name
        size = len(path.read_text(encoding="utf-8")) if path.is_file() else 0
        if size > limit:
            add(f"rule:{name}", f"файл правил {name} (знаков)", size, limit, [ACT_RULE, ACT_RAISE])
    cursor = (hot.get("cursor") or {}).get("id") or ""
    seq = int(cursor.rsplit(".", 1)[-1]) if cursor[-3:].isdigit() else 0
    # Closing a session preserves the final played cursor until the next turn.
    # Its 129 turns must not count as 129 turns of the newly opened session.
    if (hot.get("handoff") or {}).get("last_turn") == cursor:
        seq = 0
    if seq >= SESSION_TURNS * WARN:
        items.append({"key": "session", "what": f"сессия идёт {seq} ходов (рекомендуется до {SESSION_TURNS})",
                      "acts": [ACT_CLOSE], "over": seq > SESSION_TURNS})
    chat = (hot.get("fold") or {}).get("chat") or {}
    if int(chat.get("turns") or 0) >= CHAT_TURNS * WARN:
        items.append({"key": "chat", "what": f"чат ведущего идёт {chat['turns']} ходов (с {chat.get('since')}): "
                                             "правила со старта вымываются, вёрстка и канон теряются",
                      "acts": [ACT_CHAT], "over": chat["turns"] > CHAT_TURNS})
    path = state_dir / STATE
    try:
        said = json.loads(path.read_text(encoding="utf-8")).get("said") or {}
    except (OSError, ValueError, AttributeError):
        said = {}
    for key in list(said):
        if key not in {i["key"] for i in items}:
            said.pop(key)   # снова в норме: при следующем подходе сказать заново
    now = _ordinal(cursor) or 0
    tell = [i for i in items if now - said.get(i["key"], -10**6) >= REMIND_TURNS]
    for item in tell:
        said[item["key"]] = now
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "npr-limits/1", "said": said}, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    if not items:
        return None
    if not tell:   # уже сказано: коротко, без вариантов
        return {"items": [{"key": i["key"], "what": i["what"]} for i in items], "tell": False}
    return {"items": items, "tell": True,
            "rule": ("Предел близко. Скажи игроку ОДНОЙ короткой строкой вне игры после сцены (сцену не прерывай): "
                     "что подходит к пределу и варианты из acts на выбор; выбор игрока исполни: "
                     "закрыть сессию — recovery/SESSION_CLOSE.json {\"close\": true}; разгрузить — "
                     "recovery/MEMORY_TRIM.json {\"trim\": true}; поднять предел — просит хранителя игрок.")}


def trim_requested(state_dir):
    """Заявка ведущего на строгую разгрузку: одна свёртка, затем гасится."""
    import json
    path = Path(state_dir) / TRIM_REQUEST
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(request, dict) or not request.get("trim"):
        return False
    request["trim"] = False
    request["last"] = "done"
    path.write_text(json.dumps(request, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return True
