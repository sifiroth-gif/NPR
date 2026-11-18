"""Разговоры NPC с Шики по всем сессиям: что NPC сказал и что услышал.

Хранитель восстанавливает память, не пересказывая прозу: в ходе, где NPC
говорит, он был рядом и слышал прямую речь Шики из заявки игрока. Реплики
берутся дословно (до предела длины) с номером хода. Это источник для вопросов
«что он раньше говорил, обещал, знал» по всем восьми сессиям, а не только
с середины четвёртой, где начинаются дельты ходов.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SAID_CHARS = 240
HEARD_CHARS = 320
LABEL = re.compile(r"\*\*([^*\n]{2,48}?)(?:\\)?:\*\*")
NOT_PEOPLE = {"дата", "время", "место", "день", "мастеру", "мастер", "шики", "мугами шики",
              "мугами шики, мысленно", "итог", "исход", "сцена", "примечание"}


def _norm(value):
    text = str(value or "").casefold().replace("ё", "е")
    return " ".join(re.sub(r"[\\*]", "", text).split())


def _short(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _session(turn_id):
    match = re.match(r"s(\d+)\.", turn_id or "")
    return int(match.group(1)) if match else 0


def _order(turn_id):
    match = re.match(r"s(\d+)\.text\.(\d+)", turn_id or "")
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def speech(player):
    """Прямая речь Шики из заявки: без #действий# и обращений к мастеру."""
    text = re.sub(r"#[^#]*#", " ", str(player or ""))
    text = re.sub(r"(?i)\bмастеру\b.*?(?=$)", " ", text, flags=re.S)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    return _short(text, HEARD_CHARS)


def utterances(gm):
    """(метка, реплика) из прозы ведущего; реплика — до конца абзаца."""
    found = []
    for paragraph in re.split(r"\n\s*\n", str(gm or "")):
        for match in LABEL.finditer(paragraph):
            label = match.group(1).strip().rstrip("\\").strip()
            rest = paragraph[match.end():]
            nxt = LABEL.search(rest)
            if nxt:
                rest = rest[:nxt.start()]
            rest = re.sub(r"^[\s—–-]+", "", rest.replace("\\", ""))
            found.append((label, _short(rest, SAID_CHARS)))
    return found


def sessions_of(entity):
    found = set()
    for value in [entity.get("first"), entity.get("last")] + list(entity.get("sources") or []) + \
            [h.get("turn") for h in entity.get("history") or []]:
        session = _session(str(value or ""))
        if session:
            found.add(session)
    hint = entity.get("sessions_hint") or ""
    numbers = [int(n) for n in re.findall(r"\d+", hint)]
    if len(numbers) >= 2:
        found.update(range(numbers[0], numbers[1] + 1))
    return found


class Resolver:
    """Метка говорящего → страница: точное имя, псевдоним, затем часть имени с учётом сессии."""

    def __init__(self, store):
        self.store = store
        self.exact, self.alias, self.parts = {}, {}, {}
        self.sessions = {k: sessions_of(e) for k, e in store.items()}
        for key, entity in store.items():
            self.exact.setdefault(_norm(entity.get("name")), []).append(key)
            for alias in entity.get("aliases") or []:
                self.alias.setdefault(_norm(alias), []).append(key)
            for name in [entity.get("name")] + list(entity.get("aliases") or []):
                for word in _norm(name).split():
                    if len(word) >= 2:
                        self.parts.setdefault(word, set()).add(key)

    def _weight(self, key):
        entity = self.store[key]
        return len(entity.get("sources") or []) + len(entity.get("history") or [])

    def _pick(self, keys, session):
        keys = list(dict.fromkeys(keys))
        if len(keys) == 1:
            return keys[0]
        here = [k for k in keys if session in self.sessions.get(k, ())]
        if len(here) == 1:
            return here[0]
        pool = here or keys
        ranked = sorted(pool, key=self._weight, reverse=True)
        if len(ranked) > 1 and self._weight(ranked[0]) == self._weight(ranked[1]):
            return None
        return ranked[0] if here else None

    @staticmethod
    def clean(label):
        text = re.sub(r"\(.*?\)", " ", str(label or ""))
        text = text.split(",")[0]
        text = re.sub(r"^\s*\d{1,2}:\d{2}\.?\s*", "", text)
        text = re.sub(r"(?i)^\s*голос\s+(капитана\s+)?", "", text)
        return text.strip()

    def __call__(self, label, session):
        label = self.clean(label)
        name = _norm(label)
        if not name or name in NOT_PEOPLE or name.startswith("мугами шики"):
            return None
        key = None
        for table in (self.exact, self.alias):
            if name in table:
                key = self._pick(table[name], session)
                break
        else:
            sets = [self.parts[w] for w in name.split() if w in self.parts and len(w) >= 2]
            if sets and self.role(label) and len(sets) < len(name.split()):
                sets = []   # «Шиноби Нори» — человек Нори, а не она сама
            if sets:
                common = set.intersection(*sets)
                if common:
                    key = self._pick(sorted(common), session)
        if key and self.sessions.get(key) and session not in self.sessions[key]:
            if self.role(label):
                return None   # «Посыльный» в другой сессии — другой человек
            # «Курода» в шестой сессии — та, что играла в шестой, а не однофамилец из первой
            words = [w for w in name.split() if len(w) >= 2]
            others = (set.intersection(*[set(self.parts.get(w, ())) for w in words]) if words else set())
            others = {k for k in others if k != key and session in self.sessions.get(k, ())}
            if len(others) == 1:
                return others.pop()
        return key

    @staticmethod
    def role(label):
        from .wiki import _is_background
        return _is_background(label)


