"""Measure synthetic handoff correctness and evidence selection at equal byte limits."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shlex
import statistics
import subprocess
import sys
import tempfile
import time

from agentcontinuity.agents import AGENTS
from agentcontinuity.context.compiler import compile_packet
from agentcontinuity.context.projection import project
from agentcontinuity.context.rendering import render
from agentcontinuity.core.errors import ContinuityError
from agentcontinuity.core.security import excerpt
from agentcontinuity.services.capture import capture
from agentcontinuity.services.verification import verify
from agentcontinuity.storage.ledger import Ledger
from agentcontinuity.workspace.config import initialize
from agentcontinuity.workspace.git import observe

BUDGETS = (6000, 24000, 80000)
SCENARIOS = {
    "early_failure": "Failure before other tool output",
    "middle_failure": "Failure in the middle of tool output",
    "recent_failure": "Failure at the end of tool output",
    "two_file_investigation": "Two clues from different files",
    "unverified_claim": "Assistant claims tests passed",
    "stale_verification": "Project edited after a passing test",
    "long_tool_output": "Clue beyond the excerpt limit",
    "oversized_requirements": "Requirements exceed the byte limit",
}
REQUIREMENTS = ("Fix authentication expiry without changing the public API.",
                "Keep session storage on this machine.")


def measure(packet: dict) -> int:
    """Include the boundary, metadata, and size fields in the UTF-8 byte count."""
    for _ in range(12):
        size = len(render(packet).encode("utf-8"))
        if packet["budget"]["used_bytes"] == size:
            return size
        packet["budget"]["used_bytes"] = size
        packet["budget"]["estimated_tokens"] = (size + 3) // 4
    raise RuntimeError("Packet byte count did not converge.")


def newest_messages(packet: dict, events: list[dict], budget: int) -> dict:
    """Use the same mandatory state and excerpt size, changing only evidence order."""
    baseline = deepcopy(packet)
    baseline["evidence"] = []
    refs = {ref for item in [baseline["objective"], *baseline["constraints"], *baseline["progress"]]
            for ref in item["evidence"]}
    included = {event["id"] for event in events if event["id"] in refs}

    def update_manifest():
        baseline["manifest"]["included_events"] = len(included)
        baseline["manifest"]["omitted_events"] = len(events) - len(included)

    update_manifest()
    measure(baseline)
    for event in reversed(events):
        if event["kind"] == "metadata" or event["id"] in included:
            continue
        text = excerpt(event["text"], 1800)
        baseline["evidence"].append({"id": event["id"], "kind": event["kind"], "text": text,
                                     "truncated": text != event["text"],
                                     "trust": "untrusted_historical_evidence"})
        included.add(event["id"])
        update_manifest()
        if measure(baseline) > budget:
            baseline["evidence"].pop()
            included.remove(event["id"])
            update_manifest()
            measure(baseline)
            break
    return baseline


def native_records(agent: str, repo: Path, session_id: str, messages: list[tuple[str, str]]) -> list[dict]:
    if agent == "codex":
        records = [{"type": "session_meta", "payload": {"id": session_id, "cwd": str(repo)}}]
        for kind, text in messages:
            subtype = {"user": "user_message", "assistant": "agent_message"}.get(kind)
            records.append({"type": "event_msg", "payload": {"type": subtype, "message": text}}
                           if subtype else {"type": "response_item", "payload": {
                               "type": "function_call_output", "output": text}})
        return records
    return [{"type": "user" if kind == "tool_result" else kind,
             "sessionId": session_id, "cwd": str(repo),
             "message": {"role": "user" if kind == "tool_result" else kind,
                         "content": [{"type": "tool_result", "tool_use_id": f"tool-{number}",
                                      "content": text}] if kind == "tool_result" else text}}
            for number, (kind, text) in enumerate(messages)]


def fixture(scenario: str, repeat: int, budget: int) -> tuple[list[tuple[str, str]], list[str], str]:
    # Three deterministic history sizes, rather than copies of one long transcript.
    count = (12, 40, 80)[repeat % 3]
    noise = [("tool_result", f"docs/readme.md inspection {number}: " +
              "Documentation index and build cache unchanged. " * (8 + number % 4))
             for number in range(count)]
    clues = [f"EXPIRY_OFFSET_CASE_{repeat}"]
    failure = ("tool_result", f"auth.py test_expiry_timezone FAILED: {clues[0]}; "
               "expected token rejection, received accepted token; UTC offset was ignored.")
    action = "Investigate auth.py expiry timezone failure"
    if scenario == "middle_failure":
        noise.insert(count // 2, failure)
    elif scenario == "recent_failure":
        noise.append(failure)
    elif scenario == "two_file_investigation":
        clues.append(f"RETRY_LIMIT_CASE_{repeat}")
        noise.insert(0, failure)
        noise.insert(count // 2, ("tool_result", f"client.py retry test FAILED: {clues[1]}; "
                                 "expected one attempt, received three."))
        action = "Investigate auth.py expiry timezone failure and client.py retry limit"
    elif scenario == "long_tool_output":
        noise.append(("tool_result", "auth.py expiry trace\n" + "trace detail without a conclusion\n" * 160 +
                      f"\nFinal assertion FAILED: {clues[0]}; UTC offset was ignored."))
    else:
        noise.insert(0, failure)
    if scenario == "unverified_claim":
        noise.append(("assistant", "The implementation is complete and all tests passed."))
    if scenario == "oversized_requirements":
        requirement = "Keep every requirement. "
        noise.append(("user", "Required interface specification: " + requirement * (budget // len(requirement) + 1)))
    return [("user", REQUIREMENTS[0]), ("user", REQUIREMENTS[1]), *noise], clues, action


def run_case(base: Path, scenario: str, agent: str, repeat: int, budget: int) -> dict:
    case_id = f"{scenario}-{agent}-{repeat}"
    repo = base / case_id
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--initial-branch=main", str(repo)], check=True)
    (repo / "auth.py").write_text("# Synthetic authentication workspace\n")
    if scenario == "two_file_investigation":
        (repo / "client.py").write_text("# Synthetic request client\n")
    source = base / (case_id + "-sessions")
    source.mkdir()
    directory, _ = initialize(repo, roots={name: str(source if name == agent else base / "unused")
                                          for name in ("codex", "claude")})
    messages, clues, action = fixture(scenario, repeat, budget)
    records = native_records(agent, repo, case_id, messages)
    (source / "session.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records))
    snapshot = observe(repo)
    target = "claude" if agent == "codex" else "codex"
    with Ledger(directory) as ledger:
        ledger.new_task(snapshot)
        session = AGENTS[agent].sessions(source, repo)[0][0]
        captured = capture(ledger, AGENTS[agent], session)
        project(ledger, snapshot)
        if scenario == "stale_verification":
            # Execute a known local command, then change the project; never invent a test pass.
            verify(ledger, repo, shlex.join([sys.executable, "-B", "-c", "raise SystemExit(0)"]))
            (repo / "auth.py").write_text("# Edited after verification\n")
            snapshot = observe(repo)
        ledger.checkpoint(snapshot, next_action=action)
        events = ledger.events()
        started = time.perf_counter()
        try:
            packet = compile_packet(ledger, snapshot, captured, target, max_bytes=budget)
        except ContinuityError as exc:
            if scenario != "oversized_requirements" or "no requirement was silently dropped" not in str(exc):
                raise
            return {"id": case_id, "scenario": scenario, "source": agent, "target": target,
                    "outcome": "refused_oversized_requirements", "checks": {"safe_refusal": True},
                    "compile_ms": round((time.perf_counter() - started) * 1000, 3),
                    "retained_events": len(events), "refusal": str(exc)}
        elapsed = (time.perf_counter() - started) * 1000
        baseline = newest_messages(packet, events, budget)
        rendered, recent = render(packet), render(baseline)
        requirement_texts = [packet["objective"]["text"], *[item["text"] for item in packet["constraints"]]]
        action_covered = [clue for clue in clues if clue in rendered]
        recency_covered = [clue for clue in clues if clue in recent]
        clue_events = [event for event in events if any(clue in event["text"] for clue in clues)]
        retrieved = "\n".join(ledger.retrieve(event["id"], max_bytes=16000)["content"] for event in clue_events)
        observed = [item for item in packet["progress"] if item["origin"] == "observed_command"]
        checks = {
            "capture_complete": captured["complete"],
            "requirements_preserved": all(text in requirement_texts for text in REQUIREMENTS),
            "correct_direction": packet["source_agent"] == agent and packet["target_agent"] == target,
            "exact_byte_count": packet["budget"]["used_bytes"] == len(rendered.encode("utf-8")),
            "within_budget": len(rendered.encode("utf-8")) <= budget and len(recent.encode("utf-8")) <= budget,
            "evidence_retrievable": bool(clue_events) and all(clue in retrieved for clue in clues),
            "manifest_consistent": all(p["manifest"]["retained_events"] ==
                                       p["manifest"]["included_events"] + p["manifest"]["omitted_events"]
                                       for p in (packet, baseline)),
        }
        if scenario != "long_tool_output":
            checks["expected_inline_clues"] = len(action_covered) == len(clues)
        if scenario == "stale_verification":
            checks["stale_verification_identified"] = len(observed) == 1 and observed[0]["status"] == "stale"
        else:
            checks["no_invented_verification"] = not observed
        if scenario == "unverified_claim":
            checks["claim_remains_unverified"] = any(item["origin"] == "model_declaration" and
                                                       item["status"] == "implemented_unverified"
                                                       for item in packet["progress"])
        if scenario == "oversized_requirements":
            checks["safe_refusal"] = False
        return {"id": case_id, "scenario": scenario, "source": agent, "target": target,
                "outcome": "packet_built", "checks": checks, "required_clues": clues,
                "action_clues_retained": action_covered, "recency_clues_retained": recency_covered,
                "all_action_clues_retained": len(action_covered) == len(clues),
                "all_recency_clues_retained": len(recency_covered) == len(clues),
                "prompt_bytes": len(rendered.encode("utf-8")), "recency_prompt_bytes": len(recent.encode("utf-8")),
                "compile_ms": round(elapsed, 3), "retained_events": len(events),
                "truncated_evidence": sum(entry["truncated"] for entry in packet["evidence"])}


def summarize(rows: list[dict]) -> dict:
    packets = [row for row in rows if row["outcome"] == "packet_built"]
    return {"cases": len(rows), "checks_passed": sum(all(row["checks"].values()) for row in rows),
            "packets_built": len(packets),
            "safe_refusals": sum(row["outcome"] == "refused_oversized_requirements" for row in rows),
            "all_action_clues_retained": sum(row["all_action_clues_retained"] for row in packets),
            "all_recency_clues_retained": sum(row["all_recency_clues_retained"] for row in packets),
            "median_compile_ms": round(statistics.median(row["compile_ms"] for row in packets), 3) if packets else None,
            "max_prompt_bytes": max((row["prompt_bytes"] for row in packets), default=0)}


def run(repeats: int = 3, budget: int = 24000) -> dict:
    if not 1 <= repeats <= 10 or not 6000 <= budget <= 1_000_000:
        raise ValueError("Use 1 to 10 repeats and a byte limit from 6000 to 1000000.")
    rows = []
    with tempfile.TemporaryDirectory(prefix="agentcontinuity-benchmark-") as temporary:
        for scenario in SCENARIOS:
            for agent in ("codex", "claude"):
                for repeat in range(repeats):
                    rows.append(run_case(Path(temporary), scenario, agent, repeat, budget))
    return {"benchmark": "synthetic-handoff.v2", "repeats_per_direction": repeats, "budget_bytes": budget,
            "history_sizes": [12, 40, 80][:min(repeats, 3)],
            "recency_baseline": "Same mandatory state and 1800-byte excerpts; contiguous newest-first optional evidence",
            "summary": summarize(rows),
            "scenarios": {name: {"description": description, **summarize([r for r in rows if r["scenario"] == name])}
                          for name, description in SCENARIOS.items()},
            "limitation": "Deterministic synthetic fixtures, not real coding task success or model quality. "
                          "Clue retention is measured separately from correctness. Long output requires full evidence retrieval.",
            "cases": rows}


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repeats", type=int, default=3, help="Repetitions of each scenario in each direction")
    cli.add_argument("--budget-bytes", type=int, default=24000)
    args = cli.parse_args()
    if not 1 <= args.repeats <= 10 or not 6000 <= args.budget_bytes <= 1_000_000:
        cli.error("Use 1 to 10 repeats and a byte limit from 6000 to 1000000.")
    report = run(args.repeats, args.budget_bytes)
    print(json.dumps(report, indent=2))
    return 0 if report["summary"]["checks_passed"] == report["summary"]["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
