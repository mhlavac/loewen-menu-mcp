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


# ---------------------------------------------------------------------------
# get_order_history tool — `search` is filtered CLIENT-SIDE on purpose
# ---------------------------------------------------------------------------


def test_search_is_filtered_client_side_and_not_forwarded(mock_server, monkeypatch):
    """IBS5's own ?search= returns an empty table for orders that ARE present.

    Forwarding it produced confident false negatives ("No orders found" for
    orders visible in the unfiltered listing), so the filter is applied to the
    parsed rows instead and the parameter must not be sent.
    """
    import server
    from server import get_order_history

    monkeypatch.setattr(server, "_who", lambda profile: "child1 (Kind, Test Wd-0001-)")
    mock_server.history_html = build_order_history_html(
        [
            ["20.09.2026", "01.10.2026", "1", "Linseneintopf", "", "Test Kind"],
            ["20.09.2026", "02.11.2026", "1", "Milchreis", "", "Test Kind"],
        ]
    )

    # Matches the delivery date of exactly one row.
    out = get_order_history(profile="child1", search="10.2026")
    assert "Linseneintopf" in out
    assert "Milchreis" not in out

    # Matches a description instead.
    out = get_order_history(profile="child1", search="milchreis")
    assert "Milchreis" in out
    assert "Linseneintopf" not in out

    # The broken server-side parameter must never be sent.
    hist = [r for r in mock_server.requests if r.url.path.endswith("/Account/Orderhistory")]
    assert hist, "expected an Orderhistory request"
    assert all("search" not in r.url.params for r in hist)


def test_search_miss_says_so_without_claiming_there_are_no_orders(
    mock_server, monkeypatch
):
    import server
    from server import get_order_history

    monkeypatch.setattr(server, "_who", lambda profile: "child1 (Kind, Test Wd-0001-)")
    mock_server.history_html = build_order_history_html(
        [["20.09.2026", "01.10.2026", "1", "Linseneintopf", "", "Test Kind"]]
    )
    out = get_order_history(profile="child1", search="nothing-matches-this")
    assert "nothing-matches-this" in out
