"""Is this model actually usable right now?

One answer, computed in one place, so the Generate panel, the Director and the Model Hub
can never disagree about whether something is selectable.

Three states:
  FREE       runs with no API key at all -- a local engine or a local ComfyUI graph
  READY      a key is configured for a backend that can run it
  LOCKED     no configured backend; the UI fades it out and says what is missing
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .providers import Capability, Provider, Registry


class State(str, Enum):
    FREE = "free"
    READY = "ready"
    LOCKED = "locked"


@dataclass(frozen=True)
class Availability:
    state: State
    reason: str = ""
    needs: tuple[str, ...] = ()
    # False when it runs only with something the user supplies by hand -- a ComfyUI
    # workflow file. Selectable on the Generate screen, where that file can be picked;
    # never chosen automatically, because nothing could supply it.
    unattended: bool = True

    @property
    def usable(self) -> bool:
        return self.state is not State.LOCKED

    @property
    def badge(self) -> str:
        return {State.FREE: "FREE", State.READY: "", State.LOCKED: "needs a key"}[self.state]


# Backends that cost nothing and need no credential.
NO_KEY_PROVIDERS = {"comfyui", "ollama"}


def provider_availability(provider: Provider) -> Availability:
    if provider.key in NO_KEY_PROVIDERS:
        # Needing no key is not the same as being usable. A local engine that is not
        # running should read as locked, with the fix stated, rather than as ready.
        check = getattr(provider, "reachable", None)
        if callable(check) and not check():
            url = getattr(provider, "base_url", "the configured address")
            return Availability(
                State.LOCKED,
                f"{provider.name} is not running at {url}. Start it (Model Hub -> Local "
                f"engines) and it is ready to use.",
                (provider.name,),
            )
        return Availability(State.FREE, "Runs locally on your machine.")
    if not provider.credential_slot:
        return Availability(State.FREE, "No API key required")
    if provider.configured:
        return Availability(State.READY)
    return Availability(
        State.LOCKED,
        f"{provider.name} has no API key. {provider.credential_help}",
        (provider.name,),
    )


def as_capability(value: Capability | str) -> Capability:
    """Qt round-trips a str-backed enum as a bare string; put it back."""
    if isinstance(value, Capability):
        return value
    try:
        return Capability(str(value))
    except ValueError:
        return Capability.TEXT_TO_IMAGE


def catalog_model_availability(
    registry: Registry, model_id: str, capability: Capability | str
) -> Availability:
    """For a catalogue entry: can any of its declared routes actually run today?"""
    capability = as_capability(capability)
    catalog = registry.get("catalog")
    if catalog is None:
        return Availability(State.LOCKED, "Catalogue unavailable")

    routes = catalog.routes_for(model_id, capability)
    if not routes:
        return Availability(State.LOCKED, "No route declared for this task", ())

    missing: list[str] = []
    local_hint = ""
    by_hand: Availability | None = None
    for route in routes:
        provider_key = catalog.ROUTE_TO_PROVIDER.get(route.get("provider", ""))
        provider = registry.get(provider_key) if provider_key else None
        if provider is None:
            continue
        if provider.key == "comfyui":
            # A ComfyUI route is only as good as its endpoint: a built-in graph runs by
            # itself if its files are there; "workflow" needs a file the user exports.
            state = comfyui_availability(provider, route.get("endpoint", "workflow"))
            if state.state is State.FREE and state.unattended:
                return state
            if state.state is State.FREE:
                by_hand = by_hand or state
            else:
                local_hint = local_hint or state.reason
            continue
        state = provider_availability(provider)
        if state.state is State.FREE:
            return Availability(
                State.FREE, f"Runs locally through {provider.name} — no key, no cost"
            )
        if state.state is State.READY:
            return Availability(State.READY, f"Runs through {provider.name}")
        # A local engine is blocked by not running, not by a missing key -- keep its own
        # wording rather than folding it into "needs an API key".
        if provider.key in NO_KEY_PROVIDERS:
            local_hint = state.reason
        else:
            missing.append(provider.name)

    if by_hand is not None:
        return by_hand
    unique = tuple(dict.fromkeys(missing))
    if unique and local_hint:
        reason = (f"Needs an API key for {' or '.join(unique)} — or run it free locally: "
                  f"{local_hint}")
    elif unique:
        reason = f"Needs an API key for {' or '.join(unique)}"
    elif local_hint:
        reason = local_hint
    else:
        reason = "No configured backend can run this"

    return Availability(State.LOCKED, reason, unique)


def model_availability(
    registry: Registry, provider_key: str, model_id: str, capability: Capability | str
) -> Availability:
    capability = as_capability(capability)
    if provider_key == "catalog":
        return catalog_model_availability(registry, model_id, capability)
    provider = registry.get(provider_key)
    if provider is None:
        return Availability(State.LOCKED, "Provider not found")
    if provider.key == "comfyui":
        return comfyui_availability(provider, model_id)
    return provider_availability(provider)


def _rank(state: Availability) -> int:
    if state.state is State.FREE and state.unattended:
        return 0
    if state.state is State.READY:
        return 1
    return 2 if state.usable else 3


def provider_availability_for(registry: Registry, provider: Provider,
                              capability: Capability | str) -> Availability:
    """How usable a provider is for one task: the best of its models for that task.

    The provider alone is the wrong question. The catalogue needs no key itself, so it read
    as ready and was picked first for stills -- and then every model in it needed a key.
    """
    capability = as_capability(capability)
    best: Availability | None = None
    for spec in provider.models_for(capability):
        state = model_availability(registry, provider.key, spec.id, capability)
        if best is None or _rank(state) < _rank(best):
            best = state
        if _rank(best) == 0:
            break
    return best or provider_availability(provider)


def comfyui_availability(provider: Provider, endpoint: str) -> Availability:
    """A built-in graph is ready when its files are; a workflow needs one picked by hand."""
    status = getattr(provider, "builtin_status", None)
    if callable(status) and endpoint != "workflow":
        ready, reason = status(endpoint)
        return Availability(State.FREE if ready else State.LOCKED, reason,
                            () if ready else (provider.name,))
    state = provider_availability(provider)
    if state.state is not State.FREE:
        return state
    return Availability(State.FREE, "Runs in your ComfyUI with a workflow you export for it "
                        "(ComfyUI: Workflow -> Export (API)).", unattended=False)


def free_models(registry: Registry) -> list[tuple[str, str, Availability]]:
    """Everything usable right now with no API key: (provider_key, model_id, availability)."""
    found: list[tuple[str, str, Availability]] = []
    for provider in registry.all:
        for spec in provider.models:
            for capability in spec.capabilities:
                state = model_availability(registry, provider.key, spec.id, capability)
                # Counted only if it runs as it stands: a model that first needs a
                # workflow file exported by hand is not "ready with no key".
                if state.state is State.FREE and state.unattended:
                    found.append((provider.key, spec.id, state))
                    break
    return found


def decorate_combo(combo, entries: list[tuple[str, Any, Availability]]) -> int:
    """Fills a QComboBox, disabling and fading anything locked.

    entries: (label, userData, availability). Returns the index of the first usable row,
    so callers can land the selection somewhere that actually works.
    """
    from PySide6.QtGui import QBrush, QColor

    from .ui import theme

    combo.blockSignals(True)
    combo.clear()
    first_usable = -1
    first_by_hand = -1

    for label, data, state in entries:
        suffix = ""
        if state.state is State.FREE:
            suffix = "   ·  no key" if state.unattended else "   ·  needs a workflow file"
        elif state.state is State.LOCKED:
            # A local engine that is not running is not missing a key; say which it is.
            suffix = "   ·  needs a key" if "key" in state.reason.lower() else "   ·  not ready"
        combo.addItem(f"{label}{suffix}", data)
        index = combo.count() - 1
        item = combo.model().item(index)
        if item is None:
            continue
        item.setToolTip(state.reason)
        if state.state is State.LOCKED:
            item.setEnabled(False)
            item.setForeground(QBrush(QColor(theme.TEXT_FAINT)))
        elif state.state is State.FREE:
            item.setForeground(QBrush(QColor(theme.FREE)))
        if state.usable and state.unattended and first_usable < 0:
            first_usable = index
        elif state.usable and first_by_hand < 0:
            first_by_hand = index

    # Land on something that runs as it is; a workflow-file route only if nothing else can.
    if first_usable < 0:
        first_usable = first_by_hand
    if first_usable >= 0:
        combo.setCurrentIndex(first_usable)
    combo.blockSignals(False)
    return first_usable
