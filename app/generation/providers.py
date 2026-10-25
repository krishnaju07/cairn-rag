from collections.abc import Iterator
from typing import Optional

from .. import settings
from .backends.anthropic import AnthropicBackend
from .backends.base import Backend, Model, ProviderError, ProviderRefused
from .backends.extractive import NAME as EXTRACTIVE
from .backends.extractive import ExtractiveBackend
from .backends.nvidia import NvidiaBackend
from .backends.ollama import OllamaBackend

__all__ = ["EXTRACTIVE", "Model", "ProviderError", "ProviderRefused"]

DEFAULT_INPUT_BUDGET = 6000
_BACKENDS: dict[str, Backend] = {
    backend.id: backend for backend in (OllamaBackend(), NvidiaBackend(), AnthropicBackend(), ExtractiveBackend())
}
_FREE_FIRST = ("ollama", "nvidia", "anthropic")


def parse_spec(spec: str) -> tuple[str, str]:
    provider, _, model = spec.partition(":")
    return provider, model


def make_spec(provider: str, model: str) -> str:
    return f"{provider}:{model}" if model else provider


def get(provider_id: str) -> Backend:
    if provider_id not in _BACKENDS:
        raise ProviderError(f"Unknown provider '{provider_id}'.")
    return _BACKENDS[provider_id]


def catalog() -> list[dict]:
    items = []
    for backend in _BACKENDS.values():
        available, reason = backend.status()
        items.append(
            {
                "id": backend.id,
                "label": backend.label,
                "llm": backend.llm,
                "available": available,
                "reason": reason,
                "models": [
                    {
                        "id": make_spec(backend.id, model.id),
                        "label": model.label,
                        "price_in": model.price_in,
                        "price_out": model.price_out,
                    }
                    for model in backend.models()
                ],
            }
        )
    return items


def is_ready(spec: str) -> bool:
    if not spec:
        return False
    provider_id, model = parse_spec(spec)
    try:
        backend = get(provider_id)
    except ProviderError:
        return False
    available, _ = backend.status()
    return available and (not backend.llm or model in {item.id for item in backend.models()})


def default_llm() -> str:
    saved = settings.default_model()
    if saved and is_ready(saved) and get(parse_spec(saved)[0]).llm:
        return saved
    for provider_id in _FREE_FIRST:
        backend = _BACKENDS[provider_id]
        model = backend.preferred() if backend.status()[0] else ""
        if model:
            return make_spec(provider_id, model)
    return ""


def preferred_model() -> str:
    saved = settings.default_model()
    if saved and is_ready(saved):
        return saved
    return default_llm() or EXTRACTIVE


def stream(spec: str, system: str, user: str, max_tokens: int) -> Iterator[str]:
    provider_id, model = parse_spec(spec)
    backend = get(provider_id)
    if not backend.llm:
        raise ProviderError("This provider does not generate text.")
    available, reason = backend.status()
    if not available:
        raise ProviderError(reason)
    return backend.stream(model, system, user, max_tokens)


def complete(spec: str, system: str, user: str, max_tokens: int) -> str:
    return "".join(stream(spec, system, user, max_tokens))


def input_budget(spec: str) -> int:
    return get(parse_spec(spec)[0]).input_budget or DEFAULT_INPUT_BUDGET


def cost_usd(spec: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    provider_id, model = parse_spec(spec)
    return get(provider_id).cost(model, tokens_in, tokens_out)


def test(spec: str) -> tuple[bool, str]:
    provider_id, model = parse_spec(spec)
    try:
        backend = get(provider_id)
    except ProviderError as error:
        return False, str(error)
    available, reason = backend.status()
    return backend.check(model) if available else (False, reason)
