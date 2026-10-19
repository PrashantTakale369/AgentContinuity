"""Offline end-to-end checks for global setup and cross-directory handoffs."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from agentcontinuity.cli.app import handoff_main
from agentcontinuity.services.setup import setup_global
from agentcontinuity.services.handoff import prepare_handoff
from developer_guide.workflow_testing.support import WorkspaceCase


class GlobalHandoffTests(WorkspaceCase):
    def setUp(self):
        super().setUp()
        self.home = self.base / "home"
        self.home.mkdir()
        self.other = self.base / "other-project"
        self.other.mkdir()
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(self.other)], check=True)
        environment = {"HOME": str(self.home), "AGENTHANDOFF_HOME": str(self.base / "global"),
                       "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                       "CODEX_HOME": str(self.home / ".codex")}
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.detect = patch("agentcontinuity.services.setup.shutil.which", side_effect=lambda name: f"/test/bin/{name}")
        self.detect.start()
        self.addCleanup(self.detect.stop)
        setup_global({"codex": str(self.codex), "claude": str(self.claude)})

    def call(self, *args, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = handoff_main(["--cwd", str(cwd or self.other), *args])
        return code, out.getvalue(), err.getvalue()

    def listing(self, *args):
        code, out, err = self.call("sessions", "--json", *args)
        self.assertEqual(0, code, err)
        return json.loads(out)

    def test_global_init_outside_git_installs_both_and_is_repeatable(self):
        code, out, err = self.call("init", "--json", cwd=self.base)
        self.assertEqual(0, code, err)
        report = json.loads(out)
        self.assertEqual("/agenthandoff", report["agents"]["claude"]["invoke"])
        self.assertEqual("$agenthandoff", report["agents"]["codex"]["invoke"])
        for record in report["agents"].values():
            self.assertEqual("installed", record["status"])
            self.assertIn("sessions --json", Path(record["path"]).read_text())
        config = json.loads((self.base / "global/config.json").read_text())
        self.assertEqual(str(self.codex), config["roots"]["codex"])
        self.assertFalse((self.base / ".agentcontinuity").exists())

    def test_setup_preserves_unrelated_skill(self):
        path = self.home / ".agents/skills/agenthandoff/SKILL.md"
        path.write_text("User-authored skill\n")
        code, out, err = self.call("init", "--json")
        self.assertEqual(1, code, err)
        self.assertIn("conflict", json.loads(out)["agents"]["codex"]["status"])
        self.assertEqual("User-authored skill\n", path.read_text())

    def test_sessions_from_another_directory_keep_native_titles(self):
        self.native(session_id="codex-one")
        (self.codex.parent / "session_index.jsonl").write_text(json.dumps({"id": "codex-one", "thread_name": "AgentContinuity"}) + "\n")
        self.native("claude", session_id="claude-one", extra=[{"type": "custom-title", "customTitle": "Auth repair"}])
        result = self.listing()
        self.assertEqual({"AgentContinuity", "Auth repair"}, {r["title"] for r in result["sessions"]})
        self.assertTrue(all(r["workspace"] == str(self.repo) for r in result["sessions"]))
        self.assertTrue(all(r["running_status"] == "unknown" for r in result["sessions"]))
        self.assertEqual(1, len(self.listing("--query", "Auth repair")["sessions"]))

    def test_resume_selects_original_project_and_does_not_touch_current_project(self):
        self.native(session_id="source-one")
        result = self.listing()
        code, out, err = self.call("resume", "1", "--listing", result["listing_id"], "--target", "claude", "--print")
        self.assertEqual(0, code, err)
        packet = json.loads(out)
        self.assertEqual(str(self.repo), packet["workspace"])
        self.assertFalse(packet["current_workspace_matches"])
        self.assertIn("Fix the expiry bug", packet["prompt"])
        self.assertIn("--source codex", packet["continue_command"])
        self.assertFalse((self.other / ".agentcontinuity").exists())

    def test_same_project_packet_is_available_to_open_agent(self):
        self.native()
        code, out, err = self.call("resume", "source-one", "--source", "codex", "--target", "claude", "--print", cwd=self.repo)
        self.assertEqual(0, code, err)
        self.assertTrue(json.loads(out)["current_workspace_matches"])

    def test_listing_numbers_do_not_change_when_new_sessions_appear(self):
        self.native(session_id="chosen")
        result = self.listing()
        self.native(session_id="newer", prompts=["Unrelated task"])
        code, out, err = self.call("resume", "1", "--listing", result["listing_id"], "--target", "claude", "--print")
        self.assertEqual(0, code, err)
        self.assertEqual("chosen", json.loads(out)["session_id"])

    def test_unrelated_sessions_in_same_project_get_different_tasks(self):
        self.native(session_id="one", prompts=["First objective"])
        self.native(session_id="two", prompts=["Second objective"])
        packets = []
        for session in ("one", "two", "one"):
            code, out, err = self.call("resume", session, "--source", "codex", "--target", "claude", "--print")
            self.assertEqual(0, code, err)
            packets.append(json.loads(out))
        self.assertNotEqual(packets[0]["task_id"], packets[1]["task_id"])
        self.assertEqual(packets[0]["task_id"], packets[2]["task_id"])
        self.assertNotIn("Second objective", packets[0]["prompt"])

    def test_exact_handoff_prompt_preserves_task_across_agents(self):
        self.native(session_id="one")
        code, out, err = self.call("resume", "one", "--source", "codex", "--target", "claude", "--print")
        self.assertEqual(0, code, err)
        first = json.loads(out)
        self.native("claude", session_id="two", prompts=[first["prompt"], "Keep the API stable"])
        code, out, err = self.call("resume", "two", "--source", "claude", "--target", "codex", "--print")
        self.assertEqual(0, code, err)
        self.assertEqual(first["task_id"], json.loads(out)["task_id"])

    def test_duplicate_titles_require_exact_selection(self):
        self.native(session_id="one")
        self.native(session_id="two")
        code, out, err = self.call("resume", "Fix the expiry bug", "--source", "codex", "--target", "claude", "--print")
        self.assertEqual(1, code)
        self.assertIn("ambiguous", err)

    def test_partial_capture_blocks_context_import_but_allows_preview(self):
        self.native(extra=[{"type": "unrecognized-future-schema"}])
        code, out, err = self.call("resume", "source-one", "--source", "codex", "--target", "claude", "--print")
        self.assertEqual(1, code)
        self.assertIn("partial", err)
        self.assertEqual(0, self.call("resume", "source-one", "--source", "codex", "--target", "claude", "--dry-run")[0])

    def test_listing_path_traversal_and_same_agent_are_refused(self):
        self.native()
        self.assertEqual(1, self.call("resume", "1", "--listing", "../config", "--target", "claude", "--print")[0])
        code, out, err = self.call("resume", "source-one", "--source", "codex", "--target", "codex", "--print")
        self.assertEqual(1, code)
        self.assertIn("must differ", err)

    def test_resume_launches_target_in_selected_workspace(self):
        self.native()
        binary = self.base / "bin"
        binary.mkdir()
        claude = binary / "claude"
        claude.write_text(f"#!{sys.executable}\nimport pathlib\npathlib.Path('.agentcontinuity/launch-cwd').write_text(str(pathlib.Path.cwd()))\n")
        claude.chmod(0o700)
        with patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}):
            code, out, err = self.call("resume", "source-one", "--source", "codex", "--target", "claude")
        self.assertEqual(0, code, err)
        self.assertEqual(str(self.repo), (self.directory / "launch-cwd").read_text())

    def test_catalogue_discloses_limits_and_persists_private_listing(self):
        self.native(session_id="one")
        self.native(session_id="two")
        result = self.listing("--limit", "1")
        self.assertEqual(1, result["omitted"])
        path = self.base / "global/listings" / (result["listing_id"] + ".json")
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertEqual(2, len(self.listing("--all")["sessions"]))

    def test_resume_initializes_selected_workspace_on_demand(self):
        # Native sessions may belong to a repository that never used our CLI.
        records = [{"type": "session_meta", "payload": {"id": "fresh", "cwd": str(self.other)}},
                   {"type": "event_msg", "payload": {"type": "user_message", "message": "Fix this new project"}}]
        (self.codex / "fresh.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        code, out, err = self.call("resume", "fresh", "--source", "codex", "--target", "claude", "--print", cwd=self.base)
        self.assertEqual(0, code, err)
        self.assertTrue((self.other / ".agentcontinuity/ledger.sqlite3").exists())
        self.assertEqual(str(self.other), json.loads(out)["workspace"])

    def test_changing_source_blocks_import_after_compilation(self):
        session = self.native()
        def prepare_then_append(*args, **kwargs):
            packet = prepare_handoff(*args, **kwargs)
            self.append(session, {"type": "event_msg", "payload": {"type": "task_complete"}})
            return packet
        with patch("agentcontinuity.services.resume.prepare_handoff", side_effect=prepare_then_append):
            code, out, err = self.call("resume", "source-one", "--source", "codex", "--target", "claude", "--print")
        self.assertEqual(1, code)
        self.assertIn("Source is still changing", err)
