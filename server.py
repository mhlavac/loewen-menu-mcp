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
# Helper: classify a meal (pure, best-effort German keyword heuristic)
# ---------------------------------------------------------------------------
#
# classify_meal is a *best-effort, heuristic* tagger. It does NOT consult an
# allergen database or the IBS5 ingredient list — it only pattern-matches on
# the human-readable dish name. Treat the output as advisory.
#
# Keyword table (all matched case-insensitively as substrings of the dish name):
#
#   pork    : schwein, kassler, kassel, speck, schinken, bacon, wurst, bratwurst,
#             salami, leberkäs, leberkas, sülze, suelze, eisbein, kasseler,
#             schweine, gulasch (NOTE: ambiguous — see below)
#   beef     : rind, beef, gulasch, hack, hackfleisch, frikadelle, bulette,
#             boulette, tafelspitz, roulade, sauerbraten, gyros, döner, doener,
#             kalb, ragout (ambiguous — see below)
#   poultry  : huhn, hühn, huehn, hähnchen, haehnchen, hahnchen, geflügel,
#             gefluegel, pute, puten, truthahn, chicken, poulet
#   fish     : fisch, lachs, seelachs, kabeljau, thunfisch, forelle, scholle,
#             hering, garnel, shrimp, fischstäbchen, fischstaebchen, alaska,
#             pangasius, sea
#   egg      : ei, eier, eies, omelett, rührei, ruehrei, spiegelei, frikassee
#             (NOTE: "frikassee" is egg-y only when not preceded by a meat word;
#              handled by precedence below)
#   sweet    : süß, suess, pudding, milchreis, grießbrei, griessbrei, apfelmus,
#             pfannkuchen, kaiserschmarrn, schmarrn, kuchen, waffel, quark,
#             dessert, kompott, vanille, schokolad, zimt, mus, datsch, palatschin,
#             rote grütze, gruetze, kaltschale, reis mit, germknödel, knödel mit
#
# Precedence / disambiguation rules:
#   - "gulasch"/"ragout" alone are ambiguous. We resolve them by the qualifier
#     in the name: "...vom huhn"/"...geflügel" -> poultry, "fisch..." -> fish,
#     "wurst..." -> pork, otherwise default a bare "gulasch" to beef and a bare
#     "ragout" to NOT add a meat category (too generic) unless another keyword
#     fires. "wurstgulasch" -> pork (wurst wins).
#   - "frikassee" implies egg ONLY if no meat keyword (poultry/pork/beef/fish)
#     matched, e.g. "Eierfrikassee" -> egg, but "Hühnerfrikassee" -> poultry.
#   - vegetarian == True iff NONE of pork/beef/poultry/fish matched. egg does NOT
#     break vegetarian (lacto-ovo). sweet does not affect vegetarian.
#   - "sin carne"/"ohne fleisch"/"veg." / "vegan" / "vegetarisch" are vegetarian
#     even if a meat-sounding token appears (e.g. "Chili sin Carne").
#
# This table is deliberately conservative: when a name contains no recognized
# animal keyword, the meal is reported vegetarian with contains == [].

_VEG_OVERRIDE = (
    "sin carne",
    "ohne fleisch",
    "vegetarisch",
    "vegetarian",
    "vegan",
    "veg.",
    "veggie",
)

