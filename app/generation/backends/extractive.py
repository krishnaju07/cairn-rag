from collections.abc import Iterator

from .base import Model, ProviderError

NAME = "extractive"


class ExtractiveBackend:
    id = NAME
    label = "Extractive (no LLM)"
    llm = False
    input_budget = 0

    def status(self) -> tuple[bool, str]:
        return True, ""

    def models(self) -> list[Model]:
        return [Model("", "Quote the best passages, no model")]

    def preferred(self) -> str:
        return ""

    def stream(self, model: str, system: str, user: str, max_tokens: int) -> Iterator[str]:
        raise ProviderError("This provider does not generate text.")

    def check(self, model: str) -> tuple[bool, str]:
        return True, "Nothing to connect to. Extractive mode runs locally."

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        return 0.0
