"""Протокол ноль: кодовая команда игрока на начало совершенно новой игры.

Канал для ведущего, который умеет только писать файлы: игрок присылает
кодовую команду, ведущий дословно кладёт её в ``recovery/ZERO.json``,
свёртка вызывает :func:`handle`. Фраза хранится только хешем.

Решения игрока (19.11.2026):
* старая игра замораживается в архивную ветку и не удаляется;
* очищаются сохранение и канон Шики; код движка и общие правила ведения и
  прозы остаются;
* в новую игру переходят только общие постоянные поправки (``scope:
  general`` в ``memory/_curated/directives.json``), про Шики — в архив;
* пока только ПРОБНЫЙ ПРОГОН: отчёт о том, что было бы сделано; ни один
  файл игры не меняется. Настоящее очищение включается отдельно (``LIVE``).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

REQUEST = "recovery/ZERO.json"
REPORT = "recovery/ZERO_REPORT.md"
PHRASE_SHA256 = "9d46ab66952b016af539f8ac971a8958403dac08abe7093d9c6acd8640aed704"
LIVE = False
ENGINE = Path(__file__).resolve().parents[1]
MSK = timezone(timedelta(hours=3))

SAVE_KEEP = {"AGENTS.md", ".github", ".gitattributes", ".gitignore"}
DIRECTIVES = "memory/_curated/directives.json"
CAMPAIGN = "campaigns/shiki"
# Канон Шики целиком: уходит в архив без разбора.
CANON = ("SHIKI.md", "roster.json", "lore.json", "ontology.json", "WORLD_LORE.md", "WORLD_ONTOLOGY.md",
         "SCENE_PACK.json", "sessions", "arcs", "continuity")
# Общие правила: остаются, строки о Шики переписываются под новую игру.
GENERAL = ("AGENTS.md", "skills/dark-literary-gm")
SHIKI_TERMS = re.compile(r"Шики|Мугами|Немер|Договор|Бётон|Шитон|Ситон|Хётон|Нулев\w* Геном|Уцуро|"
                         r"Кусагакуре|Седьм\w* палат|Сэйн|Shiki|Nemera", re.I)


def normalize(text):
    return str(text or "").strip().strip(".,!?;:«»\"'` ").lower()


def verify(text):
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest() == PHRASE_SHA256


def _size(path):
    if path.is_file():
        return 1, path.stat().st_size
    files = [p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts]
    return len(files), sum(p.stat().st_size for p in files)


def _mb(size):
    return f"{size / 1048576:.1f} МБ" if size >= 1048576 else f"{max(1, round(size / 1024))} КБ"


def _shiki_lines(path):
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    count = 0
    for file in files:
        try:
            count += sum(1 for line in file.read_text(encoding="utf-8").splitlines() if SHIKI_TERMS.search(line))
        except (UnicodeDecodeError, OSError):
            continue
    return count


def _git_head(state_dir):
    try:
        return subprocess.run(["git", "-C", str(state_dir), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def plan(state_dir, engine_dir=ENGINE, now=None):
    """Что сделал бы Протокол ноль. Ничего не меняет."""
    state_dir, engine_dir = Path(state_dir), Path(engine_dir)
    now = now or datetime.now(MSK)
    try:
        hot = json.loads((state_dir / "recovery/HOT.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hot = {}
    cursor = hot.get("cursor") or {}
    out = {"archive_branch": f"archive/shiki-{now:%Y%m%d}", "snapshot": _git_head(state_dir),
           "cursor": cursor.get("id"), "at": cursor.get("at"), "save_clear": [], "save_keep": [],
           "canon_archive": [], "general_edit": [], "directives_keep": [], "directives_archive": []}
    for entry in sorted(state_dir.iterdir(), key=lambda p: p.name):
        if entry.name == ".git":
            continue
        files, size = _size(entry)
        row = {"path": entry.name + ("/" if entry.is_dir() else ""), "files": files, "size": size}
        (out["save_keep"] if entry.name in SAVE_KEEP else out["save_clear"]).append(row)
    campaign = engine_dir / CAMPAIGN
    if campaign.is_dir():
        for entry in sorted(campaign.iterdir(), key=lambda p: p.name):
            files, size = _size(entry)
            rel = f"{CAMPAIGN}/{entry.name}" + ("/" if entry.is_dir() else "")
            if entry.name in CANON:
                out["canon_archive"].append({"path": rel, "files": files, "size": size})
            else:
                out["general_edit"].append({"path": rel, "lines": _shiki_lines(entry)})
    for name in GENERAL:
        path = engine_dir / name
        if path.exists():
            out["general_edit"].append({"path": name + ("/" if path.is_dir() else ""), "lines": _shiki_lines(path)})
    from . import directives
    standing = directives.active(state_dir, hot)
    for item in standing:
        if not isinstance(item, dict) or not item.get("text"):
            continue
        key = "directives_keep" if item.get("scope") == "general" else "directives_archive"
        out[key].append({"text": item["text"], "scope": item.get("scope") or "не размечено"})
    return out


def report(result, now):
    if result is None:
        return (f"# Протокол ноль\n\n{now:%d.%m.%Y %H:%M} МСК: команда не распознана. Ничего не сделано.\n")
    lines = [f"# Протокол ноль — пробный прогон",
             "",
             f"{now:%d.%m.%Y %H:%M} МСК: команда распознана. **Ничего не изменено** — это план того, "
             f"что произойдёт при настоящем запуске. Настоящее очищение пока выключено.",
             "",
             "## 1. Архив старой игры",
             f"- Ветка `{result['archive_branch']}` — снимок сохранения"
             + (f" на коммите `{result['snapshot']}`" if result["snapshot"] else "")
             + (f", последний ход {result['cursor']}" if result["cursor"] else "")
             + (f" ({str(result['at'])[:16].replace('T', ' ')})" if result["at"] else "") + ".",
             "- Туда же копируется канон Шики с `main` (раздел 3). Вернуться к игре за Шики — переключиться на эту ветку.",
             "",
             "## 2. Очистится на сохранении `game-state/shiki`",
             "История ветки не переписывается: новая игра начнётся новым коммитом с чистого листа."]
    for row in result["save_clear"]:
        lines.append(f"- `{row['path']}` — {row['files']} файл(ов), {_mb(row['size'])}")
    if result["save_keep"]:
        lines.append("Остаётся: " + ", ".join(f"`{r['path']}`" for r in result["save_keep"]) + ".")
    lines += ["", "## 3. Канон Шики на `main` — в архив"]
    lines += [f"- `{row['path']}` — {row['files']} файл(ов), {_mb(row['size'])}" for row in result["canon_archive"]]
    lines += ["", "## 4. Остаётся, строки о Шики переписываются под новую игру"]
    lines += [f"- `{row['path']}` — {row['lines']} строк(и) о Шики" for row in result["general_edit"] if row["lines"]]
    clean = [row["path"] for row in result["general_edit"] if not row["lines"]]
    if clean:
        lines.append("Без упоминаний Шики, остаются как есть: " + ", ".join(f"`{p}`" for p in clean) + ".")
    lines += ["", "## 5. Твои постоянные поправки к ведению", "Перейдут в новую игру:"]
    lines += [f"- {d['text']}" for d in result["directives_keep"]] or ["- нет"]
    lines.append("Уйдут в архив вместе с Шики:")
    lines += [f"- {d['text']}" + ("" if d["scope"] == "shiki" else " *(не размечено)*")
              for d in result["directives_archive"]] or ["- нет"]
    lines += ["", "## 6. Не трогается",
              "Код движка `rpg_engine/`, тесты, процессы GitHub Actions, документация движка."]
    return "\n".join(lines) + "\n"


def handle(state_dir, engine_dir=ENGINE, now=None):
    """Выполнить ожидающую команду из ``recovery/ZERO.json``: сейчас только пробный прогон."""
    state_dir = Path(state_dir)
    path = state_dir / REQUEST
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(request, dict) or not normalize(request.get("command")):
        return None
    now = now or datetime.now(MSK)
    accepted = verify(request.get("command"))
    result = plan(state_dir, engine_dir, now) if accepted else None
    (state_dir / REPORT).write_text(report(result, now), encoding="utf-8")
    outcome = "dry-run" if accepted else "rejected"
    request["command"] = ""   # команда исполнена; фраза не остаётся в файле
    request["last"] = {"at": now.isoformat(timespec="minutes"), "result": outcome,
                       "live_requested": bool(request.pop("mode", None) == "live"), "live_enabled": LIVE}
    path.write_text(json.dumps(request, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"result": outcome, "report": REPORT}
