"""The catalogue provider: every model in models.json, routed to whichever backend can run it.

This is what makes the app extensible without code. A model entry declares one or more
routes; the provider picks the first route whose backend is configured, then delegates
with the route's endpoint id as the model id. Adding a model released next month is a
JSON edit.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..catalog import load_models
from .base import (
    Capability, GenerationRequest, GenerationResult, ModelSpec, Provider, ProviderError,
)

OPENNESS_MARK = {"open": "FREE/OPEN", "partial": "PARTLY OPEN", "closed": "API"}


class CatalogProvider(Provider):
    key = "catalog"
    name = "Model Catalogue (all vendors)"
    homepage = "https://fal.ai/models"
    credential_slot = ""
    credential_help = (
        "Each model routes to whichever backend you have a key for -- fal.ai, Replicate, "
        "Google, Volcengine or DashScope. Configure at least one."
    )

    def __init__(self, api_key: str = "", settings: Any = None, registry: Any = None) -> None:
        super().__init__(api_key, settings)
        self._registry = registry
        self._entries: dict[str, dict] = {}
        self.models = []
        self.reload()

    # -- catalogue loading -------------------------------------------------------------
    def reload(self) -> None:
        try:
            data = load_models()
        except Exception as error:
            print(f"[catalog] could not load models.json: {error}")
            self.models, self._entries = [], {}
            return

        presets: dict[str, dict] = data.get("option_presets", {})
        specs: list[ModelSpec] = []
        self._entries = {}

        for entry in data.get("models", []):
            model_id = entry.get("id")
            if not model_id:
                continue
            capabilities = []
            for name in entry.get("capabilities", []):
                try:
                    capabilities.append(Capability(name))
                except ValueError:
                    continue
            if not capabilities:
                continue

            options = dict(presets.get(entry.get("options_preset", ""), {}))
            options.update(entry.get("options", {}))

            openness = entry.get("openness", "closed")
            mark = OPENNESS_MARK.get(openness, openness)
            tags = entry.get("tags", [])
            tag_text = f"  [{', '.join(tags)}]" if tags else ""

            notes = entry.get("notes", "")
            if entry.get("repo"):
                notes = f"{notes}\nRepo: {entry['repo']}"
            unverified = [
                route["endpoint"] for route in entry.get("routes", [])
                if not route.get("verified", False)
            ]
            if unverified:
                notes = (
                    f"{notes}\nEndpoint id is unverified. If the call 404s, fix it in "
                    f"Settings -> Model Hub -> Edit catalogue."
                )

            specs.append(ModelSpec(
                id=model_id,
                label=f"{entry.get('label', model_id)}  ({mark}){tag_text}",
                capabilities=capabilities,
                notes=notes.strip(),
                options=options,
            ))
            self._entries[model_id] = entry

        self.models = specs

    def entry(self, model_id: str) -> dict | None:
        return self._entries.get(model_id)

    @property
    def configured(self) -> bool:
        # Usable as soon as any backing provider has a key.
        if self._registry is None:
            return False
        return any(
            provider.configured for provider in self._registry.all
            if provider.key in {"fal", "replicate", "google", "seedance", "omni", "comfyui"}
        )

    # -- routing -----------------------------------------------------------------------
    ROUTE_TO_PROVIDER = {
        "fal": "fal",
        "replicate": "replicate",
        "google": "google",
        "volcengine": "seedance",
        "dashscope": "omni",
        "comfyui": "comfyui",
    }

    def routes_for(self, model_id: str, capability: Capability | str) -> list[dict]:
        # Capability subclasses str, so Qt stores it in widget data as a plain string and
        # hands it back without the enum members. Normalise on the way in.
        wanted = capability.value if isinstance(capability, Capability) else str(capability)
        entry = self._entries.get(model_id, {})
        matching = [
            route for route in entry.get("routes", [])
            if route.get("capability") == wanted
        ]
        # Fall back to any route: many endpoints serve several capabilities.
        return matching or list(entry.get("routes", []))

    def resolve_route(self, model_id: str, capability: Capability,
                      has_workflow: bool = False) -> tuple[Provider, str, dict]:
        """The first route that can actually run now.

        ComfyUI needs no key, so "configured" says nothing about it: a built-in graph
        runs only if its model files are on the server, and a "workflow" route only if
        the user has picked a workflow file. Treating either as ready would stop the
        search before a cloud route that does work.
        """
        if self._registry is None:
            raise ProviderError("Catalogue is not attached to a registry.")

        candidates = self.routes_for(model_id, capability)
        if not candidates:
            raise ProviderError(
                f"'{model_id}' declares no route for {capability.label}. "
                "Add one in Settings -> Model Hub -> Edit catalogue."
            )

        tried: list[str] = []
        for route in candidates:
            provider_key = self.ROUTE_TO_PROVIDER.get(route.get("provider", ""))
            provider = self._registry.get(provider_key) if provider_key else None
            if provider is None:
                continue
            if not provider.configured:
                tried.append(f"{provider.name} (no key)")
                continue
            endpoint = route.get("endpoint", "")
            if provider.key == "comfyui":
                if endpoint == "workflow" and not has_workflow:
                    tried.append("ComfyUI (needs a workflow file: export one with Workflow -> "
                                 "Export (API) and pick it in the 'Workflow path' option)")
                    continue
                if endpoint != "workflow":
                    ready, why = provider.builtin_status(endpoint)
                    if not ready:
                        tried.append(f"ComfyUI ({why})")
                        continue
            return provider, endpoint, route

        detail = "; ".join(tried) if tried else "no matching backend"
        raise ProviderError(
            f"No configured backend can run '{model_id}'. Tried: {detail}.\n"
            "Add an API key in Settings for fal.ai or Replicate -- between them they route "
            "almost every model in the catalogue."
        )

    # -- generation ----------------------------------------------------------------------
    def generate(
        self,
        request: GenerationRequest,
        progress: Callable[[float, str], None] = lambda fraction, message: None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> GenerationResult:
        started = time.time()
        provider, endpoint, route = self.resolve_route(
            request.model_id, request.capability,
            has_workflow=bool(request.options.get("workflow_path")))
        progress(0.02, f"Routing via {provider.name} -> {endpoint}")

        # Hand the concrete provider a request carrying the route's own endpoint id.
        import copy
        delegated = copy.copy(request)
        delegated.model_id = endpoint
        delegated.options = dict(request.options)

        result = provider.generate(delegated, progress=progress, should_cancel=should_cancel)
        result.provider = f"catalog:{provider.key}"
        result.model_id = f"{request.model_id} ({endpoint})"
        result.elapsed_seconds = time.time() - started
        return result