_PORK_KW = (
    "schwein", "kassler", "kasseler", "speck", "schinken", "bacon",
    "wurst", "bratwurst", "salami", "leberkäs", "leberkas", "sülze", "suelze",
    "eisbein",
)
_BEEF_KW = (
    "rind", "beef", "hackfleisch", "hack", "frikadelle", "bulette", "boulette",
    "tafelspitz", "roulade", "sauerbraten", "gyros", "döner", "doener", "kalb",
)
_POULTRY_KW = (
    "huhn", "hühn", "huehn", "hähnchen", "haehnchen", "hahnchen", "geflügel",
    "gefluegel", "pute", "puten", "truthahn", "chicken", "poulet",
)
_FISH_KW = (
    "fisch", "lachs", "seelachs", "kabeljau", "thunfisch", "forelle", "scholle",
    "hering", "garnel", "shrimp", "alaska", "pangasius",
)
# Egg compound words (matched as substrings). Standalone "Ei"/"Eier" is handled
# separately via a word-boundary regex (see _EGG_WORD_RE) so that "…eintopf"
# does not false-match and "mit Ei" / "Ei," is not missed.
_EGG_KW = (
    "eier", "omelett", "rührei", "ruehrei", "spiegelei", "eierkuchen", "eierspeise",
)
_EGG_WORD_RE = re.compile(r"\bei\b")
_SWEET_KW = (
    "süß", "suess", "pudding", "milchreis", "grießbrei", "griessbrei",
    "apfelmus", "pfannkuchen", "kaiserschmarrn", "schmarrn", "kuchen", "waffel",
    "quark", "dessert", "kompott", "vanille", "schokolad", "zimt", "palatschin",
    "rote grütze", "gruetze", "kaltschale", "germknödel", "datsch",
)


def classify_meal(meal_name: str) -> dict[str, Any]:
    """Best-effort heuristic tagging of a German dish name.

    Returns a dict with keys:
        vegetarian : bool  — True iff no pork/beef/poultry/fish keyword matched
        contains   : list[str] — subset of ["pork","beef","poultry","fish","egg"]
        sweet      : bool  — dessert / sweet dish

    Pure function — no I/O. See the keyword table comment above for details.
    This is heuristic and advisory; it does not replace the allergen list.
    """
    name = (meal_name or "").lower()
    contains: list[str] = []

    veg_override = any(tok in name for tok in _VEG_OVERRIDE)

    def _has(keywords: tuple[str, ...]) -> bool:
        return any(kw in name for kw in keywords)

    pork = _has(_PORK_KW)
    beef = _has(_BEEF_KW)
    poultry = _has(_POULTRY_KW)
    fish = _has(_FISH_KW)

    # Ambiguous "gulasch"/"ragout": resolve by qualifier already-matched above.
    # A bare "gulasch" (no other meat word) defaults to beef.
    if "gulasch" in name and not (pork or poultry or fish):
        beef = True
    # "ragout" alone stays generic (no category) unless qualified by a matched
    # meat keyword — nothing to do here; handled by the keyword sets above.

    if veg_override:
        # Explicit vegetarian/vegan declaration overrides meat-sounding tokens.
        pork = beef = poultry = fish = False

    if pork:
        contains.append("pork")
    if beef:
        contains.append("beef")
    if poultry:
        contains.append("poultry")
    if fish:
        contains.append("fish")

    # Egg: explicit egg compounds, standalone "Ei"/"Eier" (word-boundary), OR
    # "frikassee" when no meat category fired.
    egg = _has(_EGG_KW) or bool(_EGG_WORD_RE.search(name))
    if "frikassee" in name and not (pork or beef or poultry or fish):
        egg = True
    if egg and "egg" not in contains:
        contains.append("egg")

    sweet = _has(_SWEET_KW)

    vegetarian = not (pork or beef or poultry or fish)

    return {"vegetarian": vegetarian, "contains": contains, "sweet": sweet}


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
# Helper: SaveOrder payload + POST (shared by add_to_cart and bulk_add_to_cart)
# ---------------------------------------------------------------------------


def _build_save_order_payload(
    numeric_customer_id: str,
    serve_date: str,
    menu_line_id: str,
    menu_group_id: str,
    quantity: int,
) -> dict[str, Any]:
    """Build the SaveOrder JSON payload (pure, no I/O)."""
    return {
        "mealOrderQuantity": {
            "CustomerId": numeric_customer_id,
            "ServeDate": serve_date,
            "MenuGroupId": menu_group_id,
            "MenuLineId": menu_line_id,
            "QuantityInShoppingCart": quantity,
            "ShoppingCartOrderType": "N",
        }
    }


