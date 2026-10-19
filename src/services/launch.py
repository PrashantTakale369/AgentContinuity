"""Start the receiving CLI with its normal permissions and an argv prompt."""

from pathlib import Path
import subprocess

from ..agents.base import Agent
from ..core.errors import ContinuityError


def launch(adapter: Agent, prompt: str, workspace: Path) -> int:
    if len(prompt.encode("utf-8")) > 96000:
        raise ContinuityError("Saved packet exceeds the supported launch argument size. Retry with --budget-bytes 96000 or inspect with --dry-run.")
    try:
        # No shell: imported text cannot become a shell command or option.
        return subprocess.run(adapter.launch_args(prompt), cwd=workspace, check=False).returncode
    except FileNotFoundError as exc:
        raise ContinuityError(f"{adapter.name} is not on PATH. The packet is saved; install the CLI or use --dry-run.") from exc
