# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP (Model Context Protocol) server for the **Löwen Menü IBS5** meal ordering system. Exposes tools that let an LLM browse weekly school-lunch menus, view order history, manage the shopping cart, and place/remove meal orders via the IBS5 web API at `ibs.loewen-menue.de`.

## Tech Stack

- Python 3.10, managed with **uv** (`uv.lock` + `pyproject.toml`)
- **FastMCP** (`mcp` package) for the MCP server
- **httpx** (with SOCKS proxy support) for HTTP calls to IBS5
- **BeautifulSoup4 + lxml** for HTML parsing of server-rendered pages

## Commands

```bash
# Install / sync dependencies
uv sync

# Run the MCP server (stdio transport, used by Claude Desktop / MCP clients)
uv run server.py          # or: uv run loewen-menu-mcp

# Run the test suite (no network — all HTTP is mocked)
uv run pytest -q
```

### Testing

- Tests live in `tests/` and are pure-offline: `httpx.MockTransport` (wired in
  via the `mock_server` fixture in `tests/conftest.py`) plus synthetic HTML
  fixtures. **No real IBS5 calls are made.**
- `pyproject.toml` sets `[tool.pytest.ini_options]` with `testpaths=["tests"]`
  and `pythonpath=["."]` so `server` imports from the repo root.
- The pure logic (`_parse_weekplan`, `_parse_order_history`, `classify_meal`,
  `_plan_lines`, `_build_save_order_payload`, `_iso_weeks_in_range`,
  `_parse_price`) is importable and unit-tested without HTTP. HTTP-touching
  helpers (`_save_order`, `_fetch_weekplan`) take/obtain the client via
  `_get_client()`, which tests monkeypatch.
- No linter (ruff/flake8) is configured.

## Architecture

All logic lives in a single file: **`server.py`**.

- **Profiles** — Credentials are configured via a single `PROFILES` env var (`name:customer_id:password,...`). Tools take a `profile` name instead of raw credentials. If only one profile is configured, it's used as the default.
- **Auth** — `_make_token()` builds a Base64 Bearer token in the format `customerId;password;0;0;0;timestamp`. Credentials never pass through the LLM.
- **HTTP** — A module-level `httpx.Client` singleton (`_get_client()`) is reused across calls. GET requests require browser-like `Accept`/`User-Agent` headers.
- **HTML parsing** — `_parse_weekplan()` and `_parse_order_history()` scrape the server-rendered HTML returned by IBS5 endpoints. Menu items are identified by `<input type="number">` fields with IDs like `menu_quantity_{date}_{groupId}_{lineId}`.
- **Numeric customer ID** — The login ID (e.g. `S000001`) differs from the internal numeric ID needed for orders. `_get_numeric_customer_id()` fetches a weekplan page and extracts it from a hidden `#userId` input, with an in-memory cache.
- **SaveOrder helper** — `_save_order(client, customer_id, password, serve_date, menu_line_id, menu_group_id, quantity)` resolves the numeric id and POSTs `/Mealplan/SaveOrder`; the payload build is the pure `_build_save_order_payload(...)`. Shared by `add_to_cart`, `remove_meal` (quantity 0), and `bulk_add_to_cart`.
- **Range/menu helpers** — `_iso_weeks_in_range(from,to)` (pure week iteration), `_fetch_weekplan(...)` (GET+parse one ISO week), `_meal_to_json(item)` (project a parsed item into the JSON tag-shape), `_parse_price(str)` (DE "3,50 €" → `3.5`).
- **Planning core** — `_plan_lines(items, by_date)` is **pure** (no HTTP): it matches requested items against merged weekplan data, classifies conflicts, and computes `order_total` + counts. `plan_order` is the I/O wrapper that fetches the weekplans then calls it.
- **MCP tools exposed** (10 total): `list_profiles`, `get_weekly_menu`, `get_menu`, `plan_order`, `get_order_history`, `get_cart_status`, `add_to_cart`, `bulk_add_to_cart`, `confirm_order`, `remove_meal`.

### New tools (since the original 7)

- **`get_menu(profile, from_date, to_date, as_json)`** — date-range menu (iterates/dedupes the ISO weeks overlapping the range, filters to the range). Markdown like `get_weekly_menu` but with dietary tags appended; `as_json=True` → list of `{date, weekday, meals:[...]}`.
- **`plan_order(profile, items, as_json)`** — **DRY RUN, no mutation** (GETs weekplans only). `items` = `[{serve_date, menu_line_id, menu_group_id?}]`. Returns per-line `{..., tags, conflict}` + `order_total` + `counts`. The headline feature: preview cost + conflicts before mutating.
- **`bulk_add_to_cart(profile, items)`** — add many meals at once; `items` = `[{serve_date, menu_line_id, menu_group_id?, quantity?}]`. Keeps the ≥1-day-advance check per item; failing items are **skipped + reported**, not aborted. Returns per-line ok/skip/fail + final cart total.

### `classify_meal` — heuristic dietary tagger (best-effort)

`classify_meal(meal_name) -> {vegetarian: bool, contains: list[str], sweet: bool}` is a **pure, best-effort keyword heuristic** on the German dish name. It does **not** consult the IBS5 allergen/ingredient data — it is advisory only. `contains` ⊆ `["pork","beef","poultry","fish","egg"]`; `vegetarian` is true iff no pork/beef/poultry/fish matched (egg keeps lacto-ovo vegetarian). Disambiguation rules (e.g. bare `gulasch`→beef, `frikassee`→egg only when no meat fired, `sin carne`/`veg.`/`vegan` override meat-sounding tokens) and the full keyword table are documented in a comment above the function in `server.py`. Used by `get_menu` and `plan_order`.

`main.py` is a thin entry-point that re-exports `mcp.run` from `server.py`.

## IBS5 API Endpoints (used internally)

| Action | Method | Path |
|--------|--------|------|
| Weekly menu | GET | `/IBS5/Mealplan/Weekplan?year=&week=` |
| Order history | GET | `/IBS5/Account/Orderhistory?from=&to=&search=` |
| Save/remove order | POST | `/IBS5/Mealplan/SaveOrder` |
| Confirm order | POST | `/IBS5/Cart/Order` |
| Cart & balance | POST | `/IBS5/Mealplan/UpdateBalanceAndCart` |

## Key Domain Concepts

- **Menu line IDs**: `8` = GS 1, `9` = GS 2, `10` = GS 3 (daily meal choices)
- **Menu group ID**: typically `2`
- Orders must be placed at least 1 day before the delivery date
