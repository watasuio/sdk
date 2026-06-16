"""Compatibility stub for runtime-internal API helpers."""

from ._unsupported import module_getattr


def __getattr__(name: str):
    module_getattr("api", name)

