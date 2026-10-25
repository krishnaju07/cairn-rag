import json
import os
import re
import tempfile
from typing import Any, Optional

from . import db
from .config import ENV_FILE

SECRET_NAMES = ("NVIDIA_API_KEY", "ANTHROPIC_API_KEY", "LLAMA_CLOUD_API_KEY", "LLAMA_CLOUD_PROJECT_ID")
_SAFE_VALUE = re.compile(r"[\w\-./:+=]*")


def get(name: str) -> str:
    return os.environ.get(name, "")


def mask(value: str) -> str:
    return f"…{value[-4:]}" if len(value) > 8 else "set"


def save(values: dict[str, str]) -> None:
    for name, value in values.items():
        if name not in SECRET_NAMES:
            raise ValueError(f"{name} cannot be changed here.")
        if not _SAFE_VALUE.fullmatch(value):
            raise ValueError("Keys cannot contain spaces, quotes or line breaks.")
    pending = dict(values)
    lines = []
    for line in ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []:
        name = line.split("=", 1)[0].strip()
        lines.append(f"{name}={pending.pop(name)}" if name in pending else line)
    lines.extend(f"{name}={value}" for name, value in pending.items())
    handle, temporary = tempfile.mkstemp(dir=ENV_FILE.parent, prefix=".env.")
    with os.fdopen(handle, "w") as file:
        file.write("\n".join(lines) + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, ENV_FILE)
    os.environ.update(values)


def default_model() -> Optional[str]:
    row = db.one("SELECT value FROM settings WHERE key = 'default_model'")
    return row["value"] if row else None


def set_default_model(spec: str) -> None:
    db.run("INSERT OR REPLACE INTO settings (key, value) VALUES ('default_model', ?)", (spec,))


def cached(name: str) -> Optional[Any]:
    row = db.one("SELECT value FROM settings WHERE key = ?", (f"cache:{name}",))
    return json.loads(row["value"]) if row else None


def cache(name: str, value: Any) -> None:
    db.run("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"cache:{name}", json.dumps(value)))


def forget(name: str) -> None:
    db.run("DELETE FROM settings WHERE key = ?", (f"cache:{name}",))
