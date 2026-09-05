"""Small shared deadline policy for external Vertex AI calls."""

from __future__ import annotations

import os

from google.genai import types


EXTERNAL_AI_TIMEOUT_SECONDS = float(
    os.environ.get("EXTERNAL_AI_TIMEOUT_SECONDS", "45")
)
if EXTERNAL_AI_TIMEOUT_SECONDS <= 0:
    raise ValueError("EXTERNAL_AI_TIMEOUT_SECONDS must be positive")

AI_HTTP_OPTIONS = types.HttpOptions(
    timeout=max(1, round(EXTERNAL_AI_TIMEOUT_SECONDS * 1000))
)


class ExternalCallTimeout(TimeoutError):
    """An external AI request exceeded the configured application deadline."""


def is_timeout_error(exc: BaseException) -> bool:
    """Recognize timeout wrappers used by httpx, Google APIs, and the ADK."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__.lower()
        message = str(current).lower()
        if (
            isinstance(current, TimeoutError)
            or "timeout" in name
            or "timed out" in message
            or "deadline exceeded" in message
        ):
            return True
        current = current.__cause__ or current.__context__
    return False
