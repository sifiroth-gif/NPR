"""Fail-closed boundary audit between the live SQLite and text-only fallback.

This is an explicit intersession/handoff tool, not an extra network read on
every played response. It distinguishes a verified binary scene from a
source-backed text lead. Never silently prefer a stale descriptive pointer.
"""
import json
from pathlib import Path

from . import git_state, verification
from .engine import Campaign

def audit(game, state_dir=None, *, full=True):
    root=Path(state_dir or game.path.parent)
    mfile=root/"state.json"
    if not mfile.is_file():
        raise ValueError("no canonical state.json alongside game-state")
    manifest=json.loads(mfile.read_text(encoding="utf-8"))
    git_state.verify_manifest(manifest,game.path)
    result=verification.audit(game) if full else game.verify()
    if result["revision"]!=manifest["revision"] or result["session"]!=manifest["session"]:
        raise ValueError("SQLite and manifest disagree on active session")
    current={"status":"binary_verified","source_of_truth":"campaign.sqlite",
             "session":result["session"],"revision":result["revision"],
             "at":result["at"],"pending":result["pending"],
             "full_audit":bool(result.get("full_audit"))}
    path=root/"recovery/FAST_SCENE.json"
    if not path.is_file():
        return {**current,"fallback":"none"}
    hot=json.loads(path.read_text(encoding="utf-8"))
    cursor=hot.get("cursor") or {}
    hot_session=hot.get("session")
    text_at=cursor.get("at")
    runtime=hot.get("runtime") or {}
    if hot_session!=result["session"]:
        raise ValueError("text fallback points at another session: never restore old HOT")
    if type(text_at) is not str or len(text_at)!=19:
        raise ValueError("text fallback has no consistent complete game clock")
    if text_at<result["at"]:
        raise ValueError("text checkpoint is older than canonical SQLite")
    index=root/"recovery/ACTIVE_MEMORY_INDEX_1283_09_27.json"
    if index.exists():
        memo=json.loads(index.read_text(encoding="utf-8"))
        if (memo.get("sqlite_authoritative") is True and
                (memo.get("session")!=result["session"] or
                 memo.get("sqlite_revision")!=result["revision"])):
            raise ValueError("active memory index claims a different canonical SQLite revision")
    if text_at==result["at"] and cursor.get("last_turn_id") is None:
        if runtime.get("sqlite_canonical") is not True or runtime.get("sqlite_revision")!=result["revision"]:
            raise ValueError("text handoff does not claim the exact live SQLite baseline")
        return {**current,"fallback":"synchronized","cursor_at":text_at}
    if text_at==result["at"] and runtime.get("sqlite_canonical") is True:
        return {**current,"fallback":"synchronized","cursor_at":text_at}
    last=hot.get("latest_turn")
    if (not isinstance(last,dict) or not last.get("id") or
            not last.get("player_input") or not last.get("exact_visible_gm_response")):
        raise ValueError("text fallback is ahead but has no full last exact response")
    if (runtime.get("sqlite_revision",result["revision"])!=result["revision"] or
            runtime.get("sqlite_canonical") is True):
        raise ValueError("text-only advances cannot claim to be the canonical SQLite")
    return {**current,"fallback":"text_ahead_unreplayed",
            "cursor_at":text_at,"last_text_turn_id":last["id"],
            "important":"Replay all exact text turns from source archives before closing session."}
