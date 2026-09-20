"""Tests for account-identity parsing and cancellation verification.

SYNTHETIC DATA ONLY — synthetic holder names and building labels throughout.
"""

from server import (
    _cancelled_delivery_dates,
    _normalize_name,
    _parse_account_info,
    _profile_matches_holder,
)


def build_nav_html(holder="Kind, Test Wd-0001-", school="GS Teststrasse",
                   mail="test@example.invalid", include=("lsk", "name", "mail")):
    parts = []
    if "lsk" in include:
        parts.append(f'<div class="navigation-lsk">{school}</div>')
    if "name" in include:
        parts.append(f'<div class="navigation-name">{holder}</div>')
    if "mail" in include:
        parts.append(f'<div class="navigation-mail">{mail}</div>')
    return (
        "<html><body><nav class='main-navigation'><ul class='main-navigation-inner'>"
        f"<li class='display-main-menu'>{''.join(parts)}</li>"
        "</ul></nav></body></html>"
    )


# ---------------------------------------------------------------------------
# _parse_account_info
# ---------------------------------------------------------------------------


def test_extracts_holder_school_and_mail():
    info = _parse_account_info(build_nav_html())
    assert info["holder"] == "Kind, Test Wd-0001-"
    assert info["school"] == "GS Teststrasse"
    assert info["email"] == "test@example.invalid"


def test_missing_nav_block_yields_empty_strings():
    info = _parse_account_info("<html><body><p>nothing here</p></body></html>")
    assert info == {"holder": "", "school": "", "email": ""}


def test_partial_nav_block_still_parses_what_is_present():
    info = _parse_account_info(build_nav_html(include=("name",)))
    assert info["holder"] == "Kind, Test Wd-0001-"
    assert info["school"] == ""


# ---------------------------------------------------------------------------
# _profile_matches_holder — the guard against a swapped PROFILES entry
# ---------------------------------------------------------------------------


def test_first_name_inside_holder_matches():
    assert _profile_matches_holder("test", "Kind, Test Wd-0001-")


def test_wrong_child_is_flagged():
    assert not _profile_matches_holder("child2", "Kind, Test Wd-0001-")


def test_match_ignores_diacritics_and_case():
    assert _profile_matches_holder("jarmila", "Nováková, Jarmíla St-0002-")
    assert _normalize_name("Nováková") == "novakova"


def test_empty_inputs_do_not_raise_false_alarms():
    # Nothing to contradict -> treated as matching, so a missing holder never
    # produces a scary mismatch warning.
    assert _profile_matches_holder("", "Kind, Test Wd-0001-")
    assert _profile_matches_holder("test", "")


# ---------------------------------------------------------------------------
# _cancelled_delivery_dates — IBS5 books cancellations as negative rows
# ---------------------------------------------------------------------------


def test_picks_up_negative_quantity_rows():
    orders = [
        {"delivery_date": "30.10.2026", "quantity": "-1"},
        {"delivery_date": "30.10.2026", "quantity": "1"},
        {"delivery_date": "29.10.2026", "quantity": "1"},
    ]
    assert _cancelled_delivery_dates(orders) == {"2026-10-30"}


def test_ignores_unparseable_quantities_and_dates():
    orders = [
        {"delivery_date": "30.10.2026", "quantity": "n/a"},
        {"delivery_date": "not-a-date", "quantity": "-1"},
        {"delivery_date": "", "quantity": ""},
        {},
    ]
    assert _cancelled_delivery_dates(orders) == set()


def test_multiple_cancellations_collected():
    orders = [
        {"delivery_date": "01.10.2026", "quantity": "-1"},
        {"delivery_date": "02.10.2026", "quantity": "-2"},
        {"delivery_date": "05.10.2026", "quantity": "3"},
    ]
    assert _cancelled_delivery_dates(orders) == {"2026-10-01", "2026-10-02"}


# ---------------------------------------------------------------------------
# _is_non_meal — holiday markers occupy ordinary menu slots
# ---------------------------------------------------------------------------


def test_holiday_markers_are_not_meals():
    from server import _is_non_meal

    for marker in ("FERIEN", "FERIENSTART!!!", "Feiertag", "kein Angebot", ""):
        assert _is_non_meal(marker), marker


def test_real_dishes_are_meals():
    from server import _is_non_meal

    for name in ("Milchreis mit Apfelmus", "Linseneintopf mit Kartoffeln"):
        assert not _is_non_meal(name), name


# ---------------------------------------------------------------------------
# Holder fallback — identity must survive a markup change
# ---------------------------------------------------------------------------


