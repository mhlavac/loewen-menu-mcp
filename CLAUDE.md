# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP (Model Context Protocol) server for the **Löwen Menü IBS5** meal ordering system. Exposes tools that let an LLM browse weekly school-lunch menus, view order history, manage the shopping cart, and place/remove meal orders via the IBS5 web API at `ibs.loewen-menue.de`.

## ⚠️ This repo is PUBLIC — check for personal data before every commit

This server talks to a real family's school-lunch accounts, so real names, account
numbers, building labels, school names and PINs pass through the session that writes
the code. **None of it may reach a commit.** Before staging anything, grep the diff —
including any new untracked files, which `git diff` does not show:

```bash
git diff; git ls-files --others --exclude-standard   # review BOTH
grep -rinE '(S[0-9]{6}|[A-Z][a-z]-[0-9]{3,}|@[a-z]+\.(com|net|de))' server.py tests/ *.md
```

That grep **is expected to produce hits** — every synthetic fixture matches the same
shapes. It is a candidate list, not a pass/fail: check each hit resolves to an approved
synthetic value below, and treat anything else as a leak. A grep that must stay silent
would have to hard-code the real identifiers, which is precisely what must not be here.

Scan by eye too, especially **docstrings and comments** rather than just code — a worked
example like `profile labels are first names ("<a real child's name>")` is the easiest
way to leak one, and has already happened once.

The test suite's rule is declared at the top of `tests/conftest.py` and applies to every
new test, fixture, docstring and comment: **SYNTHETIC DATA ONLY** — profiles `child1` /
`child2`, login ids `S000001` / `S000002`, numeric id `12345`, display name `Test Kind`,
fabricated building codes (`Wd-0001-`), `*.invalid` e-mail domains. Placeholder *shapes*
(`Surname, First Bldg-1234-`, the `_HOLDER_RE` regex) are fine; real values are not.

## Tech Stack

- Python 3.10, managed with **uv** (`uv.lock` + `pyproject.toml`)
- **MCPServer** (`mcp` SDK v2, `mcp.server.mcpserver`; formerly FastMCP) for the MCP server
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
- **Account identity (load-bearing safety check)** — the customer id in `PROFILES` is *unverifiable on its own*: a typo or a swapped pair silently books meals for the wrong child. **No IBS5 page contains the account-holder name.** The web client learns it from `POST /Login/Login` (form-encoded `identifierValue` / `secretValue` / `identifierType=0` / `secretType=0`) and stashes `name1` / `institutionName1` / `email` in `sessionStorage`, rendering the sidebar from there — so HTML scraping can never resolve identity, whatever the selector. `_fetch_account_identity()` therefore mirrors that login request and `_parse_login_identity()` (pure) projects the response into `_account_cache`. `_parse_account_info()` survives as a page-scraping fallback (classes, partial-class, then a `Surname, First Bldg-1234-` shape regex) in case the contract changes back. `_profile_matches_holder()` loosely compares the profile label against the holder (diacritic- and case-insensitive); `list_profiles` renders a **NAME MISMATCH** warning when it fails and an explicit reason when identity cannot be resolved, and `_who()` stamps the holder onto every mutating tool's output.
- **Cache split** — `_fetch_identity_page()` exists separately from `_get_numeric_customer_id()` because the latter early-returns from `_customer_id_cache`; folding the identity fetch inside it meant any earlier tool call permanently starved `_account_cache`.
- **SaveOrder helper** — `_save_order(client, customer_id, password, serve_date, menu_line_id, menu_group_id, quantity)` resolves the numeric id and POSTs `/Mealplan/SaveOrder`; the payload build is the pure `_build_save_order_payload(...)`. Shared by `add_to_cart`, `remove_meal` (quantity 0), and `bulk_add_to_cart`.
- **Range/menu helpers** — `_iso_weeks_in_range(from,to)` (pure week iteration), `_fetch_weekplan(...)` (GET+parse one ISO week), `_meal_to_json(item)` (project a parsed item into the JSON tag-shape), `_parse_price(str)` (DE "3,50 €" → `3.5`).
- **Planning core** — `_plan_lines(items, by_date)` is **pure** (no HTTP): it matches requested items against merged weekplan data, classifies conflicts, and computes `order_total` + counts. `plan_order` is the I/O wrapper that fetches the weekplans then calls it. Conflicts: `no_school_day`, `unknown_line`, `not_orderable`, `already_ordered`, `non_meal`.
- **Prices are frequently absent** — IBS5 leaves `data-price` (and the `.order-menu-node-price` element) empty for schools that do not publish per-meal prices, which is the case for this account. `_plan_lines` therefore returns `order_total: None` plus `price_data_available: False` rather than a fabricated `0.00`, and `get_cart_status` prints "not published by IBS5" instead of `€0.00`. Do not read a zero total as "free".
- **Non-meal rows** — holiday/closure markers ("FERIEN", "FERIENSTART!!!") occupy an ordinary menu slot with a working quantity input. The pure `_is_non_meal()` flags them; `_meal_to_json` forces `orderable: False` and sets `non_meal: True`, and `_plan_lines` raises the `non_meal` conflict.
- **Cancellation model** — IBS5 never deletes an order. `remove_meal` only writes cart quantity 0, which expires silently; a cancellation becomes real only after `/Cart/Order`, at which point IBS5 books an **offsetting negative-quantity row** for the same delivery date. `cancel_order` does both steps and verifies via the pure `_cancelled_delivery_dates()`. Never trust `confirm_order`'s count as "meals added" — it counts cart lines, cancellations included.
- **`get_order_history(search=...)` is filtered client-side on purpose** — IBS5's own `?search=` returns an empty table for orders that are present in the unfiltered listing (a confident false negative), so the parameter is not forwarded.
- **MCP tools exposed** (11 total): `list_profiles`, `get_weekly_menu`, `get_menu`, `plan_order`, `get_order_history`, `get_cart_status`, `add_to_cart`, `bulk_add_to_cart`, `confirm_order`, `remove_meal`, `cancel_order`.

