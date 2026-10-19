from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from agentcontinuity.agents import AGENTS
from agentcontinuity.workspace.config import initialize
from agentcontinuity.storage.ledger import Ledger
from agentcontinuity.workspace.git import observe


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="agentcontinuity-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.repo = self.base / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text(".agentcontinuity/\n__pycache__/\n")
        (self.repo / "main.py").write_text("answer = 41\n")
        self.codex = self.base / "codex"
        self.claude = self.base / "claude"
        self.codex.mkdir()
        self.claude.mkdir()
        self.directory, self.config = initialize(self.repo, roots={"codex": str(self.codex), "claude": str(self.claude)})
        self.snapshot = observe(self.repo)
        self.ledger = Ledger(self.directory)
        self.addCleanup(self.ledger.__exit__)
        self.ledger.new_task(self.snapshot)

    def native(self, agent="codex", session_id="source-one", prompts=None, extra=None):
        if agent == "codex":
            records = [{"type": "session_meta", "payload": {"id": session_id, "cwd": str(self.repo)}}]
            records.extend({"type": "response_item", "payload": {"type": "message", "role": "user",
                           "content": [{"type": "input_text", "text": text}]}} for text in (prompts or ["Fix the expiry bug"]))
        else:
            records = [{"type": "user", "sessionId": session_id, "cwd": str(self.repo),
                        "message": {"role": "user", "content": text}} for text in (prompts or ["Fix the expiry bug"])]
        records.extend(extra or [])
        path = (self.codex if agent == "codex" else self.claude) / (session_id + ".jsonl")
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        sessions, warnings = AGENTS[agent].sessions(self.codex if agent == "codex" else self.claude, self.repo)
        self.assertFalse(warnings)
        return next(s for s in sessions if s.id == session_id)

    def append(self, session, record):
        with Path(session.path).open("a") as stream:
            stream.write(json.dumps(record) + "\n")
