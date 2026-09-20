"""Tests for bulk_add_to_cart — payloads built correctly + 1-day skip path."""

from datetime import datetime, timedelta

import server
from server import _build_save_order_payload, bulk_add_to_cart


def _future(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _past(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def test_build_save_order_payload_shape():
    payload = _build_save_order_payload("12345", "2026-06-10", "8", "2", 2)
    assert payload == {
        "mealOrderQuantity": {
            "CustomerId": "12345",
            "ServeDate": "2026-06-10",
            "MenuGroupId": "2",
            "MenuLineId": "8",
            "QuantityInShoppingCart": 2,
            "ShoppingCartOrderType": "N",
        }
    }


def test_bulk_add_builds_correct_payloads(mock_server, monkeypatch):
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    items = [
        {"serve_date": _future(3), "menu_line_id": "8"},
        {"serve_date": _future(4), "menu_line_id": "9", "quantity": 2, "menu_group_id": "2"},
    ]
    out = bulk_add_to_cart(profile="child1", items=items)

    payloads = mock_server.save_order_payloads()
    assert len(payloads) == 2  # both future-dated items posted

    p0 = payloads[0]["mealOrderQuantity"]
    assert p0["ServeDate"] == _future(3)
    assert p0["MenuLineId"] == "8"
    assert p0["QuantityInShoppingCart"] == 1  # default quantity
    assert p0["CustomerId"] == "12345"
    assert p0["MenuGroupId"] == "2"

    p1 = payloads[1]["mealOrderQuantity"]
    assert p1["MenuLineId"] == "9"
    assert p1["QuantityInShoppingCart"] == 2

    assert "2 added" in out


def test_bulk_add_skips_past_dates_without_aborting(mock_server, monkeypatch):
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    items = [
        {"serve_date": _past(1), "menu_line_id": "8"},   # too late -> skip
        {"serve_date": _future(3), "menu_line_id": "9"},  # ok
    ]
    out = bulk_add_to_cart(profile="child1", items=items)

    # only the future item produced a SaveOrder POST
    payloads = mock_server.save_order_payloads()
    assert len(payloads) == 1
    assert payloads[0]["mealOrderQuantity"]["MenuLineId"] == "9"

    assert "SKIPPED" in out
    assert "1 added" in out
    assert "1 skipped" in out


def test_bulk_add_skips_today_and_invalid_date(mock_server, monkeypatch):
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    today = datetime.now().strftime("%Y-%m-%d")
    items = [
        {"serve_date": today, "menu_line_id": "8"},        # <1 day advance -> skip
        {"serve_date": "not-a-date", "menu_line_id": "9"},  # invalid -> skip
        {"serve_date": _future(2), "menu_line_id": "10"},   # ok
    ]
    out = bulk_add_to_cart(profile="child1", items=items)
    payloads = mock_server.save_order_payloads()
    assert len(payloads) == 1
    assert payloads[0]["mealOrderQuantity"]["MenuLineId"] == "10"
    assert "2 skipped" in out


def test_bulk_add_reports_cart_total(mock_server, monkeypatch):
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    mock_server.cart_payload = {
        "totalItemsInCart": 3,
        "totalPriceInCart": 10.5,
        "creditBalance": 50.0,
    }
    out = bulk_add_to_cart(
        profile="child1",
        items=[{"serve_date": _future(2), "menu_line_id": "8"}],
    )
    assert "3 item(s)" in out
    assert "10.50" in out
    assert "NOT YET ORDERED" in out


def test_bulk_add_empty_items():
    out = bulk_add_to_cart(profile="child1", items=[])
    assert "0 added" in out


def test_bulk_add_skips_bad_quantity_without_aborting(mock_server, monkeypatch):
    # A non-integer or <1 quantity must skip THAT item, not crash the batch
    # (regression: int() used to run outside the per-item try).
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    items = [
        {"serve_date": _future(3), "menu_line_id": "8", "quantity": "two"},  # bad -> skip
        {"serve_date": _future(4), "menu_line_id": "9", "quantity": 0},      # <1  -> skip
        {"serve_date": _future(5), "menu_line_id": "10"},                    # ok
    ]
    out = bulk_add_to_cart(profile="child1", items=items)

    payloads = mock_server.save_order_payloads()
    assert len(payloads) == 1
    assert payloads[0]["mealOrderQuantity"]["MenuLineId"] == "10"
    assert "2 skipped" in out
    assert "1 added" in out
