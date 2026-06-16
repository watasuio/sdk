from __future__ import annotations


def unsupported(feature: str):
    raise NotImplementedError(f"{feature} is not supported by Watasu yet")
