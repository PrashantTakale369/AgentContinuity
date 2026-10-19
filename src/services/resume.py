"""Resume a globally selected session in its own repository and task."""

import json
from pathlib import Path
import shlex

from ..agents import AGENTS
from ..core.errors import ContinuityError
from ..core.security import digest, private_directory, sanitize, writer_lock
from ..context.projection import project
from ..context.rendering import render
from ..storage.ledger import Ledger
from ..workspace.config import initialize, load
from ..workspace.global_config import load_global
from ..workspace.git import assert_workspace, observe, root
from .capture import capture, MAX_LINE
from .catalog import resolve_session
from .handoff import prepare_handoff
from .launch import launch
from .setup import runtime_command


def activate_source_task(ledger: Ledger, session, snapshot: dict) -> None:
    """Keep unrelated sessions separate; recognize exact emitted multi-hop prompts."""
    existing = ledger.source(f"{session.agent}:{session.id}")
    task_id = existing["task_id"] if existing else None
    if task_id is None:
        with Path(session.path).open("rb") as stream:
            for _ in range(1000):
                line = stream.readline(MAX_LINE + 1)
                if not line or len(line) > MAX_LINE or not line.endswith(b"\n"):
                    break
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue
                    kind, text, _ = AGENTS[session.agent].normalize(sanitize(record))
                except (ValueError, UnicodeDecodeError):
                    continue
                if kind == "user" and text.strip():
                    saved = ledger.db.execute("SELECT task_id,payload FROM handoffs WHERE prompt_digest=?",
                                              (digest(text.strip()),)).fetchone()
                    if saved and json.loads(saved["payload"])["target_agent"] == session.agent:
                        task_id = saved["task_id"]
                    break
    if task_id:
        with ledger.db:
            ledger.db.execute("INSERT OR REPLACE INTO meta VALUES ('active_task',?)", (task_id,))
    else:
        ledger.new_task(snapshot)


def resume_session(selector: str, target: str, cwd: Path, *, source: str | None = None,
                   listing: str | None = None, print_only: bool = False,
                   dry_run: bool = False, allow_partial: bool = False,
                   budget_bytes: int | None = None, mode: str = "fast") -> dict | int:
    session = resolve_session(selector, source=source, listing=listing)
    if session.agent == target:
        raise ContinuityError("Source and target must differ. Choose a session from another agent.")
    workspace = root(Path(session.cwd))
    if str(workspace) != session.workspace:
        raise ContinuityError("The selected session's original worktree changed; refresh the session list.")
    _, global_config = load_global()
    directory = workspace / ".agentcontinuity"
    private_directory(directory)
    with writer_lock(directory):
        if (directory / "config.json").exists() or (directory / "config.json").is_symlink():
            directory, _ = load(workspace)
        else:
            directory, _ = initialize(workspace, roots=global_config["roots"])
        with Ledger(directory) as ledger:
            snapshot = observe(workspace)
            activate_source_task(ledger, session, snapshot)
            assert_workspace(ledger.anchor(), snapshot, allow_edits=True)
            before = Path(session.path).stat()
            result = capture(ledger, AGENTS[session.agent], session)
            project(ledger, snapshot)
            if not result["complete"] and not (allow_partial or dry_run):
                raise ContinuityError("Capture is partial. Inspect with --dry-run; accept it explicitly with --allow-partial only after review. "
                                      + " ".join(result["warnings"]))
            packet, path = prepare_handoff(ledger, directory, workspace, snapshot, result, target,
                                           mode=mode, budget_bytes=budget_bytes)
            after = Path(session.path).stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ContinuityError("Source is still changing. Stop the source agent and retry.")
            try:
                matches = root(cwd) == workspace
            except ContinuityError:
                matches = False
            if print_only or dry_run:
                command = runtime_command() + ["resume", session.id, "--source", session.agent,
                                                "--target", target, "--mode", mode]
                if budget_bytes is not None:
                    command += ["--budget-bytes", str(budget_bytes)]
                if allow_partial:
                    command += ["--allow-partial"]
                return {"workspace": str(workspace), "current_workspace_matches": matches,
                        "source_agent": session.agent, "session_id": session.id,
                        "target_agent": target, "task_id": packet["task_id"],
                        "packet_path": str(path), "prompt": render(packet),
                        "continue_command": shlex.join(command), "warnings": packet["warnings"]}
            assert_workspace(snapshot, observe(workspace))
            return launch(AGENTS[target], render(packet), workspace)
