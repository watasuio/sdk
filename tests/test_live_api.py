import os

import pytest

from watasu import Sandbox


pytestmark = pytest.mark.skipif(
    os.environ.get("WATASU_LIVE_API_TESTS") != "1",
    reason="set WATASU_LIVE_API_TESTS=1 to run live API smoke tests",
)


def test_live_snapshot_list_shape():
    page = Sandbox.list_snapshots(limit=2).next_items()

    assert isinstance(page, list)
