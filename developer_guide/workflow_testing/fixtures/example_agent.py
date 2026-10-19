"""A fictional JSONL agent used to test the one-file contributor workflow."""

import os
from pathlib import Path

from .base import Agent, text_content
from ..core.security import json_text


class ExampleAgent(Agent):
    name = "example"
    command = "example-cli"

    def default_session_root(self) -> Path:
        return Path(os.environ.get("EXAMPLE_HOME", str(Path.home() / ".example"))) / "sessions"

    def session_identity(self, record: dict) -> tuple[str | None, str | None, bool]:
        if record.get("type") == "session":
            return record.get("id"), record.get("cwd"), bool(record.get("subagent"))
        return None, None, False

    def normalize(self, record: dict) -> tuple[str, str, bool]:
        kind = record.get("type")
        if kind == "session":
            return "metadata", json_text(record), False
        if kind in {"user", "assistant", "tool_call", "tool_result"}:
            return kind, text_content(record.get("text", "")), False
        return "unknown", json_text(record), True

    def launch_args(self, prompt: str) -> list[str]:
        return [self.executable, "--prompt", prompt]


AGENT = ExampleAgent()
