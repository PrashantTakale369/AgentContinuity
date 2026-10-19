import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from unittest.mock import patch

from agentcontinuity.agents import AGENTS
from agentcontinuity.services.capture import capture
from agentcontinuity.services.launch import launch
from agentcontinuity.workspace.config import load
from agentcontinuity.core.errors import ContinuityError
from agentcontinuity.core.security import excerpt, private_write, redact, writer_lock
from agentcontinuity.storage.ledger import Ledger
from agentcontinuity.services.verification import verify
from agentcontinuity.workspace.git import assert_workspace, observe
from developer_guide.workflow_testing.support import WorkspaceCase


class WorkspaceSecurityTests(WorkspaceCase):
    def test_branch_mismatch_blocks_and_explicit_acknowledgement_recovers(self):
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/another"], cwd=self.repo, check=True)
        changed = observe(self.repo)
        with self.assertRaisesRegex(ContinuityError, "Branch"):
            assert_workspace(self.snapshot, changed, allow_edits=True)
        self.ledger.checkpoint(changed, accept_workspace=True)
        assert_workspace(self.ledger.anchor(), changed)

    def test_storage_changes_do_not_invalidate_workspace(self):
        private_write(self.directory / "cache.txt", "a new checkpoint")
        self.assertEqual(self.snapshot["fingerprint"], observe(self.repo)["fingerprint"])

    def test_staged_untracked_and_deleted_files_change_fingerprint(self):
        subprocess.run(["git", "add", "main.py"], cwd=self.repo, check=True)
        staged = observe(self.repo)
        self.assertNotEqual(self.snapshot["fingerprint"], staged["fingerprint"])
        (self.repo / "main.py").unlink()
        self.assertNotEqual(staged["fingerprint"], observe(self.repo)["fingerprint"])

    def test_nested_worktree_content_changes_invalidate_parent_observations(self):
        nested = self.repo / "nested"
        nested.mkdir()
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(nested)], check=True)
        (nested / "lib.py").write_text("value = 1\n")
        before = observe(self.repo)
        (nested / "lib.py").write_text("value = 2\n")
        self.assertNotEqual(before["fingerprint"], observe(self.repo)["fingerprint"])

    def test_symlink_is_hashed_without_reading_target_secret(self):
        target = self.base / "secret.txt"
        target.write_text("private outside content")
        (self.repo / "link").symlink_to(target)
        snapshot = observe(self.repo)
        target.write_text("changed outside content")
        self.assertEqual(snapshot["fingerprint"], observe(self.repo)["fingerprint"])
        self.assertNotIn("outside content", json.dumps(snapshot))

    def test_posix_storage_permissions(self):
        self.assertEqual(0o700, self.directory.stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.directory / "config.json").stat().st_mode & 0o777)
        self.assertEqual(0o600, self.ledger.path.stat().st_mode & 0o777)

    def test_symlink_config_and_private_write_are_refused(self):
        path = self.directory / "config.json"
        original = path.read_text()
        path.unlink()
        target = self.base / "config.json"
        target.write_text(original)
        path.symlink_to(target)
        with self.assertRaises(ContinuityError):
            load(self.repo)
        with self.assertRaises(ContinuityError):
            private_write(path, "overwrite")
        self.assertEqual(original, target.read_text())

    def test_writer_lock_blocks_another_writer_and_releases(self):
        with writer_lock(self.directory):
            with self.assertRaisesRegex(ContinuityError, "Another"):
                with writer_lock(self.directory):
                    self.fail("Acquired a competing writer lock")
        with writer_lock(self.directory):
            pass

    def test_redaction_covers_native_raw_text_and_search(self):
        token = "sk-proj-" + "a" * 30
        session = self.native(prompts=[f"API_KEY={token}; password=superprivate"])
        capture(self.ledger, AGENTS["codex"], session)
        all_events = json.dumps(self.ledger.events())
        self.assertNotIn(token, all_events)
        self.assertNotIn("superprivate", all_events)
        self.assertTrue(self.ledger.events()[-1]["redacted"])
        self.assertEqual([], self.ledger.search("superprivate")["results"])
        # Source originals are intentionally unchanged and remain with the source CLI.
        self.assertIn(token, Path(session.path).read_text())

    def test_escaped_multiline_private_key_is_redacted(self):
        secret = "-----BEGIN PRIVATE KEY-----\nprivatekeybody\n-----END PRIVATE KEY-----"
        session = self.native(prompts=[secret])
        capture(self.ledger, AGENTS["codex"], session)
        self.assertNotIn("privatekeybody", json.dumps(self.ledger.events()))

    def test_future_schema_is_rejected(self):
        with self.ledger.db:
            self.ledger.db.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ContinuityError, "Unsupported ledger schema"):
            Ledger(self.directory)

    def test_readonly_connection_cannot_write(self):
        with Ledger(self.directory, readonly=True) as ledger:
            with self.assertRaises(sqlite3.OperationalError):
                ledger.db.execute("DELETE FROM events")

    def test_unicode_excerpt_respects_byte_budget(self):
        value = excerpt("☕" * 1000, 300)
        self.assertLessEqual(len(value.encode()), 300)
        self.assertIn("truncated", value)

    def test_launch_uses_argv_without_shell_or_permission_bypass(self):
        prompt = "$(touch /tmp/SHOULD_NOT_EXIST) --dangerously-bypass-approvals-and-sandbox"
        with patch("agentcontinuity.services.launch.subprocess.run") as run:
            run.return_value.returncode = 7
            code = launch(AGENTS["codex"], prompt, self.repo)
        self.assertEqual(7, code)
        self.assertEqual(["codex", "--", prompt], run.call_args.args[0])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_explicit_verification_retains_output_and_exit_code(self):
        record = verify(self.ledger, self.repo, "printf 'test failure'; exit 3")
        self.assertEqual(3, record["data"]["exit_code"])
        self.assertIn("test failure", record["data"]["output"])
        self.assertTrue(record["data"]["stable_workspace"])

    def test_test_command_that_edits_workspace_is_unverified(self):
        record = verify(self.ledger, self.repo, "printf 'changed' > main.py")
        self.assertEqual(0, record["data"]["exit_code"])
        self.assertFalse(record["data"]["stable_workspace"])

    def test_verification_timeout_is_not_a_pass(self):
        record = verify(self.ledger, self.repo, "sleep 10", timeout=1)
        self.assertTrue(record["data"]["timed_out"])
        self.assertFalse(record["data"]["stable_workspace"])

    def test_blank_verification_command_is_refused(self):
        with self.assertRaises(ContinuityError):
            verify(self.ledger, self.repo, " ")
