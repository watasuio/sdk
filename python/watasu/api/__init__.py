from __future__ import annotations

from typing import Any

from watasu._transport.control import ControlClient
from watasu.connection_config import ConnectionConfig


class ApiClient(ControlClient):
    def __init__(self, **opts: Any):
        super().__init__(ConnectionConfig(**opts))


def client(**opts: Any) -> ApiClient:
    return ApiClient(**opts)


__all__ = ["ApiClient", "client"]
