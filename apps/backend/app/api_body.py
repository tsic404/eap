"""Request-body reading for endpoints that carry secrets.

Endpoints that take a password read their body here instead of declaring a
pydantic model. The framework's validation failures echo the rejected input in
``details``, which would put a plaintext password into an error response — the
body is therefore parsed by the handler, which reports only field names.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from app.errors import missing_field, validation_error


async def json_object(request: Request) -> dict[str, Any]:
    """Parse a JSON object body; anything unusable reads as an empty object.

    A malformed or absent body must be judged by the endpoint's own rules (it
    may have to answer 404 or 429 before the request fields), so it is read here
    rather than rejected by the framework up front.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def required_text(body: dict[str, Any], *names: str) -> dict[str, str]:
    """Read the named body fields as non-empty strings, or raise ``422``.

    Every unusable field is reported by name in one answer, and the rejected
    value never appears in it — a password must not travel back to the caller
    (F8). Values are returned as given: trimming is the caller's decision.
    """
    values = {name: body.get(name) for name in names}
    usable = {name: value for name, value in values.items() if isinstance(value, str) and value}
    if len(usable) != len(values):
        raise validation_error(*(missing_field(name) for name in names if name not in usable))
    return usable
