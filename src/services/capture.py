"""Incrementally commit native evidence and its byte cursor in one transaction."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path

from ..agents.base import Agent, Session
from ..core.errors import ContinuityError
from ..core.security import digest, json_text, redact, regular_file, sanitize
from ..storage.ledger import Ledger
from ..workspace.git import now

MAX_LINE = 32 * 1024 * 1024


def capture(ledger: Ledger, adapter: Agent, session: Session) -> dict:
    """Atomically commit complete source lines and their cursor; never skip a tail."""
    path = Path(session.path)
    regular_file(path)
    key = f"{session.agent}:{session.id}"
    existing = ledger.source(key)
    task_id = ledger.task()["id"]
    if existing and (existing["task_id"] != task_id or existing["path"] != session.path):
        raise ContinuityError("This session is already bound to another task or source path. Start a fresh native session for this task.")
    cursor = existing["cursor"] if existing else {"offset": 0, "sequence": 0, "unknown": 0, "through_event": None}
    offset, sequence, unknown = cursor["offset"], cursor["sequence"], cursor["unknown"]
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        identity = [before.st_dev, before.st_ino]
        if before.st_size < offset or (existing and cursor["identity"] != identity):
            raise ContinuityError("Source was truncated or replaced. Indexed evidence is retained; refusing to skip or overwrite records.")
        prefix_size = min(offset, 4096)
        if existing:
            if digest(stream.read(prefix_size)) != cursor["prefix_digest"]:
                raise ContinuityError("Indexed source prefix changed. Refusing to reuse its cursor.")
            if cursor.get("tail_size", 0):
                stream.seek(cursor["tail_start"])
                if digest(stream.read(cursor["tail_size"])) != cursor["tail_digest"]:
                    raise ContinuityError("Indexed source boundary changed. Refusing to reuse its cursor.")
        stream.seek(offset)
        through_event = cursor.get("through_event")
        last_start, last_size, last_digest = cursor.get("tail_start", 0), cursor.get("tail_size", 0), cursor.get("tail_digest", "")
        with ledger.db:
            if not existing:
                ledger.db.execute("INSERT INTO sources VALUES (?,?,?,?,?,?)", (key, task_id, session.path, session.agent, session.id, json_text(cursor)))
            while stream.tell() < before.st_size:
                start = stream.tell()
                line = stream.readline(min(MAX_LINE + 1, before.st_size - start))
                if len(line) > MAX_LINE:
                    raise ContinuityError("Native record exceeds 32 MiB. No cursor was advanced; inspect the source file.")
                if not line.endswith(b"\n"):
                    break
                sequence += 1
                raw = line.decode("utf-8", errors="replace")
                try:
                    native = json.loads(line)
                    if not isinstance(native, dict):
                        raise ValueError("record is not an object")
                    safe = sanitize(native)
                    kind, text, unrecognized = adapter.normalize(safe)
                    safe_raw = raw if safe == native else json_text(safe)
                    timestamp = native.get("timestamp") if isinstance(native.get("timestamp"), str) else None
                except (ValueError, UnicodeDecodeError):
                    kind, text, unrecognized, timestamp = "unknown", redact(raw), True, None
                    safe_raw = text
                # A previously emitted packet can return as the next agent's user message.
                # Recognize it only by exact content against this task's local ledger;
                # a prefix or a claimed handoff ID is not sufficient authority.
                handoff_id = None
                if kind == "user":
                    saved = ledger.db.execute("SELECT id,payload FROM handoffs WHERE task_id=? AND prompt_digest=?",
                                              (task_id, digest(text.strip()))).fetchone()
                    if saved and json.loads(saved["payload"])["target_agent"] == session.agent:
                        kind, handoff_id = "metadata", saved["id"]
                event_id = "event-" + digest(f"{key}:{sequence}:{digest(line)}")
                event = {"id": event_id, "agent": session.agent, "session_id": session.id,
                         "sequence": sequence, "offset": start, "end_offset": stream.tell(),
                         "kind": kind, "text": text, "raw": safe_raw, "digest": digest(line),
                         "source_locator": session.path, "timestamp": timestamp, "ingested_at": now(),
                         "redacted": safe_raw != raw, "unrecognized": unrecognized,
                         "handoff_id": handoff_id}
                ledger.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (event_id, task_id, key, sequence, kind, text, json_text(event)))
                ledger.db.execute("INSERT INTO evidence_fts VALUES (?,?,?)", (event_id, task_id, text))
                offset = stream.tell()
                last_start, last_size, last_digest = start, len(line), digest(line)
                through_event = event_id
                unknown += int(unrecognized)
            # Detect truncation/rotation during this catch-up; appends are handled by a later capture.
            after = os.fstat(stream.fileno())
            current = path.stat()
            if after.st_size < before.st_size or [current.st_dev, current.st_ino] != identity:
                raise ContinuityError("Source changed identity during capture. Transaction rolled back; retry.")
            stream.seek(0)
            cursor = {"offset": offset, "sequence": sequence, "identity": identity, "unknown": unknown,
                      "prefix_digest": digest(stream.read(min(offset, 4096))),
                      "tail_start": last_start, "tail_size": last_size, "tail_digest": last_digest,
                      "through_event": through_event}
            ledger.db.execute("UPDATE sources SET cursor=? WHERE key=?", (json_text(cursor), key))
        size = current.st_size
    warnings = []
    if size != offset:
        warnings.append("Source has an incomplete or newly appended tail; it remains uncaptured. Retry after the source stops.")
    if unknown:
        warnings.append(f"{unknown} malformed or unsupported records retained as untrusted evidence; semantic capture is partial.")
    return {"session": asdict(session), "through_event": through_event, "offset": offset,
            "source_size": size, "complete": size == offset and unknown == 0, "warnings": warnings}
