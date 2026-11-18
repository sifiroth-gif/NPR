"""Места и фракции для вики мира.

Места выводятся из поля ``place`` сыгранных ходов всех сессий: где Шики был,
сколько раз, кто там с ним говорил и как место впервые описано (дословный
отрывок первого хода). Фракции — из карты давления мастера, только в
страницах ``*.GM_ONLY.md``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DESCRIPTION_CHARS = 700
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
                     "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))
PRESSURE = "campaigns/shiki/plans/ARC_PRESSURE_MAP_GM_ONLY.json"


def _parts(place):
    return [p.strip() for p in re.split(r"[.,;]", str(place or "")) if p.strip()]


def _key_name(place):
    parts = _parts(place)
    return ", ".join(parts[:2]) if parts else ""


def slug(name):
    text = str(name).casefold().replace("№", "n")
    out = "".join(TRANSLIT.get(ch, ch) for ch in text)
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    return out[:60] or "place"


SETTING = re.compile(r"стен|коридор|окн|потол|пол[уа]? |ламп|свет|запах|воздух|двер|камен|дерев|"
                     r"плитк|лестниц|полок|стол|крыш|двор|улиц|ворот|кровл|сыр|холод|тёпл|пыл|"
                     r"тень|тени|шкаф|скамь|фонар|пахл|пахнет|помещени|зал|комнат|палат", re.I)


def _description(gm, place_name):
    """Абзац, который описывает обстановку: больше всего слов о месте, а не о людях."""
    stems = [w[:5].casefold() for w in re.findall(r"\w{5,}", place_name)]
    best, score_best = "", 0
    for paragraph in re.split(r"\n\s*\n", str(gm or "")):
        text = paragraph.strip()
        if len(text) < 120 or text.startswith(("**", "—", "-", "#", "|", "<", ">", "«", "*")) or text.count("«") > 1:
            continue
        low = text.casefold()
        score = len(SETTING.findall(text)) + 2 * sum(1 for st in stems if st in low)
        if score > score_best:
            best, score_best = text, score
    if score_best < 3:
        return ""
    text = " ".join(best.replace("\\", "").split())
    return text if len(text) <= DESCRIPTION_CHARS else text[:DESCRIPTION_CHARS - 1] + "…"


def collect(state_dir, talkers=None):
    """{slug: {name, variants, turns, first, last, description, people}}."""
    places = {}
    speakers_by_turn = {}
    for key, entries in (talkers or {}).items():
        for entry in entries:
            speakers_by_turn.setdefault(entry["turn"], set()).add(key)
    for path in sorted(Path(state_dir, "archive").glob("session-*-turns.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            row = json.loads(raw)
            if row.get("kind") == "ooc" or not row.get("id"):
                continue
            name = _key_name(row.get("place"))
            if not name:
                continue
            item = places.setdefault(slug(name), {"name": name, "variants": {}, "turns": [],
                                                  "description": "", "people": {}})
            item["variants"][row["place"].strip()] = item["variants"].get(row["place"].strip(), 0) + 1
            item["turns"].append({"id": row["id"], "at": row.get("at", "")})
            if not item["description"] and len(item["turns"]) <= 3:
                item["description"] = _description(row.get("gm"), name)
                if item["description"]:
                    item["described_in"] = row["id"]
            for person in speakers_by_turn.get(row["id"], ()):
                item["people"][person] = item["people"].get(person, 0) + 1
    for item in places.values():
        item["turns"].sort(key=lambda t: tuple(int(x) for x in re.findall(r"\d+", t["id"])[:2]))
    return places


def page(key, item, names):
    turns = item["turns"]
    sessions = sorted({t["id"][:3] for t in turns})
    lines = [f"# {item['name']} · `places/{key}`", "",
             f"ходов здесь: {len(turns)} · сессии: {', '.join(sessions)} · "
             f"впервые: {turns[0]['id']} · последний раз: {turns[-1]['id']}", ""]
    if item.get("description"):
        lines += ["## Обстановка: отрывок из первых ходов здесь", f"> {item['description']}",
                  f"- источник: {item.get('described_in')}", ""]
    if item["people"]:
        people = sorted(item["people"].items(), key=lambda kv: -kv[1])[:20]
        lines += ["## Кто здесь говорил с Шики"]
        lines += [f"- {names.get(k, k)} · `npc/{k}` — {n} ход(ов)" for k, n in people]
        lines.append("")
    if len(item["variants"]) > 1:
        lines += ["## Как место записано в ходах"]
        lines += [f"- {v} ({n})" for v, n in sorted(item["variants"].items(), key=lambda kv: -kv[1])[:12]]
        lines.append("")
    lines += ["## Ходы"]
    lines += [f"- {t['id']} {t['at']}" for t in turns[-15:]]
    if len(turns) > 15:
        lines.append(f"- ранее: ещё {len(turns) - 15}, первый — {turns[0]['id']}")
    return "\n".join(lines) + "\n"


def index_page(places):
    lines = ["# Места", "", "Где Шики бывал в сыгранных сессиях. Описание места — на его странице.", ""]
    for key, item in sorted(places.items(), key=lambda kv: -len(kv[1]["turns"])):
        turns = item["turns"]
        lines.append(f"- {item['name']} · `places/{key}` · {len(turns)} ход(ов), "
                     f"{turns[0]['id'][:3]}–{turns[-1]['id'][:3]}")
    return "\n".join(lines) + "\n"


def _flat(value, limit=900):
    if isinstance(value, dict):
        text = "; ".join(f"{k}: {_flat(v, 300)}" for k, v in value.items())
    elif isinstance(value, list):
        text = "; ".join(_flat(v, 300) for v in value)
    else:
        text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def faction_pages(state_dir):
    """{путь: текст} для фракций из карты давления; всё — только ведущему."""
    path = Path(state_dir) / PRESSURE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sides = data.get("sides") or {}
    if not isinstance(sides, dict) or not sides:
        return {}
    files = {}
    index = ["# Фракции", "", "ТОЛЬКО ВЕДУЩИЙ. Источник: `" + PRESSURE + "`.", ""]
    log = data.get("log") or []
    for key, side in sides.items():
        lines = [f"# {key} · фракция", "", "ТОЛЬКО ВЕДУЩИЙ. Раскрывается только сыгранным.", ""]
        if isinstance(side, dict):
            for field, value in side.items():
                lines.append(f"- **{field}:** {_flat(value)}")
        else:
            lines.append(f"- {_flat(side)}")
        moves = [entry for entry in log if isinstance(entry, dict) and key in json.dumps(entry, ensure_ascii=False)]
        if moves:
            lines += ["", "## Ходы фракции (последние)"]
            for entry in moves[-3:]:
                lines.append(f"- {entry.get('session', '')} {entry.get('at', '')}: {_flat(entry.get('moves'), 600)}")
        files[f"memory/factions/{key}.GM_ONLY.md"] = "\n".join(lines) + "\n"
        summary = side.get("goal") or side.get("wants") or side.get("summary") if isinstance(side, dict) else side
        index.append(f"- `factions/{key}.GM_ONLY` — {_flat(summary, 200)}")
    files["memory/factions/INDEX.GM_ONLY.md"] = "\n".join(index) + "\n"
    return files
