"""Command-line entry point for the text RPG engine."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from subprocess import TimeoutExpired

from . import (autonomy, continuity, git_state, hot, preparation, prose_lint, scene_memory, sessions, setting,
               verification, world)
from .engine import Campaign, limit_value, state
from .history import recall


def audiences(command):
    command.add_argument("--audience", choices=("player", "gm"), default="player")


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--db", type=Path, default=Path("campaign.sqlite"))
    sub = root.add_subparsers(dest="command", required=True)

    sub.add_parser("template", help="print an empty campaign configuration")
    init = sub.add_parser("init", help="initialize a new campaign")
    init.add_argument("config", type=Path)

    context = sub.add_parser("context", help="read the current scene and recent confirmed turns")
    audiences(context)
    context.add_argument("--recent", type=int, default=3)

    packet = sub.add_parser("gm-packet", help="prepare private, revision-bound scene context")
    packet.add_argument("--pack", type=Path)
    packet.add_argument("--mode", choices=preparation.MODES, default="focus")
    packet.add_argument("--actor", action="append", default=[])
    packet.add_argument("--target")
    packet.add_argument("--query", action="append", default=[])
    packet.add_argument("--recent", type=int)
    packet.add_argument("--player-action", help="newest unconfirmed player message for pre-scene memory")
    mem = sub.add_parser("scene-memory", help="source-grounded memory preflight from current HOT")
    mem.add_argument("--state-dir", type=Path, required=True)
    mem.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    mem.add_argument("--player-action", default="")
    mem.add_argument("--limit", type=int, default=9)
    heuristic = sub.add_parser("history-heuristic", help="search all accepted session history and canon corrections")
    heuristic.add_argument("query")
    heuristic.add_argument("--state-dir", type=Path, required=True)
    heuristic.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    heuristic.add_argument("--limit", type=int, default=9)
    heuristic.add_argument("--session", action="append", type=int, default=[])
    review_scene = sub.add_parser(
        "scene-gate", help="Bleach-derived source review of NPC prior disclosures and draft novelty")
    review_scene.add_argument("--state-dir", type=Path, required=True)
    review_scene.add_argument("--player-action", required=True)
    review_scene.add_argument("--actor", action="append", required=True)
    review_scene.add_argument("--limit", type=int, default=10)
    review_scene.add_argument("--draft", type=Path)
    review_scene.add_argument("--reviewed", action="append", default=[])
    review_scene.add_argument("--new-revelation", action="store_true")
    build_mem = sub.add_parser("scene-memory-build", help="refresh compact GitHub memory catalog from canonical sources and HOT")
    build_mem.add_argument("--state-dir", type=Path, required=True)
    build_mem.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    build_mem.add_argument("--output", type=Path, required=True)
    for name, help_text in (("hot-fold", "fold new turn files into HOT v2, the verbatim log and cast cards"),
                            ("hot-check", "verify HOT v2 consistency without writing")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--state-dir", type=Path, required=True)
    boot = sub.add_parser("hot-bootstrap", help="one-off lossless migration from legacy FAST_SCENE history")
    boot.add_argument("--state-dir", type=Path, required=True)
    boot.add_argument("--seed", type=Path, required=True)
    lint = sub.add_parser("prose-lint", help="report explained, deferential and therapeutic prose in a scene text")
    lint.add_argument("file", type=Path)
    queue = sub.add_parser("world-queue", help="review upcoming NPC decisions through a target time")
    queue.add_argument("until")

    world = sub.add_parser("world", help="read facts, beliefs, NPCs and processes")
    audiences(world)
    world.add_argument("--subject", help="read records for a single subject or actor id")

    roll = sub.add_parser("roll", help="record a private roll before exposing its result")
    roll.add_argument("--turn", required=True)
    roll.add_argument("--revision", type=int, required=True)
    roll.add_argument("--check", required=True)
    roll.add_argument("--count", type=int, default=1)
    roll.add_argument("--sides", type=int, default=20)
    roll.add_argument("--modifier", type=int, default=0)
    roll.add_argument("--purpose", default="")

    prepare = sub.add_parser("prepare", help="validate and preserve the exact proposed response")
    prepare.add_argument("packet", type=Path)

    for name, help_text in (("confirm", "commit a delivered response and its consequences"),
                            ("reopen", "reopen a pending draft while preserving dice")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--turn", required=True)
        command.add_argument("--draft-sha", required=True)

    search = sub.add_parser("search", help="find confirmed journal sources")
    audiences(search)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--mode", choices=("all", "any"), default="all")

    history = sub.add_parser("recall", help="find source passages and their causal links")
    audiences(history)
    history.add_argument("query", nargs="?", default="")
    history.add_argument("--entity", action="append", default=[])
    history.add_argument("--session", type=int)
    history.add_argument("--since")
    history.add_argument("--until")
    history.add_argument("--limit", type=int, default=8)
    history.add_argument("--include-superseded", action="store_true")
    history.add_argument("--semantic-cache", type=Path)
    history.add_argument("--model", type=Path)
    history.add_argument("--model-sha")

    model_id = sub.add_parser("model-id", help="fingerprint a local embedding model")
    model_id.add_argument("model", type=Path)
    index = sub.add_parser("semantic-index", help="build or update an offline derived recall index")
    index.add_argument("cache", type=Path)
    index.add_argument("model", type=Path)
    index.add_argument("--model-sha", required=True)

    memory = sub.add_parser("memory", help="read reviewed memory or a private actor's sourced beliefs")
    audiences(memory)
    memory.add_argument("--key")
    memory.add_argument("--actor")
    memory.add_argument("--at")
    memory.add_argument("--history", action="store_true")
    memory.add_argument("--limit", type=int, default=12)
    review = sub.add_parser("record-review", help="record a source-linked correction or prepared process plan")
    review.add_argument("event", type=Path)
    review.add_argument("--revision", type=int, required=True)

    entry = sub.add_parser("entry", help="read the full source for a journal result")
    audiences(entry)
    entry.add_argument("kind", choices=("turn", "event"))
    entry.add_argument("id")

    verify = sub.add_parser("verify", help="verify the database and any pending draft")
    verify.add_argument("--full", action="store_true")
    handoff_audit = sub.add_parser(
        "audit-handoff", help="verify binary checkpoint against the current text fallback and memory index")
    handoff_audit.add_argument("--state-dir", type=Path,
                               help="private state directory, default: database parent")
    handoff_audit.add_argument("--quick", action="store_true",
                               help="skip the expensive full journal replay audit")
    compact = sub.add_parser("compact-history", help="lossless compression of only closed-session journal bodies")
    compact.add_argument("--through", type=int, help="last closed session number")
    checkpoint = sub.add_parser("checkpoint", help="write a consistent, resumable SQLite copy")
    checkpoint.add_argument("destination", type=Path)
    export_state = sub.add_parser("export-state", help="make a verified campaign.sqlite and state.json pair")
    export_state.add_argument("destination", type=Path)
    restore_state = sub.add_parser("restore-state", help="restore a verified SQLite snapshot from its transport parts")
    restore_state.add_argument("directory", type=Path)

    session = sub.add_parser("next-session", help="open the next session at the current scene")
    session.add_argument("--revision", type=int, required=True)
    close = sub.add_parser("close-session", help="export the played dialogue and open the next session")
    close.add_argument("export", type=Path)
    close.add_argument("--revision", type=int, required=True)
    close.add_argument("--transcript", type=Path, help="reviewed verbatim source passages")
    export = sub.add_parser("export-session", help="export a past or ongoing session without closing it")
    export.add_argument("destination", type=Path)
    export.add_argument("--session", type=int, required=True)

    scaffold = sub.add_parser("new-setting", help="create a separate setting template or clean engine copy")
    scaffold.add_argument("destination", type=Path)
    scaffold.add_argument("--portable", action="store_true")

    for name, help_text in (("git-load", "restore a private Git checkpoint to a new local file"),
                            ("git-save", "publish a private Git checkpoint with an expected head")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--remote", required=True)
        command.add_argument("--branch", default="game-state/default")
        if name == "git-save":
            command.add_argument("--expected", required=True)
    return root


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def run(args):
    game = Campaign(args.db)
    match args.command:
        case "template":
            return setting.TEMPLATE
        case "init":
            return Campaign.create(args.db, read_json(args.config)).context()
        case "context":
            return game.context(audience=args.audience, recent=args.recent)
        case "gm-packet":
            return preparation.packet(game, mode=args.mode, actors=args.actor, target=args.target,
                                      queries=args.query, recent=args.recent, pack_path=args.pack,
                                      player_action=args.player_action)
        case "scene-memory":
            limit_value(args.limit, 1, 20)
            return scene_memory.fast_packet_from_files(
                args.source_root, args.state_dir, action=args.player_action, limit=args.limit)
        case "scene-gate":
            from . import scene_gate
            report = scene_gate.prepare(
                args.state_dir, player_action=args.player_action,
                actors=args.actor, limit=args.limit)
            if args.draft:
                report["review"] = scene_gate.review_draft(
                    report, args.draft.read_text(encoding="utf-8"),
                    reviewed=args.reviewed, new_revelation=args.new_revelation)
            return report
        case "scene-memory-build":
            return scene_memory.write_live_index(args.source_root, args.state_dir, args.output)
        case "history-heuristic":
            from . import heuristic_history
            limit_value(args.limit, 1, 30)
            manifest = args.state_dir / heuristic_history.DESTINATION
            index = (heuristic_history.load(manifest) if manifest.is_file()
                     else heuristic_history.build(args.source_root, args.state_dir))
            return heuristic_history.query(
                index, args.query, limit=args.limit, sessions=tuple(args.session))
        case "hot-fold":
            return hot.fold(args.state_dir)
        case "hot-check":
            return hot.check(args.state_dir)
        case "hot-bootstrap":
            return hot.bootstrap(args.state_dir, read_json(args.seed))
        case "prose-lint":
            return prose_lint.lint(args.file.read_text(encoding="utf-8"))
        case "world-queue":
            with game.connection() as con:
                at = state(con)["at"]
                until = world.time_value(args.until, "world target")
                if until < at:
                    raise ValueError("world target is before the confirmed scene")
                return autonomy.queue(con, at=at, until=until)
        case "world":
            return game.world(audience=args.audience, subject=args.subject)
        case "roll":
            return game.roll(turn_id=args.turn, expected_revision=args.revision, check_id=args.check,
                             count=args.count, sides=args.sides, modifier=args.modifier, purpose=args.purpose)
        case "prepare":
            return game.prepare(read_json(args.packet))
        case "confirm":
            return game.confirm(args.turn, args.draft_sha)
        case "reopen":
            return game.reopen(args.turn, args.draft_sha)
        case "search":
            return game.search(args.query, audience=args.audience, limit=args.limit, mode=args.mode)
        case "recall":
            limit_value(args.limit, 1, 30)
            if len(args.entity) > 8:
                raise ValueError("select at most 8 entities")
            semantic = None
            with game.connection() as con:
                if args.semantic_cache or args.model or args.model_sha:
                    from . import semantic_recall
                    if not all((args.semantic_cache, args.model, args.model_sha, args.query.strip())):
                        raise ValueError("hybrid recall needs a query, cache, model and model SHA")
                    semantic = semantic_recall.candidates(
                        con, args.semantic_cache, args.model, args.model_sha, args.query,
                        audience=args.audience, limit=args.limit * 5, session=args.session,
                        since=args.since, until=args.until, entities=args.entity,
                        include_superseded=args.include_superseded)
                return recall(con, query=args.query, audience=args.audience, entities=args.entity,
                              session=args.session, since=args.since, until=args.until, limit=args.limit,
                              include_superseded=args.include_superseded, semantic=semantic)
        case "model-id" | "semantic-index":
            from . import semantic_recall
            if args.command == "model-id":
                return {"sha256": semantic_recall.model_id(args.model)}
            return semantic_recall.build(game, args.cache, args.model, args.model_sha)
        case "memory":
            limit_value(args.limit, 1, 50)
            with game.connection() as con:
                if args.actor:
                    if args.audience != "gm" or args.key or args.history:
                        raise ValueError("personal knowledge is private and uses actor/time selectors")
                    world.id_value(args.actor, "actor")
                    at = world.time_value(args.at, "known at") if args.at else state(con)["at"]
                    if at > state(con)["at"]:
                        raise ValueError("cannot read knowledge after the confirmed scene")
                    return continuity.project_history(con, actor=args.actor, at=at, limit=args.limit)
                if args.at:
                    raise ValueError("--at requires --actor")
                if args.key:
                    world.id_value(args.key, "memory key")
                return continuity.notes(con, audience=args.audience, key=args.key,
                                        history=args.history, limit=args.limit)
        case "record-review":
            return game.record_review(read_json(args.event), args.revision)
        case "entry":
            return game.entry(args.kind, args.id, audience=args.audience)
        case "verify":
            return verification.audit(game) if args.full else game.verify()
        case "compact-history":
            from . import compaction
            return compaction.compact_game(game, through=args.through)
        case "audit-handoff":
            from . import handoff_guard
            return handoff_guard.audit(game, args.state_dir, full=not args.quick)
        case "checkpoint":
            return game.checkpoint(args.destination)
        case "export-state":
            return git_state.export_directory(game, args.destination)
        case "restore-state":
            return git_state.restore_transport(args.directory, args.db)
        case "next-session":
            return game.next_session(args.revision)
        case "close-session":
            transcript = read_json(args.transcript) if args.transcript else None
            return sessions.close(game, args.revision, args.export, transcript)
        case "export-session":
            with game.connection() as con:
                data = sessions.export_session(con, args.session)
            sessions.write_export(args.destination, data)
            return {"session": args.session, "path": str(args.destination), "sha256": world.sha(data)}
        case "new-setting":
            return setting.scaffold(args.destination, portable=args.portable)
        case "git-load":
            return git_state.load(args.remote, args.branch, args.db)
        case "git-save":
            return git_state.save(game, args.remote, args.branch, args.expected)
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, TimeoutExpired) as exc:
        print(f"rpg_engine: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
