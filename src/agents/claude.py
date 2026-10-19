"""Versioned native JSONL parser for claude."""

import os
from pathlib import Path

from ..core.security import json_text
from .base import Agent, text_content


class ClaudeAgent(Agent):
    name = "claude"

    def default_session_root(self) -> Path:
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        return home / "projects"

    def handoff_integration(self) -> tuple[Path, str]:
        return self.default_session_root().parent / "skills/agenthandoff", "/agenthandoff"

    def record_title(self, record: dict) -> str | None:
        if record.get("type") == "custom-title":
            return record.get("customTitle")
        return None

    def session_identity(self, record: dict) -> tuple[str | None, str | None, bool]:
        return record.get("sessionId"), record.get("cwd"), bool(record.get("isSidechain"))

    def normalize(self, record: dict) -> tuple[str, str, bool]:
        kind = record.get("type")
        if kind in ("user", "assistant"):
            message = record.get("message", {})
            if not isinstance(message, dict):
                return "unknown", json_text(record), True
            content = message.get("content", [])
            if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content):
                return "tool_result", json_text(content), False
            if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "tool_use" for p in content):
                return "tool_call", json_text(content), False
            if record.get("isMeta") or record.get("isCompactSummary"):
                return "context", text_content(content), False
            return kind, text_content(content), False
        if kind == "attachment":
            # Attachments include source instructions and environment context. They
            # remain retrievable evidence, never new authoritative user instructions.
            return "context", json_text(record), False
        if kind in ("system", "progress", "summary", "file-history-snapshot", "queue-operation", "last-prompt",
                    "mode", "atis-latch", "api-request-shape", "api-request-blob", "api-request", "ai-title",
                    "file-history-delta", "permission-mode", "cost-state", "custom-title"):
            return "metadata", json_text(record), False
        return "unknown", json_text(record), True


AGENT = ClaudeAgent()