### New tools (since the original 7)

- **`get_menu(profile, from_date, to_date, as_json)`** — date-range menu (iterates/dedupes the ISO weeks overlapping the range, filters to the range). Markdown like `get_weekly_menu` but with dietary tags appended; `as_json=True` → list of `{date, weekday, meals:[...]}`.
- **`plan_order(profile, items, as_json)`** — **DRY RUN, no mutation** (GETs weekplans only). `items` = `[{serve_date, menu_line_id, menu_group_id?}]`. Returns per-line `{..., tags, conflict}` + `order_total` + `counts`. The headline feature: preview cost + conflicts before mutating.
- **`bulk_add_to_cart(profile, items)`** — add many meals at once; `items` = `[{serve_date, menu_line_id, menu_group_id?, quantity?}]`. Keeps the ≥1-day-advance check per item; failing items are **skipped + reported**, not aborted. Returns per-line ok/skip/fail + final cart total.

### `classify_meal` — heuristic dietary tagger (best-effort)

`classify_meal(meal_name) -> {vegetarian: bool, contains: list[str], sweet: bool}` is a **pure, best-effort keyword heuristic** on the German dish name. It does **not** consult the IBS5 allergen/ingredient data — it is advisory only. `contains` ⊆ `["pork","beef","poultry","fish","egg"]`; `vegetarian` is true iff no pork/beef/poultry/fish matched (egg keeps lacto-ovo vegetarian). Disambiguation rules (e.g. bare `gulasch`→beef, `frikassee`→egg only when no meat fired, `sin carne`/`veg.`/`vegan` override meat-sounding tokens) and the full keyword table are documented in a comment above the function in `server.py`. `_VEG_COMPOUND_RE` additionally rescues vegetable/pulse compounds that merely look meaty (`Gemüsefrikadelle`, `Gemüsebulette`, `Linsenbratling`) while leaving genuine meat patties (`Geflügelbulette`) alone. Note umlaut traps: `wurst` does **not** substring-match `Würstchen`, so both spellings are listed. Used by `get_menu` and `plan_order`.

`main.py` is a thin entry-point that re-exports `mcp.run` from `server.py`.

## IBS5 API Endpoints (used internally)

| Action | Method | Path |
|--------|--------|------|
| Weekly menu | GET | `/IBS5/Mealplan/Weekplan?year=&week=` |
| Order history | GET | `/IBS5/Account/Orderhistory?from=&to=&search=` |
| Save/remove order | POST | `/IBS5/Mealplan/SaveOrder` |
| Confirm order | POST | `/IBS5/Cart/Order` |
| Account identity | POST | `/IBS5/Login/Login` (form-encoded; returns `name1`, `institutionName1`, `email`) |
| Cart & balance | POST | `/IBS5/Mealplan/UpdateBalanceAndCart` |

## Key Domain Concepts

- **Menu line IDs**: `8` = GS 1, `9` = GS 2, `10` = GS 3 (daily meal choices)
- **Menu group ID**: typically `2`
- Orders must be placed at least 1 day before the delivery date
