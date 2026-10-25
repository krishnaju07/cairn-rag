import json
import time
from collections.abc import Iterator

import httpx

from ...config import OLLAMA_URL
from .base import Model, ProviderError

PREFERRED = ("qwen2.5:3b", "llama3.2:3b", "qwen2.5:7b", "llama3.1:8b")
CACHE_SECONDS = 5


class OllamaBackend:
    id = "ollama"
    label = "Ollama (local)"
    llm = True
    input_budget = 6000

    def __init__(self) -> None:
        self._cache: tuple[float, list[str], str] = (0.0, [], "")

    def _probe(self) -> tuple[list[str], str]:
        stamp, names, reason = self._cache
        if time.monotonic() - stamp < CACHE_SECONDS:
            return names, reason
        try:
            response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
            response.raise_for_status()
            names = [item["name"] for item in response.json().get("models", [])]
            reason = "" if names else "Ollama is running but has no models. Run: ollama pull qwen2.5:3b"
        except Exception:
            names, reason = [], "Ollama is not running. Start it with: ollama serve"
        self._cache = (time.monotonic(), names, reason)
        return names, reason

    def status(self) -> tuple[bool, str]:
        names, reason = self._probe()
        return bool(names), reason

    def models(self) -> list[Model]:
        return [Model(name, name) for name in self._probe()[0]]

    def preferred(self) -> str:
        names = self._probe()[0]
        return next((name for name in PREFERRED if name in names), names[0] if names else "")

    def stream(self, model: str, system: str, user: str, max_tokens: int) -> Iterator[str]:
        payload = {
            "model": model,
            "stream": True,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": max_tokens},
        }
        try:
            with httpx.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload, timeout=httpx.Timeout(10, read=300)) as response:
                if response.status_code != 200:
                    response.read()
                    raise ProviderError(f"Ollama returned {response.status_code}: {response.text[:200]}")
                for line in filter(None, response.iter_lines()):
                    event = json.loads(line)
                    if event.get("error"):
                        raise ProviderError(event["error"])
                    delta = event.get("message", {}).get("content", "")
                    if delta:
                        yield delta
        except httpx.HTTPError as error:
            raise ProviderError(f"Could not reach Ollama at {OLLAMA_URL}: {error}") from error

    def check(self, model: str) -> tuple[bool, str]:
        if model in self._probe()[0]:
            return True, f"Connected. {model} is installed."
        return False, f"{model} is not installed. Run: ollama pull {model}"

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        return 0.0
