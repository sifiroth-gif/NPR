"""Единый формат архива: ``archive/session-NNN-turns.jsonl`` для всех сессий.

Сессии 4–8 уже ведутся в этом формате свёрткой. Для сессий 1–3 ходы
восстанавливаются без пересказа из уже сохранённых первоисточников:

* сессия 1 — ``archive/session-001.md``, дословная стенограмма чата
  (блоки ``**Игрок**`` / ``**Ведущий**``); каждый ответ ведущего — отдельный ход;
* сессии 2 и 3 — сыгранные ходы закрытого журнала ``campaign.sqlite``
  (полный текст из ``archived_journals``, время, место и ссылка на строки
  ``session-00N-source.md`` из ``journal``).

Старые файлы не меняются. Новый id — ``s0N.text.NNN``; прежний сохраняется
в ``legacy_id``. Карта всех прежних id — ``archive/turn-ids.json``.
"""
from __future__ import annotations

import json
import re
import sqlite3
import zlib
from pathlib import Path

MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7,
          "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
YEAR_OFFSET = 1277
MARKERS = {"**Игрок**": "player", "**Ведущий**": "gm"}
DATE = re.compile(r"(\d{1,2})\s*(?:[–-]\s*\d{1,2}\s*)?(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?")
TIME = re.compile(r"(?<![\d.])(\d{1,2}):(\d{2})(?::(\d{2}))?")
PLACE_LINES = (re.compile(r"^\*\*Место\\?:\*\*\s*(.+?)\s*$"),
               re.compile(r"^\*\*([^*\n]{6,}?)\.?\*\*\s*$"),
               re.compile(r"^\*([^*\n]{6,}?)\.?\*\s*$"))
DAY_HEAD = re.compile(r"(понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье|"
                      r"дата|время|день|неделя|июля|августа|сентября)", re.I)


def _jsonl_dump(rows):
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def _scene_head(text, previous_at, previous_place, default_year):
    """Время и место из шапки ответа; без шапки — от предыдущего хода."""
    head = "\n".join(text.strip().split("\n")[:8])
    at, place = previous_at, previous_place
    date = DATE.search(head)
    if date:
        day, month, year = int(date.group(1)), MONTHS[date.group(2)], date.group(3)
        year = int(year) if year else default_year
        day_part = f"{year - YEAR_OFFSET:04}-{month:02}-{day:02}"
        after = head[date.end():]
        time = TIME.search(after) or TIME.search(head)
        if time:
            at = f"{day_part}T{int(time.group(1)):02}:{int(time.group(2)):02}:{int(time.group(3) or 0):02}"
        elif previous_at and previous_at[:10] >= day_part:
            at = previous_at  # монтаж без часа в тот же день: время не откатывается
        else:
            at = f"{day_part}T00:00:00"
    elif previous_at:
        time = TIME.search(head)
        if time:
            at = f"{previous_at[:10]}T{int(time.group(1)):02}:{int(time.group(2)):02}:{int(time.group(3) or 0):02}"
    for line in head.split("\n"):
        line = line.strip()
        for pattern in PLACE_LINES:
            match = pattern.match(line)
            if match and (pattern is PLACE_LINES[0] or not DAY_HEAD.search(match.group(1))):
                return at, match.group(1).replace("\\", "").strip()
    return at, place


def session_one(state_dir):
    """Сессия 1 из дословной стенограммы: каждый ответ ведущего — ход."""
    path = Path(state_dir) / "archive/session-001.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    blocks = []
    for number, line in enumerate(lines):
        if line in MARKERS:
            blocks.append([MARKERS[line], number + 1, number + 1])
        elif blocks:
            blocks[-1][2] = number + 1
    rows, pending, at, place = [], [], "0001-07-01T14:10:00", ""
    for role, start, end in blocks:
        body = "\n".join(lines[start:end]).strip("\n")
        if role == "player":
            pending.append((body.strip(), start))
            continue
        at, place = _scene_head(body, at, place, 1278)
        first = pending[0][1] if pending else start
        rows.append({"id": f"s01.text.{len(rows) + 1:03}", "at": at, "place": place,
                     "player": "\n\n".join(p for p, _ in pending), "gm": body.strip(),
                     "source": f"archive/session-001.md#L{first}-L{end}", "status": "transcript"})
        pending = []
    if pending:
        rows.append({"id": f"s01.text.{len(rows) + 1:03}", "at": at, "place": place,
                     "player": "\n\n".join(p for p, _ in pending), "gm": "",
                     "source": f"archive/session-001.md#L{pending[0][1]}-L{len(lines)}",
                     "status": "transcript"})
    return rows


def _journal(state_dir, session):
    db = sqlite3.connect(Path(state_dir) / "campaign.sqlite")
    try:
        payload = db.execute("select payload from archived_journals where session=?",
                             (session,)).fetchone()
        full = {r["id"]: r for r in json.loads(zlib.decompress(payload[0])) if r.get("kind") == "turn"}
        live = list(db.execute("select seq, id, at, body, player_input, details from journal "
                               "where kind='turn' and session=? order by seq", (session,)))
    finally:
        db.close()
    return full, live


