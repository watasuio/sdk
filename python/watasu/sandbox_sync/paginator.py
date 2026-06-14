from __future__ import annotations

from typing import Iterable, Iterator, List, TypeVar

T = TypeVar("T")


class SandboxPaginator(Iterable[T]):
    def __init__(self, items: List[T]):
        self._items = items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def list_items(self) -> List[T]:
        return list(self._items)


class SnapshotPaginator(SandboxPaginator[T]):
    pass
