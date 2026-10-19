"""Run the same offline validation locally and in GitHub Actions."""

import argparse
import ast
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True


class CheckFailure(Exception):
    """A failed validation step with an actionable message."""


def check_syntax(root: Path) -> dict:
    paths = [root / "install.py", root / "agenthandoff"]
    for folder in ("src", "benchmarks", "scripts", "developer_guide"):
        paths.extend((root / folder).rglob("*.py"))
    for path in paths:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            raise CheckFailure(str(exc)) from exc
    return {"python_files": len(paths)}


def check_documentation(root: Path) -> dict:
    paths = [root / "README.md", *(root / "developer_guide").rglob("*.md")]
    links = 0
    for path in paths:
        text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
        targets = re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", text)
        targets.extend(re.findall(r'src="([^"]+)"', text))
        for target in targets:
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = (path.parent / unquote(parsed.path)).resolve()
            # Artwork stays local and is intentionally absent from clean clones.
            if resolved.is_relative_to((root / "repo_img").resolve()):
                continue
            if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                raise CheckFailure(f"Broken local link in {path.relative_to(root)}: {target}")
            links += 1
    return {"markdown_files": len(paths), "local_links": links}


def run(command: list, *, cwd: Path, env: dict, timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise CheckFailure(f"Command exceeded {timeout} seconds: {command[0]}") from exc
    if result.returncode:
        raise CheckFailure(" ".join(map(str, command)) + "\n" + result.stdout + result.stderr)
    return result


def check_prerequisites() -> dict:
    if sys.version_info < (3, 11) or os.name != "posix":
        raise CheckFailure("Use Python 3.11+ on Linux, macOS, or WSL.")
    if not shutil.which("git"):
        raise CheckFailure("Git is required for the workflow tests.")
    try:
        setuptools = importlib.metadata.version("setuptools")
        wheel = importlib.metadata.version("wheel")
    except importlib.metadata.PackageNotFoundError as exc:
        raise CheckFailure('Install development tools first: python -m pip install -e ".[dev]"') from exc
    if int(setuptools.split(".")[0]) < 68:
        raise CheckFailure("setuptools 68 or later is required.")
    return {"python": sys.version.split()[0], "setuptools": setuptools, "wheel": wheel}


def build_install(root: Path, base: Path, env: dict) -> Path:
    source = base / "source"
    source.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(root / name, source / name)
    shutil.copytree(root / "src", source / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
    wheels = base / "wheels"
    run([sys.executable, "-B", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--no-index", "--wheel-dir", wheels, source], cwd=base, env=env)
    run([sys.executable, "-B", "-m", "venv", base / "venv"], cwd=base, env=env)
    python = base / "venv/bin/python"
    run([python, "-B", "-m", "pip", "install", "--no-index", "--no-deps",
         next(wheels.glob("*.whl"))], cwd=base, env=env)
    return python


def check_installed_cli(python: Path, base: Path, env: dict) -> dict:
    for command in ("agenthandoff", "agentcontinuity"):
        run([python.parent / command, "--version"], cwd=base, env=env)
    run([python, "-B", "-m", "agentcontinuity.handoff", "--help"], cwd=base, env=env)
    run([python, "-B", "-c", "from importlib.resources import files; "
         "assert files('agentcontinuity').joinpath('integrations/agenthandoff/SKILL.md').is_file()"],
        cwd=base, env=env)
    return {"commands": ["agenthandoff", "agentcontinuity"], "packaged_skill": True}


def check_workflows(python: Path, root: Path, env: dict) -> dict:
    result = run([python, "-B", root / "developer_guide/workflow_testing/run.py"], cwd=root, env=env)
    match = re.search(r"Ran (\d+) tests?", result.stderr)
    if not match or int(match.group(1)) == 0:
        raise CheckFailure("Workflow runner completed without reporting any tests.")
    return {"tests_passed": int(match.group(1))}


def check_benchmarks(python: Path, root: Path, env: dict) -> dict:
    rows = []
    for budget in (6000, 24000, 80000):
        result = run([python, "-B", "-m", "benchmarks.selection", "--repeats", "1",
                      "--budget-bytes", str(budget)], cwd=root, env=env)
        data = json.loads(result.stdout)
        cases = data["cases"]
        if len(cases) != 16 or data["summary"]["checks_passed"] != len(cases):
            raise CheckFailure("Synthetic handoff checks failed or scenarios are missing.")
        if any(not case["checks"] or not all(case["checks"].values()) for case in cases):
            raise CheckFailure("A synthetic handoff violated its correctness checks.")
        rows.append({"budget_bytes": budget, **data["summary"]})
    return {"synthetic_runs": rows, "timing_threshold": None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Optionally save a JSON validation report")
    args = parser.parse_args(argv)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    env.pop("PYTHONPATH", None)
    report = {"passed": False, "checks": []}

    def step(name, function):
        print(f"Checking {name}...", flush=True)
        started = time.monotonic()
        detail = function()
        report["checks"].append({"name": name, "passed": True,
                                  "seconds": round(time.monotonic() - started, 3), "details": detail})
        print(f"PASS: {name}" + (f" ({detail['tests_passed']} tests)" if isinstance(detail, dict) and "tests_passed" in detail else ""), flush=True)
        return detail

    try:
        step("development tools", check_prerequisites)
        step("Python syntax", lambda: check_syntax(ROOT))
        step("documentation links", lambda: check_documentation(ROOT))
        step("Git whitespace", lambda: {"clean": run(["git", "diff", "--check"], cwd=ROOT, env=env).returncode == 0})
        with tempfile.TemporaryDirectory(prefix="agenthandoff-ci-") as temporary:
            base = Path(temporary)
            python = step("wheel build and isolated installation", lambda: build_install(ROOT, base, env))
            # Paths are temporary implementation details, not retained report data.
            report["checks"][-1]["details"] = {"installed_in_temporary_environment": True}
            step("installed commands and skill resource", lambda: check_installed_cli(python, base, env))
            step("workflow tests including release ZIP installation", lambda: check_workflows(python, ROOT, env))
            step("synthetic handoff correctness and evidence retention", lambda: check_benchmarks(python, ROOT, env))
        report["passed"] = True
        print("All CI validation checks passed.")
    except (CheckFailure, OSError, ValueError, StopIteration) as exc:
        report["error"] = str(exc)
        print("FAIL: " + str(exc), file=sys.stderr)
    finally:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
