import os

import pytest

from watasu import Sandbox, Template


pytestmark = pytest.mark.skipif(
    os.environ.get("WATASU_LIVE_API_TESTS") != "1",
    reason="set WATASU_LIVE_API_TESTS=1 to run live API smoke tests",
)


def test_live_snapshot_list_shape():
    page = Sandbox.list_snapshots(limit=2).next_items()

    assert isinstance(page, list)


def test_live_template_helpers_expose_platform_template_aliases():
    assert Template.exists("base") is True
    assert Template.exists("watasu-live-missing-template") is False
    assert isinstance(Template.get_tags("base"), list)
