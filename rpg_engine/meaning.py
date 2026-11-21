"""Смысловой слой исторической памяти: векторы фрагментов всех сыгранных сессий.

Эвристический индекс (``heuristic_history``) ищет по словам и их формам. Этот
слой ищет по смыслу: каждый фрагмент прошлых сессий кодируется многоязычной
моделью e5-small (ONNX, CPU) в вектор, вопрос — тоже; ближайшие по косинусу
фрагменты становятся кандидатами. Сцена, где «предательство» ни разу не
названо, находится по вопросу о предательстве.

Модель берётся по закреплённой ревизии и кешируется в CI. Без неё (нет
переменной ``NPR_MEANING_MODEL``, файлов или библиотек) слой молча выключен,
поиск по словам работает как прежде. Векторы производные, не канон и не знание
NPC; хранятся в ``archive/meaning/`` по группам индекса (s001…, live, canon),
одна строка на фрагмент: новая реплика добавляет строку, старые не
перекодируются. За одну свёртку кодируется не больше ``BUDGET`` кусков, чтобы
ход не ждал; полная сборка — отдельным запуском с большим бюджетом.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.request
from pathlib import Path

REPO = "intfloat/multilingual-e5-small"
REVISION = "ffdcc22a9a5c973ef0470385cef91e1ecb461d9f"
FILES = {"model.onnx": "onnx/model.onnx", "tokenizer.json": "tokenizer.json"}
MODEL_ID = f"{REPO}@{REVISION[:7]}"
ENV = "NPR_MEANING_MODEL"
BUDGET_ENV = "NPR_MEANING_BUDGET"
BUDGET = 48                 # кусков за обычную свёртку: дозаполнение без задержки хода
FOLDER = "archive/meaning"
SCHEMA = "npr-meaning/1"
CHUNK, PIECES = 1200, 3     # символов в куске; кусков на фрагмент (начало, середина, конец)
MAX_TOKENS, BATCH = 512, 16
KINDS = ("played", "archived_source_review_required", "correction")
QUERY, PASSAGE = "query: ", "passage: "

_LOADED: dict = {}
_MATRIX: dict = {}


# ── модель ───────────────────────────────────────────────────────────────────

def fetch(folder):
    """Скачивает закреплённую ревизию модели в каталог (если её там ещё нет)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = folder / "MODEL.json"
    if stamp.is_file() and all((folder / name).is_file() for name in FILES):
        info = json.loads(stamp.read_text(encoding="utf-8"))
        if info.get("repo") == REPO and info.get("revision") == REVISION:
            return {"status": "cached", **info}
    sums = {}
    for name, remote in FILES.items():
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{remote}"
        part = folder / (name + ".part")
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=300) as response, part.open("wb") as out:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                digest.update(block)
                out.write(block)
        os.replace(part, folder / name)
        sums[name] = digest.hexdigest()
    info = {"repo": REPO, "revision": REVISION, "sha256": sums}
    stamp.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    return {"status": "downloaded", **info}


