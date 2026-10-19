#!/usr/bin/env python3
"""Create the downloadable, dependency-free AgentHandoff release ZIP."""

import argparse
from pathlib import Path
import re
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from install import build_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/__init__.py").read_text()).group(1)
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"agenthandoff-{version}.zip"
    with tempfile.TemporaryDirectory(prefix="agenthandoff-release-") as temporary:
        app = Path(temporary) / "agenthandoff.pyz"
        build_app(ROOT, app)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            prefix = f"agenthandoff-{version}/"
            bundle.write(app, prefix + "agenthandoff.pyz")
            bundle.write(ROOT / "install.py", prefix + "install.py")
            if (ROOT / "LICENSE").exists():
                bundle.write(ROOT / "LICENSE", prefix + "LICENSE")
            bundle.writestr(prefix + "INSTALL.md",
                "# AgentHandoff\n\nRequires Python 3.11+, Git, Linux/macOS (or WSL), and your agent CLIs.\n\n"
                "Extract this ZIP, run `python3 install.py`, then `agenthandoff init`.\n"
                "Follow the installer's PATH instruction if needed. Keep the Python interpreter installed.\n\n"
                "Claude: `/agenthandoff`. Codex: `$agenthandoff` or `/skills`.\n"
                "Terminal: `agenthandoff sessions`, then `agenthandoff resume NUMBER --listing ID --target claude`.\n"
                "Session discovery works from any directory on this machine.\n"
                "The command inside an agent displays a numbered list in chat.\n"
                "When a selected session belongs to another project, use the returned terminal command\n"
                "to open the continuation there, or switch the host's workspace first.\n"
                "Run `agenthandoff init` again after installing another supported agent.\n"
                "Rerun this installer to update the executable, then rerun `agenthandoff init` to update skills.\n")
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
