"""Observe actual Git state; declared progress never overrides this evidence."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
from typing import Any

from ..core.errors import ContinuityError
from ..core.security import digest, json_text, redact


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git(cwd: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args], cwd=cwd, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContinuityError(f"Git observation failed ({' '.join(args[:2])}); check the repository and Git installation.") from exc


def root(cwd: Path) -> Path:
    try:
        return Path(os.fsdecode(git(cwd, "rev-parse", "--show-toplevel")).strip()).resolve(strict=True)
    except (OSError, ContinuityError) as exc:
        raise ContinuityError("Run AgentContinuity inside a Git working tree.") from exc


def observe(cwd: Path, *, _visited: frozenset[Path] = frozenset()) -> dict[str, Any]:
    directory = root(cwd)
    if directory in _visited:
        raise ContinuityError("Cannot fingerprint a changed directory without an independent Git working tree.")
    visited = _visited | {directory}
    git_dir = Path(os.fsdecode(git(directory, "rev-parse", "--absolute-git-dir")).strip()).resolve()
    try:
        head = git(directory, "rev-parse", "--verify", "HEAD").decode().strip()
    except ContinuityError:
        head = None  # A repository without its first commit is supported.
    try:
        branch = git(directory, "symbolic-ref", "--short", "HEAD").decode().strip()
    except ContinuityError:
        branch = None  # Detached HEAD.
    paths = ["--", ".", ":(exclude).agentcontinuity"]
    status = git(directory, "status", "--porcelain=v1", "-z", "--untracked-files=all", *paths)
    changed = git(directory, "ls-files", "-z", "--modified", "--deleted", "--others", "--exclude-standard", *paths)
    staged = git(directory, "diff", "--cached", "--name-only", "-z", *paths)
    files = sorted(set(os.fsdecode(p) for p in (changed + staged).split(b"\0") if p))
    hashes = []
    for name in files:
        path = directory / name
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ContinuityError("Git returned a path outside the workspace.")
        if path.is_symlink():
            hashes.append([name, digest(os.readlink(path))])
        elif not path.exists():
            hashes.append([name, "deleted"])
        elif path.is_file():
            # Hash actual bytes, not just timestamps or sizes.
            hashes.append([name, digest(path.read_bytes()), path.stat().st_mode & 0o777])
        else:
            # Gitlinks/nested repositories can stay 'dirty' across further content
            # edits. Their directory timestamp alone would falsely preserve freshness.
            hashes.append([name, "nested_worktree", observe(path, _visited=visited)["fingerprint"]])
    diff = git(directory, "diff", "--no-ext-diff", "--no-textconv", "--binary", *paths)
    diff += git(directory, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--binary", *paths)
    fingerprint = digest(json_text({
        "root": str(directory), "git_dir": str(git_dir), "head": head, "branch": branch,
        "status": digest(status), "files": hashes, "diff": digest(diff),
    }))
    return {"root": str(directory), "git_dir": str(git_dir), "head": head, "branch": branch,
            "dirty": bool(status), "files": files, "fingerprint": fingerprint,
            "diff": redact(diff.decode("utf-8", errors="replace")), "observed_at": now()}


def assert_workspace(expected: dict, current: dict, *, allow_edits: bool = False) -> None:
    if any(expected[key] != current[key] for key in ("root", "git_dir", "head", "branch")):
        raise ContinuityError("Branch, HEAD, or worktree changed. Review it, then run 'agentcontinuity checkpoint --accept-workspace' or start a new task.")
    if not allow_edits and expected["fingerprint"] != current["fingerprint"]:
        raise ContinuityError("Workspace changed during handoff. Stop the other writer and retry.")