def played_session(state_dir, session):
    """Сыгранные ходы сессий 2–3 из закрытого журнала; части .pN одного хода сливаются."""
    full, live = _journal(state_dir, session)
    rows, by_number = [], {}
    for _seq, legacy, at, short_body, short_input, details in live:
        details = json.loads(details) if details else {}
        record = full.get(legacy) or {"body": short_body, "player_input": short_input}
        match = re.fullmatch(rf"s{session}\.played\.(\d+)(?:\.p(\d+))?", legacy)
        if not match:
            raise ValueError(f"unexpected id in session {session}: {legacy}")
        number = int(match.group(1))
        refs = [r.strip() for r in str(details.get("source_ref") or "").split(";") if r.strip()]
        status = details.get("source_status") or "journal"
        if any("recovery/corrections/" in r for r in refs):
            status = "corrected"
        if record["body"].startswith(("[Восстановление", "Восстановленная")):
            status = "reconstructed"
        if number in by_number:
            row = by_number[number]
            row["gm"] = row["gm"] + "\n\n" + record["body"].strip()
            row["legacy_id"].append(legacy)
            row["source"].extend(r for r in refs if r not in row["source"])
            continue
        row = {"id": f"s{session:02}.text.{number:03}", "at": at,
               "place": details.get("location") or "", "player": (record.get("player_input") or "").strip(),
               "gm": record["body"].strip(), "legacy_id": [legacy],
               "source": refs,
               "status": status}
        by_number[number] = row
        rows.append(row)
    for row in rows:
        row["legacy_id"] = row["legacy_id"][0] if len(row["legacy_id"]) == 1 else row["legacy_id"]
        row["source"] = row["source"][0] if len(row["source"]) == 1 else row["source"]
    return rows


def id_map(state_dir):
    """Прежние id сыгранных ходов сессий 2–7 → единые ``s0N.text.NNN``."""
    db = sqlite3.connect(Path(state_dir) / "campaign.sqlite")
    try:
        ids = [r[0] for r in db.execute("select id from journal where kind='turn' order by seq")]
    finally:
        db.close()
    mapping = {}
    for legacy in ids:
        match = re.fullmatch(r"s(\d)\.played\.(\d+)(?:\.p\d+)?", legacy)
        if match:
            mapping[legacy] = f"s{int(match.group(1)):02}.text.{int(match.group(2)):03}"
    return mapping


def normalize(state_dir, *, write=True):
    state_dir = Path(state_dir)
    sessions = {1: session_one(state_dir), 2: played_session(state_dir, 2), 3: played_session(state_dir, 3)}
    report = {}
    for number, rows in sessions.items():
        path = state_dir / f"archive/session-{number:03}-turns.jsonl"
        body = _jsonl_dump(rows)
        if path.is_file() and path.read_text(encoding="utf-8") != body:
            raise ValueError(f"{path} exists with different content; refusing to overwrite")
        if write:
            path.write_text(body, encoding="utf-8")
        report[number] = {"turns": len(rows), "first": rows[0]["id"], "last": rows[-1]["id"],
                          "statuses": {s: sum(1 for r in rows if r["status"] == s)
                                       for s in sorted({r["status"] for r in rows})}}
    mapping = id_map(state_dir)
    if write:
        (state_dir / "archive/turn-ids.json").write_text(
            json.dumps({"schema": "npr-turn-ids/1",
                        "rule": "Прежний id сыгранного хода → единый id archive/session-NNN-turns.jsonl. "
                                "Ходы сессии 1 нумеруются по ответам ведущего в archive/session-001.md.",
                        "ids": mapping}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report["legacy_ids"] = len(mapping)
    if write:
        (state_dir / "archive/README.md").write_text(README, encoding="utf-8")
    return report


README = """# Архив сыгранного

Единый формат всех сессий — `session-NNN-turns.jsonl`: по строке на ход с полями
`id`, `at`, `place`, `player` (заявка игрока дословно), `gm` (ответ ведущего дословно).
У сессий 1–3 есть ещё `source` (откуда взят текст), `status` и, для 2–3, `legacy_id`.
Каждый ход также лежит отдельным файлом `turns/sNN/<id>.md`.

| status | значение |
| --- | --- |
| `transcript` | сессия 1: ответ ведущего из дословной стенограммы `session-001.md` |
| `exact` | совпадает со строками экспорта чата `session-00N-source.md`, указанными в `source` |
| `corrected` | текст заменён по поправке игрока (`recovery/corrections/`) |
| `reconstructed` | в экспорте нет ответа ведущего; сохранена только заявка игрока |

Прежние файлы не удаляются и остаются первоисточниками:
`session-00N-source.md` — экспорт чата целиком, вместе с размышлениями модели;
`session-00N.md` — стенограмма закрытой сессии; `session-002-reviewed.md` — выверенные эпизоды;
`session-00N-chronicle.md` и `-index.json` — служебные сводки. При споре прав экспорт чата
и поздняя поправка игрока (`shiki-canon.jsonl`).

Прежние id (`s3.played.007.p1` и т. п.) переводятся в единые по `turn-ids.json`.
Сессия 1 в журнале движка была записана пересказом; здесь она восстановлена по стенограмме,
поэтому её номера ходов с прежними `s01.*` не совпадают.
"""
