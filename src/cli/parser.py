"""The public command-line arguments."""

import argparse
from pathlib import Path

from .. import __version__
from ..agents import AGENTS


def parser(*, global_mode: bool = False) -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(prog="agenthandoff" if global_mode else "agentcontinuity",
                                  description="Local evidence-backed handoffs between coding agents")
    cli.set_defaults(global_mode=global_mode)
    cli.add_argument("--version", action="version", version=__version__)
    cli.add_argument("--cwd", type=Path, default=Path.cwd(), help="Git working tree (default: current directory)")
    commands = cli.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check installed agent CLIs and local prerequisites without model calls")
    init = commands.add_parser("init", help="Opt in to local native-session capture; create private storage")
    if not global_mode:
        init.add_argument("--objective")
    if global_mode:
        init.add_argument("--json", action="store_true", help="Print setup results as JSON")
    for name in AGENTS:
        init.add_argument(f"--{name.replace('_', '-')}-root", dest=f"{name}_root",
                          help=f"Native {name} JSONL sessions directory")
    sessions = commands.add_parser("sessions", help="List matching native JSONL sessions without ingesting them")
    sessions.add_argument("--source", choices=AGENTS)
    if global_mode:
        sessions.add_argument("--json", action="store_true", help="Print sessions and a stable listing ID as JSON")
        sessions.add_argument("--query", default="", help="Search session titles, IDs, agents, and projects")
        sessions.add_argument("--limit", type=int, default=50)
        sessions.add_argument("--all", action="store_true", help="List all accessible sessions")
        resume = commands.add_parser("resume", help="Continue a session selected from any project")
        resume.add_argument("session", help="Exact ID/name, or number with --listing")
        resume.add_argument("--listing", help="Listing ID associated with a selected number")
        resume.add_argument("--source", choices=AGENTS)
        resume.add_argument("--target", choices=AGENTS, required=True)
        resume.add_argument("--print", dest="print_only", action="store_true", help="Return context for the already-open agent as JSON")
        resume.add_argument("--dry-run", action="store_true", help="Inspect a packet, including disclosed partial captures")
        resume.add_argument("--allow-partial", action="store_true")
        resume.add_argument("--budget-bytes", type=int)
        resume.add_argument("--mode", choices=("fast", "deep"), default="fast")
    ingest = commands.add_parser("ingest", help="Catch up source events without launching an agent")
    ingest.add_argument("--source", choices=AGENTS, required=True)
    ingest.add_argument("--session", help="Exact source session ID; required when ambiguous")
    for name in AGENTS:
        switch = commands.add_parser(name, help=f"Capture evidence, compile a packet, and launch {name}")
        switch.add_argument("--source", choices=AGENTS,
                            help="May be omitted only when exactly one other agent is registered")
        switch.add_argument("--session", help="Exact source session ID; required when ambiguous")
        switch.add_argument("--dry-run", action="store_true", help="Save and print the packet without launching")
        switch.add_argument("--allow-partial", action="store_true", help="Explicitly accept disclosed incomplete source capture")
        switch.add_argument("--budget-bytes", type=int, help="Maximum UTF-8 prompt bytes (default: 24000; deep: 64000)")
        switch.add_argument("--mode", choices=("fast", "deep", "verified"), default="fast")
        switch.add_argument("--verify-command", help="Explicit shell command for verified mode; never inferred from transcripts")
        switch.add_argument("--timeout", type=int, default=120)
    checkpoint = commands.add_parser("checkpoint", help="Record an explicit objective, constraint, decision, or next action")
    checkpoint.add_argument("--objective")
    checkpoint.add_argument("--constraint", action="append", default=[])
    checkpoint.add_argument("--decision", action="append", default=[])
    checkpoint.add_argument("--next", dest="next_action")
    checkpoint.add_argument("--accept-workspace", action="store_true", help="Acknowledge a reviewed branch/HEAD/worktree change")
    task = commands.add_parser("task", help="Inspect task state or explicitly start a new task")
    task.add_argument("--new", action="store_true")
    task.add_argument("--objective")
    commands.add_parser("status", help="Show task identity, source cursors, and workspace freshness")
    evidence = commands.add_parser("evidence", help="Retrieve evidence from this task by exact ID")
    evidence.add_argument("id")
    evidence.add_argument("--max-bytes", type=int, default=12000)
    search = commands.add_parser("search", help="Search this task's retained evidence")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--max-bytes", type=int, default=12000)
    verification = commands.add_parser("verify", help="Run an explicit command and retain verification evidence")
    verification.add_argument("--command", dest="test_command", required=True)
    verification.add_argument("--timeout", type=int, default=120)
    commands.add_parser("mcp", help="Serve opt-in read-only evidence tools over stdio")
    return cli
