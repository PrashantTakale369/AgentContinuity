"""Record the synthetic benchmark at three context limits for developer review."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import platform
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

from benchmarks.selection import BUDGETS, run


def update_documentation(results: dict) -> None:
    """Replace only the measured tables, keeping explanatory prose handwritten."""
    measured = datetime.fromisoformat(results["measured_at_utc"]).strftime("%d %B %Y")
    summary = [f"Measured on {measured} using Python {results['python']} on {results['platform']}.", "",
               "| Context limit | Cases meeting checks | Packets with all clues | Safe refusals | Median build time |",
               "| --- | --- | --- | --- | --- |"]
    for data in results["runs"]:
        row = data["summary"]
        expected_refusals = data["scenarios"]["oversized_requirements"]["cases"]
        summary.append(f"| {data['budget_bytes']:,} bytes | {row['checks_passed']} / {row['cases']} | "
                       f"{row['all_action_clues_retained']} / {row['packets_built']} | "
                       f"{row['safe_refusals']} / {expected_refusals} | {row['median_compile_ms']:.3f} ms |")
    selected = next(data for data in results["runs"] if data["budget_bytes"] == 24000)
    comparison = ["At a 24,000 byte limit:", "",
                  "| Scenario | Cases | AgentHandoff includes all clues | Newest first window includes all clues | Cases meeting checks |",
                  "| --- | --- | --- | --- | --- |"]
    for row in selected["scenarios"].values():
        action = f"{row['all_action_clues_retained']} / {row['packets_built']}" if row["packets_built"] else "Not applicable"
        recency = f"{row['all_recency_clues_retained']} / {row['packets_built']}" if row["packets_built"] else "Not applicable"
        comparison.append(f"| {row['description']} | {row['cases']} | {action} | {recency} | "
                          f"{row['checks_passed']} / {row['cases']} |")
    for relative, marker, lines in (("README.md", "benchmark-summary", summary),
                                     ("developer_guide/Readme.md", "benchmark-comparison", comparison)):
        path = ROOT / relative
        pattern = rf"<!-- {marker}:start -->.*?<!-- {marker}:end -->"
        replacement = f"<!-- {marker}:start -->\n" + "\n".join(lines) + f"\n<!-- {marker}:end -->"
        content, count = re.subn(pattern, lambda _: replacement, path.read_text(), flags=re.S)
        if count != 1:
            raise ValueError(f"Expected one {marker} section in {relative}.")
        path.write_text(content)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3, help="Repetitions of each scenario in each direction, from 1 to 10")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "benchmark_results.json")
    parser.add_argument("--update-docs", action="store_true", help="Refresh the measured tables in README and developer guide")
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("Use 1 to 10 repeats per scenario and direction.")
    # A stale editable install must not be measured while hashing a different checkout.
    for name in ("context.compiler", "context.rendering", "context.projection", "agents.codex", "agents.claude"):
        installed = Path(importlib.import_module("agentcontinuity." + name).__file__)
        checkout = ROOT / "src" / (name.replace(".", "/") + ".py")
        if installed.read_bytes() != checkout.read_bytes():
            parser.error('Installed implementation differs from this checkout. Run python -m pip install -e ".[dev]" first.')
    results = {"measured_at_utc": datetime.now(timezone.utc).isoformat(),
               "python": platform.python_version(), "platform": platform.system() + " " + platform.machine(),
               "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                 for name in ("benchmarks/selection.py", "src/context/compiler.py",
                                              "src/context/rendering.py", "src/context/projection.py",
                                              "src/agents/codex.py", "src/agents/claude.py",
                                              "developer_guide/workflow_testing/benchmark.py")},
               "scope": "Synthetic handoff correctness and evidence retention; no live models or task completion measurements.",
               "runs": []}
    for budget in BUDGETS:
        data = run(repeats=args.repeats, budget=budget)
        results["runs"].append(data)
        print(json.dumps({"budget_bytes": budget, **data["summary"]}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(f"Saved results: {args.output}")
    passed = all(data["summary"]["checks_passed"] == data["summary"]["cases"] for data in results["runs"])
    if args.update_docs and passed:
        update_documentation(results)
        print("Updated the measured README and developer guide tables.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
