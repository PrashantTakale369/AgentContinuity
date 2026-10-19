"""User-level setup, independent of the current Git working directory."""

import json
import os
from pathlib import Path

from ..core.errors import ContinuityError
from ..core.security import private_directory, regular_file
from .config import session_roots


def global_directory() -> Path:
    default = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "agenthandoff"
    path = Path(os.environ.get("AGENTHANDOFF_HOME", str(default))).expanduser()
    if not path.is_absolute():
        raise ContinuityError("AGENTHANDOFF_HOME must be an absolute directory.")
    return path


def load_global(*, required: bool = True) -> tuple[Path, dict]:
    directory = global_directory()
    if directory.is_symlink():
        raise ContinuityError("Refusing symlinked global storage.")
    path = directory / "config.json"
    regular_file(path)
    if not path.exists():
        if required:
            raise ContinuityError("Run 'agenthandoff init' once to enable local session discovery.")
        return directory, {"version": 1, "roots": session_roots()}
    try:
        config = json.loads(path.read_text())
        if (not isinstance(config, dict) or config["version"] != 1
                or not isinstance(config["roots"], dict)
                or not all(isinstance(k, str) and isinstance(v, str) and Path(v).is_absolute()
                           for k, v in config["roots"].items())):
            raise ValueError("Invalid global configuration")
    except (ValueError, KeyError, TypeError) as exc:
        raise ContinuityError("Global configuration is invalid; existing settings were preserved.") from exc
    config["roots"] = session_roots(config["roots"])
    return directory, config


def ensure_global_directory() -> Path:
    directory = global_directory()
    private_directory(directory)
    return directory
