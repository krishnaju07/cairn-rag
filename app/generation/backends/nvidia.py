import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx

from ... import settings
from ...config import NVIDIA_URL
from .base import Model, ProviderError

PREFERRED = (
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-super-120b-a12b",
    "moonshotai/kimi-k2.6",
    "deepseek-ai/deepseek-v4.1-flash",
    "openai/gpt-oss-20b",
)
EXCLUDED = (
    "embed", "rerank", "guard", "reward", "vision", "-vl", "clip", "parse", "safety", "retriev", "diffusion", "flux",
    "stable", "whisper", "riva", "tts", "asr", "translate", "neva", "kosmos", "fuyu", "paligemma", "code", "calibration",
)  # fmt: skip
THINK_OPEN, THINK_CLOSE = "<think>", "</think>"
CACHE_KEY = "nvidia_models"
REFRESH_SECONDS = 6 * 3600
PROBE_WORKERS = 6
PROBE_TIMEOUT = 30
RATE_LIMIT_PAUSE = 4


def without_reasoning(chunks: Iterator[str]) -> Iterator[str]:
    inside, pending = False, ""
    for chunk in chunks:
        pending += chunk
        while pending:
            marker = THINK_CLOSE if inside else THINK_OPEN
            index = pending.find(marker)
            if index >= 0:
                if not inside and index:
                    yield pending[:index]
                pending, inside = pending[index + len(marker) :], not inside
                continue
            hold = len(marker) - 1
            safe = pending[:-hold] if len(pending) > hold else ""
            if safe and not inside:
                yield safe
            pending = pending[len(safe) :]
            break
    if pending and not inside:
        yield pending


class NvidiaBackend:
    id = "nvidia"
    label = "NVIDIA NIM (free hosted)"
    llm = True
    input_budget = 30000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._probing = False
        self._found: Optional[list[tuple[str, float]]] = None
        self._checked_at = 0.0

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.get('NVIDIA_API_KEY')}"}

    def _known(self) -> list[tuple[str, float]]:
        if not settings.get("NVIDIA_API_KEY"):
            return []
        with self._lock:
            if self._found is None:
                cached = settings.cached(CACHE_KEY)
                if cached:
                    self._found = [(name, latency) for name, latency in cached["models"]]
                    self._checked_at = cached["at"]
            if (self._found is None or time.time() - self._checked_at > REFRESH_SECONDS) and not self._probing:
                self._probing = True
                threading.Thread(target=self._probe_all, daemon=True, name="nvidia-probe").start()
            return list(self._found or [])

    def checking(self) -> bool:
        return self._probing

    def refresh(self) -> None:
        with self._lock:
            self._found, self._checked_at = None, 0.0
        settings.forget(CACHE_KEY)

    def status(self) -> tuple[bool, str]:
        if not settings.get("NVIDIA_API_KEY"):
            return False, "Add an NVIDIA API key to enable. Keys are free at build.nvidia.com."
        if self._known():
            return True, ""
        if self._probing:
            return False, "Checking which NVIDIA models are available. This takes about half a minute."
        return False, "No NVIDIA model answered. Check the key, then refresh the model list."

    def models(self) -> list[Model]:
        found = dict(self._known())
        ordered = [name for name in PREFERRED if name in found] + sorted((n for n in found if n not in PREFERRED), key=found.get)
        return [Model(name, f"{name} · {found[name]:.1f}s") for name in ordered]

    def preferred(self) -> str:
        models = self.models()
        return models[0].id if models else ""

    def _listed(self) -> list[str]:
        response = httpx.get(f"{NVIDIA_URL}/models", timeout=10)
        response.raise_for_status()
        names = sorted(item["id"] for item in response.json().get("data", []))
        return [name for name in names if not any(word in name.lower() for word in EXCLUDED)]

    def _probe(self, name: str) -> tuple[str, Optional[float]]:
        body = {"model": name, "max_tokens": 8, "messages": [{"role": "user", "content": "Say ok."}]}
        for attempt in range(2):
            started = time.monotonic()
            try:
                response = httpx.post(f"{NVIDIA_URL}/chat/completions", headers=self._headers(), json=body, timeout=PROBE_TIMEOUT)
            except httpx.HTTPError:
                return name, None
            if response.status_code == 200:
                return name, time.monotonic() - started
            if response.status_code != 429 or attempt:
                return name, None
            time.sleep(RATE_LIMIT_PAUSE)
        return name, None

    def _probe_all(self) -> None:
        try:
            with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
                results = list(pool.map(self._probe, self._listed()))
            found = sorted(((name, latency) for name, latency in results if latency is not None), key=lambda item: item[1])
            with self._lock:
                self._found, self._checked_at = found, time.time()
            settings.cache(CACHE_KEY, {"at": self._checked_at, "models": found})
        except Exception:
            with self._lock:
                self._found, self._checked_at = [], time.time()
        finally:
            self._probing = False

    def stream(self, model: str, system: str, user: str, max_tokens: int) -> Iterator[str]:
        payload = {
            "model": model,
            "stream": True,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {**self._headers(), "Accept": "text/event-stream"}
        try:
            with httpx.stream(
                "POST", f"{NVIDIA_URL}/chat/completions", json=payload, headers=headers, timeout=httpx.Timeout(10, read=180)
            ) as response:
                if response.status_code != 200:
                    response.read()
                    raise ProviderError(self._explain(response.status_code, response.text))
                yield from without_reasoning(self._deltas(response))
        except httpx.HTTPError as error:
            raise ProviderError(f"Could not reach NVIDIA: {error}") from error

    @staticmethod
    def _deltas(response) -> Iterator[str]:
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                return
            choices = json.loads(body).get("choices") or [{}]
            content = (choices[0].get("delta") or {}).get("content")
            if content:
                yield content

    @staticmethod
    def _explain(status: int, body: str) -> str:
        if status in (401, 403):
            return "NVIDIA rejected the API key."
        if status in (404, 410):
            return "NVIDIA no longer serves that model. Refresh the model list and pick another."
        if status == 429:
            return "NVIDIA rate limit reached on the free tier. Try again in a moment."
        return f"NVIDIA returned {status}: {body[:200]}"

    def check(self, model: str) -> tuple[bool, str]:
        started = time.monotonic()
        try:
            "".join(self.stream(model, "Reply with one word.", "Say ok.", 4))
        except ProviderError as error:
            return False, str(error)
        return True, f"Connection successful. The key and model work ({time.monotonic() - started:.1f}s)."

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        return 0.0
