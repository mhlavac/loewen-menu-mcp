"""Tests for _parse_order_history against a synthetic HTML table."""

from conftest import build_order_history_html

from server import _parse_order_history


def test_parses_rows_skipping_header():
    html = build_order_history_html(
        [
            ["Mon Jun 01 2026", "Wed Jun 03 2026", "1", "Gemüsepfanne", "", "Test Kind"],
            ["Tue Jun 02 2026", "Thu Jun 04 2026", "2", "Hähnchenkeule", "info", "Test Kind"],
        ]
    )
    orders = _parse_order_history(html)
    assert len(orders) == 2
    first = orders[0]
    assert first["order_date"] == "Mon Jun 01 2026"
    assert first["delivery_date"] == "Wed Jun 03 2026"
    assert first["quantity"] == "1"
    assert first["description"] == "Gemüsepfanne"
    assert first["user"] == "Test Kind"


def test_empty_table_returns_empty_list():
    html = build_order_history_html([])
    assert _parse_order_history(html) == []


def test_handles_missing_optional_cells():
    # Only the 4 required cells present.
    html = build_order_history_html(
        [["Mon Jun 01 2026", "Wed Jun 03 2026", "1", "Linseneintopf"]]
    )
    orders = _parse_order_history(html)
    assert len(orders) == 1
    assert orders[0]["information"] == ""
    assert orders[0]["user"] == ""


def test_short_rows_are_ignored():
    # Rows with fewer than 4 cells are skipped by the parser.
    html = "<html><body><table>" \
        "<tr><th>h</th></tr>" \
        "<tr><td>only</td><td>three</td><td>cells</td></tr>" \
        "</table></body></html>"
    assert _parse_order_history(html) == []
