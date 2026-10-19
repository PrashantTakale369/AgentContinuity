"""Exercise a contributor's new file in a fresh process, without registry edits."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import agentcontinuity
from agentcontinuity.context.rendering import BOUNDARY


class AgentExtensionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="agentcontinuity-extension-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.code = self.base / "code"
        self.package = self.code / "agentcontinuity"
        shutil.copytree(Path(agentcontinuity.__file__).parent, self.package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.fixture = (Path(__file__).parent / "fixtures/example_agent.py").read_text()
        (self.package / "agents/example.py").write_text(self.fixture)
        self.repo = self.base / "workspace"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "--initial-branch=main", str(self.repo)], check=True)
        self.codex = self.base / "codex"
        self.codex.mkdir()
        self.example = self.base / "example/sessions"
        self.example.mkdir(parents=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        executable = self.bin / "example-cli"
        executable.write_text(
            f"#!{sys.executable}\nimport json, pathlib, sys\n"
            "if sys.argv[1:] == ['--version']:\n    print('example 1.0')\n"
            "else:\n    pathlib.Path('.agentcontinuity/launched.json').write_text(json.dumps(sys.argv[1:]))\n")
        executable.chmod(0o700)
        self.env = {**os.environ, "PYTHONPATH": str(self.code), "PYTHONDONTWRITEBYTECODE": "1",
                    "EXAMPLE_HOME": str(self.example.parent),
                    "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}

    def call(self, *args, success=True):
        run = subprocess.run([sys.executable, "-B", "-m", "agentcontinuity", "--cwd", str(self.repo), *args],
                             cwd=self.base, env=self.env, capture_output=True, text=True)
        if success:
            self.assertEqual(0, run.returncode, run.stderr)
        else:
            self.assertNotEqual(0, run.returncode)
        return run

    def initialize(self, *args):
        self.call("init", "--objective", "Fix expiry", "--codex-root", str(self.codex),
                  "--claude-root", str(self.base / "unused"), *args)

    def example_session(self, root=None):
        root = root or self.example
        root.mkdir(parents=True, exist_ok=True)
        records = [{"type": "session", "id": "example-one", "cwd": str(self.repo)},
                   {"type": "user", "text": "Preserve the public API"},
                   {"type": "tool_result", "text": "expiry test FAILED"}]
        (root / "one.jsonl").write_text("".join(json.dumps(item) + "\n" for item in records))

    def test_new_file_adds_cli_options_and_doctor_executable(self):
        self.assertIn("example", self.call("--help").stdout)
        self.assertIn("--example-root", self.call("init", "--help").stdout)
        report = json.loads(self.call("doctor").stdout)
        self.assertEqual(str(self.bin / "example-cli"), report["agents"]["example"]["executable"])
        self.assertEqual("example 1.0", report["agents"]["example"]["version"])

    def test_new_agent_captures_hands_off_and_launches(self):
        custom_root = self.base / "custom-sessions"
        self.example_session(custom_root)
        self.initialize("--example-root", str(custom_root))
        sessions = json.loads(self.call("sessions", "--source", "example").stdout)["sessions"]
        self.assertEqual(["example-one"], [session["id"] for session in sessions])
        self.assertTrue(json.loads(self.call("ingest", "--source", "example").stdout)["complete"])
        preview = self.call("codex", "--source", "example", "--dry-run").stdout
        packet = json.loads(preview[len(BOUNDARY):])
        self.assertEqual("example", packet["source_agent"])
        self.assertIn("Preserve the public API", [item["text"] for item in packet["constraints"]])
        records = [{"type": "session_meta", "payload": {"id": "codex-one", "cwd": str(self.repo)}},
                   {"type": "event_msg", "payload": {"type": "user_message", "message": "Fix expiry"}}]
        (self.codex / "one.jsonl").write_text("".join(json.dumps(item) + "\n" for item in records))
        self.call("example", "--source", "codex")
        args = json.loads((self.repo / ".agentcontinuity/launched.json").read_text())
        self.assertEqual("--prompt", args[0])
        self.assertEqual("example", json.loads(args[1][len(BOUNDARY):])["target_agent"])

    def test_multiple_source_choices_and_self_handoff_are_explicit(self):
        self.initialize()
        error = self.call("example", "--dry-run", success=False).stderr
        self.assertIn("--source", error)
        self.assertIn("claude, codex", error)
        self.assertIn("Source and target must differ",
                      self.call("example", "--source", "example", success=False).stderr)

    def test_existing_config_accepts_added_and_removed_agents_without_rewrite(self):
        self.initialize()
        config_path = self.repo / ".agentcontinuity/config.json"
        config = json.loads(config_path.read_text())
        del config["roots"]["example"]
        config["roots"]["retired_agent"] = str(self.base / "old-sessions")
        config_path.write_text(json.dumps(config))
        before = config_path.read_bytes()
        self.example_session()
        sessions = json.loads(self.call("sessions", "--source", "example").stdout)["sessions"]
        self.assertEqual("example-one", sessions[0]["id"])
        self.assertEqual(before, config_path.read_bytes())

    def test_invalid_integration_exports_are_rejected(self):
        plugin = self.package / "agents/example.py"
        for source, message in (("AGENT = object()\n", "must export AGENT"),
                                (self.fixture.replace('name = "example"', 'name = "other"'), "Invalid agent name")):
            with self.subTest(message=message):
                plugin.write_text(source)
                self.assertIn(message, self.call("--help", success=False).stderr)
