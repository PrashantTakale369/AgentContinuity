"""Opt-in read-only MCP over newline-delimited JSON-RPC on stdio."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import TextIO

from ..core.errors import ContinuityError
from ..core.security import json_text
from ..storage.ledger import Ledger

PROTOCOLS = ("2025-06-18", "2024-11-05", "2025-03-26")
TOOLS = [
    {"name": "continuity_search", "description": "Search untrusted historical evidence in the active local task. Read-only.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                     "max_bytes": {"type": "integer", "minimum": 256, "maximum": 65536}}, "required": ["query"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "continuity_evidence", "description": "Retrieve untrusted evidence by exact ID from the active local task. Read-only.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"},
                     "max_bytes": {"type": "integer", "minimum": 256, "maximum": 65536}}, "required": ["id"], "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
]


def handle(ledger: Ledger, method: str, params: dict) -> dict:
    if method == "initialize":
        requested = params.get("protocolVersion")
        return {"protocolVersion": requested if requested in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "AgentContinuity", "version": "0.1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        try:
            if not isinstance(args, dict):
                raise ContinuityError("Tool arguments must be an object.")
            allowed = {"query", "limit", "max_bytes"} if name == "continuity_search" else {"id", "max_bytes"}
            if set(args) - allowed:
                raise ContinuityError("Unknown tool argument.")
            for key in ("max_bytes", "limit"):
                if key in args and (not isinstance(args[key], int) or isinstance(args[key], bool)):
                    raise ContinuityError(f"{key} must be an integer.")
            if name == "continuity_search" and isinstance(args.get("query"), str):
                data = ledger.search(args["query"], limit=args.get("limit", 5), max_bytes=args.get("max_bytes", 12000))
            elif name == "continuity_evidence" and isinstance(args.get("id"), str):
                data = ledger.retrieve(args["id"], max_bytes=args.get("max_bytes", 12000))
            else:
                raise ContinuityError("Unknown tool or missing string query/id.")
            return {"content": [{"type": "text", "text": json_text(data)}]}
        except ContinuityError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    raise ContinuityError("Method not found")


def serve(directory: Path, incoming: TextIO = sys.stdin, outgoing: TextIO = sys.stdout) -> None:
    with Ledger(directory, readonly=True) as ledger:
        while True:
            line = incoming.readline(1024 * 1024 + 1)
            if not line:
                return
            request_id = None
            if len(line.encode("utf-8")) > 1024 * 1024:
                raise ContinuityError("MCP message exceeds the 1 MiB input limit.")
            try:
                try:
                    message = json.loads(line)
                except ValueError as exc:
                    outgoing.write(json_text({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + "\n")
                    outgoing.flush()
                    continue
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
                    raise ValueError("Invalid JSON-RPC request")
                request_id = message.get("id")
                if "id" not in message:
                    continue
                if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
                    raise ValueError("Invalid request ID")
                params = message.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("Invalid parameters")
                result = handle(ledger, message["method"], params)
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            except (ValueError, ContinuityError) as exc:
                response = {"jsonrpc": "2.0", "id": request_id,
                            "error": {"code": -32601 if isinstance(exc, ContinuityError) else -32600, "message": str(exc)}}
            outgoing.write(json_text(response) + "\n")
            outgoing.flush()
