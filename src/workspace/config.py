from __future__ import annotations

import json
from pathlib import Path

from ..agents import AGENTS
from ..core.errors import ContinuityError
from ..core.security import private_directory, private_write, regular_file
from ..workspace.git import root

DIRECTORY = ".agentcontinuity"


def session_roots(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve agent defaults, preserving saved/custom roots across additions."""
    roots = {name: str(agent.default_session_root().expanduser().resolve())
             for name, agent in AGENTS.items()}
    roots.update({name: str(Path(path).expanduser().resolve())
                  for name, path in (overrides or {}).items()})
    return roots


def initialize(cwd: Path, *, roots: dict[str, str] | None = None) -> tuple[Path, dict]:
    workspace = root(cwd)
    directory = workspace / DIRECTORY
    private_directory(directory)
    path = directory / "config.json"
    if path.exists() or path.is_symlink():
        raise ContinuityError("Already initialized. Existing configuration and evidence were preserved.")
    config = {"version": 1, "workspace": str(workspace), "roots": session_roots(roots)}
    private_write(path, json.dumps(config, indent=2) + "\n")
    return directory, config


def load(cwd: Path) -> tuple[Path, dict]:
    workspace = root(cwd)
    directory = workspace / DIRECTORY
    if directory.is_symlink() or not directory.is_dir():
        raise ContinuityError("No local configuration. Run 'agentcontinuity init' in this repository.")
    path = directory / "config.json"
    regular_file(path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        if (config["version"] != 1 or config["workspace"] != str(workspace)
                or not isinstance(config["roots"], dict)
                or not all(isinstance(name, str) and isinstance(p, str) and Path(p).is_absolute()
                           for name, p in config["roots"].items())):
            raise ValueError("Invalid configuration")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ContinuityError("Configuration is invalid or belongs to another worktree; review .agentcontinuity/config.json.") from exc
    # Existing repositories gain newly registered agents without rewriting config.
    config["roots"] = session_roots(config["roots"])
    return directory, config
