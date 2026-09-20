"""Tests for get_menu — range spanning two ISO weeks + JSON shape."""

from conftest import build_weekplan_html

from server import get_menu

# 2026-06-12 (Fri, ISO week 24) .. 2026-06-15 (Mon, ISO week 25) spans two weeks.
WEEK24 = (2026, 24)
WEEK25 = (2026, 25)


def _week24_html():
    return build_weekplan_html(
        week_info="Week 24",
        days={
            "2026-06-11": [  # in week 24, but BEFORE the range -> filtered out
                {"menu_line_id": "8", "meal_name": "Gemüsepfanne", "price": "3,50"},
            ],
            "2026-06-12": [
                {"menu_line_id": "8", "meal_name": "Linseneintopf", "price": "3,50"},
                {"menu_line_id": "9", "meal_name": "Hähnchenkeule", "price": "4,20"},
            ],
        },
    )


def _week25_html():
    return build_weekplan_html(
        week_info="Week 25",
        days={
            "2026-06-15": [
                {"menu_line_id": "8", "meal_name": "Kichererbsencurry", "price": "3,80"},
            ],
            "2026-06-16": [  # in week 25 but AFTER the range -> filtered out
                {"menu_line_id": "8", "meal_name": "Milchreis", "price": "2,00"},
            ],
        },
    )


def _setup(mock_server, monkeypatch):
    import server

    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    mock_server.weekplan_by_week = {
        WEEK24: _week24_html(),
        WEEK25: _week25_html(),
    }


def test_range_spans_two_weeks_json(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    result = get_menu(
        profile="child1",
        from_date="2026-06-12",
        to_date="2026-06-15",
        as_json=True,
    )
    dates = [day["date"] for day in result]
    # Only dates inside [from,to] survive; 06-11 and 06-16 filtered out.
    assert dates == ["2026-06-12", "2026-06-15"]


def test_two_weeks_were_fetched(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    get_menu(
        profile="child1",
        from_date="2026-06-12",
        to_date="2026-06-15",
        as_json=True,
    )
    weekplan_reqs = [
        r for r in mock_server.requests if r.url.path.endswith("/Mealplan/Weekplan")
    ]
    weeks = {(int(r.url.params["year"]), int(r.url.params["week"])) for r in weekplan_reqs}
    assert WEEK24 in weeks and WEEK25 in weeks


def test_json_meal_shape(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    result = get_menu(
        profile="child1",
        from_date="2026-06-12",
        to_date="2026-06-15",
        as_json=True,
    )
    day0 = result[0]
    assert day0["date"] == "2026-06-12"
    assert day0["weekday"] == "Friday"
    meal = day0["meals"][0]
    assert set(meal.keys()) == {
        "line_label",
        "menu_line_id",
        "menu_group_id",
        "meal_name",
        "price",
        "max_quantity",
        "already_ordered",
        "orderable",
        "non_meal",
        "tags",
    }
    assert meal["meal_name"] == "Linseneintopf"
    assert meal["tags"]["vegetarian"] is True
    # second meal of the day is poultry
    assert day0["meals"][1]["tags"]["contains"] == ["poultry"]


def test_markdown_output_has_tags(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    out = get_menu(
        profile="child1",
        from_date="2026-06-12",
        to_date="2026-06-15",
    )
    assert isinstance(out, str)
    assert "Linseneintopf" in out
    assert "vegetarian" in out
    assert "poultry" in out  # from Hähnchenkeule tag


def test_single_day_defaults_to_dataset(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    result = get_menu(
        profile="child1",
        from_date="2026-06-15",
        to_date="2026-06-15",
        as_json=True,
    )
    assert [d["date"] for d in result] == ["2026-06-15"]


def test_holiday_row_comes_back_non_orderable(mock_server, monkeypatch):
    """A FERIEN marker sits in a normal menu slot with a working quantity input."""
    _setup(mock_server, monkeypatch)
    mock_server.weekplan_html = build_weekplan_html(
        week_info="Week 43",
        days={"2026-10-19": [{"menu_line_id": "10", "meal_name": "FERIEN"}]},
    )
    result = get_menu(
        profile="child1", from_date="2026-10-19", to_date="2026-10-19", as_json=True
    )
    meal = result[0]["meals"][0]
    assert meal["non_meal"] is True
    assert meal["orderable"] is False
