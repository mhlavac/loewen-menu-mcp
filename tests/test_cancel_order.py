"""Tests for cancel_order and the account-identity tools over MockTransport.

SYNTHETIC DATA ONLY — see the note at the top of conftest.py.

cancel_order is the tool that undoes a confirmed order. IBS5 never deletes a
row; it books an offsetting negative-quantity row. The tool must therefore
prove a NEW negative row appeared, not merely that one exists.
"""

from conftest import build_order_history_html, build_weekplan_html

import server
from server import cancel_order, list_profiles


def _history(rows):
    return build_order_history_html(rows)


def _no_cancellations():
    return _history(
        [["20.09.2026", "08.06.2026", "1", "Gemüsepfanne", "", "Test Kind"]]
    )


def _with_cancellation():
    return _history(
        [
            ["20.09.2026", "08.06.2026", "1", "Gemüsepfanne", "", "Test Kind"],
            ["20.09.2026", "08.06.2026", "-1", "Gemüsepfanne", "", "Test Kind"],
        ]
    )


def _setup(mock_server, monkeypatch):
    monkeypatch.setattr(server, "_get_numeric_customer_id", lambda *a, **k: "12345")
    mock_server.weekplan_html = build_weekplan_html("Week 24", {})


def test_cancel_order_verifies_a_new_offsetting_row(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    # before -> no cancellation; after -> one appeared
    mock_server.history_sequence = [_no_cancellations(), _with_cancellation()]
    out = cancel_order(
        profile="child1",
        items=[{"serve_date": "2026-06-08", "menu_line_id": "9"}],
    )
    assert "✅ 2026-06-08" in out
    assert "1/1 verified cancelled" in out
    # It must actually stage quantity 0, not just confirm an empty cart.
    payloads = mock_server.save_order_payloads()
    assert len(payloads) == 1
    assert payloads[0]["mealOrderQuantity"]["QuantityInShoppingCart"] == 0
    assert payloads[0]["mealOrderQuantity"]["ServeDate"] == "2026-06-08"


def test_cancel_order_reports_when_nothing_changed(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    # No cancellation row ever appears -> past the cutoff, still ordered.
    mock_server.history_sequence = [_no_cancellations(), _no_cancellations()]
    out = cancel_order(
        profile="child1",
        items=[{"serve_date": "2026-06-08", "menu_line_id": "9"}],
    )
    assert "STILL ORDERED" in out
    assert "0/1 verified cancelled" in out


def test_stale_cancellation_row_is_not_counted_as_success(mock_server, monkeypatch):
    """The regression this verification exists to prevent.

    A negative row left over from an earlier run must not be read as proof
    that THIS call cancelled anything.
    """
    _setup(mock_server, monkeypatch)
    mock_server.history_sequence = [_with_cancellation(), _with_cancellation()]
    out = cancel_order(
        profile="child1",
        items=[{"serve_date": "2026-06-08", "menu_line_id": "9"}],
    )
    assert "NOT new" in out
    assert "0/1 verified cancelled" in out


def test_cancel_order_rejects_bad_dates_without_confirming(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    out = cancel_order(profile="child1", items=[{"serve_date": "08/06/2026", "menu_line_id": "9"}])
    assert "bad date format" in out
    assert "Nothing staged" in out
    assert mock_server.save_order_payloads() == []


def test_cancel_order_with_no_items_is_a_noop(mock_server, monkeypatch):
    _setup(mock_server, monkeypatch)
    out = cancel_order(profile="child1", items=[])
    assert "nothing to cancel" in out.lower()
    assert mock_server.save_order_payloads() == []


# ---------------------------------------------------------------------------
# list_profiles — the mismatch warning is the whole safety feature
# ---------------------------------------------------------------------------


def test_list_profiles_reports_holder_and_no_warning_when_it_matches(
    mock_server, monkeypatch
):
    mock_server.login_json = {
        "name1": "Kind, Child1          Wd-0001-",
        "institutionName1": "GS Teststrasse",
        "email": "test@example.invalid",
    }
    out = list_profiles()
    assert "Kind, Child1 Wd-0001-" in out  # padding collapsed
    assert "GS Teststrasse" in out
    # conftest configures two profiles against one synthetic holder, so child2
    # is EXPECTED to be flagged; the point here is that child1 is not.
    assert "labelled `child1`" not in out
    assert "labelled `child2`" in out


def test_list_profiles_warns_loudly_when_the_holder_is_someone_else(
    mock_server, monkeypatch
):
    # Every profile resolves to a holder whose name is NOT the profile label.
    mock_server.login_json = {
        "name1": "Andersson, Someone Else Wd-0009-",
        "institutionName1": "GS Teststrasse",
        "email": "test@example.invalid",
    }
    out = list_profiles()
    assert "MISMATCH" in out
    assert "before ordering" in out.lower()


def test_list_profiles_states_the_reason_when_identity_cannot_be_read(
    mock_server, monkeypatch
):
    def boom(*a, **k):
        raise RuntimeError("synthetic outage")

    monkeypatch.setattr(server, "_fetch_account_identity", boom)
    monkeypatch.setattr(server, "_fetch_identity_page", boom)
    out = list_profiles()
    assert "UNVERIFIED" in out
    assert "synthetic outage" in out
    assert "do not assume" in out.lower()


def test_fetch_account_identity_posts_the_browsers_login_shape(mock_server):
    info = server._fetch_account_identity("S000001", "1234")
    assert info["holder"] == "Kind, Test Wd-0001-"
    assert info["school"] == "GS Teststrasse"
    login_reqs = [r for r in mock_server.requests if r.url.path.endswith("/Login/Login")]
    assert len(login_reqs) == 1
    body = login_reqs[0].content.decode()
    # Form-encoded, mirroring the web client exactly.
    assert "identifierValue=S000001" in body
    assert "identifierType=0" in body
    assert "secretType=0" in body
