"""Discover bundled integrations: add <name>.py with an AGENT instance."""

from importlib import import_module
import pkgutil
import re

from ..core.errors import ContinuityError
from .base import Agent, Session

# These names belong to the CLI and cannot also name an agent.
RESERVED_NAMES = {"doctor", "init", "sessions", "ingest", "checkpoint", "task",
                  "status", "evidence", "search", "verify", "mcp", "resume"}


def load_agents() -> dict[str, Agent]:
    agents = {}
    for module in sorted(pkgutil.iter_modules(__path__), key=lambda item: item.name):
        if module.name.startswith("_") or module.name in {"base", "discovery"}:
            continue
        integration = import_module(f"{__name__}.{module.name}")
        agent = getattr(integration, "AGENT", None)
        if not isinstance(agent, Agent):
            raise ContinuityError(f"agents/{module.name}.py must export AGENT = YourAgent().")
        name = getattr(agent, "name", "")
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name)
                or name != module.name or name in RESERVED_NAMES):
            raise ContinuityError(f"Invalid agent name in agents/{module.name}.py: use its lowercase filename stem, excluding CLI command names.")
        agents[name] = agent
    return agents


AGENTS = load_agents()

__all__ = ["AGENTS", "Agent", "Session", "load_agents"]
