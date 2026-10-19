"""Execute only commands explicitly supplied by the local CLI user."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile

from ..core.errors import ContinuityError
from ..core.security import redact
from ..storage.ledger import Ledger
from ..workspace.git import assert_workspace, now, observe


def verify(ledger: Ledger, workspace: Path, command: str, timeout: int = 120) -> dict:
    if not command.strip() or not 1 <= timeout <= 3600:
        raise ContinuityError("Verification needs an explicit command and a timeout from 1 to 3600 seconds.")
    before = observe(workspace)
    assert_workspace(ledger.anchor(), before, allow_edits=True)
    started = now()
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(command, shell=True, cwd=workspace, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            if isinstance(exc, KeyboardInterrupt):
                raise
            timed_out, code = True, process.returncode
        output.seek(0)
        data = output.read(32 * 1024 * 1024 + 1)
        complete = len(data) <= 32 * 1024 * 1024
        text = redact(data[:32 * 1024 * 1024].decode("utf-8", errors="replace"))
    after = observe(workspace)
    record_data = {"command": redact(command), "exit_code": code, "output": text,
                   "output_complete": complete, "timed_out": timed_out, "started_at": started,
                   "finished_at": now(), "stable_workspace": before["fingerprint"] == after["fingerprint"] and not timed_out and complete}
    with ledger.db:
        return ledger.observation("verification", after, record_data)