class _Onnx:
    """e5-small в onnxruntime: средний пул по маске, нормированный вектор."""

    def __init__(self, folder):
        import numpy
        import onnxruntime
        from tokenizers import Tokenizer
        model, vocab = folder / "model.onnx", folder / "tokenizer.json"
        if not model.is_file() or not vocab.is_file():
            raise ValueError("в каталоге модели нет model.onnx или tokenizer.json")
        self.np = numpy
        self.tok = Tokenizer.from_file(str(vocab))
        self.tok.enable_truncation(max_length=MAX_TOKENS)
        pad = self.tok.token_to_id("<pad>")
        self.tok.enable_padding(pad_id=1 if pad is None else pad, pad_token="<pad>")
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self.session = onnxruntime.InferenceSession(str(model), options,
                                                    providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.id = MODEL_ID

    def __call__(self, texts):
        np = self.np
        vectors = []
        for start in range(0, len(texts), BATCH):
            batch = self.tok.encode_batch(list(texts[start:start + BATCH]))
            ids = np.array([e.ids for e in batch], dtype=np.int64)
            mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)}
            hidden = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            weight = mask[..., None].astype(np.float32)
            pooled = (hidden * weight).sum(1) / np.clip(weight.sum(1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            vectors.extend(pooled.tolist())
        return vectors


def encoder():
    """Кодировщик или None: слой выключен, если модели или библиотек нет."""
    folder = os.getenv(ENV)
    if not folder:
        return None
    if folder not in _LOADED:
        try:
            _LOADED[folder] = _Onnx(Path(folder))
        except (OSError, ImportError, ValueError, RuntimeError):
            _LOADED[folder] = None
    return _LOADED[folder]


# ── тексты фрагментов ────────────────────────────────────────────────────────

def _turn_text(obj):
    return "\n".join(str(obj.get(k) or "") for k in ("line", "player", "gm") if obj.get(k))


def passages(root, state, records):
    """(запись индекса, полный текст её фрагмента) для сыгранного и поправок канона."""
    root, state = Path(root), Path(state)
    files = {}

    def lines(source):
        if source not in files:
            path = state / source if (state / source).is_file() else root / source
            files[source] = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        return files[source]

    for row in records:
        if row.get("kind") not in KINDS:
            continue
        source, first, last = row["source"], int(row.get("start") or 0), int(row.get("end") or 0)
        body = ""
        if first >= 1:
            span = lines(source)[first - 1:last]
            body = "\n".join(span)
            if source.endswith(".jsonl") and len(span) == 1:
                try:
                    body = _turn_text(json.loads(span[0])) or body
                except ValueError:
                    pass
        elif source.endswith(".json"):
            try:
                body = _turn_text(json.loads("\n".join(lines(source))))
            except ValueError:
                body = ""
        text = "\n".join(x for x in (row.get("headline"), body or row.get("preview")) if x)
        if text.strip():
            yield row, text


def pieces(text):
    """До трёх кусков: начало, середина и конец длинного фрагмента."""
    text = " ".join(str(text).split())
    if len(text) <= CHUNK:
        return [text]
    count = min(PIECES, -(-len(text) // CHUNK))
    step = (len(text) - CHUNK) / (count - 1)
    return [text[round(i * step):round(i * step) + CHUNK] for i in range(count)]


def _digest(text):
    return hashlib.sha256((MODEL_ID + "\0" + text).encode("utf-8")).hexdigest()[:16]


def _pack(vector):
    top = max(abs(x) for x in vector) or 1.0
    return base64.b64encode(bytes(round(x / top * 127) & 0xFF for x in vector)).decode("ascii")


def _unpack(text):
    return [b - 256 if b > 127 else b for b in base64.b64decode(text)]


# ── хранение ─────────────────────────────────────────────────────────────────

def _read(folder):
    rows = {}
    for path in sorted(Path(folder).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[row["id"]] = row
    return rows


def _write_if_changed(path, body):
    if path.is_file() and path.read_text(encoding="utf-8") == body:
        return False
    path.write_text(body, encoding="utf-8")
    return True


def update(root, state, *, budget=None, enc=None):
    """Дозаполняет векторы под текущий исторический индекс."""
    state = Path(state)
    manifest = state / "archive/heuristic-recall/manifest.json"
    if not manifest.is_file():
        return {"status": "no_index"}
    enc = enc or encoder()
    if enc is None:
        return {"status": "off"}
    from . import heuristic_history
    if budget is None:
        budget = int(os.getenv(BUDGET_ENV) or BUDGET)
    index = heuristic_history.load(manifest)
    folder = state / FOLDER
    old = _read(folder) if folder.is_dir() else {}
    groups, pending = {}, []
    for row, text in passages(root, state, index["records"]):
        item = {"id": row["id"], "h": _digest(text)}
        previous = old.get(row["id"])
        if previous and previous.get("h") == item["h"]:
            item["v"] = previous["v"]
        else:
            pending.append((item, pieces(text)))
        groups.setdefault(heuristic_history._group(row), []).append(item)
    # Свежее — первым: ближние сессии нужнее эху и запросам ведущего.
    pending.sort(key=lambda p: -_recency(p[0]["id"]))
    chosen, chunks = [], 0
    for item, parts in pending:
        if chosen and chunks + len(parts) > budget:
            break
        chosen.append((item, parts))
        chunks += len(parts)
    if chosen:
        flat = [(n, j, PASSAGE + part) for n, (_, parts) in enumerate(chosen) for j, part in enumerate(parts)]
        flat.sort(key=lambda x: len(x[2]))
        vectors = enc([x[2] for x in flat])
        packed = {}
        for (n, j, _), vector in zip(flat, vectors, strict=True):
            packed.setdefault(n, {})[j] = _pack(vector)
        for n, (item, parts) in enumerate(chosen):
            item["v"] = [packed[n][j] for j in range(len(parts))]
    folder.mkdir(parents=True, exist_ok=True)
    changed, total, meta_groups = False, 0, {}
    for group, items in groups.items():
        ready = [x for x in items if "v" in x]
        body = "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":")) + "\n" for x in ready)
        changed |= _write_if_changed(folder / f"{group}.jsonl", body)
        meta_groups[group] = {"records": len(items), "ready": len(ready)}
        total += len(ready)
    for path in folder.glob("*.jsonl"):
        if path.stem not in groups:
            path.unlink()
            changed = True
    meta = {"schema": SCHEMA, "model": MODEL_ID, "dim": 384, "chunk": CHUNK, "pieces": PIECES,
            "records": total, "missing": len(pending) - len(chosen), "groups": meta_groups,
            "rule": "Производный смысловой слой: кандидаты, не канон и не знание NPC."}
    changed |= _write_if_changed(folder / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1,
                                                                  sort_keys=True) + "\n")
    if changed:
        _MATRIX.clear()
    return {"status": "updated" if changed else "current", "encoded": len(chosen), "chunks": chunks,
            "missing": meta["missing"], "records": total}


def _recency(record_id):
    """Порядок дозаполнения: живое и поздние сессии раньше ранних и канона."""
    text = str(record_id)
    if text.startswith("canon"):
        return -1
    digits = "".join(ch for ch in text.split(".")[0] if ch.isdigit())
    return int(digits) if digits else 0


# ── поиск ────────────────────────────────────────────────────────────────────

def _norm(vector):
    size = sum(x * x for x in vector) ** 0.5 or 1.0
    return [x / size for x in vector]


def _matrix(state):
    """(ids, матрица) всех кусков; numpy, если есть, иначе списки."""
    folder = Path(state) / FOLDER
    meta = folder / "meta.json"
    if not meta.is_file():
        return None
    key = (str(folder.resolve()), meta.stat().st_mtime_ns)
    if key not in _MATRIX:
        ids, rows = [], []
        for record_id, row in _read(folder).items():
            for vector in row.get("v") or []:
                ids.append(record_id)
                rows.append(_unpack(vector))
        if not rows:
            return None
        try:
            import numpy as np
            matrix = np.asarray(rows, dtype=np.float32)
            matrix /= np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12, None)
        except ImportError:
            matrix = [_norm(r) for r in rows]
        _MATRIX.clear()
        _MATRIX[key] = (ids, matrix)
    return _MATRIX[key]


def search(state, question, *, enc=None, limit=12, allow=None):
    """[(id записи, косинус)] по убыванию; None — слой выключен или пуст."""
    enc = enc or encoder()
    if enc is None or not str(question).strip():
        return None
    try:
        loaded = _matrix(state)
    except (OSError, ValueError):
        return None
    if loaded is None:
        return None
    ids, matrix = loaded
    query = _norm(list(enc([QUERY + " ".join(str(question).split())])[0]))
    if isinstance(matrix, list):
        scores = [sum(a * b for a, b in zip(row, query)) for row in matrix]
    else:
        import numpy as np
        scores = (matrix @ np.asarray(query, dtype=np.float32)).tolist()
    best = {}
    for record_id, score in zip(ids, scores):
        if allow is not None and not allow(record_id):
            continue
        if score > best.get(record_id, -2.0):
            best[record_id] = score
    ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(k, round(v, 4)) for k, v in ranked[:limit]]