def collect(state_dir, store):
    """{key: [{turn, at, heard, said: [...]}]} по всем сессиям архива."""
    resolve = Resolver(store)
    talk, unknown = {}, {}
    rows = []
    for path in sorted(Path(state_dir, "archive").glob("session-*-turns.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                if row.get("id") and row.get("kind") != "ooc" and row.get("gm"):
                    rows.append(row)
    rows.sort(key=lambda r: _order(r["id"]))
    for row in rows:
        session = _session(row["id"])
        heard = speech(row.get("player"))
        lines = {}
        for label, said in utterances(row["gm"]):
            key = resolve(label, session)
            if key is None:
                if _norm(label) not in NOT_PEOPLE and not _norm(label).startswith("мугами шики"):
                    unknown[label] = unknown.get(label, 0) + 1
                continue
            if said:
                lines.setdefault(key, []).append(said)
            else:
                lines.setdefault(key, [])
        for key, said in lines.items():
            talk.setdefault(key, []).append({"turn": row["id"], "at": row.get("at", ""),
                                             "heard": heard or None, "said": said[:6]})
    return talk, unknown


PAGE_LIMIT = 30000


def _entries(entity, entries):
    lines = []
    for entry in entries:
        lines.append(f"## {entry['turn']}{(' · ' + entry['at']) if entry.get('at') else ''}")
        if entry.get("heard"):
            lines.append(f"- Шики: «{entry['heard']}»")
        for said in entry.get("said") or []:
            lines.append(f"- {entity['name']}: «{said}»")
        lines.append("")
    return lines


def pages(entity, entries, folder):
    """{путь: текст}: одна страница, а при большом объёме — по странице на сессию."""
    head = [f"# {entity['name']} · разговоры с Шики ({len(entries)} ходов)", "",
            "Дословно, по ходам: что персонаж говорил и что в том же ходе слышал от Шики. "
            "Это память о сказанном, а не о мыслях. Ход целиком — `archive/turns/sNN/<id>.md`.", ""]
    base = f"{folder}/{entity['id']}.talk"
    whole = "\n".join(head + _entries(entity, entries))
    if len(whole) <= PAGE_LIMIT:
        return {f"{base}.md": whole}
    by_session = {}
    for entry in entries:
        by_session.setdefault(entry["turn"][:3], []).append(entry)
    files = {}
    index = head + ["## По сессиям"]
    for session, chunk in by_session.items():
        path = f"{base}.{session}.md"
        files[path] = "\n".join([f"# {entity['name']} · разговоры с Шики, {session} ({len(chunk)} ходов)", ""]
                                + _entries(entity, chunk))
        index.append(f"- {session}: {len(chunk)} ходов, {chunk[0]['turn']}–{chunk[-1]['turn']} → "
                     f"`{path}`")
    files[f"{base}.md"] = "\n".join(index) + "\n"
    return files
