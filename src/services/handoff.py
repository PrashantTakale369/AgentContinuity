"""Verify, compile, and durably save a packet before the CLI may launch it."""

from pathlib import Path
import json

from ..context.compiler import compile_packet
from ..context.rendering import render
from ..core.errors import ContinuityError
from ..core.security import digest, json_text, private_directory, private_write
from ..storage.ledger import Ledger
from ..workspace.git import assert_workspace, observe
from .verification import verify


def prepare_handoff(ledger: Ledger, directory: Path, workspace: Path, snapshot: dict,
                    result: dict, target: str, *, mode: str = "fast",
                    budget_bytes: int | None = None, verify_command: str | None = None,
                    timeout: int = 120) -> tuple[dict, Path]:
    """Caller holds the writer lock through preparation and any target launch."""
    if mode == "verified":
        if not verify_command:
            raise ContinuityError("Verified mode requires an explicit --verify-command. Imported commands are never replayed.")
        verify(ledger, workspace, verify_command, timeout)
        snapshot = observe(workspace)
        assert_workspace(ledger.anchor(), snapshot, allow_edits=True)
    elif verify_command:
        raise ContinuityError("--verify-command requires --mode verified.")
    with ledger.db:
        observation = ledger.observation("workspace", snapshot, snapshot)
    packet = compile_packet(ledger, snapshot, result, target,
                            max_bytes=budget_bytes if budget_bytes is not None else (64000 if mode == "deep" else 24000),
                            mode=mode, workspace_evidence=observation["id"])
    assert_workspace(snapshot, observe(workspace))
    packets = directory / "handoffs"
    private_directory(packets)
    path = packets / (packet["id"] + ".json")
    private_write(path, json.dumps(packet, indent=2) + "\n")
    with ledger.db:
        ledger.db.execute("INSERT INTO handoffs VALUES (?,?,?,?)", (packet["id"], packet["task_id"], json_text(packet), digest(render(packet).strip())))
    return packet, path
