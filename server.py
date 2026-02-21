"""
Löwen Menü IBS5 MCP Server

MCP server for interacting with the Löwen Menü Internet-Bestell-System (IBS5).
Supports browsing weekly menus, viewing order history, and placing meal orders.

Credentials are configured via the PROFILES environment variable:
    PROFILES=child1:CUSTOMER_ID:PASSWORD,child2:CUSTOMER_ID:PASSWORD
"""

import base64
import os
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://ibs.loewen-menue.de/IBS5"

# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def _parse_profiles() -> dict[str, dict[str, str]]:
    """Parse PROFILES env var.

    Format: name:customer_id:password,name2:customer_id2:password2
    """
    raw = os.environ.get("PROFILES", "")
    if not raw:
        return {}
    profiles: dict[str, dict[str, str]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            continue
        name, customer_id, password = parts
        profiles[name.strip().lower()] = {
            "customer_id": customer_id.strip(),
            "password": password.strip(),
        }
    return profiles


_profiles = _parse_profiles()


def _resolve_profile(profile: str) -> tuple[str, str]:
    """Return (customer_id, password) for a profile name.

    If only one profile is configured, it is used as the default when
    profile is empty.
    """
    key = profile.strip().lower() if profile else ""
    if not key and len(_profiles) == 1:
        key = next(iter(_profiles))
    if key not in _profiles:
        available = ", ".join(sorted(_profiles)) or "(none configured)"
        raise ValueError(
            f"Unknown profile '{profile}'. Available profiles: {available}. "
            "Configure via PROFILES env var: PROFILES=name:customer_id:password,..."
        )
    p = _profiles[key]
    return p["customer_id"], p["password"]


# ---------------------------------------------------------------------------
# Helper: auth token
# ---------------------------------------------------------------------------


def _make_token(customer_id: str, password: str) -> str:
    """Build the Bearer token expected by IBS5.

    Format: base64(customerId;password;0;0;0;yyyyMMddHHmmss)
    """
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    raw = f"{customer_id};{password};0;0;0;{ts}"
    return base64.b64encode(raw.encode()).decode()


def _headers(customer_id: str, password: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_make_token(customer_id, password)}",
        "Content-Type": "application/json",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (compatible; LoewendMenuMCP/1.0)",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }


# ---------------------------------------------------------------------------
# Helper: HTTP client (reusable)
# ---------------------------------------------------------------------------

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(timeout=30, follow_redirects=True)
    return _client


# ---------------------------------------------------------------------------
# Helper: parse week plan HTML
# ---------------------------------------------------------------------------


def _parse_weekplan(html: str) -> dict[str, Any]:
    """Parse the Weekplan HTML and return structured menu data."""
    soup = BeautifulSoup(html, "lxml")

    # Extract week info from heading
    week_info = ""
    heading = soup.select_one("h1, .week-heading, .calendar-week")
    if heading:
        week_info = heading.get_text(strip=True)

    # Extract date headers
    dates: list[str] = []
    day_headers = soup.select("th.mealplan-date-header, .mealplan-date-header")
    for dh in day_headers:
        date_text = dh.get_text(strip=True)
        dates.append(date_text)

    # Extract menu items from input fields
    menu_items: list[dict[str, Any]] = []
    inputs = soup.select('input[type="number"]')
    for inp in inputs:
        input_id = inp.get("id", "")
        # Parse ID: menu_quantity_{date}_{groupId}_{lineId}
        match = re.match(
            r"menu_quantity_(\d{4}-\d{2}-\d{2})_(\d+)_(\d+)", input_id
        )
        if not match:
            continue

        serve_date = match.group(1)
        menu_group_id = match.group(2)
        menu_line_id = match.group(3)

        meal_name = inp.get("data-name", "")
        price = inp.get("data-price", "")
        current_qty = inp.get("value", "0") or "0"
        max_qty = inp.get("max", "")

        # Determine menu line label (GS 1, GS 2, etc.)
        line_label = f"GS {int(menu_line_id) - 7}"  # 8->GS1, 9->GS2, 10->GS3

        # Check if ordering is allowed (no "not-allowed" class on parent)
        disabled = False
        parent = inp.find_parent(class_="menu-portions")
        if parent:
            container = parent.find_parent(class_="order-group-container")
            if container:
                increase = container.select_one(".increase.not-allowed")
                if increase:
                    disabled = True

        menu_items.append(
            {
                "serve_date": serve_date,
                "menu_group_id": menu_group_id,
                "menu_line_id": menu_line_id,
                "line_label": line_label,
                "meal_name": meal_name,
                "price": price if price else None,
                "current_quantity": int(current_qty) if current_qty else 0,
                "max_quantity": int(max_qty) if max_qty else None,
                "ordering_disabled": disabled,
            }
        )

    # Group by date
    by_date: dict[str, list[dict]] = {}
    for item in menu_items:
        d = item["serve_date"]
        by_date.setdefault(d, []).append(item)

    return {
        "week_info": week_info,
        "dates": dates,
        "menu_items": menu_items,
        "by_date": by_date,
    }


# ---------------------------------------------------------------------------
# Helper: parse order history HTML
# ---------------------------------------------------------------------------


def _parse_order_history(html: str) -> list[dict[str, str]]:
    """Parse the Order History HTML table."""
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("tr")
    orders: list[dict[str, str]] = []

    for row in rows[1:]:  # skip header row
        cells = row.select("td")
        if len(cells) >= 4:
            orders.append(
                {
                    "order_date": cells[0].get_text(strip=True),
                    "delivery_date": cells[1].get_text(strip=True),
                    "quantity": cells[2].get_text(strip=True),
                    "description": cells[3].get_text(strip=True),
                    "information": (
                        cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    ),
                    "user": cells[5].get_text(strip=True) if len(cells) > 5 else "",
                }
            )

    return orders


# ---------------------------------------------------------------------------
# Helper: get numeric customer ID
# ---------------------------------------------------------------------------

# Cache to avoid repeated lookups
_customer_id_cache: dict[str, str] = {}


def _get_numeric_customer_id(
    customer_id: str, password: str, reference_date: datetime | None = None
) -> str:
    """Extract the numeric customer ID from a weekplan page.

    The IBS5 system uses a numeric internal ID that differs from the
    login customer number (e.g. S000001). This ID is found in
    a hidden <input id="userId"> field on the weekplan page.
    """
    if customer_id in _customer_id_cache:
        return _customer_id_cache[customer_id]

    if reference_date is None:
        reference_date = datetime.now()

    week = reference_date.isocalendar()[1]
    year = reference_date.year

    client = _get_client()
    hdrs = _headers(customer_id, password)
    get_headers = {k: v for k, v in hdrs.items() if k != "Content-Type"}
    resp = client.get(
        f"{BASE_URL}/Mealplan/Weekplan",
        params={"year": year, "week": week},
        headers=get_headers,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")

    # Primary: hidden input with id="userId"
    user_id_input = soup.select_one('input#userId')
    if user_id_input:
        val = user_id_input.get("value", "")
        if val:
            _customer_id_cache[customer_id] = val
            return val

    # Fallback: search for CustomerId pattern in page source
    match = re.search(r'"CustomerId"\s*:\s*"?(\d+)"?', resp.text)
    if match:
        val = match.group(1)
        _customer_id_cache[customer_id] = val
        return val

    raise ValueError(
        f"Could not determine numeric customer ID for {customer_id}. "
        "Please check credentials."
    )


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "loewen-menu",
    instructions=(
        "MCP server for Löwen Menü IBS5 meal ordering system. "
        "Use list_profiles to see available children/profiles, then "
        "use get_weekly_menu to browse available meals, "
        "get_order_history to see past orders, "
        "add_to_cart to add items to the cart, "
        "get_cart_status to check the shopping cart, "
        "confirm_order to finalize and submit the cart, and "
        "remove_meal to cancel an order. "
        "All tools take a 'profile' parameter (e.g. 'child1') to identify the child."
    ),
)


@mcp.tool()
def list_profiles() -> str:
    """List all configured profiles (children).

    Returns:
        Names of available profiles that can be used with other tools.
    """
    if not _profiles:
        return (
            "No profiles configured. Set the PROFILES environment variable: "
            "PROFILES=name:customer_id:password,name2:customer_id2:password2"
        )

    lines = ["# Configured Profiles\n"]
    for name in sorted(_profiles):
        cid = _profiles[name]["customer_id"]
        lines.append(f"- **{name}** (account: {cid})")
    return "\n".join(lines)


@mcp.tool()
def get_weekly_menu(
    profile: str = "",
    year: int | None = None,
    week: int | None = None,
) -> str:
    """Get the weekly meal plan for a given week.

    Args:
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.
        year: Year (defaults to current year)
        week: Calendar week number (defaults to current week)

    Returns:
        Formatted menu for the week with all available meals grouped by date
        and menu line (GS 1, GS 2, GS 3).
    """
    customer_id, password = _resolve_profile(profile)

    if year is None:
        year = datetime.now().year
    if week is None:
        week = datetime.now().isocalendar()[1]

    client = _get_client()
    hdrs = _headers(customer_id, password)
    # Remove Content-Type for GET request
    get_headers = {k: v for k, v in hdrs.items() if k != "Content-Type"}

    resp = client.get(
        f"{BASE_URL}/Mealplan/Weekplan",
        params={"year": year, "week": week},
        headers=get_headers,
    )
    resp.raise_for_status()

    data = _parse_weekplan(resp.text)

    # Format output
    lines = [f"# Menu for {profile or next(iter(_profiles))} — Week {week}, {year}\n"]

    for date in sorted(data["by_date"].keys()):
        items = data["by_date"][date]
        # Format date nicely
        dt = datetime.strptime(date, "%Y-%m-%d")
        lines.append(f"\n## {dt.strftime('%A, %B %d')} ({date})")

        for item in sorted(items, key=lambda x: x["menu_line_id"]):
            status = ""
            if item["ordering_disabled"]:
                status = " [EXPIRED]"
            elif item["current_quantity"] > 0:
                status = f" [ORDERED: {item['current_quantity']}]"

            price_str = f" — {item['price']}" if item.get("price") else ""
            max_str = f" (max: {item['max_quantity']})" if item.get("max_quantity") else ""

            lines.append(
                f"  - **{item['line_label']}**: {item['meal_name']}{price_str}{max_str}{status}"
            )
            lines.append(
                f"    IDs: menu_group={item['menu_group_id']}, menu_line={item['menu_line_id']}"
            )

    return "\n".join(lines)


@mcp.tool()
def get_order_history(
    profile: str = "",
    days_back: int = 60,
    search: str = "",
) -> str:
    """Get past order history.

    Args:
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.
        days_back: How many days back to look (default 60)
        search: Optional search text to filter orders

    Returns:
        List of past orders with dates, descriptions, and quantities.
    """
    customer_id, password = _resolve_profile(profile)
    client = _get_client()
    hdrs = _headers(customer_id, password)
    get_headers = {k: v for k, v in hdrs.items() if k != "Content-Type"}

    date_from = (datetime.now() - timedelta(days=days_back)).strftime("%a %b %d %Y")
    date_to = datetime.now().strftime("%a %b %d %Y")

    resp = client.get(
        f"{BASE_URL}/Account/Orderhistory",
        params={"from": date_from, "to": date_to, "search": search},
        headers=get_headers,
    )
    resp.raise_for_status()

    orders = _parse_order_history(resp.text)

    if not orders:
        return f"No orders found for {profile or next(iter(_profiles))} in the last {days_back} days."

    lines = [f"# Order History for {profile or next(iter(_profiles))} (last {days_back} days)\n"]
    for order in orders:
        lines.append(
            f"- **{order['delivery_date']}**: {order['description']} "
            f"(qty: {order['quantity']}, ordered: {order['order_date']})"
        )

    return "\n".join(lines)


@mcp.tool()
def get_cart_status(
    profile: str = "",
) -> str:
    """Check the current shopping cart status and balance.

    Args:
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.

    Returns:
        Cart item count, total price, and credit balance.
    """
    customer_id, password = _resolve_profile(profile)
    client = _get_client()
    hdrs = _headers(customer_id, password)

    resp = client.post(
        f"{BASE_URL}/Mealplan/UpdateBalanceAndCart",
        headers=hdrs,
        json={},
    )
    resp.raise_for_status()

    data = resp.json()
    items = data.get("itemsInCart", 0)
    total = data.get("totalItemsInCart", 0)
    price = data.get("totalPriceInCart", 0.0)
    credits = data.get("creditBalance")
    remaining = data.get("remainingCredits")

    pname = profile or next(iter(_profiles), "?")
    lines = [f"# Shopping Cart Status — {pname}\n"]
    lines.append(f"- Items in cart: {items}")
    lines.append(f"- Total items: {total}")
    lines.append(f"- Total price: €{price:.2f}")
    if credits is not None:
        lines.append(f"- Credit balance: €{credits:.2f}")
    if remaining is not None:
        lines.append(f"- Remaining credits: €{remaining:.2f}")

    return "\n".join(lines)


@mcp.tool()
def add_to_cart(
    serve_date: str,
    menu_line_id: str,
    profile: str = "",
    quantity: int = 1,
    menu_group_id: str = "2",
) -> str:
    """Add a meal to the shopping cart. Does NOT place the order yet.

    You MUST call confirm_order afterwards to finalize the order.

    Note: Orders must be placed at least 1 day before the delivery date.

    Args:
        serve_date: Delivery date in YYYY-MM-DD format (e.g. "2026-02-25")
        menu_line_id: Menu line ID (8=GS1, 9=GS2, 10=GS3 — shown in get_weekly_menu output)
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.
        quantity: Number of portions (default 1, usually max 2)
        menu_group_id: Menu group ID (default "2", shown in get_weekly_menu output)

    Returns:
        Cart status. Remember to call confirm_order to finalize.
    """
    customer_id, password = _resolve_profile(profile)

    # Validate date is in the future
    serve_dt = datetime.strptime(serve_date, "%Y-%m-%d")
    tomorrow = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if serve_dt < tomorrow:
        return f"Cannot order for {serve_date}: orders must be placed at least 1 day in advance."

    client = _get_client()
    hdrs = _headers(customer_id, password)
    numeric_customer_id = _get_numeric_customer_id(customer_id, password, serve_dt)

    payload = {
        "mealOrderQuantity": {
            "CustomerId": numeric_customer_id,
            "ServeDate": serve_date,
            "MenuGroupId": menu_group_id,
            "MenuLineId": menu_line_id,
            "QuantityInShoppingCart": quantity,
            "ShoppingCartOrderType": "N",
        }
    }

    resp = client.post(
        f"{BASE_URL}/Mealplan/SaveOrder",
        headers=hdrs,
        json=payload,
    )
    resp.raise_for_status()

    # Verify by checking cart
    cart_resp = client.post(
        f"{BASE_URL}/Mealplan/UpdateBalanceAndCart",
        headers=hdrs,
        json={},
    )
    cart_data = cart_resp.json() if cart_resp.status_code == 200 else {}
    items_in_cart = cart_data.get("totalItemsInCart", "?")

    pname = profile or next(iter(_profiles), "?")
    return (
        f"Added to cart for {pname}: {quantity}x meal (line {menu_line_id}) "
        f"for {serve_date}. Cart now has {items_in_cart} item(s). "
        f"⚠️ NOT YET ORDERED — call confirm_order to finalize."
    )


@mcp.tool()
def remove_meal(
    serve_date: str,
    menu_line_id: str,
    profile: str = "",
    menu_group_id: str = "2",
) -> str:
    """Remove a meal from the shopping cart (set quantity to 0).

    Args:
        serve_date: Delivery date in YYYY-MM-DD format
        menu_line_id: Menu line ID (8=GS1, 9=GS2, 10=GS3)
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.
        menu_group_id: Menu group ID (default "2")

    Returns:
        Confirmation that the meal was removed from cart.
    """
    customer_id, password = _resolve_profile(profile)
    serve_dt = datetime.strptime(serve_date, "%Y-%m-%d")
    client = _get_client()
    hdrs = _headers(customer_id, password)
    numeric_customer_id = _get_numeric_customer_id(customer_id, password, serve_dt)

    payload = {
        "mealOrderQuantity": {
            "CustomerId": numeric_customer_id,
            "ServeDate": serve_date,
            "MenuGroupId": menu_group_id,
            "MenuLineId": menu_line_id,
            "QuantityInShoppingCart": 0,
            "ShoppingCartOrderType": "N",
        }
    }

    resp = client.post(
        f"{BASE_URL}/Mealplan/SaveOrder",
        headers=hdrs,
        json=payload,
    )
    resp.raise_for_status()

    pname = profile or next(iter(_profiles), "?")
    return f"Removed meal (line {menu_line_id}) for {serve_date} from {pname}'s cart."


@mcp.tool()
def confirm_order(
    profile: str = "",
) -> str:
    """Confirm and finalize all items currently in the shopping cart.

    This submits the order. Items must first be added with add_to_cart.
    Call get_cart_status first to verify what will be ordered.

    Args:
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.

    Returns:
        Confirmation message from the server.
    """
    customer_id, password = _resolve_profile(profile)
    client = _get_client()
    hdrs = _headers(customer_id, password)

    # Check cart first
    cart_resp = client.post(
        f"{BASE_URL}/Mealplan/UpdateBalanceAndCart",
        headers=hdrs,
        json={},
    )
    cart_data = cart_resp.json() if cart_resp.status_code == 200 else {}
    items = cart_data.get("totalItemsInCart", 0)

    if items == 0:
        return "Cart is empty — nothing to confirm. Add meals with add_to_cart first."

    # Confirm the order
    resp = client.post(
        f"{BASE_URL}/Cart/Order",
        headers=hdrs,
        json={},
    )
    resp.raise_for_status()

    data = resp.json()
    success = data.get("Success", False)
    message = data.get("Message", "")

    pname = profile or next(iter(_profiles), "?")
    if success:
        return f"Order confirmed for {pname}! {items} item(s) ordered. Server: {message}"
    else:
        return f"Order failed for {pname}. Server: {message}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
