import json
import os
import requests
from mcp.server.fastmcp import FastMCP


SUPERSET_URL = os.environ.get("SUPERSET_URL", "http://localhost:8088")
SUPERSET_USER = os.environ.get("SUPERSET_USER", "admin")
SUPERSET_PASS = os.environ.get("SUPERSET_PASS", "admin")

mcp = FastMCP("superset")


def get_session() -> requests.Session:
    session = requests.Session()

    r = session.post(f"{SUPERSET_URL}/api/v1/security/login", json={
        "username": SUPERSET_USER,
        "password": SUPERSET_PASS,
        "provider": "db",
        "refresh": True,
    })
    r.raise_for_status()
    token = r.json()["access_token"]
    session.headers.update({"Authorization": f"Bearer {token}"})

    csrf_r = session.get(f"{SUPERSET_URL}/api/v1/security/csrf_token/")
    csrf_r.raise_for_status()
    session.headers.update({
        "X-CSRFToken": csrf_r.json()["result"],
        "Referer": SUPERSET_URL,
    })

    return session


@mcp.tool()
def list_datasets() -> str:
    """List all available datasets in Superset with their IDs and names."""
    session = get_session()
    r = session.get(f"{SUPERSET_URL}/api/v1/dataset/")
    r.raise_for_status()
    results = [
        {"id": d["id"], "name": d["table_name"], "database": d["database"]["database_name"]}
        for d in r.json().get("result", [])
    ]
    return json.dumps(results, indent=2)


@mcp.tool()
def run_sql(database_id: int, sql: str) -> str:
    """Execute a SQL query against a Superset database and return columns and rows."""
    session = get_session()
    r = session.post(f"{SUPERSET_URL}/api/v1/sqllab/execute/", json={
        "database_id": database_id,
        "sql": sql,
        "runAsync": False,
    })
    r.raise_for_status()
    data = r.json()
    return json.dumps({
        "columns": [c["name"] for c in data.get("columns", [])],
        "rows": data.get("data", []),
    }, indent=2)


@mcp.tool()
def list_charts() -> str:
    """List all existing charts in Superset."""
    session = get_session()
    r = session.get(f"{SUPERSET_URL}/api/v1/chart/")
    r.raise_for_status()
    results = [
        {"id": c["id"], "name": c["slice_name"], "viz_type": c["viz_type"]}
        for c in r.json().get("result", [])
    ]
    return json.dumps(results, indent=2)


@mcp.tool()
def create_chart(
    name: str,
    dataset_id: int,
    viz_type: str,
    params: dict,
) -> str:
    """
    Create a chart in Superset 6.x.

    Parameters:
    - name: chart name
    - dataset_id: dataset ID (use list_datasets to find it)
    - viz_type: chart type. Valid options in Superset 6:
        "deck_scatter"            -> point map with lat/lon
        "deck_heatmap"            -> heat map
        "echarts_timeseries_line" -> line chart (time series)
        "echarts_timeseries_bar"  -> bar chart (time series)
        "echarts_area"            -> area chart
        "pie"                     -> pie chart
        "table"                   -> table
    - params: chart configuration as dict. Examples by type:

      deck_scatter:
        {
          "spatial": {"type": "latlong", "latCol": "latitude", "lonCol": "longitude"},
          "point_radius_fixed": {"type": "fix", "value": 50},
          "color_picker": {"r": 255, "g": 85, "b": 0, "a": 0.8},
          "mapbox_style": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
          "viewport": {"longitude": -95, "latitude": 37, "zoom": 3, "pitch": 0, "bearing": 0},
          "row_limit": 10000
        }

      echarts_timeseries_line:
        {
          "x_axis": "date",
          "time_grain_sqla": "P1M",
          "metrics": ["count"],
          "groupby": [],
          "color_scheme": "supersetColors"
        }

      pie:
        {
          "metric": "count",
          "groupby": ["source"],
          "color_scheme": "supersetColors"
        }

      table:
        {
          "all_columns": ["id", "title", "latitude", "longitude", "date"],
          "order_by_cols": []
        }
    """
    session = get_session()
    params["viz_type"] = viz_type
    params["datasource"] = f"{dataset_id}__table"

    payload = {
        "slice_name": name,
        "datasource_id": dataset_id,
        "datasource_type": "table",
        "viz_type": viz_type,
        "params": json.dumps(params),
    }
    r = session.post(f"{SUPERSET_URL}/api/v1/chart/", json=payload)
    r.raise_for_status()
    result = r.json().get("result", {})
    return json.dumps({"id": result.get("id"), "name": result.get("slice_name")}, indent=2)


@mcp.tool()
def list_dashboards() -> str:
    """List all existing dashboards in Superset."""
    session = get_session()
    r = session.get(f"{SUPERSET_URL}/api/v1/dashboard/")
    r.raise_for_status()
    results = [
        {"id": d["id"], "title": d["dashboard_title"], "status": d["status"]}
        for d in r.json().get("result", [])
    ]
    return json.dumps(results, indent=2)


@mcp.tool()
def create_dashboard(title: str) -> str:
    """Create a new dashboard in Superset."""
    session = get_session()
    r = session.post(f"{SUPERSET_URL}/api/v1/dashboard/", json={"dashboard_title": title})
    r.raise_for_status()
    result = r.json().get("result", {})
    return json.dumps({"id": result.get("id"), "title": result.get("dashboard_title")}, indent=2)


@mcp.tool()
def add_chart_to_dashboard(dashboard_id: int, chart_ids: list[int]) -> str:
    """
    Add one or more charts to an existing dashboard.

    Note: Superset 6 REST API only supports chart binding via position_json.
    The ORM relationship (dashboard_slices table) requires direct access to the
    container's metadata database if needed.
    """
    session = get_session()

    r = session.get(f"{SUPERSET_URL}/api/v1/dashboard/{dashboard_id}")
    r.raise_for_status()
    current = r.json().get("result", {})

    existing_pos = current.get("position_json") or "{}"
    position = json.loads(existing_pos) if isinstance(existing_pos, str) else existing_pos

    if "ROOT_ID" not in position:
        position = {
            "DASHBOARD_VERSION_KEY": "v2",
            "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
            "GRID_ID": {"children": [], "id": "GRID_ID", "type": "GRID", "parents": ["ROOT_ID"]},
        }

    existing_chart_ids = {
        v["meta"]["chartId"]
        for v in position.values()
        if isinstance(v, dict) and v.get("type") == "CHART"
    }
    new_ids = [i for i in chart_ids if i not in existing_chart_ids]

    if new_ids:
        row_id = f"ROW-{dashboard_id}"
        if row_id not in position:
            position[row_id] = {
                "children": [], "id": row_id,
                "meta": {"background": "BACKGROUND_TRANSPARENT"},
                "type": "ROW", "parents": ["ROOT_ID", "GRID_ID"],
            }
            position["GRID_ID"]["children"].append(row_id)

        for cid in new_ids:
            key = f"CHART-{cid}"
            position[key] = {
                "children": [], "id": key,
                "meta": {"chartId": cid, "height": 50, "width": 4},
                "type": "CHART",
                "parents": ["ROOT_ID", "GRID_ID", row_id],
            }
            position[row_id]["children"].append(key)

    put_r = session.put(
        f"{SUPERSET_URL}/api/v1/dashboard/{dashboard_id}",
        json={"position_json": json.dumps(position)},
    )
    put_r.raise_for_status()
    all_ids = list(existing_chart_ids | set(chart_ids))
    return json.dumps({"dashboard_id": dashboard_id, "chart_ids": all_ids}, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
