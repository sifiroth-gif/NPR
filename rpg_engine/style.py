"""Память стиля: утверждённые игроком сцены как образец манеры и контроль её ухода.

Писать сцены здесь не может ни одна бесплатная модель на уровне ChatGPT, но держать
ведущего в утверждённой манере можно:

* **эталоны** — игрок пишет ``#эталон`` в заявке: предыдущая сцена (или указанный
  ход ``#эталон s10.text.092``) попадает в ``archive/style/exemplars.jsonl`` — лучшие
  абзацы, без служебного;
* **образец к сцене** — ``NEXT.style``: один-два абзаца эталонов, ближайших к нынешней
  сцене по смыслу (модель ``meaning``) или по словам; не чаще раза в ``EVERY`` ходов и при
  смене сцены — чтобы пакет не толстел;
* **уход манеры** — стилометрия хода (длина фразы, доля абзацев-строчек, доля реплик,
  слов на абзац) против коридора эталонов (или ``baseline.json``): выход за коридор по
  двум признакам — замечание kind=style.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import aging
from .heuristic_history import stem, tokens

FOLDER = "archive/style"
EXEMPLARS = f"{FOLDER}/exemplars.jsonl"
BASELINE = f"{FOLDER}/baseline.json"
TAG = re.compile(r"#\s*эталон", re.I)
TURN_ID = re.compile(r"s\d+\.text\.\d+")
EVERY = 6            # образец в NEXT не чаще раза в столько ходов (и при смене места)
SAMPLE_CHARS = 450
MIN_EXEMPLARS = 3    # коридор по эталонам — с этого числа, иначе baseline
RULE = ("Образец утверждённой игроком манеры (не сюжет, не копировать): плотность, ритм, подписи реплик, "
        "присутствие Шики. Пиши сцену в этом голосе.")
_SENT = re.compile(r"[^.!?…]+[.!?…]+")
_WORD = re.compile(r"[А-Яа-яЁёA-Za-z]+")
_SERVICE = re.compile(r"^\s*(<text|###|\*\*Ход Шики|---|\*\*\*)")
KINDS = (("бой", r"удар|клин|рывок|кровь|атак|сенбон|шуншин|печат"),
         ("медицина", r"пациент|рана|шов|сосуд|ампул|диагноз|операц|чакров\w* скальпел"),
         ("ужас", r"мёртв|гнил|некроз|чёрн\w* чакр|немер|отрост|холод"),
         ("разговор", r"\*\*[^*]+:\*\* —"))


def _turns(state_dir):
    rows = {}
    for path in sorted(Path(state_dir).glob("archive/session-*-turns.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                rows[row.get("id")] = row
    return rows


def best_paragraphs(text, limit=1200):
    """Лучшие абзацы сцены: без шапки и служебного, повествование подлиннее и реплики с подписью."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip() and not _SERVICE.match(p)]
    ranked = sorted(paras, key=lambda p: -len(p))
    keep = set()
    total = 0
    for p in ranked:
        if total + len(p) > limit:
            continue
        keep.add(p)
        total += len(p)
    return "\n\n".join(p for p in paras if p in keep)


def kind_of(text):
    lowered = (text or "").casefold()
    scores = sorted(((len(re.findall(rx, lowered)), name) for name, rx in KINDS), reverse=True)
    return scores[0][1] if scores and scores[0][0] else "сцена"


def load(state_dir):
    path = Path(state_dir) / EXEMPLARS
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tag(state_dir, applied):
    """#эталон в заявке — предыдущая (или названная) сцена в эталоны. Возвращает добавленные ходы."""
    rows = None
    added = []
    current = load(state_dir)
    have = {e["turn"] for e in current}
    for turn in applied or []:
        player = str(turn.get("player") or "")
        if not TAG.search(player):
            continue
        rows = rows if rows is not None else _turns(state_dir)
        named = TURN_ID.findall(player)
        if named:
            targets = named
        else:
            order = sorted(rows, key=lambda i: aging._ordinal(i) or 0)
            mine = aging._ordinal(turn.get("id")) or 0
            before = [i for i in order if (aging._ordinal(i) or 0) < mine and rows[i].get("kind") not in ("ooc",)]
            targets = before[-1:]
        for target in targets:
            if target in have or target not in rows:
                continue
            text = best_paragraphs(rows[target].get("gm") or "")
            if not text:
                continue
            current.append({"turn": target, "tagged": turn.get("id"), "kind": kind_of(text), "text": text})
            have.add(target)
            added.append(target)
    if added:
        path = Path(state_dir) / EXEMPLARS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in current), encoding="utf-8")
    return added


# ── стилометрия ──────────────────────────────────────────────────────────────

