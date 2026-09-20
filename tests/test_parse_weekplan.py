"""Tests for _parse_weekplan against a synthetic HTML fixture."""

from conftest import build_weekplan_html

from server import _parse_weekplan


def _fixture_html():
    return build_weekplan_html(
        week_info="Calendar Week 24, 2026",
        days={
            "2026-06-08": [
                {
                    "menu_line_id": "8",
                    "meal_name": "Gemüsepfanne",
                    "price": "3,50",
                    "current_quantity": 0,
                    "max_quantity": 2,
                    "ordering_disabled": False,
                },
                {
                    "menu_line_id": "9",
                    "meal_name": "Hähnchenkeule",
                    "price": "4,20",
                    "current_quantity": 1,  # already ordered
                    "max_quantity": 2,
                    "ordering_disabled": False,
                },
                {
                    "menu_line_id": "10",
                    "meal_name": "Milchreis",
                    "price": None,  # missing price
                    "current_quantity": 0,
                    "max_quantity": 2,
                    "ordering_disabled": True,  # expired / not orderable
                },
            ],
            "2026-06-09": [
                {
                    "menu_line_id": "8",
                    "meal_name": "Linseneintopf",
                    "price": "3,50",
                    "current_quantity": 0,
                    "max_quantity": 3,
                    "ordering_disabled": False,
                },
            ],
        },
    )


def test_week_info_and_dates():
    data = _parse_weekplan(_fixture_html())
    assert "Calendar Week 24" in data["week_info"]
    # two distinct serve dates
    assert set(data["by_date"].keys()) == {"2026-06-08", "2026-06-09"}


def test_menu_item_count():
    data = _parse_weekplan(_fixture_html())
    assert len(data["menu_items"]) == 4


def test_price_and_max_parsing():
    data = _parse_weekplan(_fixture_html())
    by_line = {
        (i["serve_date"], i["menu_line_id"]): i for i in data["menu_items"]
    }
    veg = by_line[("2026-06-08", "8")]
    assert veg["price"] == "3,50"
    assert veg["max_quantity"] == 2
    assert veg["line_label"] == "GS 1"

    eintopf = by_line[("2026-06-09", "8")]
    assert eintopf["max_quantity"] == 3


def test_current_quantity_is_already_ordered():
    data = _parse_weekplan(_fixture_html())
    by_line = {
        (i["serve_date"], i["menu_line_id"]): i for i in data["menu_items"]
    }
    ordered = by_line[("2026-06-08", "9")]
    assert ordered["current_quantity"] == 1
    assert ordered["line_label"] == "GS 2"


def test_missing_price_is_none():
    data = _parse_weekplan(_fixture_html())
    by_line = {
        (i["serve_date"], i["menu_line_id"]): i for i in data["menu_items"]
    }
    milchreis = by_line[("2026-06-08", "10")]
    assert milchreis["price"] is None


def test_ordering_disabled_detection():
    data = _parse_weekplan(_fixture_html())
    by_line = {
        (i["serve_date"], i["menu_line_id"]): i for i in data["menu_items"]
    }
    expired = by_line[("2026-06-08", "10")]
    assert expired["ordering_disabled"] is True
    # an orderable line is not disabled
    assert by_line[("2026-06-08", "8")]["ordering_disabled"] is False


def test_line_labels_map_correctly():
    data = _parse_weekplan(_fixture_html())
    labels = {i["menu_line_id"]: i["line_label"] for i in data["menu_items"]}
    assert labels["8"] == "GS 1"
    assert labels["9"] == "GS 2"
    assert labels["10"] == "GS 3"
