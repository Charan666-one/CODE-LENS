"""The LLM seam — the only place a language model is ever called.

Everything below the semantic layer is forbidden to import this module
(ARCHITECTURE.md corollary 1: the graph is fully functional with zero LLM
calls). Everything inside the semantic layer goes through the `LLMClient`
protocol, so tests run against `CountingFakeLLM` — deterministic, free, and
call-counted, which is precisely what CP-3.2's zero-second-call gate needs.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import settings

#: Summaries are one-sentence transformations of small, graph-selected
#: context — the fast, cheap model is the right tool. Narration (CP-3.4)
#: passes its own choice.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class LLMError(Exception):
    """The model call failed. Callers degrade gracefully — facts never wait."""


class LLMClient(Protocol):
    """What the semantic layer needs a model to do. Nothing more."""

    @property
    def model_name(self) -> str: ...

    def complete(self, *, system: str, prompt: str, max_tokens: int = 300) -> str: ...


class AnthropicClient:
    """The real thing. Constructed lazily so importing the semantic layer
    never requires a key — only *calling* it does."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        if not settings.ANTHROPIC_API_KEY:
            raise LLMError(
                "ANTHROPIC_API_KEY is not set. The semantic layer is optional: "
                "everything deterministic runs without it."
            )
        import anthropic  # imported here so the graph layers never load it

        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, *, system: str, prompt: str, max_tokens: int = 300) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:  # noqa: BLE001 - network/SDK errors become ours
            raise LLMError(str(exc)) from exc
        parts = [block.text for block in response.content if hasattr(block, "text")]
        return "".join(parts).strip()


class CountingFakeLLM:
    """Deterministic stand-in for tests: echoes a digest of its input and
    counts invocations — the instrument the caching gate is measured with."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    @property
    def model_name(self) -> str:
        return "fake-llm"

    def complete(self, *, system: str, prompt: str, max_tokens: int = 300) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        first_line = prompt.strip().splitlines()[0] if prompt.strip() else ""
        return f"[fake summary #{self.calls}] {first_line[:80]}"
