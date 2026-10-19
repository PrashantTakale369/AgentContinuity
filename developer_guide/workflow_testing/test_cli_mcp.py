import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from agentcontinuity.cli import main
from agentcontinuity.context.rendering import BOUNDARY
from agentcontinuity.integrations.mcp import handle, serve
from developer_guide.workflow_testing.support import WorkspaceCase


class CLIMCPTests(WorkspaceCase):
    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--cwd", str(self.repo), *args])
        return code, out.getvalue(), err.getvalue()

    def test_real_module_cli_help(self):
        run = subprocess.run([sys.executable, "-m", "agentcontinuity", "--help"],
                             cwd=self.base, capture_output=True, text=True)
        self.assertEqual(0, run.returncode, run.stderr)
        self.assertIn("agentcontinuity", run.stdout)
        self.assertIn("checkpoint", run.stdout)

    def test_doctor_reports_installed_tools_without_model_calls(self):
        with patch("agentcontinuity.services.health.load", return_value=(self.directory, self.config)), \
                patch("agentcontinuity.services.health.shutil.which", return_value="/example/claude"), \
                patch("agentcontinuity.services.health.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "2.1.301 (Claude Code)"
            code, out, err = self.call("doctor")
        self.assertEqual(0, code, err)
        self.assertEqual("AgentContinuity", json.loads(out)["product"])
        self.assertTrue(json.loads(out)["agents"]["claude"]["installed"])
        self.assertTrue(all(call.args[0][-1] == "--version" for call in run.call_args_list))

    def test_dry_run_saves_packet_and_never_launches(self):
        self.native()
        code, out, err = self.call("claude", "--dry-run")
        self.assertEqual(0, code, err)
        packet = json.loads(out[len(BOUNDARY):])
        self.assertEqual("claude", packet["target_agent"])
        self.assertEqual("continuity.v1", packet["protocol"])
        self.assertEqual(1, len(list((self.directory / "handoffs").glob("*.json"))))

    def test_real_cli_launches_a_fake_agent_in_the_correct_workspace(self):
        self.native()
        binary_dir = self.base / "bin"
        binary_dir.mkdir()
        binary = binary_dir / "claude"
        binary.write_text(f"#!{sys.executable}\nimport json, pathlib, sys\n"
                          "pathlib.Path('.agentcontinuity/launch.json').write_text(json.dumps(sys.argv[1:]))\n")
        binary.chmod(0o700)
        with patch.dict(os.environ, {"PATH": str(binary_dir) + os.pathsep + os.environ["PATH"]}):
            code, _, err = self.call("claude")
        self.assertEqual(0, code, err)
        args = json.loads((self.directory / "launch.json").read_text())
        self.assertEqual("--", args[0])
        self.assertTrue(args[1].startswith(BOUNDARY))

    def test_unknown_records_block_launch_but_allow_preview(self):
        self.native(extra=[{"type": "future_type"}])
        code, _, err = self.call("claude")
        self.assertEqual(1, code)
        self.assertIn("partial", err)
        self.assertEqual(0, self.call("claude", "--dry-run")[0])

    def test_ambiguous_sessions_fail_safely(self):
        self.native(session_id="first")
        self.native(session_id="second")
        code, _, err = self.call("claude", "--dry-run")
        self.assertEqual(1, code)
        self.assertIn("ambiguous", err)
        self.assertEqual(0, self.call("claude", "--session", "first", "--dry-run")[0])

    def test_checkpoint_and_new_task(self):
        code, out, err = self.call("checkpoint", "--objective", "Fix auth", "--constraint", "No dependencies", "--next", "Run expiry tests")
        self.assertEqual(0, code, err)
        old = json.loads(out)
        self.assertEqual("Run expiry tests", old["next_action"]["text"])
        code, out, err = self.call("task", "--new", "--objective", "Second task")
        self.assertEqual(0, code, err)
        self.assertNotEqual(old["id"], json.loads(out)["id"])

    def test_verified_mode_requires_explicit_command(self):
        self.native()
        code, _, err = self.call("claude", "--dry-run", "--mode", "verified")
        self.assertEqual(1, code)
        self.assertIn("explicit", err)

    def test_verified_mode_records_a_real_test_failure(self):
        self.native()
        code, out, err = self.call("claude", "--dry-run", "--mode", "verified", "--verify-command", "exit 4")
        self.assertEqual(0, code, err)
        self.assertEqual("tested_fail", json.loads(out[len(BOUNDARY):])["progress"][0]["status"])

    def test_status_and_search_cli(self):
        self.native()
        self.assertEqual(0, self.call("ingest", "--source", "codex")[0])
        code, out, err = self.call("search", "expiry")
        self.assertEqual(0, code, err)
        self.assertTrue(json.loads(out)["results"])
        self.assertTrue(json.loads(self.call("status")[1])["anchor_matches"])

    def test_mcp_initialize_tools_and_search_without_stdio_noise(self):
        self.native()
        self.call("ingest", "--source", "codex")
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "continuity_search", "arguments": {"query": "expiry"}}},
        ]
        incoming = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
        outgoing = io.StringIO()
        serve(self.directory, incoming, outgoing)
        responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(3, len(responses))
        self.assertEqual("AgentContinuity", responses[0]["result"]["serverInfo"]["name"])
        self.assertEqual(2, len(responses[1]["result"]["tools"]))
        self.assertTrue(json.loads(responses[2]["result"]["content"][0]["text"])["results"])

    def test_mcp_errors_are_jsonrpc_errors_or_tool_errors(self):
        outgoing = io.StringIO()
        serve(self.directory, io.StringIO('invalid\n{"jsonrpc":"2.0","id":1,"method":"delete"}\n'), outgoing)
        one, two = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(-32700, one["error"]["code"])
        self.assertEqual(-32601, two["error"]["code"])
        self.assertTrue(handle(self.ledger, "tools/call", {"name": "delete", "arguments": {}})["isError"])

    def test_mcp_rejects_wrong_argument_types(self):
        for args in ({"query": "expiry", "limit": "five"}, {"query": "expiry", "max_bytes": True}, {"query": 2}, {"query": "x", "extra": "bad"}):
            with self.subTest(args=args):
                result = handle(self.ledger, "tools/call", {"name": "continuity_search", "arguments": args})
                self.assertTrue(result["isError"])