def _save_order(
    client: httpx.Client,
    customer_id: str,
    password: str,
    serve_date: str,
    menu_line_id: str,
    menu_group_id: str,
    quantity: int,
) -> httpx.Response:
    """Resolve the numeric customer id and POST a SaveOrder request.

    Shared by add_to_cart, remove_meal and bulk_add_to_cart. Returns the raw
    httpx.Response (caller decides how to interpret it). The payload build is
    delegated to the pure helper _build_save_order_payload so it is unit-testable
    without HTTP.
    """
    serve_dt = datetime.strptime(serve_date, "%Y-%m-%d")
    hdrs = _headers(customer_id, password)
    numeric_customer_id = _get_numeric_customer_id(customer_id, password, serve_dt)
    payload = _build_save_order_payload(
        numeric_customer_id, serve_date, menu_line_id, menu_group_id, quantity
    )
    resp = client.post(
        f"{BASE_URL}/Mealplan/SaveOrder",
        headers=hdrs,
        json=payload,
    )
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Helper: fetch + parse a single ISO-week weekplan (HTTP)
# ---------------------------------------------------------------------------


def _fetch_weekplan(
    customer_id: str, password: str, year: int, week: int
) -> dict[str, Any]:
    """GET one ISO-week weekplan page and return the parsed structure."""
    client = _get_client()
    hdrs = _headers(customer_id, password)
    get_headers = {k: v for k, v in hdrs.items() if k != "Content-Type"}
    resp = client.get(
        f"{BASE_URL}/Mealplan/Weekplan",
        params={"year": year, "week": week},
        headers=get_headers,
    )
    resp.raise_for_status()
    return _parse_weekplan(resp.text)


# ---------------------------------------------------------------------------
# Helper: week iteration over a date range (pure date math)
# ---------------------------------------------------------------------------


def _iso_weeks_in_range(from_date: str, to_date: str) -> list[tuple[int, int]]:
    """Return the ordered, de-duplicated list of (iso_year, iso_week) tuples
    that cover the inclusive [from_date, to_date] date range.

    Pure — operates on YYYY-MM-DD strings, no I/O.
    """
    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()
    if end < start:
        start, end = end, start
    weeks: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    cur = start
    while cur <= end:
        iso_year, iso_week, _ = cur.isocalendar()
        key = (iso_year, iso_week)
        if key not in seen:
            seen.add(key)
            weeks.append(key)
        cur += timedelta(days=7)
    # Make sure the final week (end's week) is included even if the 7-day stride
    # overshot it.
    end_key = (end.isocalendar()[0], end.isocalendar()[1])
    if end_key not in seen:
        weeks.append(end_key)
    return weeks


def _meal_to_json(item: dict[str, Any]) -> dict[str, Any]:
    """Project a _parse_weekplan menu item into the get_menu/plan_order JSON shape."""
    price = item.get("price")
    return {
        "line_label": item["line_label"],
        "menu_line_id": item["menu_line_id"],
        "menu_group_id": item["menu_group_id"],
        "meal_name": item["meal_name"],
        "price": price,
        "max_quantity": item.get("max_quantity"),
        "already_ordered": item.get("current_quantity", 0),
        "orderable": not item.get("ordering_disabled", False),
        "tags": classify_meal(item["meal_name"]),
    }


