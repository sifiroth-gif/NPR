"""Command-line entry point for the text RPG engine."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from subprocess import TimeoutExpired

from . import git_state
from .engine import Campaign

TEMPLATE = {
    "id": "new-campaign",
    "title": "Название кампании",
    "start_at": "0001-01-01T09:00:00",
    "location": "Стартовая локация",
    "player": {"id": "player", "name": "Имя персонажа"},
    "rules": {},
    "npcs": [],
    "operations": [],
}


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

    entry = sub.add_parser("entry", help="read the full source for a journal result")
    audiences(entry)
    entry.add_argument("kind", choices=("turn", "event"))
    entry.add_argument("id")

    sub.add_parser("verify", help="verify the database and any pending draft")
    checkpoint = sub.add_parser("checkpoint", help="write a consistent, resumable SQLite copy")
    checkpoint.add_argument("destination", type=Path)

    session = sub.add_parser("next-session", help="open the next session at the current scene")
    session.add_argument("--revision", type=int, required=True)

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
            return TEMPLATE
        case "init":
            return Campaign.create(args.db, read_json(args.config)).context()
        case "context":
            return game.context(audience=args.audience, recent=args.recent)
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
        case "entry":
            return game.entry(args.kind, args.id, audience=args.audience)
        case "verify":
            return game.verify()
        case "checkpoint":
            return game.checkpoint(args.destination)
        case "next-session":
            return game.next_session(args.revision)
        case "git-load":
            return git_state.load(args.remote, args.branch, args.db)
        case "git-save":
            return git_state.save(game, args.remote, args.branch, args.expected)
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, sqlite3.Error, TimeoutExpired) as exc:
        print(f"rpg_engine: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
