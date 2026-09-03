"""Provider routing: which API a model name belongs to, and how to reach it.

A model is named `provider:model` (`anthropic:claude-sonnet-5`) or bare, in which
case it belongs to `Config.llm.default_provider` — Venice by default, so both the
tick model and Sonnet 5 draw on the same prepaid credits. Keeping the provider
inside the model string means one knob per call kind: `--set llm.final_model=…`
is enough to move the wrap-up to a different vendor.

:class:`ClientPool` is itself a client. It satisfies the same ``complete_json``
contract as the concrete clients and dispatches on the ``model`` argument, so the
reasoner and the wrap-up never learn that more than one vendor exists.
"""

from __future__ import annotations

from typing import Any, Protocol

from livecaster.config import Config, ProviderConfig
from livecaster.llm.client import CallLog
from livecaster.log import get_logger

log = get_logger(__name__)


class CompletionClient(Protocol):  # pragma: no cover - structural typing only
    """What the reasoner and the wrap-up need from any provider."""

    async def complete_json(self, kind: str, messages: list[dict[str, str]], **kwargs: Any) -> Any: ...
    async def aclose(self) -> None: ...


def split_model(spec: str, default_provider: str) -> tuple[str, str]:
    """``"anthropic:claude-sonnet-5"`` -> ``("anthropic", "claude-sonnet-5")``.

    Bare names keep the default provider. Venice's own model ids contain dashes and
    digits but never a colon, so the split is unambiguous.
    """
    provider, sep, model = spec.partition(":")
    if not sep:
        return default_provider, spec.strip()
    return provider.strip().lower(), model.strip()


def provider_config(config: Config, name: str) -> ProviderConfig:
    try:
        return config.llm.providers[name]
    except KeyError:
        known = ", ".join(sorted(config.llm.providers)) or "none configured"
        raise ValueError(f"unknown LLM provider {name!r} (known: {known})") from None


def make_client(
    config: Config,
    provider: str,
    *,
    call_log: CallLog | None = None,
    timeout: float = 60.0,
) -> CompletionClient:
    """Build a client for one provider. The SDK is imported only when needed."""
    pc = provider_config(config, provider)
    if pc.kind == "anthropic":
        from livecaster.llm.anthropic_client import AnthropicClient

        return AnthropicClient(call_log=call_log, timeout=timeout, api_key_env=pc.api_key_env)
    if pc.kind == "openai":
        from livecaster.llm.client import LLMClient

        return LLMClient(
            pc.base_url,
            call_log=call_log,
            timeout=timeout,
            api_key_env=pc.api_key_env,
            venice_extensions=pc.venice_extensions,
        )
    raise ValueError(f"provider {provider!r} has unknown kind {pc.kind!r}")


class ClientPool:
    """One client per provider, built on demand and closed together.

    The tick loop must not rebuild an HTTP client every 25 seconds, and the tick
    and the wrap-up may sit on different vendors.
    """

    def __init__(self, config: Config, call_log: CallLog | None = None) -> None:
        self.config = config
        self.call_log = call_log or CallLog(None)
        self._clients: dict[str, CompletionClient] = {}

    # --- routing -----------------------------------------------------------

    def resolve(self, spec: str, *, timeout: float = 60.0) -> tuple[CompletionClient, str]:
        """The client for a model spec, and the bare model id to send to it."""
        provider, model = split_model(spec, self.config.llm.default_provider)
        client = self._clients.get(provider)
        if client is None:
            client = make_client(self.config, provider, call_log=self.call_log, timeout=timeout)
            self._clients[provider] = client
            log.info("routing %s to the %s provider", model, provider)
        return client, model

    async def complete_json(self, kind: str, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        spec = kwargs.pop("model")
        client, model = self.resolve(spec, timeout=kwargs.get("timeout") or 60.0)
        return await client.complete_json(kind, messages, model=model, **kwargs)

    async def list_models(self, provider: str | None = None) -> dict[str, Any]:
        client, _ = self.resolve(f"{provider or self.config.llm.default_provider}:")
        lister = getattr(client, "list_models", None)
        if lister is None:  # pragma: no cover - every client has one today
            return {"data": []}
        return await lister()

    # --- lifecycle ---------------------------------------------------------

    def set_call_log(self, call_log: CallLog) -> None:
        """Point this pool and every client it already built at a log file."""
        self.call_log = call_log
        for client in self._clients.values():
            if hasattr(client, "call_log"):
                client.call_log = call_log

    async def aclose(self) -> None:
        for client in self._clients.values():
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - closing must never raise
                log.debug("error closing a client", exc_info=True)
        self._clients.clear()

    @property
    def providers_in_use(self) -> list[str]:
        return sorted(self._clients)
