"""Run the downloadable ZIP installer with a temporary user profile."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]


class InstallTests(unittest.TestCase):
    def test_release_zip_runs_after_extracted_download_is_removed(self):
        repo = ROOT
        with tempfile.TemporaryDirectory(prefix="agenthandoff-install-") as temporary:
            base = Path(temporary)
            env = {**os.environ, "HOME": str(base / "home"), "AGENTHANDOFF_HOME": str(base / "global"),
                   "CLAUDE_CONFIG_DIR": str(base / "home/.claude"), "CODEX_HOME": str(base / "home/.codex"),
                   "PYTHONDONTWRITEBYTECODE": "1"}
            def run(args, cwd=base):
                result = subprocess.run(list(map(str, args)), cwd=cwd, env=env, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr + result.stdout)
                return result
            run([sys.executable, "-B", repo / "scripts/build_release.py", "--output", base / "dist"])
            archive = next((base / "dist").glob("*.zip"))
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(base / "download")
            source = next((base / "download").iterdir())
            prefix = base / "local install"
            run([sys.executable, "-B", source / "install.py", "--prefix", prefix])
            # Installer updates must remain safe and repeatable.
            run([sys.executable, "-B", source / "install.py", "--prefix", prefix])
            shutil.rmtree(base / "download")
            cli = prefix / "bin/agenthandoff"
            self.assertIn("0.1.0", run([cli, "--version"]).stdout)
            report = json.loads(run([cli, "init", "--json"]).stdout)
            self.assertEqual(str(base / "global"), report["storage"])
            sessions = json.loads(run([cli, "sessions", "--json"]).stdout)
            self.assertEqual([], sessions["sessions"])
            for name, entry in report["agents"].items():
                if entry["status"] == "installed":
                    skill = Path(entry["path"]).read_text()
                    self.assertIn("agenthandoff.pyz", skill)
                    self.assertNotIn("{{CLI}}", skill)
                    self.assertNotIn("{{TARGET}}", skill)

    def test_installer_preserves_existing_unrelated_executable(self):
        repo = ROOT
        with tempfile.TemporaryDirectory(prefix="agenthandoff-install-conflict-") as temporary:
            prefix = Path(temporary)
            binary = prefix / "bin/agenthandoff"
            binary.parent.mkdir()
            binary.write_text("User executable\n")
            run = subprocess.run([sys.executable, "-B", str(repo / "install.py"), "--prefix", str(prefix)],
                                 capture_output=True, text=True)
            self.assertEqual(1, run.returncode)
            self.assertIn("Existing executable preserved", run.stderr)
            self.assertEqual("User executable\n", binary.read_text())

    def test_installer_preserves_existing_global_configuration(self):
        repo = ROOT
        with tempfile.TemporaryDirectory(prefix="agenthandoff-install-config-") as temporary:
            prefix = Path(temporary)
            config = prefix / "share/agenthandoff/config.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"version": 1, "roots": {}}\n')
            before = config.read_bytes()
            run = subprocess.run([sys.executable, "-B", str(repo / "install.py"), "--prefix", str(prefix)],
                                 capture_output=True, text=True)
            self.assertEqual(0, run.returncode, run.stderr)
            self.assertEqual(before, config.read_bytes())

    def test_installer_refuses_symlinked_configuration_marker(self):
        repo = ROOT
        with tempfile.TemporaryDirectory(prefix="agenthandoff-install-symlink-") as temporary:
            prefix = Path(temporary)
            victim = prefix / "preserve.txt"
            victim.write_text("Preserve this file\n")
            directory = prefix / "share/agenthandoff"
            directory.mkdir(parents=True)
            (directory / "installer.json").symlink_to(victim)
            run = subprocess.run([sys.executable, "-B", str(repo / "install.py"), "--prefix", str(prefix)],
                                 capture_output=True, text=True)
            self.assertEqual(1, run.returncode)
            self.assertIn("Refusing symlinked install file", run.stderr)
            self.assertEqual("Preserve this file\n", victim.read_text())
