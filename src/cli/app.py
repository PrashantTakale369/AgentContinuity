"""Process entry point and consistent exit/error handling."""

from __future__ import annotations

import sqlite3
import sys

from ..core.errors import ContinuityError
from .commands import execute
from .parser import parser


def main(argv: list[str] | None = None, *, global_mode: bool = False) -> int:
    arguments = parser(global_mode=global_mode)
    args = arguments.parse_args(argv)
    try:
        if global_mode:
            from .global_commands import execute_global
            result = execute_global(args)
            if result is not None:
                return result
        return execute(args)
    except (ContinuityError, OSError, sqlite3.Error) as exc:
        print(f"{arguments.prog}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{arguments.prog}: interrupted; committed evidence is retained.", file=sys.stderr)
        return 130


def handoff_main(argv: list[str] | None = None) -> int:
    return main(argv, global_mode=True)