def _parse_price(price: Any) -> float | None:
    """Parse a data-price string into a float euro amount, or None.

    Handles "3,50", "3.50", "€3,50", "3,50 €" forms.
    """
    if price is None:
        return None
    s = str(price).replace("\xa0", " ").strip()
    if not s:
        return None
    s = s.replace("€", "").replace("EUR", "").replace(" ", "").strip()
    # Normalize the decimal separator. When both "," and "." appear, the
    # rightmost is the decimal point and the other is a thousands separator
    # (handles German "1.234,56" and US "1,234.56" without mangling either).
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


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
    # NOTE: totalPriceInCart may read 0 (or be missing) until the order is
    # confirmed. The authoritative per-meal price is the weekplan `data-price`
    # value, which plan_order / get_menu surface. Treat the cart total here as
    # best-effort and tolerate missing fields throughout.
    items = data.get("itemsInCart", 0)
    total = data.get("totalItemsInCart", 0)
    # Money fields may arrive as numbers or German-formatted strings — coerce
    # defensively so formatting never crashes on a "10,50"-style value.
    price = _parse_price(data.get("totalPriceInCart")) or 0.0
    credits = _parse_price(data.get("creditBalance"))
    remaining = _parse_price(data.get("remainingCredits"))

    pname = profile or next(iter(_profiles), "?")
    lines = [f"# Shopping Cart Status — {pname}\n"]
    lines.append(f"- Items in cart: {items}")
    lines.append(f"- Total items: {total}")
    lines.append(f"- Total price: €{price:.2f}")
    if credits is not None:
        lines.append(f"- Credit balance: €{credits:.2f}")
    if remaining is not None:
        lines.append(f"- Remaining credits: €{remaining:.2f}")
    # Show projected balance after the cart is paid for, when credit is known.
    if credits is not None:
        lines.append(f"- Balance after cart: €{(credits - price):.2f}")

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

    _save_order(
        client,
        customer_id,
        password,
        serve_date,
        menu_line_id,
        menu_group_id,
        quantity,
    )

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
    client = _get_client()

    _save_order(
        client,
        customer_id,
        password,
        serve_date,
        menu_line_id,
        menu_group_id,
        0,
    )

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
# Pure planning logic (importable, no I/O) — used by plan_order
# ---------------------------------------------------------------------------