def profile(text):
    """Признаки манеры: слов во фразе, доля абзацев-строчек, доля реплик, слов на абзац."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip() and not _SERVICE.match(p)]
    if len(paras) < 6:
        return None
    words = [len(_WORD.findall(s)) for p in paras if not p.startswith("**") for s in _SENT.findall(p)]
    words = [w for w in words if w]
    if len(words) < 8:
        return None
    return {"sentence": round(sum(words) / len(words), 1),
            "short": round(sum(1 for p in paras if len(p) < 80) / len(paras), 2),
            "speech": round(sum(1 for p in paras if re.match(r"\*\*[^*]+:\*\* —|— ", p)) / len(paras), 2),
            "paragraph": round(sum(len(_WORD.findall(p)) for p in paras) / len(paras), 1)}


LABELS = {"sentence": ("фразы", "слов"), "short": ("абзацы-строчки", "доля"),
          "speech": ("реплики", "доля"), "paragraph": ("абзацы", "слов")}


def band(state_dir):
    """Коридор манеры: по эталонам (от трёх) или по baseline.json."""
    exemplars = load(state_dir)
    profiles = [p for p in (profile(e["text"]) for e in exemplars) if p]
    if len(profiles) >= MIN_EXEMPLARS:
        return {k: (min(p[k] for p in profiles), max(p[k] for p in profiles)) for k in LABELS}, "эталоны"
    path = Path(state_dir) / BASELINE
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: tuple(v) for k, v in (data.get("band") or {}).items()}, data.get("source") or "baseline"
    return None, None


def drift(state_dir, turn):
    """Замечание kind=style, если ход вышел из коридора манеры по двум признакам и больше."""
    if turn.get("kind") in ("ooc", "technical", "void"):
        return []
    mine = profile(turn.get("gm") or "")
    corridor, source = band(state_dir)
    if not mine or not corridor:
        return []
    out = []
    for key, (low, high) in corridor.items():
        value = mine.get(key)
        span = max(high - low, 0.05 if key in ("short", "speech") else 1.0)
        if value is not None and (value < low - 0.25 * span or value > high + 0.25 * span):
            what, unit = LABELS[key]
            out.append(f"{what} {value} ({unit}; у образцов {low}–{high})")
    if len(out) < 2:
        return []
    return [{"turn": turn["id"], "kind": "style",
             "text": "Манера ушла от образцов (" + source + "): " + "; ".join(out) + ". Верни голос утверждённых сцен."}]


def seed_baseline(state_dir, turn_ids, source):
    """Хранитель: коридор манеры по выбранным чистым ходам, пока эталонов мало."""
    rows = _turns(state_dir)
    profiles = [p for p in (profile((rows.get(t) or {}).get("gm") or "") for t in turn_ids) if p]
    if not profiles:
        return None
    data = {"source": source, "turns": list(turn_ids),
            "band": {k: [min(p[k] for p in profiles), max(p[k] for p in profiles)] for k in LABELS}}
    path = Path(state_dir) / BASELINE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return data


# ── образец к сцене ──────────────────────────────────────────────────────────

def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def sample(state_dir, hot, applied):
    """Образец манеры для NEXT.style — при смене места или раз в EVERY ходов."""
    exemplars = load(state_dir)
    if not exemplars:
        return None
    cursor = hot.get("cursor") or {}
    now = aging._ordinal(cursor.get("id")) or 0
    state = hot.setdefault("fold", {}).setdefault("style", {})
    place = str(cursor.get("place") or "").split(". ", 1)[0]
    if state.get("at") == cursor.get("id"):
        return state.get("current")
    state["at"] = cursor.get("id")
    if state.get("place") == place and now - int(state.get("last") or -10**6) < EVERY:
        state["current"] = None
        return None
    played = [t for t in applied or [] if t.get("kind") not in ("ooc", "technical", "void")]
    query = " ".join(x for x in (" ".join(str(t.get("player") or "") for t in played[-1:]),
                                 str((hot.get("scene") or {}).get("situation") or ""), place) if x)
    ranked = None
    try:
        from . import meaning
        enc = meaning.encoder()
        if enc is not None and query.strip():
            q = meaning._norm(list(enc([meaning.QUERY + query])[0]))
            vecs = enc([meaning.PASSAGE + e["text"][:1200] for e in exemplars])
            ranked = sorted(range(len(exemplars)),
                            key=lambda i: -sum(a * b for a, b in zip(meaning._norm(list(vecs[i])), q)))
    except Exception:
        ranked = None
    if ranked is None:
        want = {stem(w) for w in tokens(query) if len(w) >= 4}
        ranked = sorted(range(len(exemplars)),
                        key=lambda i: -len(want & {stem(w) for w in tokens(exemplars[i]["text"]) if len(w) >= 4}))
    best = exemplars[ranked[0]]
    text = best["text"]
    if len(text) > SAMPLE_CHARS:
        cut = text[:SAMPLE_CHARS]
        text = cut[:cut.rfind("\n\n")] if "\n\n" in cut else cut.rstrip() + "…"
    state.update(place=place, last=now, current={"rule": RULE, "turn": best["turn"], "kind": best.get("kind"),
                                                  "sample": text})
    return state["current"]
