from collections.abc import Iterator
from dataclasses import dataclass
from typing import Optional, Protocol


class ProviderError(Exception):
    pass


class ProviderRefused(ProviderError):
    pass


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    price_in: Optional[float] = None
    price_out: Optional[float] = None


class Backend(Protocol):
    id: str
    label: str
    llm: bool
    input_budget: int

    def status(self) -> tuple[bool, str]: ...

    def models(self) -> list[Model]: ...

    def preferred(self) -> str: ...

    def stream(self, model: str, system: str, user: str, max_tokens: int) -> Iterator[str]: ...

    def check(self, model: str) -> tuple[bool, str]: ...

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> Optional[float]: ...
