"""Optional checkpoint transport to a dedicated Git state branch."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

from .engine import Campaign, ConflictError, file_sha256, new_database

FORMAT = "text-rpg-git-state/1"
SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
DATABASE = "campaign.sqlite"
MANIFEST = "state.json"


def export_directory(game, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    snapshot = game.checkpoint(directory / DATABASE)
    snapshot.pop("path")
    manifest = {"format": FORMAT, **snapshot}
    (directory / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return manifest


def require_prefix(previous, updated):
    """Reject diverging confirmed sources and rerolled checks even with a higher revision."""
    with Campaign(previous).connection() as before, Campaign(updated).connection() as after:
        for table, order in (("journal", "seq"), ("sessions", "number")):
            last = before.execute(f"SELECT max({order}) FROM {table}").fetchone()[0]
            if last is None:
                continue
            old = before.execute(f"SELECT * FROM {table} ORDER BY {order}")
            new = after.execute(f"SELECT * FROM {table} WHERE {order}<=? ORDER BY {order}", (last,))
            for left, right in zip(old, new, strict=True):
                if tuple(left) != tuple(right):
                    raise ConflictError(f"confirmed {table} diverged from the remote checkpoint")
        for old in before.execute("SELECT * FROM rolls"):
            new = after.execute("SELECT * FROM rolls WHERE turn_id=? AND check_id=?",
                                (old["turn_id"], old["check_id"])).fetchone()
            if new is None or tuple(new) != tuple(old):
                raise ConflictError("a persisted RNG check changed or disappeared")


def git(root, *args, data=None):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(["git", "-C", str(root), *args], input=data,
                            capture_output=True, env=env, timeout=30)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {args[0]} failed: {message}")
    return result.stdout


def remote_value(remote):
    if not remote or remote.startswith("-"):
        raise ValueError("expected a Git remote URL or path")
    if "://" in remote:
        url = urlsplit(remote)
        if url.password is not None or (url.scheme in ("http", "https") and url.username is not None):
            raise ValueError("use Git's credential configuration with an uncredentialed URL")
        return remote
    if re.match(r"^[^/]+@[^/:]+:", remote):
        return remote
    return str(Path(remote).resolve())


def state_ref(root, branch):
    if branch != "game-state" and not branch.startswith("game-state/"):
        raise ValueError("state branch must be game-state or game-state/<campaign>")
    ref = f"refs/heads/{branch}"
    git(root, "check-ref-format", ref)
    return ref


def fetch_state(root, remote, ref):
    if not git(root, "ls-remote", "--refs", "--", remote, ref).strip():
        return None
    git(root, "fetch", "--depth=1", "--no-tags", "--", remote, ref)
    return git(root, "rev-parse", "FETCH_HEAD").decode("ascii").strip()


def read_state(root, commit, destination):
    manifest = json.loads(git(root, "show", f"{commit}:{MANIFEST}"))
    if not isinstance(manifest, dict):
        raise ValueError("state manifest must be an object")
    destination.write_bytes(git(root, "show", f"{commit}:{DATABASE}"))
    if manifest.get("sha256") != file_sha256(destination):
        raise ValueError("state snapshot SHA-256 mismatch")
    expected = {"format": FORMAT, **Campaign(destination).verify(), "sha256": manifest["sha256"]}
    if manifest != expected:
        raise ValueError("state snapshot metadata mismatch")
    return expected


def result(commit, manifest):
    return {"head": commit, **manifest}


def load(remote, branch, destination):
    remote = remote_value(remote)
    with tempfile.TemporaryDirectory(prefix="rpg-git-") as directory:
        root = Path(directory)
        git(root, "init", "--quiet")
        ref = state_ref(root, branch)
        head = fetch_state(root, remote, ref)
        if head is None:
            raise ValueError(f"no state branch {branch}")
        with new_database(Path(destination)) as temporary:
            manifest = read_state(root, head, temporary)
        return result(head, manifest)


def save(game, remote, branch, expected):
    remote = remote_value(remote)
    if expected != "new" and not SHA.fullmatch(expected):
        raise ValueError("expected must be new or the full SHA of the loaded state branch")
    with tempfile.TemporaryDirectory(prefix="rpg-git-") as directory:
        root = Path(directory)
        git(root, "init", "--quiet")
        ref = state_ref(root, branch)
        snapshot = game.checkpoint(root / DATABASE)
        snapshot.pop("path")
        manifest = {"format": FORMAT, **snapshot}

        head = fetch_state(root, remote, ref)
        if head is not None:
            previous = read_state(root, head, root / ".git" / "previous.sqlite")
            if manifest == previous:
                return result(head, manifest)
        base = None if expected == "new" else expected
        if head != base:
            raise ConflictError(f"state branch changed; expected {base or 'new'}, found {head or 'none'}")
        if head is not None and (manifest["instance"] != previous["instance"]
                                 or manifest["revision"] < previous["revision"]
                                 or manifest["generation"] <= previous["generation"]):
            raise ConflictError("snapshot is from a different campaign or is older than remote state")
        if head is not None:
            require_prefix(root / ".git" / "previous.sqlite", root / DATABASE)

        (root / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
        git(root, "add", "-f", "--", DATABASE, MANIFEST)
        tree = git(root, "write-tree").decode("ascii").strip()
        parents = ("-p", head) if head is not None else ()
        commit = git(root, "commit-tree", tree, *parents, data=b"Save RPG campaign checkpoint\n")
        commit = commit.decode("ascii").strip()
        # The commit is a direct child of the observed head. The lease also
        # protects initial creation and a concurrent remote rewind.
        git(root, "push", f"--force-with-lease={ref}:{head or ''}", "--", remote, f"{commit}:{ref}")
        remote_head = git(root, "ls-remote", "--refs", "--", remote, ref).decode("ascii").split()
        if not remote_head or remote_head[0] != commit:
            raise ConflictError("state branch moved after push; reload its latest snapshot")
        return result(commit, manifest)
