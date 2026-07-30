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

#: Per-provider defaults: (base_url, model). Chosen for the free tiers —
#: Groq's llama-3.3-70b and Ollama's llama3.2 are both free and fast enough
#: that a one-sentence summary feels instant.
PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free"),
    "ollama": ("", "llama3.2"),  # base_url comes from settings.OLLAMA_BASE_URL
}


class LLMError(Exception):
    """The model call failed. Callers degrade gracefully — facts never wait."""


class LLMClient(Protocol):
    """What the semantic layer needs a model to do. Nothing more."""

    @property
    def model_name(self) -> str: ...

    def complete(self, *, system: str, prompt: str, max_tokens: int = 300) -> str: ...


class OpenAICompatibleClient:
    """Groq, OpenRouter, Ollama, LM Studio, OpenAI — one client for all.

    Every one of these speaks the OpenAI chat-completions shape, so a single
    implementation covers the free options that matter: Groq (free tier,
    very fast), OpenRouter (free models), and Ollama (local, unlimited, no
    key). Only `base_url` and `model` differ, which is why this file is the
    entire cost of changing provider — `summaries.py` and `narration.py`
    take an `LLMClient` and never learn which one they got.
    """

    def __init__(self, base_url: str, model: str, api_key: str | None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise LLMError(
                "The `openai` package is required for OpenAI-compatible "
                "providers (Groq, OpenRouter, Ollama). pip install openai"
            ) from exc

        # Ollama and LM Studio serve locally and ignore the key, but the SDK
        # insists on a non-empty string — hence the placeholder.
        self._client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def complete(self, *, system: str, prompt: str, max_tokens: int = 300) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:  # noqa: BLE001 - network/SDK errors become ours
            raise LLMError(str(exc)) from exc
        return (response.choices[0].message.content or "").strip()


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


def build_llm() -> LLMClient:
    """The one place a provider is chosen.

    `LLM_PROVIDER=auto` (the default) walks cheapest-first: a local Ollama if
    one is reachable, then Groq, then OpenRouter, then Anthropic — so the
    semantic layer switches on the moment ANY key exists, without a code
    change. Pin a provider by name to remove the guessing.

    Raises LLMError with an actionable message when nothing is configured;
    callers turn that into an honest 503 rather than a crash.
    """
    provider = (settings.LLM_PROVIDER or "auto").lower()

    def _openai_compatible(name: str) -> LLMClient:
        base_url, default_model = PROVIDER_DEFAULTS[name]
        key = {
            "groq": settings.GROQ_API_KEY,
            "openrouter": settings.OPENROUTER_API_KEY,
            "ollama": None,
        }[name]
        return OpenAICompatibleClient(
            base_url=settings.OLLAMA_BASE_URL if name == "ollama" else base_url,
            model=settings.LLM_MODEL or default_model,
            api_key=key,
        )

    if provider in PROVIDER_DEFAULTS:
        return _openai_compatible(provider)
    if provider == "anthropic":
        return AnthropicClient(settings.LLM_MODEL or DEFAULT_MODEL)

    # auto: prefer whatever costs nothing.
    if settings.GROQ_API_KEY:
        return _openai_compatible("groq")
    if settings.OPENROUTER_API_KEY:
        return _openai_compatible("openrouter")
    if settings.ANTHROPIC_API_KEY:
        return AnthropicClient(settings.LLM_MODEL or DEFAULT_MODEL)
    raise LLMError(
        "No LLM provider configured. Set one of GROQ_API_KEY (free tier), "
        "OPENROUTER_API_KEY, or ANTHROPIC_API_KEY in backend/.env — or run "
        "Ollama locally and set LLM_PROVIDER=ollama. Everything deterministic "
        "(the graph, all queries, the map, blast radius) runs without any of them."
    )
