"""Контрольные вопросы памяти: находит ли ведущий ответ по INDEX и страницам.

Два набора. ``memory/EVAL.jsonl`` ведёт хранитель: вопрос, кто или какая
страница, ожидаемые фрагменты ответа, источник. Второй набор строится сам:
каждая поправка игрока из ``archive/shiki-canon.jsonl`` должна стоять хотя бы
на одной странице вики. Проверка без ИИ: только поиск страницы и текста.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

EVAL = "memory/EVAL.jsonl"
TARGET = 0.95
FIX_PAGES = ("memory/shiki-fixes.md", "memory/world-fixes.md")


def _norm(text):
    return " ".join(str(text or "").casefold().replace("ё", "е").split())


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _by_index(state_dir, who):
    index = _read(Path(state_dir) / "memory/INDEX.md") or ""
    want = _norm(who)
    for line in index.splitlines():
        if want and want in _norm(line):
            match = re.search(r"`((?:npc|items|threads|places)/[^`]+)`", line)
            if match:
                return f"memory/{match.group(1)}.md"
    return None


def _page_text(state_dir, source_root, page):
    base = Path(source_root) if page.startswith(("campaigns/", "skills/", "docs/")) else Path(state_dir)
    return _read(base / page)


def run(state_dir, source_root=None):
    state_dir = Path(state_dir)
    source_root = Path(source_root) if source_root else Path(__file__).resolve().parents[1]
    results = []
    path = state_dir / EVAL
    items = []
    if path.is_file():
        items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for item in items:
        page = item.get("page") or (_by_index(state_dir, item["who"]) if item.get("who") else None)
        text = _page_text(state_dir, source_root, page) if page else None
        missing = [e for e in item.get("expect") or [] if text is None or _norm(e) not in _norm(text)]
        ok = text is not None and not missing
        results.append({"q": item.get("q"), "page": page, "ok": ok,
                        "why": None if ok else ("страница не найдена по INDEX" if text is None else
                                                "нет на странице: " + "; ".join(missing))})
    fixes = []
    canon = state_dir / "archive/shiki-canon.jsonl"
    if canon.is_file():
        fixes = [json.loads(line) for line in canon.read_text(encoding="utf-8").splitlines() if line.strip()]
    pages = [(_read(state_dir / p) or "") for p in FIX_PAGES]
    npc_dir = state_dir / "memory/npc"
    if npc_dir.is_dir():
        pages += [p.read_text(encoding="utf-8") for p in npc_dir.glob("*.md")
                  if not p.name.endswith((".talk.md", ".knows.md")) and ".talk." not in p.name]
    corpus = "\n".join(pages)
    for fix in fixes:
        turn = fix.get("turn")
        head = _norm(fix.get("rule"))[:40]
        ok = bool(turn) and any(turn in line and head[:25] in _norm(line) for line in corpus.splitlines())
        results.append({"q": f"Поправка игрока {turn}", "page": "по имени в тексте поправки", "ok": ok,
                        "why": None if ok else "поправка не стоит ни на одной странице"})
    passed = sum(1 for r in results if r["ok"])
    return {"total": len(results), "passed": passed,
            "score": round(passed / len(results), 3) if results else None,
            "target": TARGET, "manual": len(items), "fixes": len(fixes),
            "failed": [r for r in results if not r["ok"]]}
