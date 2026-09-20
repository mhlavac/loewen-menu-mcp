"""Tests for _parse_price (pure, no I/O)."""

import pytest

from server import _parse_price


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3,90", 3.90),
        ("3.90", 3.90),
        ("3,90 €", 3.90),
        ("€3,50", 3.50),
        ("3,90\xa0€", 3.90),       # NBSP between amount and currency
        ("1.234,56", 1234.56),     # German thousands separator
        ("1,234.56", 1234.56),     # US thousands separator (was mangled before)
        ("12", 12.0),
        ("", None),
        (None, None),
        ("k.A.", None),
    ],
)
def test_parse_price(raw, expected):
    assert _parse_price(raw) == expected
