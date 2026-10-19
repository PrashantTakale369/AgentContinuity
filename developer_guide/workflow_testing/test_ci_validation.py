"""Check that CI catches real failures and works without local artwork."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from developer_guide.ci_validation.checks import (
    CheckFailure, check_documentation, check_syntax, main, run,
)


class CIValidationTests(unittest.TestCase):
    def test_documentation_checks_work_without_ignored_logo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "developer_guide").mkdir()
            (root / "developer_guide/Readme.md").write_text("# Guide\n")
            (root / "README.md").write_text(
                '<img src="repo_img/image.png">\n[Guide](developer_guide/Readme.md)\n')
            self.assertEqual(1, check_documentation(root)["local_links"])

    def test_missing_documentation_link_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("[Missing guide](developer_guide/missing.md)\n")
            with self.assertRaisesRegex(CheckFailure, "Broken local link"):
                check_documentation(root)

    def test_invalid_python_fails_syntax_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "install.py").write_text("pass\n")
            (root / "agenthandoff").write_text("pass\n")
            (root / "src").mkdir()
            (root / "src/broken.py").write_text("result = (\n")
            with self.assertRaises(CheckFailure):
                check_syntax(root)

    def test_command_failure_is_not_reported_as_a_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(CheckFailure):
                run([sys.executable, "-B", "-c", "raise SystemExit(3)"],
                    cwd=Path(temporary), env=dict(os.environ))

    def test_failed_gate_returns_nonzero_and_saves_failure_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            with patch("developer_guide.ci_validation.checks.check_prerequisites",
                       side_effect=CheckFailure("Missing development tools")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["--report", str(report)])
            self.assertEqual(1, code)
            saved = json.loads(report.read_text())
            self.assertFalse(saved["passed"])
            self.assertEqual("Missing development tools", saved["error"])