def test_holder_found_via_partial_class_match():
    html = (
        "<html><body><div class='sidebar navigation-name-v2'>"
        "Kind, Test Wd-0001-</div></body></html>"
    )
    assert _parse_account_info(html)["holder"] == "Kind, Test Wd-0001-"


def test_holder_found_by_shape_when_classes_absent():
    # No recognizable classes at all — the "Surname, First Bldg-1234-" shape
    # is distinctive enough to recover on its own.
    html = "<html><body><span>Kind, Test Wd-0001-</span><p>other text</p></body></html>"
    assert _parse_account_info(html)["holder"] == "Kind, Test Wd-0001-"


def test_unrelated_prose_does_not_produce_a_false_holder():
    html = "<html><body><p>Guten Tag, willkommen im Bestellsystem.</p></body></html>"
    assert _parse_account_info(html)["holder"] == ""


# ---------------------------------------------------------------------------
# _parse_login_identity — POST /Login/Login is the real identity source
# ---------------------------------------------------------------------------


def test_login_identity_projection():
    from server import _parse_login_identity

    info = _parse_login_identity(
        {
            # IBS5 pads name1 to a fixed width — it must not reach the output.
            "name1": "Kind, Test          Wd-0001-",
            "institutionName1": "GS Teststrasse",
            "email": "test@example.invalid",
            "token": "irrelevant",
        }
    )
    assert info == {
        "holder": "Kind, Test Wd-0001-",
        "school": "GS Teststrasse",
        "email": "test@example.invalid",
    }


def test_login_identity_tolerates_missing_and_null_fields():
    from server import _parse_login_identity

    assert _parse_login_identity({}) == {"holder": "", "school": "", "email": ""}
    assert _parse_login_identity({"name1": None})["holder"] == ""


def test_padded_holder_still_matches_profile_label():
    from server import _clean_field

    assert _clean_field("Kind, Test          Wd-0001-") == "Kind, Test Wd-0001-"
    assert _clean_field(None) == ""
    assert _profile_matches_holder("test", _clean_field("Kind, Test    Wd-0001-"))


# ---------------------------------------------------------------------------
# Regression: substring matching silently passed genuine mismatches
# ---------------------------------------------------------------------------


def test_short_name_inside_a_different_name_is_a_mismatch():
    """A false PASS here is the worst outcome — it is the whole safety check.

    Plain substring containment matched sibling/classmate pairs where one name
    merely contains another, producing no warning at all.
    """
    for profile_name, holder in [
        ("emma", "Mueller, Gemma Wd-1234-"),
        ("lena", "Schmidt, Milena Wd-5678-"),
        ("ben", "Fischer, Ruben Wd-9999-"),
        ("mia", "Weber, Amia Wd-1111-"),
        ("leon", "Klein, Cleon Wd-2222-"),
    ]:
        assert not _profile_matches_holder(profile_name, holder), (
            f"{profile_name!r} must NOT silently match {holder!r}"
        )


def test_genuine_match_still_passes_after_tightening():
    assert _profile_matches_holder("test", "Kind, Test Wd-0001-")
    assert _profile_matches_holder("TEST", "Kind, Test Wd-0001-")
    assert _profile_matches_holder("jarmila", "Nováková, Jarmíla St-0002-")


# ---------------------------------------------------------------------------
# Cancellation counting — distinguishing a NEW cancellation from a stale one
# ---------------------------------------------------------------------------


def test_cancellation_counts_per_date():
    from server import _cancelled_delivery_counts

    orders = [
        {"delivery_date": "30.10.2026", "quantity": "-1"},
        {"delivery_date": "30.10.2026", "quantity": "-1"},
        {"delivery_date": "30.10.2026", "quantity": "1"},
        {"delivery_date": "29.10.2026", "quantity": "1"},
    ]
    assert _cancelled_delivery_counts(orders) == {"2026-10-30": 2}


def test_stale_cancellation_is_not_mistaken_for_a_new_one():
    from server import _cancelled_delivery_counts

    rows = [{"delivery_date": "30.10.2026", "quantity": "-1"}]
    before = _cancelled_delivery_counts(rows)
    after = _cancelled_delivery_counts(rows)  # nothing actually changed
    assert after.get("2026-10-30", 0) - before.get("2026-10-30", 0) == 0


def test_history_dates_parse_in_both_server_formats():
    from server import _history_date_to_iso

    assert _history_date_to_iso("30.10.2026") == "2026-10-30"
    assert _history_date_to_iso("Wed Jun 03 2026") == "2026-06-03"
    assert _history_date_to_iso("2026-10-30") == "2026-10-30"
    assert _history_date_to_iso("garbage") is None
    assert _history_date_to_iso("") is None
