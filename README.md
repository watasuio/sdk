# Watasu SDKs

Official SDKs for Watasu.

Each package has a registry-specific README so package managers show examples in
the language the user is installing:

- Python: [python/README.md](python/README.md)
- TypeScript: [ts/README.md](ts/README.md)
- Rust: [rust/README.md](rust/README.md)

`Sandbox.create` and `Sandbox.connect` are single provider operations: the
control-plane API waits for the runtime lifecycle internally and returns success
only with a usable data-plane session. SDKs do not poll sandbox readiness.

Supported sandbox helpers include sandbox list pagination/filtering, streaming
commands, PTY sessions, filesystem read/write/list/watch, Git clone/auth/status/
branches/add/commit/pull/push/remotes/config, signed upload/download URLs,
metrics, disk snapshots, snapshot list pagination, snapshot restore, snapshot
delete, and live sandbox network-policy updates.
