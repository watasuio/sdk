"""Compatibility stub for runtime-internal RPC helpers."""

from ._unsupported import module_getattr


def __getattr__(name: str):
    module_getattr("rpc", name)

