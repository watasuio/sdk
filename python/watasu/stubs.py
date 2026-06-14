from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict


def unsupported(feature: str):
    raise NotImplementedError(f"{feature} is not supported by Watasu yet")


class _UnsupportedMeta(type):
    def __getattr__(cls, name: str):
        unsupported(f"{cls._feature}.{name}")


class _Unsupported(metaclass=_UnsupportedMeta):
    _feature = "This feature"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._args = args
        self._kwargs = kwargs

    def __getattr__(self, name: str):
        unsupported(f"{self._feature}.{name}")

    def __call__(self, *args: Any, **kwargs: Any):
        unsupported(self._feature)


class AsyncSandbox(_Unsupported):
    _feature = "AsyncSandbox"


class AsyncWatchHandle(_Unsupported):
    _feature = "AsyncWatchHandle"


class AsyncSnapshotPaginator(_Unsupported):
    _feature = "AsyncSnapshotPaginator"


class AsyncTemplate(_Unsupported):
    _feature = "AsyncTemplate"


class Template(_Unsupported):
    _feature = "Template"


class Volume(_Unsupported):
    _feature = "Volume"


class AsyncVolume(_Unsupported):
    _feature = "AsyncVolume"


class VolumeConnectionConfig(_Unsupported):
    _feature = "VolumeConnectionConfig"


class ReadyCmd(_Unsupported):
    _feature = "ReadyCmd"


TemplateBase = str
TemplateClass = type
TemplateBuildStatus = str
BuildStatusReason = str
TemplateTag = str
TemplateTagInfo = Dict[str, Any]
TemplateBuildStatusResponse = Dict[str, Any]
BuildInfo = Dict[str, Any]
CopyItem = Dict[str, Any]
VolumeApiParams = Dict[str, Any]
VolumeInfo = Dict[str, Any]
VolumeAndToken = Dict[str, Any]
VolumeEntryStat = Dict[str, Any]
VolumeFileType = str
OutputHandler = Callable[[str], None]
LogEntry = Dict[str, Any]
LogEntryStart = Dict[str, Any]
LogEntryEnd = Dict[str, Any]
LogEntryLevel = str


def default_build_logger(*args: Any, **kwargs: Any):
    unsupported("template build logger")


def wait_for_file(*args: Any, **kwargs: Any):
    unsupported("ReadyCmd.wait_for_file")


def wait_for_port(*args: Any, **kwargs: Any):
    unsupported("ReadyCmd.wait_for_port")


def wait_for_process(*args: Any, **kwargs: Any):
    unsupported("ReadyCmd.wait_for_process")


def wait_for_timeout(*args: Any, **kwargs: Any):
    unsupported("ReadyCmd.wait_for_timeout")


def wait_for_url(*args: Any, **kwargs: Any):
    unsupported("ReadyCmd.wait_for_url")
