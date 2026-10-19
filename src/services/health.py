"""Read-only checks for the installed tools; never opens a model session."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from ..agents import AGENTS
from ..workspace.config import load
from ..core.errors import ContinuityError
from ..core.security import redact


def diagnose(cwd: Path) -> dict:
    agents = {}
    for name, adapter in AGENTS.items():
        executable = shutil.which(adapter.executable)
        record = {"installed": executable is not None, "executable": executable,
                  "version": None, "capabilities": adapter.capabilities()}
        if executable:
            try:
                run = subprocess.run([executable, "--version"], check=False, capture_output=True, text=True, timeout=10)
                record["version"] = redact(run.stdout.strip())[:500] if run.returncode == 0 else "version check failed"
            except (OSError, subprocess.TimeoutExpired):
                record["version"] = "version check failed"
        agents[name] = record
    try:
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        fts5 = True
    except sqlite3.Error:
        fts5 = False
    try:
        directory, config = load(cwd)
        configuration = {"initialized": True, "storage": str(directory), "workspace": config["workspace"],
                         "session_roots_exist": {name: Path(path).is_dir() for name, path in config["roots"].items()}}
    except ContinuityError as exc:
        configuration = {"initialized": False, "message": str(exc)}
    return {"product": "AgentContinuity", "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
            "fts5": fts5, "agents": agents, "configuration": configuration,
            "note": "Availability/version checks only. No model calls, authentication changes, or native session writes."}
