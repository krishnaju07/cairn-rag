import threading

from ..config import DEFAULT_EMBED_MODEL
from . import embeddings, rerank

_state: dict[str, str] = {"status": "idle", "message": ""}


def status() -> dict[str, str]:
    return dict(_state)


def start() -> None:
    if _state["status"] != "idle":
        return
    _state.update(status="loading", message="Downloading local models. This only happens on the first run.")
    threading.Thread(target=_load, daemon=True, name="warmup").start()


def _load() -> None:
    try:
        embeddings.embed(DEFAULT_EMBED_MODEL, ["warm up"])
        rerank.rerank("warm up", ["warm up"])
        _state.update(status="ready", message="")
    except Exception as error:
        _state.update(status="error", message=f"Could not load local models: {error}")
