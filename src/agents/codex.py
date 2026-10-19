"""Versioned native JSONL parser for codex."""

import json
import os
from pathlib import Path

from ..core.security import json_text, regular_file
from .base import Agent, text_content


class CodexAgent(Agent):
    name = "codex"

    def default_session_root(self) -> Path:
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        return home / "sessions"

    def handoff_integration(self) -> tuple[Path, str]:
        return Path.home() / ".agents/skills/agenthandoff", "$agenthandoff"

    def session_titles(self, source_root: Path) -> dict[str, str]:
        index = source_root.parent / "session_index.jsonl"
        if not index.exists():
            return {}
        regular_file(index)
        titles = {}
        with index.open("rb") as stream:
            for _ in range(100_000):
                line = stream.readline(65537)
                if not line:
                    break
                if len(line) > 65536:
                    break
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if (isinstance(item, dict) and isinstance(item.get("id"), str)
                        and isinstance(item.get("thread_name"), str)):
                    titles[item["id"]] = item["thread_name"]
        return titles

    def record_title(self, record: dict) -> str | None:
        payload = record.get("payload")
        if record.get("type") == "session_meta" and isinstance(payload, dict):
            return payload.get("title") or payload.get("name")
        return None

    def session_identity(self, record: dict) -> tuple[str | None, str | None, bool]:
        payload = record.get("payload", {})
        if record.get("type") == "session_meta" and isinstance(payload, dict):
            return payload.get("id"), payload.get("cwd"), False
        return None, None, False

    def normalize(self, record: dict) -> tuple[str, str, bool]:
        kind = record.get("type")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            return "unknown", json_text(record), True
        subtype = payload.get("type")
        if kind == "response_item":
            if subtype == "message":
                role = payload.get("role")
                if role in ("user", "assistant"):
                    return role, text_content(payload.get("content", [])), False
                if role in ("system", "developer"):
                    return "metadata", text_content(payload.get("content", [])), False
            if subtype in ("function_call", "custom_tool_call"):
                return "tool_call", json_text(payload), False
            if subtype in ("function_call_output", "custom_tool_call_output"):
                return "tool_result", text_content(payload.get("output", "")), False
            if subtype == "reasoning":
                return "metadata", text_content(payload.get("summary", [])), False
        if kind == "event_msg":
            if subtype == "user_message":
                return "user", text_content(payload.get("message", "")), False
            if subtype == "agent_message":
                return "assistant", text_content(payload.get("message", "")), False
            if subtype in ("agent_reasoning", "token_count", "task_started", "task_complete", "turn_aborted",
                           "context_compacted", "item_completed", "item_started", "error", "warning"):
                return "metadata", json_text(payload), False
        if kind in ("session_meta", "turn_context", "compacted"):
            return "metadata", json_text(payload), False
        return "unknown", json_text(record), True


AGENT = CodexAgent()
