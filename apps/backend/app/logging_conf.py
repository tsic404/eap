"""structlog configuration: JSON to stdout with context propagation and redaction."""

import logging
import sys
from collections.abc import Iterable
from typing import Any, cast

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

# Keys whose values must never reach stdout. Compared case-insensitively after
# normalising ``_`` to ``-`` so ``api_key``, ``API-KEY`` and ``api-key`` all match.
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "token",
        "api-key",
        "secret",
        "x-api-key",
    }
)


def _normalise(key: str) -> str:
    return key.lower().replace("_", "-")


def _redact_sensitive(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Drop secrets from any emitted event before rendering."""
    sanitized: dict[str, Any] = {}
    for key, value in event_dict.items():
        sanitized[key] = "[REDACTED]" if _normalise(key) in _SENSITIVE_KEYS else value
    return sanitized


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog globally. Idempotent — safe to call per-app creation."""
    level_value = getattr(logging, level.upper(), logging.INFO)
    renderer: Processor
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    processors: Iterable[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        structlog.processors.format_exc_info,
        _redact_sensitive,
        renderer,
    ]

    structlog.configure(
        processors=list(processors),
        wrapper_class=structlog.make_filtering_bound_logger(level_value),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, httpx, …) through the same stdout stream so
    # service output is a single JSON stream rather than a mix of formats.
    logging.basicConfig(stream=sys.stdout, level=level_value, format="%(message)s")


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
