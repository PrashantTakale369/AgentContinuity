"""Route parsed commands to the application services and format CLI output."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from ..agents import AGENTS
from ..agents.discovery import select_session
from ..context.projection import project
from ..context.rendering import render
from ..core.errors import ContinuityError
from ..core.security import writer_lock
from ..services.capture import capture
from ..services.handoff import prepare_handoff
from ..services.launch import launch
from ..services.verification import verify
from ..storage.ledger import Ledger
from ..workspace.config import initialize, load
from ..workspace.git import assert_workspace, observe


def emit(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=True))


def find_session(config: dict, source: str, session_id: str | None):
    adapter = AGENTS[source]
    sessions, warnings = adapter.sessions(Path(config["roots"][source]), Path(config["workspace"]))
    for warning in warnings:
        print(f"agentcontinuity: {warning}", file=sys.stderr)
    return adapter, select_session(sessions, session_id)


def select_source(target: str | None, requested: str | None) -> str:
    if requested is not None:
        if requested == target:
            raise ContinuityError("Source and target must differ for a cross-agent handoff.")
        return requested
    candidates = [name for name in AGENTS if name != target]
    if len(candidates) != 1:
        raise ContinuityError("Select a source agent explicitly with --source. Available sources: "
                              + ", ".join(candidates))
    return candidates[0]


def execute(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        from ..services.health import diagnose
        emit(diagnose(args.cwd))
        return 0
    if args.command == "init":
        roots = {name: getattr(args, f"{name}_root") for name in AGENTS
                 if getattr(args, f"{name}_root") is not None}
        directory, config = initialize(args.cwd, roots=roots)
        with writer_lock(directory), Ledger(directory) as ledger:
            state = ledger.new_task(observe(args.cwd), args.objective)
        emit({"workspace": config["workspace"], "task_id": state["id"], "storage": str(directory),
              "message": "Local JSONL capture enabled. No hooks, cloud services, or native session writes. Add .agentcontinuity/ to your Git ignores."})
        return 0
    directory, config = load(args.cwd)
    workspace = Path(config["workspace"])
    if args.command == "mcp":
        from ..integrations.mcp import serve
        serve(directory)
        return 0
    if args.command == "sessions":
        records, warnings = [], []
        for name in ([args.source] if args.source else AGENTS):
            sessions, notes = AGENTS[name].sessions(Path(config["roots"][name]), workspace)
            records.extend(asdict(s) for s in sessions)
            warnings.extend(notes)
        emit({"sessions": records, "warnings": warnings,
              "coverage": "Native JSONL only; database-backed sessions and subagents are not imported."})
        return 0
    readonly = args.command in ("evidence", "search", "status") or (args.command == "task" and not args.new)
    if readonly:
        with Ledger(directory, readonly=True) as ledger:
            if args.command == "evidence":
                emit(ledger.retrieve(args.id, max_bytes=args.max_bytes))
            elif args.command == "search":
                emit(ledger.search(args.query, limit=args.limit, max_bytes=args.max_bytes))
            elif args.command == "task":
                if args.objective:
                    raise ContinuityError("Use 'agentcontinuity checkpoint --objective TEXT' or 'agentcontinuity task --new --objective TEXT'.")
                emit(ledger.task())
            else:
                snapshot, anchor = observe(workspace), ledger.anchor()
                emit({"task": ledger.task(), "workspace": {k: v for k, v in snapshot.items() if k != "diff"},
                      "anchor_matches": all(anchor[k] == snapshot[k] for k in ("root", "git_dir", "branch", "head")),
                      "sources": [dict(row) for row in ledger.db.execute("SELECT key,path,cursor FROM sources WHERE task_id=?", (ledger.task()["id"],))]})
        return 0
    with writer_lock(directory), Ledger(directory) as ledger:
        if args.command == "task":
            emit(ledger.new_task(observe(workspace), args.objective))
            return 0
        snapshot = observe(workspace)
        if args.command != "checkpoint" or not args.accept_workspace:
            assert_workspace(ledger.anchor(), snapshot, allow_edits=True)
        if args.command == "checkpoint":
            if not any((args.objective, args.constraint, args.decision, args.next_action, args.accept_workspace)):
                raise ContinuityError("Supply an objective, constraint, decision, next action, or --accept-workspace.")
            emit(ledger.checkpoint(snapshot, objective=args.objective, constraints=args.constraint,
                                  decisions=args.decision, next_action=args.next_action, accept_workspace=args.accept_workspace))
            return 0
        if args.command == "verify":
            record = verify(ledger, workspace, args.test_command, args.timeout)
            emit(record)
            return 0 if record["data"]["exit_code"] == 0 and record["data"]["stable_workspace"] else 1
        target = args.command if args.command in AGENTS else None
        source = select_source(target, args.source)
        adapter, session = find_session(config, source, args.session)
        result = capture(ledger, adapter, session)
        project(ledger, snapshot)
        if target is None:
            emit(result)
            return 0 if result["complete"] else 2
        if not result["complete"] and not (args.allow_partial or args.dry_run):
            raise ContinuityError("Capture is partial: " + " ".join(result["warnings"]) + " Inspect with --dry-run or explicitly use --allow-partial.")
        packet, path = prepare_handoff(
            ledger, directory, workspace, snapshot, result, target, mode=args.mode,
            budget_bytes=args.budget_bytes, verify_command=args.verify_command, timeout=args.timeout)
        if args.dry_run:
            print(render(packet), end="")
            return 0
        print(f"agentcontinuity: saved {path} ({packet['budget']['used_bytes']} prompt bytes)", file=sys.stderr)
        for warning in packet["warnings"]:
            print(f"agentcontinuity: {warning}", file=sys.stderr)
        # Keep the lock while the target is active so another continuity command cannot launch a competing writer.
        return launch(AGENTS[target], render(packet), workspace)
