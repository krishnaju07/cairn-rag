from collections.abc import Iterator
from typing import Optional

from ... import settings
from .base import Model, ProviderError, ProviderRefused

MODELS = (
    Model("claude-opus-5-5", "Claude Opus 5.5", 4.0, 20.0),
    Model("claude-sonnet-5-5", "Claude Sonnet 5.5", 2.0, 10.0),
    Model("claude-haiku-5-5", "Claude Haiku 5.5", 0.10, 0.50),
)
FALLBACK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicBackend:
    id = "anthropic"
    label = "Anthropic API (paid)"
    llm = True
    input_budget = 30000

    def status(self) -> tuple[bool, str]:
        if settings.get("ANTHROPIC_API_KEY"):
            return True, ""
        return False, "Add an Anthropic API key to enable."

    def models(self) -> list[Model]:
        return list(MODELS)

    def preferred(self) -> str:
        return MODELS[0].id if self.status()[0] else ""

    def stream(self, model: str, system: str, user: str, max_tokens: int) -> Iterator[str]:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.get("ANTHROPIC_API_KEY"))
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": "low"},
        }
        try:
            yield from self._run(client, request, protected=model in FALLBACK_MODELS)
        except anthropic.AuthenticationError:
            raise ProviderError("Anthropic rejected the API key.") from None
        except anthropic.RateLimitError:
            raise ProviderError("Anthropic rate limit reached. Try again shortly.") from None
        except anthropic.APIConnectionError:
            raise ProviderError("Could not reach the Anthropic API.") from None
        except anthropic.APIStatusError as error:
            raise ProviderError(f"Anthropic API error: {error.message}") from error

    def _run(self, client, request: dict, protected: bool) -> Iterator[str]:
        import anthropic

        if protected:
            try:
                yield from self._consume(
                    client.beta.messages.stream(**request, betas=[FALLBACK_BETA], extra_body={"fallbacks": "default"})
                )
                return
            except anthropic.BadRequestError as error:
                if "fallback" not in str(error).lower():
                    raise
        yield from self._consume(client.messages.stream(**request))

    @staticmethod
    def _consume(manager) -> Iterator[str]:
        with manager as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
        if final.stop_reason == "refusal":
            raise ProviderRefused("The model declined to answer this request.")

    def check(self, model: str) -> tuple[bool, str]:
        import anthropic

        try:
            anthropic.Anthropic(api_key=settings.get("ANTHROPIC_API_KEY")).models.retrieve(model)
        except anthropic.AuthenticationError:
            return False, "Anthropic rejected the API key."
        except anthropic.NotFoundError:
            return False, f"Anthropic does not know the model {model}."
        except anthropic.APIError as error:
            return False, f"Could not reach Anthropic: {error}"
        return True, "Connection successful. The credentials are valid."

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> Optional[float]:
        for candidate in MODELS:
            if candidate.id == model:
                return (tokens_in * candidate.price_in + tokens_out * candidate.price_out) / 1_000_000
        return None
