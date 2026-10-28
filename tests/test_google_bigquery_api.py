"""The BigQuery half of the Google helper routes.

Only the dataset-location read is covered: it is the one place where a wrong
answer is invisible. A dataset outside US still lists, still fills the dropdown,
and only fails later when the property-graph query is aimed at the wrong region
-- at which point the form looks like it simply has no graphs.
"""

from __future__ import annotations

from kineviz_database_proxy.api.google import _dataset_location


class ListItem:
    """What `client.list_datasets()` yields: the raw resource, no location property."""

    def __init__(self, resource):
        self._properties = resource


def test_the_location_is_read_off_the_listed_resource():
    # `DatasetListItem` exposes dataset_id and little else, so a plain attribute
    # read returns None for every dataset there is.
    assert _dataset_location(ListItem({"location": "EU"})) == "EU"


def test_a_listing_without_a_location_gives_none_rather_than_guessing():
    assert _dataset_location(ListItem({})) is None


def test_a_real_location_attribute_wins():
    # `client.get_dataset()` returns a full `Dataset`, which does have one.
    class Full:
        location = "asia-northeast1"
        _properties = {"location": "US"}

    assert _dataset_location(Full()) == "asia-northeast1"


def test_an_object_with_neither_is_tolerated():
    assert _dataset_location(object()) is None
