"""Minimal JSON-over-HTTP client for the local Ollama server (standard-library urllib only).

Shared by embedding_service.py and llm_service.py. Every failure raises the caller's error
class with a clear message and a `kind` ("timeout", "unavailable", "http", "invalid_response")
so callers can react without parsing messages; nothing is retried or hidden here.
"""

import http.client
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from backend.app.config import MissingSettingError


class OllamaError(RuntimeError):
    """Base class for failures talking to Ollama. kind says what went wrong."""

    def __init__(self, message: str, kind: str = "invalid_response") -> None:
        super().__init__(message)
        self.kind = kind


def ollama_url(base_url: str, path: str) -> str:
    """Join OLLAMA_BASE_URL and an API path; only http(s) URLs are accepted."""
    base_url = base_url.rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise MissingSettingError(f"OLLAMA_BASE_URL must be an http(s) URL, got {base_url!r}")
    return f"{base_url}{path}"


def post_json(
    base_url: str, path: str, body: dict[str, Any], timeout: float, error: type[OllamaError] = OllamaError
) -> Any:
    """POST body as JSON and return the decoded JSON response, raising `error` on any failure."""
    request = urllib.request.Request(
        ollama_url(base_url, path), data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    return _send(request, body.get("model"), timeout, error)


def get_json(base_url: str, path: str, timeout: float, error: type[OllamaError] = OllamaError) -> Any:
    """GET a read-only metadata endpoint (e.g. /api/version, /api/tags, /api/ps)."""
    return _send(urllib.request.Request(ollama_url(base_url, path), method="GET"), None, timeout, error)


def _send(request: urllib.request.Request, model: str | None, timeout: float, error: type[OllamaError]) -> Any:
    url = request.full_url
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise error(f"Ollama returned HTTP {exc.code} for model {model!r}: {_error_detail(exc)}", "http") from exc
    except TimeoutError as exc:
        raise error(f"Ollama did not respond within {timeout:g} s at {url}", "timeout") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise error(f"Ollama did not respond within {timeout:g} s at {url}", "timeout") from exc
        raise error(f"Cannot reach Ollama at {url} - is it running? ({exc.reason})", "unavailable") from exc
    except (OSError, http.client.HTTPException) as exc:  # e.g. connection reset mid-response
        raise error(f"Connection to Ollama at {url} failed: {exc!r}", "unavailable") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error(f"Ollama returned a response that is not valid JSON: {exc}", "invalid_response") from exc


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """Ollama reports errors as {"error": "..."}; fall back to the raw body."""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except OSError:
        return exc.reason
    try:
        return json.loads(raw).get("error", raw)
    except (json.JSONDecodeError, AttributeError):
        return raw or exc.reason
