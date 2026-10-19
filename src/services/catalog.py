"""A global metadata catalogue with stable, numbered selection snapshots."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from ..agents import AGENTS
from ..agents.base import Session
from ..core.errors import ContinuityError
from ..core.security import private_directory, private_write, regular_file
from ..workspace.global_config import load_global


def discover_all(config: dict, source: str | None = None) -> tuple[list[Session], list[str]]:
    sessions, warnings = [], []
    for name in ([source] if source else AGENTS):
        try:
            found, notes = AGENTS[name].sessions(Path(config["roots"][name]))
            sessions.extend(found)
            warnings.extend(notes)
        except (ContinuityError, OSError) as exc:
            warnings.append(f"{name}: {exc}")
    sessions.sort(key=lambda item: (-item.modified_ns, item.agent, item.id, item.path))
    return sessions, warnings


def list_sessions(*, source: str | None = None, query: str = "", limit: int | None = 50) -> dict:
    if limit is not None and not 1 <= limit <= 5000:
        raise ContinuityError("Use a limit from 1 to 5000, or --all.")
    directory, config = load_global()
    sessions, warnings = discover_all(config, source)
    terms = query.casefold().split()
    sessions = [s for s in sessions if all(term in f"{s.title} {s.agent} {s.id} {s.workspace}".casefold()
                                          for term in terms)]
    total = len(sessions)
    selected = sessions if limit is None else sessions[:limit]
    records = []
    for number, session in enumerate(selected, 1):
        record = asdict(session)
        record.update({"number": number, "last_activity": datetime.fromtimestamp(
            session.modified_ns / 1e9, timezone.utc).isoformat(), "running_status": "unknown"})
        records.append(record)
    result = {"listing_id": str(uuid4()), "sessions": records, "total": total,
              "omitted": total - len(records), "warnings": warnings,
              "coverage": "Accessible native JSONL sessions on this machine; database-only, remote, and subagent sessions are outside coverage."}
    listings = directory / "listings"
    private_directory(listings)
    private_write(listings / (result["listing_id"] + ".json"), json.dumps(result) + "\n")
    return result


def resolve_session(selector: str, *, source: str | None = None, listing: str | None = None) -> Session:
    directory, config = load_global()
    if listing:
        if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", listing):
            raise ContinuityError("Invalid listing ID; refresh agenthandoff sessions.")
        path = directory / "listings" / (listing + ".json")
        regular_file(path)
        try:
            result = json.loads(path.read_text())
            chosen = [record for record in result["sessions"] if str(record["number"]) == selector]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ContinuityError("Session listing is missing or invalid; refresh agenthandoff sessions.") from exc
        if len(chosen) != 1:
            raise ContinuityError("That number is not in the selected session listing.")
        record = chosen[0]
        source, selector = record["agent"], record["id"]
    if source is not None and source not in AGENTS:
        raise ContinuityError("That source agent is no longer registered. Refresh agenthandoff sessions.")
    sessions, warnings = discover_all(config, source)
    matches = [s for s in sessions if s.id == selector or (not listing and s.title == selector)]
    if listing:
        matches = [s for s in matches if s.path == record["path"] and s.workspace == record["workspace"]]
    if not matches:
        raise ContinuityError("Session is no longer available. Refresh agenthandoff sessions. " + " ".join(warnings))
    if len(matches) != 1:
        raise ContinuityError("Session name/ID is ambiguous. Select its number with --listing, or supply --source and an exact ID.")
    return matches[0]
