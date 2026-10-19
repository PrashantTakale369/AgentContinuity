import json
from pathlib import Path
from unittest.mock import patch

from agentcontinuity.agents import AGENTS
from agentcontinuity.agents.discovery import select_session
from agentcontinuity.services.capture import capture
from agentcontinuity.core.errors import ContinuityError
from agentcontinuity.storage.ledger import Ledger
from developer_guide.workflow_testing.support import WorkspaceCase


class CaptureTests(WorkspaceCase):
    def test_idempotent_ingestion_and_reopen(self):
        session = self.native()
        one = capture(self.ledger, AGENTS["codex"], session)
        first = self.ledger.events()
        two = capture(self.ledger, AGENTS["codex"], session)
        self.assertTrue(one["complete"])
        self.assertEqual(one["offset"], two["offset"])
        self.assertEqual(first, self.ledger.events())
        with Ledger(self.directory) as reopened:
            capture(reopened, AGENTS["codex"], session)
            self.assertEqual(len(first), len(reopened.events()))

    def test_incremental_tail_and_utf8(self):
        session = self.native()
        first = capture(self.ledger, AGENTS["codex"], session)
        line = json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "Keep café ☕ compatible"}}, ensure_ascii=False).encode()
        with Path(session.path).open("ab") as stream:
            stream.write(line[:-3])
        partial = capture(self.ledger, AGENTS["codex"], session)
        self.assertFalse(partial["complete"])
        self.assertEqual(first["offset"], partial["offset"])
        with Path(session.path).open("ab") as stream:
            stream.write(line[-3:] + b"\n")
        recovered = capture(self.ledger, AGENTS["codex"], session)
        self.assertTrue(recovered["complete"])
        self.assertEqual("Keep café ☕ compatible", self.ledger.events()[-1]["text"])

    def test_malformed_and_unknown_records_are_retained(self):
        session = self.native(extra=[{"type": "new_vendor_schema", "secret": "fake-password"}])
        with Path(session.path).open("ab") as stream:
            stream.write(b"not valid JSON\n")
        result = capture(self.ledger, AGENTS["codex"], session)
        self.assertFalse(result["complete"])
        self.assertEqual(4, len(self.ledger.events()))
        self.assertEqual(2, len([e for e in self.ledger.events() if e["unrecognized"]]))
        self.assertIn("not valid JSON", self.ledger.events()[-1]["raw"])
        self.assertNotIn("fake-password", str(self.ledger.events()))

    def test_source_truncation_blocks_without_losing_evidence(self):
        session = self.native()
        capture(self.ledger, AGENTS["codex"], session)
        count = len(self.ledger.events())
        Path(session.path).write_text("{}\n")
        with self.assertRaisesRegex(ContinuityError, "truncated"):
            capture(self.ledger, AGENTS["codex"], session)
        self.assertEqual(count, len(self.ledger.events()))

    def test_source_prefix_mutation_blocks(self):
        session = self.native()
        capture(self.ledger, AGENTS["codex"], session)
        path = Path(session.path)
        path.write_text(path.read_text().replace("expiry", "EXPIRY"))
        with self.assertRaisesRegex(ContinuityError, "prefix"):
            capture(self.ledger, AGENTS["codex"], session)

    def test_transaction_rolls_back_events_and_cursor(self):
        session = self.native()
        with patch.object(type(AGENTS["codex"]), "normalize", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                capture(self.ledger, AGENTS["codex"], session)
        self.assertEqual([], self.ledger.events())
        self.assertIsNone(self.ledger.source("codex:source-one"))
        self.assertTrue(capture(self.ledger, AGENTS["codex"], session)["complete"])

    def test_ambiguous_session_requires_exact_id(self):
        one = self.native(session_id="one")
        two = self.native(session_id="two")
        with self.assertRaisesRegex(ContinuityError, "ambiguous"):
            select_session([one, two])
        self.assertEqual(one, select_session([one, two], "one"))
        with self.assertRaisesRegex(ContinuityError, "No matching"):
            select_session([one, two], "o")

    def test_exact_workspace_filter_and_subagents(self):
        self.native()
        other = self.codex / "other.jsonl"
        other.write_text(json.dumps({"type": "session_meta", "payload": {"id": "other", "cwd": str(self.base)}}) + "\n")
        directory = self.codex / "subagents"
        directory.mkdir()
        (directory / "hidden.jsonl").write_text(Path(self.codex / "source-one.jsonl").read_text())
        sessions, _ = AGENTS["codex"].sessions(self.codex, self.repo)
        self.assertEqual(["source-one"], [s.id for s in sessions])

    def test_sessions_started_in_a_subdirectory_are_discovered(self):
        nested = self.repo / "package"
        nested.mkdir()
        path = self.codex / "nested.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "nested", "cwd": str(nested)}}) + "\n")
        sessions, _ = AGENTS["codex"].sessions(self.codex, self.repo)
        self.assertEqual(["nested"], [s.id for s in sessions])

    def test_claude_tool_results_are_not_user_requirements(self):
        session = self.native("claude", extra=[
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "FAILED expiry"}]}}])
        self.assertTrue(capture(self.ledger, AGENTS["claude"], session)["complete"])
        self.assertEqual(["user", "tool_call", "tool_result"], [e["kind"] for e in self.ledger.events()])

    def test_installed_claude_2_1_301_shapes_capture_without_false_gaps(self):
        fixture = Path(__file__).parent / "fixtures" / "claude-2.1.301.jsonl"
        native = fixture.read_text().replace("${WORKSPACE}", str(self.repo))
        (self.claude / "installed-shape.jsonl").write_text(native)
        sessions, warnings = AGENTS["claude"].sessions(self.claude, self.repo)
        self.assertFalse(warnings)
        result = capture(self.ledger, AGENTS["claude"], sessions[0])
        self.assertTrue(result["complete"])
        self.assertEqual(15, len(self.ledger.events()))
        users = [e for e in self.ledger.events() if e["kind"] == "user"]
        self.assertEqual(["Fix the expiry bug"], [e["text"] for e in users])
        self.assertEqual(2, len([e for e in self.ledger.events() if e["kind"] == "context"]))

    def test_session_cannot_be_reassigned_to_a_different_task(self):
        session = self.native()
        capture(self.ledger, AGENTS["codex"], session)
        self.ledger.new_task(self.snapshot, "Another task")
        with self.assertRaisesRegex(ContinuityError, "another task"):
            capture(self.ledger, AGENTS["codex"], session)
