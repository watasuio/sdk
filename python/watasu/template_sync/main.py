"""Synchronous template builder.

This module mirrors the public import path used by Python sandbox SDKs while
delegating to Watasu's package-spec template implementation.
"""

from watasu.template import Template

__all__ = ["Template"]
