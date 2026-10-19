"""Read bounded session headers and require unambiguous workspace identity."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..core.errors import ContinuityError
from ..core.security import excerpt, redact, regular_file
from ..workspace.git import root
from .base import Agent, Session


def discover_sessions(adapter: Agent, source_root: Path, workspace: Path | None = None) -> tuple[list[Session], list[str]]:
    result, warnings = [], []
    if not source_root.exists():
        return [], [f"No {adapter.name} JSONL session directory: {source_root}"]
    if source_root.is_symlink() or not source_root.is_dir():
        raise ContinuityError(f"Session root must be a real directory: {source_root}")
    titles = adapter.session_titles(source_root)
    def on_error(error: OSError) -> None:
        warnings.append(f"Could not scan a {adapter.name} session directory ({error.__class__.__name__}).")
    for directory, dirs, files in os.walk(source_root, onerror=on_error, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in ("subagents", ".git") and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            if not name.endswith(".jsonl"):
                continue
            path = Path(directory) / name
            try:
                regular_file(path)
                # Discover identity from metadata only; never infer a workspace from a filename.
                with path.open("rb") as stream:
                    header = stream.read(256 * 1024)
                    # Native rename records can be appended late in a session.
                    stream.seek(0, os.SEEK_END)
                    start = max(0, stream.tell() - 256 * 1024)
                    stream.seek(start)
                    tail = stream.read()
                    if start:
                        tail = tail.partition(b"\n")[2]
                session_id = cwd = None
                sidechain = False
                records = []
                for line in header.splitlines()[:100]:
                    try:
                        record = json.loads(line)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    records.append(record)
                    if not (session_id and cwd):
                        session_id, cwd, sidechain = adapter.session_identity(record)
                if isinstance(session_id, str) and isinstance(cwd, str) and not sidechain:
                    source_cwd = Path(cwd)
                    if not source_cwd.is_absolute():
                        continue
                    repository = root(source_cwd)
                    if workspace is None or (source_cwd.resolve().is_relative_to(workspace.resolve())
                                              and repository == workspace.resolve()):
                        title = ""
                        for line in tail.splitlines():
                            try:
                                item = json.loads(line)
                                if isinstance(item, dict):
                                    records.append(item)
                            except (ValueError, UnicodeDecodeError):
                                continue
                        for item in records:
                            candidate = adapter.record_title(item)
                            if isinstance(candidate, str) and candidate.strip():
                                title = candidate.strip()
                        title = titles.get(session_id) or title
                        if not title:
                            for item in records:
                                kind, text, _ = adapter.normalize(item)
                                if kind == "user" and text.strip():
                                    title = text.strip().splitlines()[0]
                                    break
                        result.append(Session(adapter.name, session_id, str(path.resolve()), cwd,
                                              path.stat().st_mtime_ns, f"{adapter.name}.jsonl.v1",
                                              excerpt(redact(title or session_id), 512), str(repository)))
            except (OSError, ContinuityError):
                warnings.append(f"Could not read a {adapter.name} session file: {path.name}")
    result.sort(key=lambda s: (-s.modified_ns, s.id))
    return result, warnings


def select_session(sessions: list[Session], session_id: str | None = None) -> Session:
    candidates = [s for s in sessions if session_id is None or s.id == session_id]
    if not candidates:
        raise ContinuityError("No matching native JSONL session for this exact workspace. Use 'agentcontinuity sessions' and check --source/--session.")
    if len(candidates) != 1:
        raise ContinuityError("Session identity is ambiguous. Select an exact ID with --session; use 'agentcontinuity sessions' to list candidates.")
    return candidates[0]
