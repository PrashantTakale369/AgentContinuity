"""Global agenthandoff CLI, also usable through python -m."""

from .cli.app import handoff_main

if __name__ == "__main__":
    raise SystemExit(handoff_main())