def _plan_lines(
    items: list[dict[str, Any]],
    by_date: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Compute a dry-run plan from requested items against parsed weekplan data.

    Pure function — no HTTP. `by_date` is the merged {serve_date: [menu_item,...]}
    mapping (as produced by _parse_weekplan, possibly across several weeks).

    Each requested item: {serve_date, menu_line_id, menu_group_id?(default "2")}.

    conflict ∈ {None, "no_school_day", "unknown_line", "not_orderable",
                "already_ordered"} with that precedence.

    Returns {lines, order_total, counts}.
    """
    lines: list[dict[str, Any]] = []
    order_total = 0.0
    orderable_count = 0
    conflict_count = 0

    for req in items:
        serve_date = req.get("serve_date", "")
        menu_line_id = str(req.get("menu_line_id", ""))
        menu_group_id = str(req.get("menu_group_id", "2") or "2")

        conflict: str | None = None
        line_label = None
        meal_name = None
        price = None
        already_ordered = 0
        orderable = False
        tags: dict[str, Any] | None = None

        day_items = by_date.get(serve_date)
        if day_items is None:
            conflict = "no_school_day"
        else:
            match = None
            for it in day_items:
                if str(it["menu_line_id"]) == menu_line_id and (
                    str(it["menu_group_id"]) == menu_group_id
                ):
                    match = it
                    break
            # Fall back to line-only match if group didn't line up.
            if match is None:
                for it in day_items:
                    if str(it["menu_line_id"]) == menu_line_id:
                        match = it
                        break
            if match is None:
                conflict = "unknown_line"
            else:
                line_label = match["line_label"]
                meal_name = match["meal_name"]
                price = match.get("price")
                already_ordered = match.get("current_quantity", 0)
                orderable = not match.get("ordering_disabled", False)
                tags = classify_meal(meal_name)
                if match.get("ordering_disabled", False):
                    conflict = "not_orderable"
                elif already_ordered > 0:
                    conflict = "already_ordered"

        line = {
            "serve_date": serve_date,
            "menu_line_id": menu_line_id,
            "menu_group_id": menu_group_id,
            "line_label": line_label,
            "meal_name": meal_name,
            "price": price,
            "already_ordered": already_ordered,
            "orderable": orderable,
            "tags": tags,
            "conflict": conflict,
        }
        lines.append(line)

        if conflict is None and orderable:
            orderable_count += 1
            parsed = _parse_price(price)
            if parsed is not None:
                order_total += parsed
        else:
            conflict_count += 1

    return {
        "lines": lines,
        "order_total": round(order_total, 2),
        "counts": {"orderable": orderable_count, "conflicts": conflict_count},
    }


# ---------------------------------------------------------------------------
# New tool: get_menu (date-range menu with tags)
# ---------------------------------------------------------------------------


@mcp.tool()
def get_menu(
    profile: str = "",
    from_date: str = "",
    to_date: str = "",
    as_json: bool = False,
) -> str | list[dict[str, Any]]:
    """Get the menu for an arbitrary date range, with dietary tags per meal.

    Fetches every ISO week that overlaps [from_date, to_date] (deduped/cached
    within the call), then filters meals down to the requested range.

    Args:
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.
        from_date: Start date YYYY-MM-DD (defaults to today).
        to_date: End date YYYY-MM-DD (defaults to from_date).
        as_json: If True, return a structured JSON list instead of markdown.

    Returns:
        Markdown (as_json=False) listing meals per day with dietary tags, or a
        JSON list (as_json=True) of {date, weekday, meals:[...]} where each meal
        carries line_label, ids, meal_name, price, max_quantity, already_ordered,
        orderable and tags (classify_meal output).
    """
    customer_id, password = _resolve_profile(profile)

    if not from_date:
        from_date = datetime.now().strftime("%Y-%m-%d")
    if not to_date:
        to_date = from_date

    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date()
    if end < start:
        start, end = end, start

    # Fetch + merge all overlapping weeks (cache per (year, week) within the call).
    merged_by_date: dict[str, list[dict[str, Any]]] = {}
    week_cache: dict[tuple[int, int], dict[str, Any]] = {}
    for year, week in _iso_weeks_in_range(from_date, to_date):
        if (year, week) not in week_cache:
            week_cache[(year, week)] = _fetch_weekplan(customer_id, password, year, week)
        for d, day_items in week_cache[(year, week)]["by_date"].items():
            merged_by_date.setdefault(d, []).extend(day_items)

    # Filter to range.
    result: list[dict[str, Any]] = []
    for date in sorted(merged_by_date.keys()):
        d = datetime.strptime(date, "%Y-%m-%d").date()
        if d < start or d > end:
            continue
        meals = [
            _meal_to_json(it)
            for it in sorted(merged_by_date[date], key=lambda x: x["menu_line_id"])
        ]
        result.append(
            {
                "date": date,
                "weekday": datetime.strptime(date, "%Y-%m-%d").strftime("%A"),
                "meals": meals,
            }
        )

    if as_json:
        return result

    pname = profile or next(iter(_profiles), "?")
    lines = [f"# Menu for {pname} — {from_date} to {to_date}\n"]
    if not result:
        lines.append("_No meals found in this range._")
        return "\n".join(lines)
    for day in result:
        dt = datetime.strptime(day["date"], "%Y-%m-%d")
        lines.append(f"\n## {dt.strftime('%A, %B %d')} ({day['date']})")
        for meal in day["meals"]:
            status = ""
            if not meal["orderable"]:
                status = " [EXPIRED]"
            elif meal["already_ordered"] > 0:
                status = f" [ORDERED: {meal['already_ordered']}]"
            price_str = f" — {meal['price']}" if meal.get("price") else ""
            max_str = (
                f" (max: {meal['max_quantity']})" if meal.get("max_quantity") else ""
            )
            tags = meal["tags"]
            tag_bits: list[str] = []
            if tags["vegetarian"]:
                tag_bits.append("vegetarian")
            if tags["contains"]:
                tag_bits.append("contains: " + ", ".join(tags["contains"]))
            if tags["sweet"]:
                tag_bits.append("sweet")
            tag_str = f"  _[{'; '.join(tag_bits)}]_" if tag_bits else ""
            lines.append(
                f"  - **{meal['line_label']}**: {meal['meal_name']}"
                f"{price_str}{max_str}{status}{tag_str}"
            )
            lines.append(
                f"    IDs: menu_group={meal['menu_group_id']}, "
                f"menu_line={meal['menu_line_id']}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# New tool: plan_order (DRY RUN — preview cost + conflicts, no mutation)
# ---------------------------------------------------------------------------


@mcp.tool()
def plan_order(
    profile: str = "",
    items: list[dict[str, Any]] | None = None,
    as_json: bool = False,
) -> str | dict[str, Any]:
    """DRY RUN: preview a multi-meal order — cost and conflicts — without mutating.

    Only GETs weekplans; never touches the cart. Use this before bulk_add_to_cart.

    Args:
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.
        items: list of {serve_date, menu_line_id, menu_group_id?(default "2")}.
        as_json: If True, return the structured dict instead of markdown.

    Returns:
        Per-line plan with {serve_date, menu_line_id, line_label, meal_name,
        price, already_ordered, orderable, tags, conflict}, plus order_total
        (sum of prices for lines with conflict None and orderable True) and
        counts {orderable, conflicts}. conflict is one of None, "no_school_day",
        "unknown_line", "not_orderable", "already_ordered".
    """
    customer_id, password = _resolve_profile(profile)
    items = items or []

    if not items:
        plan = {"lines": [], "order_total": 0.0, "counts": {"orderable": 0, "conflicts": 0}}
    else:
        # Determine the date span to fetch.
        dates = [str(it.get("serve_date", "")) for it in items if it.get("serve_date")]
        valid_dates = [d for d in dates if re.match(r"\d{4}-\d{2}-\d{2}$", d)]
        merged_by_date: dict[str, list[dict[str, Any]]] = {}
        if valid_dates:
            from_date = min(valid_dates)
            to_date = max(valid_dates)
            week_cache: dict[tuple[int, int], dict[str, Any]] = {}
            for year, week in _iso_weeks_in_range(from_date, to_date):
                if (year, week) not in week_cache:
                    week_cache[(year, week)] = _fetch_weekplan(
                        customer_id, password, year, week
                    )
                for d, day_items in week_cache[(year, week)]["by_date"].items():
                    merged_by_date.setdefault(d, []).extend(day_items)
        plan = _plan_lines(items, merged_by_date)

    if as_json:
        return plan

    pname = profile or next(iter(_profiles), "?")
    out = [f"# Order plan (DRY RUN) — {pname}\n"]
    for line in plan["lines"]:
        if line["conflict"] is None:
            mark = "✅"
        elif line["conflict"] == "already_ordered":
            mark = "☑️"
        else:
            mark = "⚠️"
        meal = line["meal_name"] or "(not found)"
        price = f" — {line['price']}" if line.get("price") else ""
        conf = f"  [{line['conflict']}]" if line["conflict"] else ""
        label = line["line_label"] or f"line {line['menu_line_id']}"
        out.append(
            f"- {mark} **{line['serve_date']}** {label}: {meal}{price}{conf}"
        )
    out.append("")
    out.append(f"**Order total (orderable lines): €{plan['order_total']:.2f}**")
    out.append(
        f"Orderable: {plan['counts']['orderable']} · "
        f"Conflicts: {plan['counts']['conflicts']}"
    )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# New tool: bulk_add_to_cart (mutating — adds many meals at once)
# ---------------------------------------------------------------------------


@mcp.tool()
def bulk_add_to_cart(
    profile: str = "",
    items: list[dict[str, Any]] | None = None,
) -> str:
    """Add multiple meals to the cart in one call. Does NOT finalize the order.

    Each item is validated for the >=1-day-advance rule; items that fail are
    SKIPPED and reported (the rest still proceed). Call confirm_order afterwards.

    Args:
        profile: Profile name (e.g. "child1"). Optional if only one profile is configured.
        items: list of {serve_date, menu_line_id, menu_group_id?(default "2"),
               quantity?(default 1)}.

    Returns:
        Per-line ok/skip/fail summary plus the final cart total.
    """
    customer_id, password = _resolve_profile(profile)
    items = items or []
    client = _get_client()
    hdrs = _headers(customer_id, password)

    tomorrow = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)

    results: list[dict[str, Any]] = []
    for it in items:
        serve_date = str(it.get("serve_date", ""))
        menu_line_id = str(it.get("menu_line_id", ""))
        menu_group_id = str(it.get("menu_group_id", "2") or "2")

        # Parse quantity inside the guard: a bad or <1 value skips THIS item,
        # it does not crash the whole batch (the tool's documented contract).
        try:
            quantity = int(it.get("quantity", 1))
        except (TypeError, ValueError):
            results.append(
                {
                    "serve_date": serve_date,
                    "menu_line_id": menu_line_id,
                    "status": "skip",
                    "reason": "invalid quantity (must be an integer)",
                }
            )
            continue
        if quantity < 1:
            results.append(
                {
                    "serve_date": serve_date,
                    "menu_line_id": menu_line_id,
                    "status": "skip",
                    "reason": "quantity must be >= 1",
                }
            )
            continue

        # >=1-day-advance validation — skip (don't abort) on failure.
        try:
            serve_dt = datetime.strptime(serve_date, "%Y-%m-%d")
        except ValueError:
            results.append(
                {
                    "serve_date": serve_date,
                    "menu_line_id": menu_line_id,
                    "status": "skip",
                    "reason": "invalid date format (expected YYYY-MM-DD)",
                }
            )
            continue

        if serve_dt < tomorrow:
            results.append(
                {
                    "serve_date": serve_date,
                    "menu_line_id": menu_line_id,
                    "status": "skip",
                    "reason": "must be ordered at least 1 day in advance",
                }
            )
            continue

        try:
            _save_order(
                client,
                customer_id,
                password,
                serve_date,
                menu_line_id,
                menu_group_id,
                quantity,
            )
            results.append(
                {
                    "serve_date": serve_date,
                    "menu_line_id": menu_line_id,
                    "status": "ok",
                    "quantity": quantity,
                }
            )
        except Exception as exc:  # noqa: BLE001 — report per-line, keep going
            results.append(
                {
                    "serve_date": serve_date,
                    "menu_line_id": menu_line_id,
                    "status": "fail",
                    "reason": str(exc),
                }
            )

    # Final cart total via UpdateBalanceAndCart (tolerate missing fields).
    cart_total = "?"
    cart_price = None
    try:
        cart_resp = client.post(
            f"{BASE_URL}/Mealplan/UpdateBalanceAndCart",
            headers=hdrs,
            json={},
        )
        if cart_resp.status_code == 200:
            cart_data = cart_resp.json()
            cart_total = cart_data.get("totalItemsInCart", "?")
            cart_price = cart_data.get("totalPriceInCart")
    except Exception:  # noqa: BLE001
        pass

    pname = profile or next(iter(_profiles), "?")
    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_skip = sum(1 for r in results if r["status"] == "skip")
    n_fail = sum(1 for r in results if r["status"] == "fail")

    lines = [f"# Bulk add to cart — {pname}\n"]
    for r in results:
        if r["status"] == "ok":
            lines.append(
                f"- ✅ {r['serve_date']} line {r['menu_line_id']} "
                f"(x{r.get('quantity', 1)}) added"
            )
        elif r["status"] == "skip":
            lines.append(
                f"- ⏭️ {r['serve_date']} line {r['menu_line_id']} "
                f"SKIPPED — {r['reason']}"
            )
        else:
            lines.append(
                f"- ❌ {r['serve_date']} line {r['menu_line_id']} "
                f"FAILED — {r['reason']}"
            )
    lines.append("")
    lines.append(f"**{n_ok} added · {n_skip} skipped · {n_fail} failed**")
    if cart_price is not None:
        lines.append(
            f"Cart now has {cart_total} item(s), total €{float(cart_price):.2f}."
        )
    else:
        lines.append(f"Cart now has {cart_total} item(s).")
    lines.append("⚠️ NOT YET ORDERED — call confirm_order to finalize.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
