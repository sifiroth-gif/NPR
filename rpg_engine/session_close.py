"""Закрытие сессии одной командой: дословный журнал → холодный архив → глубокая разгрузка.

Обобщает разовые сценарии прежних сессий (``maintenance/recover_sessionN_*.py``):
источник — неизменяемый ``archive/session-NNN-turns.jsonl``, который свёртка
пишет дословно по каждому ходу. Выгрузка чата игроком, если положена в
``archive/inbox/``, сохраняется дословно рядом как вторичная сверка.

Шаги: проверка журнала (порядок, время, пустые сцены) → свёртка текущая →
дословный перенос сыгранных ходов в SQLite → закрытие сессии и экспорт
``archive/session-NNN.md`` → индекс и передача → глубокая разгрузка
оперативной памяти (строгое старение) → NEXT нового номера.
Ничего не сочиняет: игровое время и часы NPC не трогаются.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from . import aging, compaction, git_state, hot, live_scene, nextpack, scene_gate, sessions, verification
from .engine import Campaign, file_sha256

REQUEST = "recovery/SESSION_CLOSE.json"
REPORT = "recovery/SESSION_CLOSE_REPORT.md"
INBOX = "archive/inbox"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rows(root, number):
    """Дословный журнал сессии с проверкой целостности."""
    source = f"archive/session-{number:03}-turns.jsonl"
    data = (Path(root) / source).read_bytes()
    out = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    if not out:
        raise ValueError(f"{source}: пусто")
    last = ""
    for n, row in enumerate(out, 1):
        if row.get("id") != f"s{number:02}.text.{n:03}":
            raise ValueError(f"{source}: разрыв или повтор на позиции {n} ({row.get('id')})")
        if row.get("kind") == "ooc":
            continue
        if not (row.get("gm") and row.get("place")):
            raise ValueError(f"пустая сыгранная сцена {row['id']}")
        if row["at"] < last:
            raise ValueError(f"время назад в {row['id']}: {row['at']} < {last}")
        last = row["at"]
    return source, out, hashlib.sha256(data).hexdigest()


def _inbox(root, number):
    """Выгрузка чата игроком — дословно в архив рядом с журналом (вторичная сверка)."""
    moved = []
    box = Path(root) / INBOX
    for path in sorted(box.glob("*")) if box.is_dir() else []:
        if path.is_file():
            target = Path(root) / f"archive/session-{number:03}-chat{path.suffix or '.md'}"
            if target.exists():
                target = target.with_name(f"{target.stem}-{len(moved) + 2}{target.suffix}")
            shutil.move(str(path), target)
            moved.append(str(target.relative_to(root)))
    return moved


MEMORY_FILES = ("HOT.json", "GM_ONLY.json", "GM_BRIEF.json", "NEXT.json")


def memory_sizes(root):
    recovery = Path(root) / "recovery"
    return {name: (recovery / name).stat().st_size for name in MEMORY_FILES
            if (recovery / name).is_file()}


def close(root, number=None):
    root = Path(root)
    game = Campaign(root / "campaign.sqlite")
    cold = game.verify()
    number = number or cold["session"]
    if cold["session"] != number or cold.get("pending"):
        raise ValueError(f"холодный журнал на сессии {cold['session']}, просили закрыть {number}")
    source, played_rows, digest = rows(root, number)
    last = next(r for r in reversed(played_rows) if r.get("kind") != "ooc")
    first_next = f"s{number + 1:02}.text.001"

    # Живое состояние — на последнем ходе журнала, свёртка без ошибок.
    turn = _read(root / "recovery/TURN.json")
    if turn.get("id") != played_rows[-1]["id"]:
        raise ValueError(f"TURN.json ({turn.get('id')}) не последний ход журнала ({played_rows[-1]['id']})")
    folded = hot.fold(root)
    if folded.get("errors"):
        raise ValueError(f"свёртка с ошибками: {folded['errors']}")
    git_state.verify_manifest(_read(root / "state.json"), game.path)

    memory_path = root / "recovery/SCENE_MEMORY_INDEX.json"
    memory = _read(memory_path) if memory_path.is_file() else {}
    memory_sha = hashlib.sha1(b"blob " + str(memory_path.stat().st_size).encode() + b"\0" +
                              memory_path.read_bytes()).hexdigest() if memory_path.is_file() else "0" * 40
    index, played = [], 0
    hot_state, _ = hot._read_state(root)
    scene = hot_state.get("scene") or {}
    for n, row in enumerate(played_rows, 1):
        ref = f"{source}#{row['id']}"
        if row.get("kind") == "ooc":
            index.append({"id": row["id"], "status": "ooc", "at": row["at"], "source": ref})
            continue
        state = game.verify()
        with game.connection() as con:
            predecessor = con.execute("SELECT id FROM journal WHERE kind='turn' ORDER BY seq DESC LIMIT 1").fetchone()["id"]
        packet = {"id": f"s{number}.played.{n:03}", "expected_revision": state["revision"], "at": row["at"],
                  "location": row["place"], "player_input": row.get("player") or "", "text": row["gm"],
                  "input_mode": "direction", "events": [], "source_ref": ref, "source_status": "exact",
                  "memory_gate": {"schema": "npr-memory-gate/1", "previous_turn_id": predecessor,
                                  "index_turn": (memory.get("cursor") or {}).get("turn", last["id"]),
                                  "index_blob_sha": memory_sha, "player_input": row.get("player") or "",
                                  "source_refs": [{"source": source, "sha256": digest}], "unretrieved": [],
                                  "mode": "stale_index_plus_current_hot"}}
        if row is last:
            packet["scene"] = {"id": f"s{number + 1}.open", "actors": ["shiki"], "beat": "transition",
                               "focus": str(scene.get("situation") or row.get("line") or "")[:240]}
        try:
            receipt = game.prepare(packet)
            game.confirm(packet["id"], receipt["draft_sha"])
        except Exception as exc:
            raise RuntimeError(f"перенос сессии {number} сорвался на {row['id']}") from exc
        played += 1
        index.append({"id": row["id"], "status": "played", "sqlite": packet["id"], "at": row["at"], "source": ref})

    closed = sessions.close(game, game.verify()["revision"], root / f"archive/session-{number:03}.md")
    audit = verification.audit(game)
    if audit["session"] != number + 1:
        raise ValueError(f"после закрытия журнал на сессии {audit['session']}")
    with game.connection() as con:
        if not compaction.audit(con, root):
            raise ValueError("проверка сжатого архива не прошла")
    chats = _inbox(root, number)
    _save(root / f"archive/session-{number:03}-index.json", {
        "format": "npr-session-replay/1", "source": source, "source_sha256": digest,
        "count": len(played_rows), "played": played, "ooc": len(played_rows) - played,
        "last_turn": last["id"], "terminal": last["at"], "sqlite_revision": audit["revision"],
        "archive_sha256": closed.get("sha256"), "chat_exports": chats, "index": index})
    handoff_path = f"campaigns/shiki/continuity/SESSION_{number:03}_TO_{number + 1:03}_HANDOFF.json"
    _save(root / handoff_path, {
        "format": "npr-shiki-intersession-handoff/2", "closed_session": number, "next_session": number + 1,
        "sqlite_revision": audit["revision"], "at": last["at"], "last_live_turn": last["id"],
        "first_live_turn": first_next, "source": f"archive/session-{number:03}-index.json",
        "place": hot_state.get("cursor", {}).get("place"), "scene": scene.get("situation"),
        "present": scene.get("present"), "last_line": last.get("line"),
        "open_threads": {k: (v or {}).get("state") if isinstance(v, dict) else v
                         for k, v in (hot_state.get("threads") or {}).items()},
        "live_next_must": "Читать recovery/NEXT.json заново: must и offscreen действуют, сессия их не решала."})

    before_sizes = memory_sizes(root)
    # Глубокая разгрузка: строгое старение, затем пакет нового номера.
    state, _ = hot._read_state(root)
    state["_undo"] = None
    # Передача — до старения: закрытая сессия становится прошлой, строгий проход разгружает и её.
    state["handoff"] = {"closed": number, "next_session": number + 1, "sqlite_revision": audit["revision"],
                        "at": last["at"], "last_turn": last["id"], "first_turn": first_next, "source": handoff_path}
    aged = aging._pass(root, state, aging.TIGHT)
    state.setdefault("fold", {})["consolidated"] = {"session": number, "aged": {k: v for k, v in aged.items() if v}}
    hot.refresh(state)
    hot._write_state(root, state)
    _, accepted = scene_gate.played_turns(root)
    live_scene.write(root / live_scene.PATH, live_scene.build_for_state(state, accepted, root))
    hot._write_checks(root, state)
    # Пакет нового номера — полной свёрткой без новых ходов: с просьбой хранителя перечитать стартовый
    # пакет (resync), аркой канона, знанием присутствующих и каноном к сцене (раньше NEXT выходил без них).
    refold = hot.fold(root)
    if refold.get("errors"):
        raise ValueError(f"свёртка после закрытия: {refold['errors']}")
    checked = hot.check(root)
    if not checked.get("ok"):
        raise ValueError(f"проверка HOT после закрытия: {checked}")
    pack = _read(root / "recovery/NEXT.json")
    if pack.get("next_id") != first_next:
        raise ValueError(f"NEXT ждёт {pack.get('next_id')}, а не {first_next}")
    final = {"format": git_state.FORMAT, **game.verify(), "sha256": file_sha256(game.path)}
    _save(root / "state.json", final)
    shutil.rmtree(root / "recovery/sqlite", ignore_errors=True)
    git_state.write_transport(root, final)
    git_state.verify_manifest(final, game.path)
    return {"closed": number, "opened": number + 1, "played": played, "ooc": len(played_rows) - played,
            "last_turn": last["id"], "next_id": first_next, "chat_exports": chats,
            "aged": {k: v for k, v in aged.items() if v}, "hot_bytes": checked.get("bytes"),
            "memory_before": before_sizes, "memory_after": memory_sizes(root),
            "archive": f"archive/session-{number:03}.md", "handoff": handoff_path}


def handle(root):
    """Ожидающая заявка ведущего ``recovery/SESSION_CLOSE.json`` → закрытие и отчёт."""
    root = Path(root)
    path = root / REQUEST
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(request, dict) or not request.get("close"):
        return None
    try:
        result = close(root, int(request["close"]) if str(request["close"]).isdigit() else None)
        text = (f"# Сессия {result['closed']} закрыта\n\n"
                f"- Сыграно ходов: {result['played']}, внеигровых: {result['ooc']}; последний — {result['last_turn']}.\n"
                f"- Дословный архив: `{result['archive']}`; передача: `{result['handoff']}`.\n"
                f"- Выгрузки чата: {', '.join(result['chat_exports']) or 'нет'}.\n"
                f"- Разгружено из оперативной памяти: {result['aged'] or 'нечего'}; HOT {result['hot_bytes']} байт.\n"
                f"- Следующий ход: {result['next_id']}.\n")
        text += "\n## Память до → после (байт)\n\n"
        for name, size in result["memory_after"].items():
            text += f"- {name}: {result['memory_before'].get(name, 0)} → {size}.\n"
        outcome = "closed"
    except Exception as exc:   # сессия не закрыта — игра продолжается в прежней
        text = f"# Сессия не закрыта\n\nПричина: {type(exc).__name__}: {exc}\n\nИгра продолжается в прежней сессии.\n"
        outcome = "failed"
    (root / REPORT).write_text(text, encoding="utf-8")
    request["close"] = None
    request["last"] = outcome
    path.write_text(json.dumps(request, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"result": outcome, "report": REPORT}
