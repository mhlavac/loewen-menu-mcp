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
```

No test suite or linter is configured yet.

## Architecture

All logic lives in a single file: **`server.py`**.

- **Profiles** — Credentials are configured via a single `PROFILES` env var (`name:customer_id:password,...`). Tools take a `profile` name instead of raw credentials. If only one profile is configured, it's used as the default.
- **Auth** — `_make_token()` builds a Base64 Bearer token in the format `customerId;password;0;0;0;timestamp`. Credentials never pass through the LLM.
- **HTTP** — A module-level `httpx.Client` singleton (`_get_client()`) is reused across calls. GET requests require browser-like `Accept`/`User-Agent` headers.
- **HTML parsing** — `_parse_weekplan()` and `_parse_order_history()` scrape the server-rendered HTML returned by IBS5 endpoints. Menu items are identified by `<input type="number">` fields with IDs like `menu_quantity_{date}_{groupId}_{lineId}`.
- **Numeric customer ID** — The login ID (e.g. `S000001`) differs from the internal numeric ID needed for orders. `_get_numeric_customer_id()` fetches a weekplan page and extracts it from a hidden `#userId` input, with an in-memory cache.
- **MCP tools exposed** (7 total): `list_profiles`, `get_weekly_menu`, `get_order_history`, `get_cart_status`, `add_to_cart`, `confirm_order`, `remove_meal`.

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
