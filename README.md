# Superset MCP Server

A custom MCP (Model Context Protocol) server that wraps the Apache Superset REST API, allowing Claude to create and manage charts and dashboards through natural language.

## Requirements

- Python 3.11+
- Docker + Docker Compose
- Claude Code

---

## Part 1 — Running Superset

The repo includes a ready-to-use Docker Compose setup with Superset pre-configured (OpenStreetMap tiles, no Mapbox key required).

```bash
docker-compose up --build
```

Superset will be available at `http://localhost:8088`.  
Default credentials: `admin` / `admin`.

First startup takes a few minutes — Superset runs database migrations and initializes on boot.

**What's pre-configured in `superset/superset_config.py`:**
- `PREVENT_UNSAFE_DB_CONNECTIONS = False` — required to connect to local databases (SQLite, files)
- `MAPBOX_API_KEY = ""` — map charts use OpenStreetMap tiles instead, no API key needed

**Connecting your database:**

Go to `Settings → Database Connections → + Database` and add your database.  
For SQLite, use the path as it appears **inside the container**. Mount your data directory as a volume first:

```yaml
# docker-compose.yml
volumes:
  - ./data:/app/data          # your data folder
  - superset_home:/app/superset_home
  - ./superset/superset_config.py:/app/pythonpath/superset_config.py
```

Then connect using `sqlite:////app/data/your_file.db`.

---

## Part 2 — MCP Server

The MCP server runs as a local Python process — **not in Docker**. Claude Code spawns it as a subprocess and communicates via stdio. Dockerizing it breaks this.

### Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Registering with Claude Code

Copy `.mcp.json.example` to `.mcp.json` in your project root and fill in the absolute paths:

```json
{
  "mcpServers": {
    "superset": {
      "type": "stdio",
      "command": "/absolute/path/to/venv/bin/python3",
      "args": ["/absolute/path/to/SUPERSET_MCP/mcp_server.py"],
      "env": {
        "SUPERSET_URL": "http://localhost:8088",
        "SUPERSET_USER": "admin",
        "SUPERSET_PASS": "admin"
      }
    }
  }
}
```

Reopen the project in Claude Code after saving. Tools register at session startup.

### Available Tools

| Tool | Description |
|---|---|
| `list_datasets` | List all datasets with IDs and names |
| `run_sql` | Execute a SQL query and return results |
| `list_charts` | List all existing charts |
| `create_chart` | Create a new chart |
| `list_dashboards` | List all dashboards |
| `create_dashboard` | Create a new dashboard |
| `add_chart_to_dashboard` | Add charts to a dashboard via `position_json` |

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SUPERSET_URL` | `http://localhost:8088` | Superset base URL |
| `SUPERSET_USER` | `admin` | Superset username |
| `SUPERSET_PASS` | `admin` | Superset password |

---

## Known Limitations & Workarounds

These are real issues you will hit when using Superset 6.x via REST API.

### 1. Charts show "no chart definition" error after being added to a dashboard

**Why it happens:** `PUT /api/v1/dashboard/{id}` only updates `position_json` (the visual layout). It silently ignores the ORM relationship between the dashboard and its charts — that lives in the `dashboard_slices` table inside Superset's internal SQLite metadata database.

**Fix:** After calling `add_chart_to_dashboard`, insert the ORM links manually into the container:

```bash
docker exec superset python3 -c "
import sqlite3
conn = sqlite3.connect('/app/superset_home/superset.db')
for chart_id in [1, 2, 3]:  # replace with your chart IDs
    conn.execute(
        'INSERT OR IGNORE INTO dashboard_slices (dashboard_id, slice_id) VALUES (?, ?)',
        (<dashboard_id>, chart_id)
    )
conn.commit()
conn.close()
"
```

### 2. `create_dashboard` returns `null` for the ID

The tool response returns `{"id": null, ...}`. Call `list_dashboards` immediately after to get the real ID.

### 3. Valid viz_types in Superset 6.x

The legacy `bar` and `line` types are not registered in the Superset 6 frontend and will silently break charts. Use only:

| viz_type | Description |
|---|---|
| `deck_scatter` | Point map with lat/lon |
| `deck_heatmap` | Heat map |
| `echarts_timeseries_line` | Line chart |
| `echarts_timeseries_bar` | Bar chart |
| `echarts_area` | Area chart |
| `pie` | Pie / donut chart |
| `table` | Table |

### 4. `deck_scatter` params format

Do **not** pass `latitude` and `longitude` at the root of `params`. Use the `spatial` object:

```json
{
  "spatial": {
    "type": "latlong",
    "latCol": "latitude",
    "lonCol": "longitude"
  },
  "mapbox_style": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  "viewport": {"longitude": 0, "latitude": 20, "zoom": 2, "pitch": 0, "bearing": 0},
  "point_radius_fixed": {"type": "fix", "value": 50},
  "row_limit": 10000
}
```

### 5. Authentication requires both JWT and CSRF token

Every session needs a Bearer token (from `/api/v1/security/login`) **and** a CSRF token (from `/api/v1/security/csrf_token/`). POSTs without the CSRF token fail with 401. The `get_session()` function handles both automatically.
