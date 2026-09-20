# loewen-menu-mcp

MCP (Model Context Protocol) server for the **Löwen Menü IBS5** school-lunch ordering system. Lets an LLM browse weekly menus, manage a shopping cart, and place meal orders via the IBS5 web portal at `ibs.loewen-menue.de`.

## Features

- Browse weekly meal plans by calendar week
- Browse an **arbitrary date range** with per-meal dietary tags (`get_menu`)
- **Dry-run an order** to preview total cost + conflicts before mutating (`plan_order`)
- Add a single meal, or **many meals at once** (`bulk_add_to_cart`), to the cart
- View order history
- Remove meals from the shopping cart
- Confirm orders
- Multi-profile support (order for multiple children from one server)

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager
- A Löwen Menü IBS5 account

## Setup

```bash
# Clone and install
git clone https://github.com/yourusername/loewen-menu-mcp.git
cd loewen-menu-mcp
uv sync
```

## Configuration

Credentials are passed via the `PROFILES` environment variable:

```
PROFILES=child1:CUSTOMER_ID:PASSWORD
```

For multiple children:

```
PROFILES=child1:S000001:1234,child2:S000002:5678
```

If only one profile is configured, it is used as the default and the `profile` parameter can be omitted from all tool calls.

## Usage with Claude Desktop

Add to your Claude Desktop MCP config (`~/.claude/settings.json` or a project-level MCP file):

```json
{
  "mcpServers": {
    "loewen-menu": {
      "command": "/bin/bash",
      "args": ["-c", "cd /path/to/loewen-menu-mcp && uv run server.py"],
      "env": {
        "PROFILES": "child1:S000001:1234,child2:S000002:5678"
      }
    }
  }
}
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_profiles` | List configured profiles **with the real account holder name**, warning on any label/holder mismatch |
| `get_weekly_menu` | Browse meals for a given calendar week |
| `get_menu` | Browse meals for an arbitrary date range, with dietary tags per meal (markdown or JSON) |
| `plan_order` | **Dry run** — preview total cost + per-line conflicts for a multi-meal order, **without mutating** |
| `get_order_history` | View past orders (filterable by days back and search text) |
| `get_cart_status` | Check shopping cart contents, balance, and projected balance after the cart |
| `add_to_cart` | Add a single meal to the cart (does **not** finalize the order) |
| `bulk_add_to_cart` | Add **many** meals to the cart at once; per-item validation, skip+report on failure |
| `confirm_order` | Finalize and submit all items in the cart |
| `remove_meal` | Stage a cart quantity of 0 (does **not** cancel a confirmed order on its own) |
| `cancel_order` | Cancel confirmed orders: stage + confirm + verify the offsetting `-1` rows |

### Ordering flow

1. `get_menu` (or `get_weekly_menu`) — see what's available, with dietary tags
2. `plan_order` — dry-run the meals you intend to order; check `order_total` and conflicts
3. `bulk_add_to_cart` (or `add_to_cart`) — add the meals
4. `get_cart_status` — verify the cart
5. `confirm_order` — submit the order

### `get_menu`

```
get_menu(profile="child1", from_date="2026-06-08", to_date="2026-06-19", as_json=False)
```

Fetches every ISO week overlapping the range (deduped within the call) and
filters meals to `[from_date, to_date]`. With `as_json=True` returns a list of
`{date, weekday, meals:[{line_label, menu_line_id, menu_group_id, meal_name,
price, max_quantity, already_ordered, orderable, tags}]}`.

### `plan_order` (dry run)

```
plan_order(profile="child1", items=[
  {"serve_date": "2026-06-08", "menu_line_id": "8"},
  {"serve_date": "2026-06-09", "menu_line_id": "9", "menu_group_id": "2"},
], as_json=True)
```

Only GETs weekplans — it **never touches the cart**. Returns per-line
`{serve_date, menu_line_id, line_label, meal_name, price, already_ordered,
orderable, tags, conflict}`, plus `order_total` (sum of prices for clean,
orderable lines) and `counts {orderable, conflicts}`. `conflict` is one of:

| conflict | meaning |
|----------|---------|
| `null` | line is clean and orderable |
| `no_school_day` | that date isn't in the weekplan at all |
| `unknown_line` | the date is present but the menu line wasn't found |
| `not_orderable` | ordering window is closed (`ordering_disabled`) |
| `already_ordered` | a portion is already ordered for this line |

### `bulk_add_to_cart`

```
bulk_add_to_cart(profile="child1", items=[
  {"serve_date": "2026-06-08", "menu_line_id": "8", "quantity": 1},
  {"serve_date": "2026-06-09", "menu_line_id": "9"},
])
```

Each item is validated for the **≥1-day-advance** rule; items that fail are
**skipped and reported** (the rest still proceed). Returns a per-line
ok/skip/fail summary and the final cart total. You still call `confirm_order`
to finalize.

### Dietary tags (`classify_meal`) — heuristic / best-effort

`get_menu` and `plan_order` attach a `tags` object to each meal:

```json
{ "vegetarian": true, "contains": ["egg"], "sweet": false }
```

- `vegetarian` is `true` when no pork/beef/poultry/fish keyword matched
  (egg does **not** break lacto-ovo vegetarian).
- `contains` lists matched animal categories, a subset of
  `["pork","beef","poultry","fish","egg"]`.
- `sweet` flags desserts / sweet mains.

> **This is a best-effort keyword heuristic on the German dish name.** It does
> **not** consult the allergen list or ingredient data — treat it as advisory,
> not a substitute for the official allergen declaration. The full keyword
> table and disambiguation rules live in a comment above `classify_meal` in
> `server.py`.

## Development

```bash
uv sync            # installs dev deps (pytest) too
uv run pytest -q   # run the test suite (no network — HTTP is mocked)
```

Tests use `httpx.MockTransport` and synthetic HTML fixtures; **no real IBS5
calls** are made.

## How it works

The server authenticates to IBS5 using a Base64-encoded Bearer token built from the customer ID and password. Credentials are configured server-side via environment variables and never pass through the LLM.

Weekly menus and order history are scraped from server-rendered HTML pages. Cart operations and order confirmation use JSON API endpoints.

## License

MIT
