"""Deterministic next-action context selection with explicit coverage limits."""

from __future__ import annotations

import re
from uuid import uuid4

from ..core.errors import ContinuityError
from ..core.security import excerpt
from ..storage.ledger import Ledger
from ..workspace.git import now
from .projection import assertion
from .rendering import render


def compile_packet(ledger: Ledger, snapshot: dict, capture: dict, target: str, *,
                   max_bytes: int = 24000, mode: str = "fast", workspace_evidence: str = "") -> dict:
    if not 4096 <= max_bytes <= 1_000_000:
        raise ContinuityError("Packet budget must be between 4096 and 1000000 UTF-8 bytes.")
    state = ledger.task()
    if state["objective"] is None:
        raise ContinuityError("Task objective is unknown. Set it with 'agentcontinuity checkpoint --objective TEXT'.")
    events = ledger.events()
    requirements = list(state["constraints"])
    seen = {item["text"] for item in requirements}
    seen.add(state["objective"]["text"])
    for event in events:
        if event["kind"] == "user" and event["text"].strip() and event["text"] not in seen:
            requirements.append(assertion(event["text"], [event["id"]], snapshot["fingerprint"]))
            seen.add(event["text"])
    next_action = state["next_action"] or assertion(
        "Inspect the latest user requirements and workspace changes; confirm unfinished work before editing. "
        "No semantic next-action summary was supplied.", [], snapshot["fingerprint"], status="unknown", origin="fallback")
    warnings = list(capture["warnings"])
    warnings.append("Only accessible native JSONL records are indexed; hidden reasoning, native databases, and uncaptured sessions are outside coverage.")
    if state["next_action"] is None:
        warnings.append("Next action is unknown. Supply 'agentcontinuity checkpoint --next TEXT' for action-specific selection.")
    decisions = []
    for item in state["decisions"]:
        fresh = item["fingerprint"] == snapshot["fingerprint"]
        decisions.append({**item, "status": item["status"] if fresh else "stale"})
    progress = []
    verifications = ledger.observations("verification")
    if verifications:
        record = verifications[-1]
        stable = record["data"]["stable_workspace"]
        fresh = record["fingerprint"] == snapshot["fingerprint"]
        status = "unknown" if not stable else ("tested_pass" if record["data"]["exit_code"] == 0 else "tested_fail")
        if not fresh:
            status = "stale"
        progress.append(assertion(
            "Explicit verification command: " + excerpt(record["data"]["command"], 1000),
            [record["id"]], record["fingerprint"], status=status, origin="observed_command"))
    packet = {
        "protocol": "continuity.v1", "id": "handoff-" + str(uuid4()), "created_at": now(),
        "task_id": state["id"], "state_version": state["version"], "mode": mode,
        "source_agent": capture["session"]["agent"], "target_agent": target,
        "repository": {key: value for key, value in snapshot.items() if key != "diff"},
        "workspace_evidence": workspace_evidence,
        "objective": state["objective"], "constraints": requirements, "decisions": decisions,
        "next_action": next_action, "progress": progress, "capture": capture,
        "evidence": [],
        "manifest": {"retained_events": len(events), "included_events": 0, "omitted_events": len(events),
                     "retrieval": "agentcontinuity evidence ID --max-bytes 12000; agentcontinuity search QUERY; agentcontinuity mcp"},
        "coverage": {"known_user_prompts": "included_in_full", "explicit_constraints": "included_in_full",
                     "semantic_requirement_extraction": False, "arbitrary_history_sufficiency_proven": False},
        "warnings": warnings, "budget": {"max_bytes": max_bytes, "used_bytes": 0, "estimated_tokens": 0},
    }

    def measure() -> int:
        # Resolve the small self-referential size field to an exact byte count.
        for _ in range(12):
            size = len(render(packet).encode("utf-8"))
            if packet["budget"]["used_bytes"] == size:
                return size
            packet["budget"]["used_bytes"] = size
            packet["budget"]["estimated_tokens"] = (size + 3) // 4
        return len(render(packet).encode("utf-8"))

    if measure() > max_bytes:
        raise ContinuityError("Mandatory requirements and provenance exceed the packet budget. Increase --budget-bytes; no requirement was silently dropped.")
    terms = set(re.findall(r"[\w./-]{3,}", next_action["text"].lower()))
    terms.update(snapshot["files"])
    def score(pair: tuple[int, dict]) -> tuple[int, int]:
        position, event = pair
        text = event["text"].lower()
        relevance = sum(1 for term in terms if term in text)
        failure = bool(re.search(r"\b(fail(?:ed|ure)?|error|exit code[: ]+[1-9])\b", text))
        return (relevance * 10 + int(failure) * 8 + int(event["kind"] == "tool_result") * 2, position)
    ranked = sorted(enumerate(events), key=score, reverse=True)
    mandatory_ids = {ref for item in [packet["objective"], *requirements] for ref in item["evidence"]}
    included = {e["id"] for e in events if e["id"] in mandatory_ids}
    packet["manifest"]["included_events"] = len(included)
    packet["manifest"]["omitted_events"] = len(events) - len(included)
    if measure() > max_bytes:
        raise ContinuityError("Mandatory packet metadata exceeds the budget. Increase --budget-bytes.")
    # Keep reported implementation progress visibly distinct from observed verification.
    for declaration in reversed(state["progress"]):
        item = {**declaration, "text": excerpt(declaration["text"], 1200)}
        item["truncated"] = item["text"] != declaration["text"]
        packet["progress"].append(item)
        refs = set(item["evidence"]) - included
        packet["manifest"]["included_events"] += len(refs)
        packet["manifest"]["omitted_events"] -= len(refs)
        if measure() > max_bytes:
            packet["progress"].pop()
            packet["manifest"]["included_events"] -= len(refs)
            packet["manifest"]["omitted_events"] += len(refs)
        else:
            included.update(refs)
    for _, event in ranked:
        if event["id"] in included or event["kind"] == "metadata":
            continue
        text = excerpt(event["text"], 6000 if mode == "deep" else 1800)
        entry = {"id": event["id"], "kind": event["kind"], "text": text,
                 "truncated": text != event["text"], "trust": "untrusted_historical_evidence"}
        packet["evidence"].append(entry)
        packet["manifest"]["included_events"] += 1
        packet["manifest"]["omitted_events"] -= 1
        if measure() > max_bytes:
            packet["evidence"].pop()
            packet["manifest"]["included_events"] -= 1
            packet["manifest"]["omitted_events"] += 1
        else:
            included.add(event["id"])
    measure()
    return packet
