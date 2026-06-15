from __future__ import annotations

from typing import Callable, Generic, Iterable, Iterator, List, Optional, Tuple, TypeVar

T = TypeVar("T")
PageLoader = Callable[[Optional[str], dict], Tuple[List[T], Optional[str]]]


class SandboxPaginator(Iterable[T], Generic[T]):
    """Paginator for listing Watasu sandboxes.

    ``next_items`` fetches one page. ``list_items`` drains all remaining pages
    and returns them as a list.
    """

    def __init__(
        self,
        items: Optional[List[T]] = None,
        *,
        load_page: Optional[PageLoader[T]] = None,
        next_token: Optional[str] = None,
    ):
        self._items = items
        self._load_page = load_page
        self._next_token = next_token
        self._has_next = load_page is not None

    @property
    def has_next(self) -> bool:
        """Return whether another page can be fetched."""
        return self._has_next

    @property
    def next_token(self) -> Optional[str]:
        """Return the pagination cursor that will be used on the next request."""
        return self._next_token

    def __iter__(self) -> Iterator[T]:
        return iter(self.list_items())

    def list_items(self) -> List[T]:
        if self._load_page is None:
            return list(self._items or [])

        items: List[T] = []
        while self.has_next:
            items.extend(self.next_items())
        return items

    def next_items(self, **opts) -> List[T]:
        """Fetch and return the next page of items."""
        if not self.has_next:
            raise Exception("No more items to fetch")

        if self._load_page is None:
            self._has_next = False
            return list(self._items or [])

        items, next_token = self._load_page(self._next_token, opts)
        self._next_token = next_token
        self._has_next = bool(next_token)
        return items


class SnapshotPaginator(SandboxPaginator[T]):
    pass
