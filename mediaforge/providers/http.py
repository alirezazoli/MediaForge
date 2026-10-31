"""Shared HTTP helpers so providers stay thin.

Uses `requests` when present, falling back to urllib so the app still runs on a bare
interpreter. Also holds the polling loop that nearly every async video API needs.
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .base import ProviderError

try:  # pragma: no cover - trivial import guard
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_TIMEOUT = 180


def request_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: int = DEFAULT_TIMEOUT,
    proxy: str = "",
) -> dict[str, Any]:
    headers = dict(headers or {})
    payload = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    if payload is not None:
        headers.setdefault("Content-Type", "application/json")

    if requests is not None:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            response = requests.request(
                method, url, headers=headers, data=payload, timeout=timeout, proxies=proxies
            )
        except requests.RequestException as error:
            raise ProviderError(f"Network error calling {url}: {error}") from error
        if response.status_code >= 400:
            raise ProviderError(_describe_http_error(url, response.status_code, response.text))
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"_text": response.text}

    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            body = handle.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        raise ProviderError(
            _describe_http_error(url, error.code, error.read().decode("utf-8", "replace"))
        ) from error
    except urllib.error.URLError as error:
        raise ProviderError(f"Network error calling {url}: {error.reason}") from error
    return json.loads(body) if body else {}


def _describe_http_error(url: str, status: int, body: str) -> str:
    hint = {
        401: "The API key was rejected. Check it in Settings -> Credentials.",
        403: "Access denied -- the key may lack permission for this model, or the model "
             "is not enabled for your account/region.",
        404: "Endpoint or model not found. The model id may have been renamed by the provider.",
        429: "Rate limited or out of quota. Wait, or check billing on the provider console.",
    }.get(status, "")
    snippet = body.strip()[:600]
    return f"HTTP {status} from {url}. {hint}\n{snippet}".strip()


def download(url: str, destination: Path, headers: dict[str, str] | None = None,
             proxy: str = "", timeout: int = 600) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if requests is not None:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        with requests.get(url, headers=headers or {}, stream=True, timeout=timeout,
                          proxies=proxies) as response:
            if response.status_code >= 400:
                raise ProviderError(_describe_http_error(url, response.status_code, response.text))
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    handle.write(chunk)
        return destination

    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as source, destination.open("wb") as handle:
        while chunk := source.read(1 << 16):
            handle.write(chunk)
    return destination


def guess_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def poll_until(
    fetch: Callable[[], dict[str, Any]],
    is_done: Callable[[dict[str, Any]], bool],
    is_failed: Callable[[dict[str, Any]], str | None],
    progress: Callable[[float, str], None],
    label: str = "Generating",
    interval: float = 5.0,
    max_wait: float = 1200.0,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Polls an async job. Progress is a time-based estimate -- most APIs report none."""
    started = time.time()
    while True:
        if should_cancel and should_cancel():
            raise ProviderError("Cancelled")
        payload = fetch()
        failure = is_failed(payload)
        if failure:
            raise ProviderError(failure)
        if is_done(payload):
            return payload
        elapsed = time.time() - started
        if elapsed > max_wait:
            raise ProviderError(
                f"Timed out after {int(elapsed)}s waiting for the provider. "
                "The job may still finish on the provider's console."
            )
        # Asymptotic estimate: approaches but never reaches 95%.
        progress(min(0.95, elapsed / (elapsed + 90.0)), f"{label} ({int(elapsed)}s)")
        time.sleep(interval)
