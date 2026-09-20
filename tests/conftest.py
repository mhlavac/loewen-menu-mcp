"""Shared pytest fixtures and synthetic HTML builders for the loewen-menu-mcp tests.

SYNTHETIC DATA ONLY. No real customer credentials, child names, or subdomains
appear anywhere in this test suite. Profiles are child1/child2, login ids
S000001/S000002, numeric id 12345, child display name "Test Kind".
"""

import os

# Configure synthetic profiles BEFORE importing server (it parses PROFILES at
# import time).
os.environ.setdefault("PROFILES", "child1:S000001:1234,child2:S000002:5678")

import httpx  # noqa: E402
import pytest  # noqa: E402

import server  # noqa: E402

NUMERIC_CUSTOMER_ID = "12345"


# ---------------------------------------------------------------------------
# Synthetic weekplan HTML builder
# ---------------------------------------------------------------------------


def _meal_input_html(
    serve_date: str,
    menu_group_id: str,
    menu_line_id: str,
    meal_name: str,
    price: str | None = "3,50",
    current_quantity: int = 0,
    max_quantity: int = 2,
    ordering_disabled: bool = False,
) -> str:
    """Render one meal as the nested HTML structure _parse_weekplan walks.

    Mirrors the real selectors:
      .order-group-container > .menu-portions > input[type=number]
    and, when ordering is disabled, an `.increase.not-allowed` element inside
    the .order-group-container (sibling of .menu-portions).
    """
    input_id = f"menu_quantity_{serve_date}_{menu_group_id}_{menu_line_id}"
    price_attr = f' data-price="{price}"' if price is not None else ""
    increase_cls = "increase not-allowed" if ordering_disabled else "increase"
    return f"""
    <div class="order-group-container">
      <span class="{increase_cls}">+</span>
      <div class="menu-portions">
        <input type="number" id="{input_id}"
               data-name="{meal_name}"{price_attr}
               value="{current_quantity}" max="{max_quantity}" min="0" />
      </div>
    </div>
    """


def build_weekplan_html(
    week_info: str,
    days: dict[str, list[dict]],
    menu_group_id: str = "2",
) -> str:
    """Build a full synthetic weekplan page.

    Args:
        week_info: text for the <h1> heading.
        days: {serve_date: [ {menu_line_id, meal_name, price?, current_quantity?,
               max_quantity?, ordering_disabled?}, ... ]}
        menu_group_id: applied to every meal unless overridden per-meal.
    """
    date_headers = "".join(
        f'<th class="mealplan-date-header">{d}</th>' for d in sorted(days)
    )
    bodies = []
    for serve_date in sorted(days):
        cells = []
        for meal in days[serve_date]:
            cells.append(
                _meal_input_html(
                    serve_date=serve_date,
                    menu_group_id=str(meal.get("menu_group_id", menu_group_id)),
                    menu_line_id=str(meal["menu_line_id"]),
                    meal_name=meal["meal_name"],
                    price=meal.get("price", "3,50"),
                    current_quantity=meal.get("current_quantity", 0),
                    max_quantity=meal.get("max_quantity", 2),
                    ordering_disabled=meal.get("ordering_disabled", False),
                )
            )
        bodies.append(f'<td class="day-col">{"".join(cells)}</td>')
    return f"""<!DOCTYPE html>
    <html><head><meta charset="utf-8"></head>
    <body>
      <h1>{week_info}</h1>
      <input type="hidden" id="userId" value="{NUMERIC_CUSTOMER_ID}" />
      <table class="mealplan">
        <thead><tr>{date_headers}</tr></thead>
        <tbody><tr>{"".join(bodies)}</tr></tbody>
      </table>
    </body></html>
    """


def build_order_history_html(rows: list[list[str]]) -> str:
    """Build a synthetic order-history table. First <tr> is the header row."""
    header = (
        "<tr><th>Ordered</th><th>Delivery</th><th>Qty</th>"
        "<th>Description</th><th>Info</th><th>User</th></tr>"
    )
    body = ""
    for r in rows:
        cells = "".join(f"<td>{c}</td>" for c in r)
        body += f"<tr>{cells}</tr>"
    return f"<html><body><table>{header}{body}</table></body></html>"


# ---------------------------------------------------------------------------
# Mock transport plumbing
# ---------------------------------------------------------------------------


class RecordingHandler:
    """A httpx.MockTransport handler that records requests and serves canned
    responses keyed by URL path. Defaults cover the SaveOrder / cart endpoints.
    """

    def __init__(self, weekplan_html: str = "", history_html: str = ""):
        self.requests: list[httpx.Request] = []
        self.weekplan_html = weekplan_html or build_weekplan_html("Synthetic week", {})
        # Optional per-week override: {(year, week): html}. When the requested
        # (year, week) is present here it takes precedence over weekplan_html.
        self.weekplan_by_week: dict[tuple[int, int], str] = {}
        self.history_html = history_html
        # Successive Orderhistory responses, popped in order before falling
        # back to history_html. cancel_order reads the history twice (before
        # and after confirming) and compares, so a test needs the second read
        # to differ from the first.
        self.history_sequence: list[str] = []
        # JSON returned by /Login/Login — the only endpoint that reveals the
        # account holder. Default is a synthetic holder.
        self.login_json: dict = {
            "name1": "Kind, Test          Wd-0001-",
            "institutionName1": "GS Teststrasse",
            "email": "test@example.invalid",
            "token": "synthetic-token",
        }
        # Cart payload returned by UpdateBalanceAndCart.
        self.cart_payload = {
            "itemsInCart": 0,
            "totalItemsInCart": 0,
            "totalPriceInCart": 0.0,
            "creditBalance": 50.0,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/Mealplan/Weekplan"):
            params = request.url.params
            try:
                key = (int(params.get("year")), int(params.get("week")))
            except (TypeError, ValueError):
                key = None
            if key is not None and key in self.weekplan_by_week:
                return httpx.Response(200, text=self.weekplan_by_week[key])
            return httpx.Response(200, text=self.weekplan_html)
        if path.endswith("/Account/Orderhistory"):
            if self.history_sequence:
                return httpx.Response(200, text=self.history_sequence.pop(0))
            return httpx.Response(200, text=self.history_html)
        if path.endswith("/Login/Login"):
            return httpx.Response(200, json=self.login_json)
        if path.endswith("/Mealplan/SaveOrder"):
            return httpx.Response(200, json={"Success": True})
        if path.endswith("/Mealplan/UpdateBalanceAndCart"):
            return httpx.Response(200, json=self.cart_payload)
        if path.endswith("/Cart/Order"):
            return httpx.Response(200, json={"Success": True, "Message": "OK"})
        return httpx.Response(404, text="not found")

    def save_order_payloads(self) -> list[dict]:
        """Return the JSON bodies of every SaveOrder POST recorded."""
        import json

        out = []
        for req in self.requests:
            if req.url.path.endswith("/Mealplan/SaveOrder"):
                out.append(json.loads(req.content.decode()))
        return out


@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear module-level caches between tests so they don't leak state."""
    server._customer_id_cache.clear()
    server._account_cache.clear()
    yield
    server._customer_id_cache.clear()
    server._account_cache.clear()


@pytest.fixture
def mock_server(monkeypatch):
    """Install a httpx.MockTransport-backed client onto the server module and
    return the RecordingHandler so tests can inspect requests / set responses.
    """
    handler = RecordingHandler()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(server, "_client", client)
    monkeypatch.setattr(server, "_get_client", lambda: client)
    yield handler
    client.close()
