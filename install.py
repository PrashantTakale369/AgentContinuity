#!/usr/bin/env python3
"""Install the source ZIP or release ZIP without pip or third-party packages."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import zipapp

sys.dont_write_bytecode = True
MARKER = "# Managed by AgentHandoff installer"


def build_app(source: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="agenthandoff-bundle-") as temporary:
        staging = Path(temporary)
        shutil.copytree(source / "src", staging / "agentcontinuity",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
        (staging / "__main__.py").write_text(
            "import sys\nsys.dont_write_bytecode = True\n"
            "from agentcontinuity.cli.app import handoff_main\nraise SystemExit(handoff_main())\n")
        zipapp.create_archive(staging, destination, compressed=True)


def install(prefix: Path, source: Path) -> Path:
    prefix = prefix.expanduser().absolute()
    app_directory = prefix / "share/agenthandoff"
    binary_directory = prefix / "bin"
    executable = binary_directory / "agenthandoff"
    for path in (executable, app_directory / "agenthandoff.pyz", app_directory / "installer.json"):
        if path.is_symlink():
            raise ValueError(f"Refusing symlinked install file: {path}")
    for path in (app_directory, binary_directory):
        for parent in (path, *path.parents):
            if parent.is_symlink():
                raise ValueError(f"Refusing symlinked install path: {parent}")
    if executable.exists() and MARKER not in executable.read_text():
        raise ValueError(f"Existing executable preserved: {executable}")
    if (app_directory / "agenthandoff.pyz").exists() and not (app_directory / "installer.json").is_file():
        raise ValueError(f"Existing directory preserved: {app_directory}")
    app_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(suffix=".pyz", dir=app_directory)
    os.close(fd)
    staging = Path(name)
    try:
        bundled = source / "agenthandoff.pyz"
        if bundled.is_file():
            shutil.copyfile(bundled, staging)
        elif (source / "src").is_dir():
            build_app(source, staging)
        else:
            raise ValueError("Extract the complete ZIP before running install.py.")
        staging.chmod(0o600)
        app = app_directory / "agenthandoff.pyz"
        os.replace(staging, app)
    finally:
        staging.unlink(missing_ok=True)
    # Avoid shebang restrictions when the interpreter path contains spaces.
    command = shlex.join([sys.executable, "-B", str(app)])
    launcher = "#!/bin/sh\n" + MARKER + "\nexec " + command + ' "$@"\n'
    fd, name = tempfile.mkstemp(dir=binary_directory)
    with os.fdopen(fd, "w") as stream:
        stream.write(launcher)
    os.chmod(name, 0o700)
    os.replace(name, executable)
    (app_directory / "installer.json").write_text(json.dumps({"version": 1, "python": sys.executable}) + "\n")
    return executable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required.")
    if os.name != "posix":
        parser.error("This release supports Linux and macOS; on Windows use WSL.")
    try:
        executable = install(args.prefix, Path(__file__).resolve().parent)
    except (OSError, ValueError) as exc:
        print(f"AgentHandoff installation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed: {executable}")
    if str(executable.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print("Add the executable directory to your shell PATH:")
        print("  export PATH=" + shlex.quote(str(executable.parent)) + ':"$PATH"')
    print("Next: agenthandoff init")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
