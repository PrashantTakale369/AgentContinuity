"""Install small personal agent skills around the shared handoff CLI."""

from importlib.resources import files
import json
from pathlib import Path
import shlex
import shutil
import sys

from ..agents import AGENTS
from ..core.errors import ContinuityError
from ..core.security import private_directory, private_write, regular_file, writer_lock
from ..workspace.config import session_roots
from ..workspace.global_config import ensure_global_directory, load_global

MANAGED_MARKER = "<!-- Managed by AgentHandoff. -->"


def runtime_command() -> list[str]:
    """Keep generated skills tied to the Python environment that installed them."""
    entry = Path(sys.argv[0])
    if entry.suffix == ".pyz" and entry.is_file():
        return [sys.executable, "-B", str(entry.resolve())]
    package = __package__.split(".")[0]
    if package == "src":
        return [sys.executable, "-B", str(Path(__file__).resolve().parents[2] / "agenthandoff")]
    return [sys.executable, "-B", "-m", f"{package}.handoff"]


def install_skill(directory: Path, content: str) -> str:
    """Replace only our managed skill, preserving unrelated names/settings."""
    # Refuse symlinked ancestors before creating personal configuration folders.
    for parent in (directory, *directory.parents):
        if parent.is_symlink():
            raise ContinuityError(f"Refusing symlinked integration directory: {parent}")
    path = directory / "SKILL.md"
    regular_file(path)
    if path.exists() and MANAGED_MARKER not in path.read_text():
        return "conflict: existing skill preserved"
    private_directory(directory)
    private_write(path, content)
    return "installed"


def setup_global(overrides: dict[str, str] | None = None) -> dict:
    directory, config = load_global(required=False)
    ensure_global_directory()
    template = files(__package__.split(".")[0]).joinpath("integrations/agenthandoff/SKILL.md").read_text()
    integrations = {}
    with writer_lock(directory):
        # Reload while locked so a repeated setup preserves the latest settings.
        _, config = load_global(required=False)
        config["roots"] = session_roots({**config["roots"], **(overrides or {})})
        command = shlex.join(runtime_command())
        for name, agent in AGENTS.items():
            integration = agent.handoff_integration()
            executable = shutil.which(agent.executable)
            root = Path(config["roots"][name])
            record = {"cli_installed": executable is not None, "session_root": str(root)}
            if integration is None:
                record["status"] = "no personal command integration provided"
            elif not executable and not root.exists():
                record["status"] = "not detected; install the agent, then rerun agenthandoff init"
            else:
                destination, invocation = integration
                content = template.replace("{{CLI}}", command).replace("{{TARGET}}", name)
                record.update({"path": str(destination / "SKILL.md"), "invoke": invocation})
                try:
                    record["status"] = install_skill(destination, content)
                except (ContinuityError, OSError) as exc:
                    record["status"] = "installation failed: " + str(exc)
            integrations[name] = record
        config["integrations"] = integrations
        private_write(directory / "config.json", json.dumps(config, indent=2) + "\n")
    return {"storage": str(directory), "agents": integrations,
            "message": "Local setup complete. Use the listed command in each agent. Restart the agent if it has not loaded the new skill."}
