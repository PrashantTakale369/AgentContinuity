"""Run the offline workflow tests from any working directory."""

import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        import agentcontinuity
    except ImportError:
        print('Install the project in this Python environment first: python -m pip install -e ".[dev]"',
              file=sys.stderr)
        return 1
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_*.py")
    if suite.countTestCases() == 0:
        print("No workflow tests were discovered.", file=sys.stderr)
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
