# loewen-menu-mcp

MCP (Model Context Protocol) server for the **Löwen Menü IBS5** school-lunch ordering system. Lets an LLM browse weekly menus, manage a shopping cart, and place meal orders via the IBS5 web portal at `ibs.loewen-menue.de`.

## Features

- Browse weekly meal plans by calendar week
- View order history
- Add/remove meals from the shopping cart
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
| `list_profiles` | List configured profiles (children) |
| `get_weekly_menu` | Browse meals for a given calendar week |
| `get_order_history` | View past orders (filterable by days back and search text) |
| `get_cart_status` | Check shopping cart contents and balance |
| `add_to_cart` | Add a meal to the cart (does **not** finalize the order) |
| `confirm_order` | Finalize and submit all items in the cart |
| `remove_meal` | Remove a meal from the cart |

### Ordering flow

1. `get_weekly_menu` — see what's available
2. `add_to_cart` — add meals (repeat for each day/meal)
3. `get_cart_status` — verify the cart
4. `confirm_order` — submit the order

## How it works

The server authenticates to IBS5 using a Base64-encoded Bearer token built from the customer ID and password. Credentials are configured server-side via environment variables and never pass through the LLM.

Weekly menus and order history are scraped from server-rendered HTML pages. Cart operations and order confirmation use JSON API endpoints.

## License

MIT
