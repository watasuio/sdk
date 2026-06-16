"""Compatibility namespace for lower-level runtime internals.

The public Watasu SDK talks to Watasu REST and WebSocket runtime endpoints.
This namespace exists so import-level compatible code can resolve generated
runtime-internal modules without silently depending on an unsupported protocol.
"""

from . import api, filesystem, process, rpc, versions

__all__ = ["api", "filesystem", "process", "rpc", "versions"]

