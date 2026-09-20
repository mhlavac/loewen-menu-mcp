"""Tests for plan_order (dry run) — every conflict type, happy path, order_total.

The pure planning core (_plan_lines) is exercised directly without HTTP; the
plan_order tool is exercised end-to-end over the MockTransport.
"""

from conftest import build_weekplan_html

import server
from server import _parse_weekplan, _plan_lines, plan_order


def _by_date():
    html = build_weekplan_html(
        week_info="Week 24",
        days={
            "2026-06-08": [
                {"menu_line_id": "8", "meal_name": "Gemüsepfanne", "price": "3,50"},
                {
                    "menu_line_id": "9",
                    "meal_name": "Hähnchenkeule",
                    "price": "4,20",
                    "ordering_disabled": True,  # not orderable
                },
                {
                    "menu_line_id": "10",
                    "meal_name": "Milchreis",
                    "price": "2,00",
                    "current_quantity": 1,  # already ordered
                },
            ],
        },
    )
    return _parse_weekplan(html)["by_date"]


def test_happy_path_and_order_total():
    by_date = _by_date()
    # Two orderable lines: 3,50 only (line 8). Add another date/line that's fine.
    by_date["2026-06-09"] = [
        {
            "serve_date": "2026-06-09",
            "menu_group_id": "2",
            "menu_line_id": "8",
            "line_label": "GS 1",
            "meal_name": "Linseneintopf",
            "price": "3,50",
            "current_quantity": 0,
            "max_quantity": 2,
            "ordering_disabled": False,
        }
    ]
    items = [
        {"serve_date": "2026-06-08", "menu_line_id": "8"},
        {"serve_date": "2026-06-09", "menu_line_id": "8"},
    ]
    plan = _plan_lines(items, by_date)
    assert plan["order_total"] == 7.0
    assert plan["counts"] == {"orderable": 2, "conflicts": 0}
    assert all(line["conflict"] is None for line in plan["lines"])
    # tags surface
    assert plan["lines"][0]["tags"]["vegetarian"] is True


def test_conflict_no_school_day():
    by_date = _by_date()
    plan = _plan_lines(
        [{"serve_date": "2026-12-25", "menu_line_id": "8"}], by_date
    )
    assert plan["lines"][0]["conflict"] == "no_school_day"
    assert plan["counts"]["conflicts"] == 1
    # No priced line contributed, so the total is UNKNOWN, not free.
    assert plan["order_total"] is None
    assert plan["price_data_available"] is False


def test_conflict_unknown_line():
    by_date = _by_date()
    plan = _plan_lines(
        [{"serve_date": "2026-06-08", "menu_line_id": "99"}], by_date
    )
    assert plan["lines"][0]["conflict"] == "unknown_line"


def test_conflict_not_orderable():
    by_date = _by_date()
    plan = _plan_lines(
        [{"serve_date": "2026-06-08", "menu_line_id": "9"}], by_date
    )
    assert plan["lines"][0]["conflict"] == "not_orderable"
    assert plan["lines"][0]["orderable"] is False


def test_conflict_already_ordered():
    by_date = _by_date()
    plan = _plan_lines(
        [{"serve_date": "2026-06-08", "menu_line_id": "10"}], by_date
    )
    line = plan["lines"][0]
    assert line["conflict"] == "already_ordered"
    assert line["already_ordered"] == 1
    # orderable True but excluded from total because of the conflict
    assert line["orderable"] is True
    assert plan["order_total"] is None
    assert plan["price_data_available"] is False


def test_order_total_only_counts_clean_orderable_lines():
    by_date = _by_date()
    items = [
        {"serve_date": "2026-06-08", "menu_line_id": "8"},   # clean 3,50
        {"serve_date": "2026-06-08", "menu_line_id": "9"},   # not_orderable
        {"serve_date": "2026-06-08", "menu_line_id": "10"},  # already_ordered
        {"serve_date": "2026-12-25", "menu_line_id": "8"},   # no_school_day
    ]
    plan = _plan_lines(items, by_date)
    assert plan["order_total"] == 3.5
    assert plan["counts"] == {"orderable": 1, "conflicts": 3}


def test_plan_order_tool_json_over_mock(mock_server, monkeypatch):
    # Avoid a real numeric-id lookup; plan_order only GETs weekplans anyway.
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    mock_server.weekplan_html = build_weekplan_html(
        week_info="Week 24",
        days={
            "2026-06-08": [
                {"menu_line_id": "8", "meal_name": "Gemüsepfanne", "price": "3,50"},
            ],
        },
    )
    result = plan_order(
        profile="child1",
        items=[{"serve_date": "2026-06-08", "menu_line_id": "8"}],
        as_json=True,
    )
    assert result["order_total"] == 3.5
    assert result["counts"]["orderable"] == 1
    assert result["lines"][0]["meal_name"] == "Gemüsepfanne"
    # plan_order must NOT mutate — no SaveOrder calls
    assert mock_server.save_order_payloads() == []


def test_plan_order_tool_markdown(mock_server, monkeypatch):
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    mock_server.weekplan_html = build_weekplan_html(
        week_info="Week 24",
        days={
            "2026-06-08": [
                {"menu_line_id": "8", "meal_name": "Gemüsepfanne", "price": "3,50"},
            ],
        },
    )
    out = plan_order(
        profile="child1",
        items=[{"serve_date": "2026-06-08", "menu_line_id": "8"}],
    )
    assert isinstance(out, str)
    assert "DRY RUN" in out
    assert "Order total" in out


def test_priced_plan_reports_price_data_available():
    by_date = _by_date()
    plan = _plan_lines([{"serve_date": "2026-06-08", "menu_line_id": "8"}], by_date)
    assert plan["price_data_available"] is True
    assert isinstance(plan["order_total"], float)


def test_holiday_marker_is_flagged_as_non_meal():
    # IBS5 renders "FERIEN" in an ordinary menu slot with a working quantity
    # input; it must never come back orderable.
    by_date = {
        "2026-10-19": [
            {
                "serve_date": "2026-10-19",
                "menu_line_id": "10",
                "menu_group_id": "2",
                "line_label": "GS 3",
                "meal_name": "FERIEN",
                "price": None,
                "current_quantity": 0,
                "max_quantity": 2,
                "ordering_disabled": False,
            }
        ]
    }
    plan = _plan_lines(
        [{"serve_date": "2026-10-19", "menu_line_id": "10"}], by_date
    )
    line = plan["lines"][0]
    assert line["conflict"] == "non_meal"
    assert line["orderable"] is False
    assert plan["counts"]["orderable"] == 0


def test_plan_order_markdown_survives_missing_prices(mock_server, monkeypatch):
    """Regression: the None order_total sentinel crashed the markdown renderer.

    IBS5 publishes no per-meal price for many schools, so data-price is empty
    and order_total is None. f"€{None:.2f}" raises TypeError, which took down
    the whole plan_order tool for exactly the case the sentinel was added for.
    """
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    mock_server.weekplan_html = build_weekplan_html(
        week_info="Week 24",
        days={
            "2026-06-08": [
                # price=None -> no data-price, exactly like the live server,
                # which emits an empty data-price for schools without prices.
                {"menu_line_id": "8", "meal_name": "Gemüsepfanne", "price": None},
            ],
        },
    )
    out = plan_order(
        profile="child1",
        items=[{"serve_date": "2026-06-08", "menu_line_id": "8"}],
        as_json=False,
    )
    assert isinstance(out, str)
    assert "unknown" in out.lower()
    # Must not render a numeric total at all — a €0.00 here reads as "free".
    assert "Order total (orderable lines):" not in out
    assert "Orderable: 1" in out
