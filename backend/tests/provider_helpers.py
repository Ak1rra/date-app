"""Helpers for the provider tests."""

import json
from typing import Any

import httpx


def ok_json(body: Any) -> httpx.Response:
    """A 200 with `body` as JSON, including the NaN and Infinity that httpx's `json=` refuses."""
    return httpx.Response(
        200, content=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
