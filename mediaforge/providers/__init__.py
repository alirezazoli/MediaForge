"""Provider registry.

`build_registry` is the only entry point the rest of the app uses. Adding a provider
means writing the class and appending it to PROVIDER_CLASSES -- nothing else changes.
"""

from __future__ import annotations

from typing import Any, Iterable

from .assist import AnthropicProvider, ElevenLabsProvider, OpenAICompatibleProvider
from .base import (
    Capability, GenerationRequest, GenerationResult, ModelSpec, Provider, ProviderError,
)
from .catalog_provider import CatalogProvider
from .gateways import ComfyUIProvider, FalProvider, OmniProvider, ReplicateProvider, SeedanceProvider
from .google import GoogleProvider
from .ollama import OllamaProvider
from .windows_speech import WindowsSpeechProvider

PROVIDER_CLASSES: list[type[Provider]] = [
    GoogleProvider,
    SeedanceProvider,
    OmniProvider,
    FalProvider,
    ReplicateProvider,
    ComfyUIProvider,
    OllamaProvider,
    AnthropicProvider,
    ElevenLabsProvider,
    OpenAICompatibleProvider,
    WindowsSpeechProvider,
]


class Registry:
    def __init__(self, providers: list[Provider]) -> None:
        self._providers = providers

    def __iter__(self) -> Iterable[Provider]:
        return iter(self._providers)

    @property
    def all(self) -> list[Provider]:
        return list(self._providers)

    def get(self, key: str) -> Provider | None:
        return next((provider for provider in self._providers if provider.key == key), None)

    def for_capability(self, capability: Capability, configured_only: bool = False) -> list[Provider]:
        return [
            provider for provider in self._providers
            if capability in provider.capabilities()
            and (provider.configured or not configured_only)
        ]

    def resolve(self, provider_key: str, model_id: str) -> tuple[Provider, ModelSpec]:
        provider = self.get(provider_key)
        if provider is None:
            raise ProviderError(f"Unknown provider '{provider_key}'.")
        spec = provider.model(model_id)
        if spec is None:
            raise ProviderError(f"'{model_id}' is not a known model on {provider.name}.")
        return provider, spec


def build_registry(credentials: Any, settings: Any) -> Registry:
    providers: list[Provider] = []
    for provider_class in PROVIDER_CLASSES:
        slot = provider_class.credential_slot
        api_key = credentials.get(slot) if slot else ""
        providers.append(provider_class(api_key=api_key, settings=settings))

    registry = Registry(providers)

    # The catalogue delegates to the concrete providers, so it is built last and given
    # the registry it will route through. It goes first in the list because it is the
    # entry point users should reach for.
    catalog = CatalogProvider(settings=settings, registry=registry)
    providers.insert(0, catalog)
    return registry


__all__ = [
    "Capability", "GenerationRequest", "GenerationResult", "ModelSpec", "Provider",
    "ProviderError", "Registry", "build_registry", "PROVIDER_CLASSES", "CatalogProvider", "OllamaProvider",
]
