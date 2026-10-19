"""Versioned SQLite WAL evidence ledger and task projections."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from ..core.errors import ContinuityError
from ..core.security import excerpt, json_text, regular_file
from ..workspace.git import now

from .schema import SCHEMA


class Ledger:
    def __init__(self, directory: Path, *, readonly: bool = False):
        self.path = directory / "ledger.sqlite3"
        for suffix in ("", "-wal", "-shm"):
            regular_file(Path(str(self.path) + suffix))
        if readonly and not self.path.exists():
            raise ContinuityError("No evidence ledger. Run 'agentcontinuity init' first.")
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5) if readonly else sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1) or (readonly and version != 1):
                raise ContinuityError(f"Unsupported ledger schema {version}; use a compatible AgentContinuity version.")
            if not readonly:
                self.path.chmod(0o600)
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=FULL")
                if version == 0:
                    self.db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(self.path) + suffix)
                    if sidecar.exists():
                        sidecar.chmod(0o600)
        except BaseException:
            self.db.close()
            raise

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *_: object) -> None:
        self.db.close()

    def new_task(self, snapshot: dict, objective: str | None = None) -> dict:
        task_id = "task-" + str(uuid4())
        state = {"id": task_id, "version": 1, "objective": None, "constraints": [],
                 "decisions": [], "next_action": None, "progress": []}
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES (?,?,?)", (task_id, json_text(state), json_text(snapshot)))
            self.db.execute("INSERT INTO task_versions VALUES (?,?,?,?)", (task_id, 1, json_text(state), now()))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('active_task',?)", (task_id,))
        if objective:
            self.checkpoint(snapshot, objective=objective)
        return self.task()

    def task(self) -> dict:
        row = self.db.execute("SELECT state FROM tasks WHERE id=(SELECT value FROM meta WHERE key='active_task')").fetchone()
        if row is None:
            raise ContinuityError("No active task. Run 'agentcontinuity init' or 'agentcontinuity task --new'.")
        return json.loads(row[0])

    def anchor(self) -> dict:
        return json.loads(self.db.execute("SELECT anchor FROM tasks WHERE id=?", (self.task()["id"],)).fetchone()[0])

    def save_state(self, state: dict) -> None:
        state["version"] += 1
        self.db.execute("UPDATE tasks SET state=? WHERE id=?", (json_text(state), state["id"]))
        self.db.execute("INSERT INTO task_versions VALUES (?,?,?,?)", (state["id"], state["version"], json_text(state), now()))

    def observation(self, kind: str, snapshot: dict, data: dict) -> dict:
        record = {"id": "obs-" + str(uuid4()), "task_id": self.task()["id"], "kind": kind,
                  "fingerprint": snapshot["fingerprint"], "created_at": now(), "data": data}
        self.db.execute("INSERT INTO observations VALUES (?,?,?,?)", (record["id"], record["task_id"], kind, json_text(record)))
        self.db.execute("INSERT INTO evidence_fts VALUES (?,?,?)", (record["id"], record["task_id"], json_text(data)))
        return record

    def checkpoint(self, snapshot: dict, *, objective: str | None = None,
                   constraints: list[str] | None = None, decisions: list[str] | None = None,
                   next_action: str | None = None, accept_workspace: bool = False) -> dict:
        from ..core.security import sanitize
        data = sanitize({"objective": objective, "constraints": constraints or [],
                         "decisions": decisions or [], "next_action": next_action})
        state = self.task()
        with self.db:
            record = self.observation("checkpoint", snapshot, data)
            def assertion(text: str) -> dict:
                return {"text": text, "status": "proposed", "evidence": [record["id"]],
                        "fingerprint": snapshot["fingerprint"], "origin": "explicit_checkpoint"}
            if data["objective"]:
                state["objective"] = assertion(data["objective"])
            if data["next_action"]:
                state["next_action"] = assertion(data["next_action"])
            state["constraints"].extend(assertion(text) for text in data["constraints"])
            state["decisions"].extend(assertion(text) for text in data["decisions"])
            self.save_state(state)
            if accept_workspace:
                self.db.execute("UPDATE tasks SET anchor=? WHERE id=?", (json_text(snapshot), state["id"]))
        return state

    def source(self, key: str) -> dict | None:
        row = self.db.execute("SELECT * FROM sources WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "cursor": json.loads(row["cursor"])}

    def events(self, task_id: str | None = None) -> list[dict]:
        task_id = task_id or self.task()["id"]
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM events WHERE task_id=? ORDER BY rowid", (task_id,))]

    def observations(self, kind: str) -> list[dict]:
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM observations WHERE task_id=? AND kind=? ORDER BY rowid", (self.task()["id"], kind))]

    def retrieve(self, evidence_id: str, *, max_bytes: int = 12000) -> dict:
        if not 256 <= max_bytes <= 65536:
            raise ContinuityError("Evidence budget must be between 256 and 65536 bytes.")
        task_id = self.task()["id"]
        row = self.db.execute("SELECT payload FROM events WHERE id=? AND task_id=?", (evidence_id, task_id)).fetchone()
        if row is None:
            row = self.db.execute("SELECT payload FROM observations WHERE id=? AND task_id=?", (evidence_id, task_id)).fetchone()
        if row is None:
            raise ContinuityError("Evidence ID was not found in the active task.")
        text = row[0]
        result = {"id": evidence_id, "content": text, "truncated": False,
                  "trust": "untrusted_historical_evidence"}
        if len(json_text(result).encode()) <= max_bytes:
            return result
        result["truncated"] = True
        low, high, best = 0, min(len(text.encode()), max_bytes), ""
        while low <= high:
            middle = (low + high) // 2
            result["content"] = excerpt(text, middle)
            if len(json_text(result).encode()) <= max_bytes:
                best, low = result["content"], middle + 1
            else:
                high = middle - 1
        result["content"] = best
        return result

    def search(self, query: str, *, limit: int = 5, max_bytes: int = 12000) -> dict:
        if not query.strip() or len(query.encode()) > 4096 or not 1 <= limit <= 20:
            raise ContinuityError("Search requires 1–4096 bytes of text and a limit from 1 to 20.")
        if not 256 <= max_bytes <= 65536:
            raise ContinuityError("Search budget must be between 256 and 65536 bytes.")
        # Quoted terms make the query literal; callers cannot inject FTS operators.
        match = " OR ".join('"' + part.replace('"', '""') + '"' for part in query.split())
        rows = self.db.execute(
            "SELECT id, snippet(evidence_fts,2,'','', ' … ',32) AS text FROM evidence_fts "
            "WHERE evidence_fts MATCH ? AND task_id=? ORDER BY rank LIMIT ?",
            (match, self.task()["id"], limit)).fetchall()
        result = {"results": [], "trust": "untrusted_historical_evidence", "truncated": False}
        per_item = max(1, (max_bytes - 256) // max(1, len(rows)))
        for row in rows:
            text = excerpt(row["text"], per_item)
            result["results"].append({"id": row["id"], "text": text})
            if text != row["text"]:
                result["truncated"] = True
            if len(json_text(result).encode()) > max_bytes:
                result["results"].pop()
                result["truncated"] = True
        return result
