"""Local storage boundaries and best-effort credential minimization."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator

from ..core.errors import ContinuityError


def digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def redact(text: str) -> str:
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----",
                  "[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"\b(?:sk-(?:proj-|ant-)?[\w-]{16,}|gh[pousr]_[\w]{20,}|github_pat_[\w]{20,}|AKIA[A-Z0-9]{16})\b",
                  "[REDACTED TOKEN]", text)
    text = re.sub(r"(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)\b[\"']?\s*[:=]\s*)(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}]+)",
                  r"\1[REDACTED]", text, flags=re.I)
    text = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.I)
    return re.sub(r"(https?://)[^\s/@:]+:[^\s/@]+@", r"\1[REDACTED]@", text)


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.fullmatch(
            r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)", key, re.I
        ) else sanitize(item) for key, item in value.items()}
    return value


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def regular_file(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ContinuityError(f"Refusing non-regular file: {path}")


def private_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ContinuityError(f"Refusing symlink or non-directory storage: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def private_write(path: Path, content: str) -> None:
    regular_file(path)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def writer_lock(directory: Path) -> Iterator[None]:
    """An OS lock survives crashes without leaving an unrecoverable stale lock."""
    try:
        import fcntl
    except ImportError as exc:
        raise ContinuityError("This release requires POSIX file locking (Linux/macOS).") from exc
    path = directory / "writer.lock"
    regular_file(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContinuityError("Another agentcontinuity writer is active in this workspace. Retry after it exits.") from exc
        yield
    finally:
        os.close(fd)


def excerpt(text: str, max_bytes: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text
    suffix = "\n[truncated; retrieve full evidence by ID]"
    size = max_bytes - len(suffix.encode())
    if size < 0:
        return ""
    return data[:size].decode("utf-8", errors="ignore") + suffix
