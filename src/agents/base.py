"""Small agent contract and reusable JSONL discovery/CLI launch behavior."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class Session:
    agent: str
    id: str
    path: str
    cwd: str
    modified_ns: int
    parser: str
    title: str = ""
    workspace: str = ""


class Agent(ABC):
    """Subclass this in agents/<name>.py and export one AGENT instance."""

    name: str
    command: str | None = None

    @property
    def executable(self) -> str:
        return self.command or self.name

    @abstractmethod
    def default_session_root(self) -> Path:
        """Return this agent's native session directory without reading it."""

    @abstractmethod
    def session_identity(self, record: dict) -> tuple[str | None, str | None, bool]:
        """Return (session_id, absolute_cwd, is_subagent) from a native record."""

    @abstractmethod
    def normalize(self, record: dict) -> tuple[str, str, bool]:
        """Return (kind, text, unsupported); see developer_guide/Readme.md."""

    def sessions(self, source_root: Path, workspace: Path | None = None) -> tuple[list[Session], list[str]]:
        from .discovery import discover_sessions

        return discover_sessions(self, source_root, workspace)

    def session_titles(self, source_root: Path) -> dict[str, str]:
        """Optional native name index; keys are exact session IDs."""
        return {}

    def record_title(self, record: dict) -> str | None:
        """Optional native title record. Never infer identity from a display name."""
        return None

    def handoff_integration(self) -> tuple[Path, str] | None:
        """Optional (personal skill directory, invocation) for global setup."""
        return None

    def launch_args(self, prompt: str) -> list[str]:
        """Override when a CLI needs different arguments; keep normal permissions."""
        return [self.executable, "--", prompt]

    def capabilities(self) -> dict:
        return {"agent": self.name, "parser": f"{self.name}.jsonl.v1", "read_only_capture": True,
                "launch": True, "private_reasoning": False, "native_database_capture": False}


def text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_content(part) for part in value if isinstance(part, (dict, str)))
    if isinstance(value, dict):
        for key in ("text", "output", "content"):
            if key in value:
                return text_content(value[key])
    return ""
