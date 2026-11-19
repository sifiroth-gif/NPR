"""Поправка хранителя к сюжету и линиям — без участия ведущего.

Планировщик и линии ведёт ведущий через ``d.plot`` и ``d.threads``. Когда он
забывает (сыгранное побочное событие так и висит в очереди, линия описывает
состояние трёх сессий назад), хранитель пишет
``memory/_curated/keeper.GM_ONLY.json``::

    {"threads": {"ключ": {"closed": "исход"} | {"state": "…"}},
     "plot": {"ключ": {"closed": "исход"} | {"status": "active", "limit": {...}, "notes": [...]}}}

Применяется один раз на версию файла, после ходов: сцену событиям не
засчитывает и сыгранного не переписывает.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import plot

SEED = "memory/_curated/keeper.GM_ONLY.json"


def apply(state_dir, hot):
    path = Path(state_dir) / SEED
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    body = {k: data.get(k) for k in ("threads", "plot")}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    fold = hot.setdefault("fold", {})
    if fold.get("keeper_delta") == digest:
        return False
    cursor = hot.get("cursor") or {}
    at, turn = cursor.get("at"), f"keeper@{cursor.get('id')}"
    threads = hot.setdefault("threads", {})
    for key, patch in (body["threads"] or {}).items():
        if not isinstance(patch, dict):
            continue
        if patch.get("closed"):
            closed = threads.pop(key, None)
            if closed is not None:
                hot.setdefault("offscreen", {}).setdefault("resolved", []).append(
                    {"id": f"thread:{key}", "at": at, "turn": turn, "outcome": patch["closed"]})
        elif key in threads or patch.get("state"):
            threads.setdefault(key, {}).update(patch)
            threads[key]["turn"] = turn
    _plot, events = plot._events(hot)
    for key, patch in (body["plot"] or {}).items():
        if not isinstance(patch, dict):
            continue
        if patch.get("closed"):
            if key in events:
                plot._close(_plot, events, key, patch["closed"], turn, at)
            continue
        event = events.setdefault(key, {"id": key, "kind": patch.get("kind") or "side", "status": "queued",
                                        "opened": turn, "opened_at": at, "scenes": 0})
        for field in plot.FIELDS:
            if field in patch and patch[field] is not None:
                event[field] = patch[field]
    fold["keeper_delta"] = digest
    return True
