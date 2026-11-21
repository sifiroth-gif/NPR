"""Вопрос ведущего к давнему прошлому: recovery/RECALL.json → ответ к следующему ходу.

Ведущий не может сам выполнить поиск по индексу: он только читает файлы.
Поэтому он пишет вопрос (до трёх) в ``recovery/RECALL.json``::

    {"q": ["кто передал Шики договор №1184", "разговор о цене бессмертия"],
     "sessions": [5, 6]}          # необязательно: только эти сессии

Свёртка ищет по всем сессиям сразу двумя способами — по словам (эвристический
индекс) и по смыслу (``meaning``, если модель доступна), — сводит выдачи
(взаимный ранг), к каждому найденному добавляет связи: соседние фрагменты того
же источника и эпизоды других сессий с теми же редкими именами. Полный ответ —
``recovery/RECALL_ANSWER.json``, краткий — ``NEXT.recall`` на несколько ходов.
Запрос помечается ``done``; новый вопрос — новая запись файла.

Найденное — кандидаты, не канон и не знание NPC: перед тем как опереться на
факт, ведущий открывает источник (``source``/``lines``).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from . import aging, meaning
from .heuristic_history import stem

REQUEST = "recovery/RECALL.json"
ANSWER = "recovery/RECALL_ANSWER.json"
SCHEMA = "npr-recall-answer/1"
MAX_Q, MAX_LEN, HITS, NEXT_HITS = 3, 300, 6, 2
SHOW_TURNS = 3       # сколько ходов краткий ответ держится в NEXT
RRF = 60             # сглаживание взаимного ранга
NAME = re.compile(r"(?<![\w-])([А-ЯЁ][а-яё]{3,})")
RULE = ("Кандидаты из прошлых сессий, не канон и не знание NPC. Перед тем как опереться на факт, "
        "открой source/lines; знает ли об этом NPC — решают его свидетельства.")
NEXT_RULE = ("Ответ на твой запрос памяти (полностью — recovery/RECALL_ANSWER.json). Кандидаты: "
             "прежде чем опереться, открой источник; вплетай, не объявляя поиска.")


def _session(row):
    match = re.search(r"session-(\d{3})", row.get("source", ""))
    if match:
        return int(match.group(1))
    match = re.match(r"s(\d+)\.", str(row.get("id", "")))
    return int(match.group(1)) if match else None


ROW_ID = re.compile(r"\s*\bs\d+\.(?:played|lines|text)\.[\d.]+\s*")


def _gist(row, limit):
    """Суть фрагмента: сводная строка хода или место + начало текста."""
    head = " ".join(str(row.get("headline") or "").split())
    if len(head) >= 30 and not ROW_ID.search(head):
        return _short(head, limit)
    head = ROW_ID.sub(" ", head).strip(" ,")
    return _short(" — ".join(x for x in (head, row.get("preview")) if x), limit)


def _names(text):
    """Имена собственные: заглавное слово не в начале фразы."""
    out = []
    for match in NAME.finditer(text):
        before = text[:match.start()].rstrip()
        if before and before[-1] not in ".!?…:—\n*#«\"„(":
            out.append(match.group(1))
    return out


def _cast_names(state):
    """Основы имён персонажей из memory/INDEX.md: связи по людям, а не по случайным заглавным."""
    path = Path(state) / "memory/INDEX.md"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    section = text.split("## Персонажи", 1)[-1].split("\n## ", 1)[0]
    vocab = set()
    for line in section.splitlines():
        head = line.lstrip("- ").split(" — ", 1)[0]
        vocab.update(stem(w.casefold()) for w in NAME.findall(head))
    return vocab


def _short(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class _Graph:
    """Лёгкий граф над записями индекса: соседство в источнике и общие редкие имена."""

    def __init__(self, index, state=None):
        self.vocab = _cast_names(state) if state is not None else set()
        self.records = index["records"]
        self.df = index["df"]
        self.position = {}
        by_source = defaultdict(list)
        for row in self.records:
            by_source[row["source"]].append(row)
        self.order = {}
        for source, rows in by_source.items():
            rows.sort(key=lambda r: (int(r.get("start") or 0), r["id"]))
            for n, row in enumerate(rows):
                self.position[row["id"]] = (source, n)
            self.order[source] = rows
        self.with_term = None

    def _term_index(self):
        if self.with_term is None:
            self.with_term = defaultdict(list)
            for row in self.records:
                for term in set(row["terms"].split()):
                    if self.df.get(term, 0) <= 40:
                        self.with_term[term].append(row)
        return self.with_term

    def linked(self, row, limit=3):
        out = []
        source, n = self.position.get(row["id"], (None, None))
        if source is not None:
            rows = self.order[source]
            for step, why in ((-1, "до"), (1, "после")):
                if 0 <= n + step < len(rows):
                    near = rows[n + step]
                    out.append({"id": near["id"], "why": why, "what": _gist(near, 120)})
        names = []
        text = " " + " ".join((row.get("headline") or "", row.get("preview") or ""))
        for word in _names(text):
            base = stem(word.casefold())
            if self.vocab and base not in self.vocab:
                continue
            if base and 2 <= self.df.get(base, 0) <= 40 and base not in (b for b, _ in names):
                names.append((base, word))
        names.sort(key=lambda bw: self.df.get(bw[0], 0))
        session = _session(row)
        for base, word in names[:2]:
            others = [r for r in self._term_index().get(base, []) if r.get("kind") in meaning.KINDS
                      and _session(r) != session and r["id"] != row["id"]]
            if others:
                other = others[-1]
                out.append({"id": other["id"], "why": f"имя: {word}", "what": _gist(other, 120)})
        return out[:limit + 1]


def ask(root, state, question, *, limit=HITS, sessions=(), index=None, enc=None, graph=None):
    """Сводный поиск по словам и смыслу с короткими связями к каждому найденному."""
    from . import heuristic_history
    state = Path(state)
    if index is None:
        index = heuristic_history.load(state / "archive/heuristic-recall/manifest.json")
    by_id = {row["id"]: row for row in index["records"]}
    words = heuristic_history.query(index, question, limit=12, sessions=tuple(sessions))["matches"]
    allowed = None
    if sessions:
        wanted = set(sessions)
        allowed = lambda rid: rid in by_id and _session(by_id[rid]) in wanted  # noqa: E731
    sense = meaning.search(state, question, enc=enc, limit=12, allow=allowed)
    fused = {}
    for rank, hit in enumerate(words):
        fused.setdefault(hit["id"], {"score": 0.0, "how": set()})
        # Частичное совпадение слов (покрыта часть вопроса) весит меньше полного.
        fused[hit["id"]]["score"] += float(hit.get("coverage") or 0) / (RRF + rank)
        fused[hit["id"]]["how"].add("слова")
    for rank, (record_id, cosine) in enumerate(sense or []):
        if record_id not in by_id:
            continue
        fused.setdefault(record_id, {"score": 0.0, "how": set()})
        fused[record_id]["score"] += 1 / (RRF + rank)
        fused[record_id]["how"].add("смысл")
        fused[record_id]["cosine"] = cosine
    ranked = sorted(fused.items(), key=lambda kv: (-kv[1]["score"], -len(kv[1]["how"]), kv[0]))[:limit]
    graph = graph or _Graph(index, state)
    hits = []
    for record_id, info in ranked:
        row = by_id.get(record_id)
        if row is None:
            continue
        hits.append({"id": record_id, "session": _session(row), "at": row.get("at", ""),
                     "how": "+".join(h for h in ("слова", "смысл") if h in info["how"]),
                     "what": _gist(row, 300),
                     "text": _short(row.get("preview"), 460),
                     "source": row["source"], "lines": [row.get("start"), row.get("end")],
                     "linked": graph.linked(row)})
    return {"q": question, "mode": "слова+смысл" if sense is not None else "слова", "hits": hits}


def _questions(request):
    raw = request.get("q")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("q — строка или список строк")
    out = [" ".join(str(x).split())[:MAX_LEN] for x in raw if str(x).strip()]
    if not out:
        raise ValueError("пустой вопрос")
    return out[:MAX_Q]


def handle(root, state, hot):
    """Отвечает на неотвеченный запрос; возвращает ответ или None."""
    state = Path(state)
    path = state / REQUEST
    if not path.is_file():
        return None
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        request = {"q": None}
    if not isinstance(request, dict) or request.get("done"):
        return None
    cursor = (hot.get("cursor") or {}).get("id")
    answer = {"schema": SCHEMA, "cursor": cursor, "rule": RULE}
    try:
        questions = _questions(request)
        sessions = [int(x) for x in request.get("sessions") or []]
        from . import heuristic_history
        index = heuristic_history.load(state / "archive/heuristic-recall/manifest.json")
        graph = _Graph(index, state)
        answer["answers"] = [ask(Path(root), state, q, sessions=sessions, index=index, graph=graph)
                             for q in questions]
        answer["mode"] = answer["answers"][0]["mode"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        answer["error"] = f"запрос памяти не выполнен: {exc}"
    (state / ANSWER).write_text(json.dumps(answer, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    done = dict(request) if isinstance(request, dict) else {}
    done["done"] = cursor or True
    path.write_text(json.dumps(done, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return answer


def view(state, hot):
    """Краткий ответ для NEXT, пока он свежий (SHOW_TURNS ходов)."""
    path = Path(state) / ANSWER
    if not path.is_file():
        return None
    try:
        answer = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    now = aging._ordinal((hot.get("cursor") or {}).get("id"))
    asked = aging._ordinal(answer.get("cursor"))
    if now is None or asked is None or now - asked > SHOW_TURNS:
        return None
    if answer.get("error"):
        return {"error": answer["error"], "file": ANSWER}
    return {"file": ANSWER, "mode": answer.get("mode"), "rule": NEXT_RULE,
            "answers": [{"q": a["q"], "top": [{"id": h["id"], "at": h["at"], "how": h["how"],
                                                 "what": _short(h["what"], 150)}
                                                for h in a["hits"][:NEXT_HITS]]}
                        for a in answer.get("answers") or []]}
