"""
Travel agent tools:
  - get_weather:    fetches a forecast from yr.no (MET Norway) for any location name
  - search_flights: searches flight offers via Amadeus for Developers (free sandbox)
                    set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET env vars
  - create_trip_task / list_trip_tasks / complete_trip_task / delete_trip_task:
    SQLite-backed task manager for trip planning
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx
from claude_agent_sdk import tool, create_sdk_mcp_server

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent / "trip_tasks.db"


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                destination TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'pending',
                created_at  TEXT    NOT NULL
            )
            """
        )
        conn.commit()


_init_db()

# ---------------------------------------------------------------------------
# Weather tool (yr.no)
# ---------------------------------------------------------------------------


@tool(
    "get_weather",
    (
        "Get the current weather and short-term forecast for a travel destination. "
        "Provide a city or region name (e.g. 'Paris', 'Bishkek', 'Tian Shan mountains'). "
        "Returns temperature, wind, humidity, and a symbol for the next 12 h."
    ),
    {"location": str},
)
async def get_weather(args: dict) -> dict:
    location: str = args["location"]

    async with httpx.AsyncClient(timeout=10) as client:
        # Step 1 — geocode the location name with Nominatim (OpenStreetMap)
        geo_resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location, "format": "json", "limit": 1},
            headers={"User-Agent": "TravelPlanningAgent/1.0"},
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()

        if not geo_data:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Could not geocode '{location}'. Try a more specific place name.",
                    }
                ]
            }

        lat = float(geo_data[0]["lat"])
        lon = float(geo_data[0]["lon"])
        display_name = geo_data[0].get("display_name", location)

        # Step 2 — fetch forecast from yr.no (MET Norway)
        yr_resp = await client.get(
            "https://api.met.no/weatherapi/locationforecast/2.0/compact",
            params={"lat": round(lat, 4), "lon": round(lon, 4)},
            headers={"User-Agent": "TravelPlanningAgent/1.0 (for personal use)"},
        )
        yr_resp.raise_for_status()
        yr_data = yr_resp.json()

    timeseries = yr_data["properties"]["timeseries"]
    now_entry = timeseries[0]
    instant = now_entry["data"]["instant"]["details"]
    next_1h = now_entry["data"].get("next_1_hours", {})
    next_6h = now_entry["data"].get("next_6_hours", {})
    next_12h = now_entry["data"].get("next_12_hours", {})

    temp_c = instant.get("air_temperature", "N/A")
    wind_ms = instant.get("wind_speed", "N/A")
    humidity = instant.get("relative_humidity", "N/A")
    cloud_pct = instant.get("cloud_area_fraction", "N/A")

    symbol = (
        next_1h.get("summary", {}).get("symbol_code")
        or next_6h.get("summary", {}).get("symbol_code")
        or next_12h.get("summary", {}).get("symbol_code")
        or "N/A"
    )

    # Precipitation over next 6 h
    precip_6h = next_6h.get("details", {}).get("precipitation_amount", "N/A")

    text = (
        f"Weather for {location}\n"
        f"  Location resolved to: {display_name}\n"
        f"  Coordinates: {lat:.4f}°N, {lon:.4f}°E\n"
        f"  Time (UTC): {now_entry['time']}\n"
        f"\nCurrent conditions:\n"
        f"  Temperature:    {temp_c} °C\n"
        f"  Wind speed:     {wind_ms} m/s\n"
        f"  Humidity:       {humidity} %\n"
        f"  Cloud cover:    {cloud_pct} %\n"
        f"\nForecast:\n"
        f"  Next 6 h precipitation: {precip_6h} mm\n"
        f"  12 h outlook:           {symbol}\n"
        f"\nSource: yr.no / MET Norway (CC BY 4.0)"
    )

    return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# Flight search tool (Amadeus for Developers — free sandbox)
# Register at https://developers.amadeus.com/register to get credentials.
# Set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in your environment.
# ---------------------------------------------------------------------------

_AMADEUS_BASE = "https://test.api.amadeus.com"


async def _amadeus_token(client: httpx.AsyncClient) -> str:
    """Fetch a short-lived OAuth2 bearer token (client_credentials grant)."""
    resp = await client.post(
        f"{_AMADEUS_BASE}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["AMADEUS_CLIENT_ID"],
            "client_secret": os.environ["AMADEUS_CLIENT_SECRET"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def _resolve_iata(client: httpx.AsyncClient, token: str, query: str) -> tuple[str, str]:
    """
    Resolve a city/airport name or bare IATA code to (iata_code, display_name).
    If the query is already a 3-letter IATA code it is returned as-is.
    """
    query = query.strip()
    if len(query) == 3 and query.isalpha():
        return query.upper(), query.upper()

    resp = await client.get(
        f"{_AMADEUS_BASE}/v1/reference-data/locations",
        params={"keyword": query, "subType": "CITY,AIRPORT", "page[limit]": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    if not data:
        raise ValueError(f"Could not find an airport/city matching '{query}'")
    loc = data[0]
    iata = loc.get("iataCode") or loc["address"].get("cityCode", "???")
    name = loc.get("name", query)
    return iata, name


@tool(
    "search_flights",
    (
        "Search for available flights between two cities. "
        "Accepts city names, airport names, or IATA codes for origin and destination. "
        "departure_date must be YYYY-MM-DD. "
        "Set return_date (YYYY-MM-DD) for a round trip, or leave it empty for one-way. "
        "passengers is the number of adults (default 1). "
        "Returns the cheapest options with airline, stops, duration, and price. "
        "NOTE: uses the Amadeus test sandbox — prices and availability are illustrative. "
        "Requires AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET environment variables."
    ),
    {
        "origin": str,
        "destination": str,
        "departure_date": str,
        "return_date": str,
        "passengers": int,
    },
)
async def search_flights(args: dict) -> dict:
    origin_q: str = args["origin"]
    dest_q: str = args["destination"]
    departure_date: str = args["departure_date"]
    return_date: str = args.get("return_date", "").strip()
    passengers: int = int(args.get("passengers") or 1)

    if not os.environ.get("AMADEUS_CLIENT_ID") or not os.environ.get("AMADEUS_CLIENT_SECRET"):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Flight search is not configured.\n"
                        "Register for free at https://developers.amadeus.com/register, "
                        "then set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in your environment."
                    ),
                }
            ]
        }

    async with httpx.AsyncClient(timeout=15) as client:
        token = await _amadeus_token(client)
        origin_iata, origin_name = await _resolve_iata(client, token, origin_q)
        dest_iata, dest_name = await _resolve_iata(client, token, dest_q)

        params: dict = {
            "originLocationCode": origin_iata,
            "destinationLocationCode": dest_iata,
            "departureDate": departure_date,
            "adults": passengers,
            "max": 5,
            "currencyCode": "USD",
        }
        if return_date:
            params["returnDate"] = return_date

        resp = await client.get(
            f"{_AMADEUS_BASE}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        offers = resp.json().get("data", [])

    if not offers:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"No flights found from {origin_name} ({origin_iata}) "
                        f"to {dest_name} ({dest_iata}) on {departure_date}."
                    ),
                }
            ]
        }

    trip_type = f"round-trip (return {return_date})" if return_date else "one-way"
    lines = [
        f"Flights from {origin_name} ({origin_iata}) → {dest_name} ({dest_iata})",
        f"  Date: {departure_date}  |  {trip_type}  |  {passengers} adult(s)",
        f"  (Amadeus test sandbox — illustrative data)\n",
    ]

    for i, offer in enumerate(offers, 1):
        price = offer["price"]["grandTotal"]
        currency = offer["price"]["currency"]
        itineraries = offer["itineraries"]

        lines.append(f"Option {i}  —  ${price} {currency}")
        for leg_idx, itin in enumerate(itineraries):
            label = "Outbound" if leg_idx == 0 else "Return "
            duration = itin["duration"].replace("PT", "").lower()
            segments = itin["segments"]
            stops = len(segments) - 1
            stop_label = "non-stop" if stops == 0 else f"{stops} stop(s)"

            first_seg = segments[0]
            last_seg = segments[-1]
            dep = first_seg["departure"]["at"].replace("T", " ")
            arr = last_seg["arrival"]["at"].replace("T", " ")
            carrier = first_seg["carrierCode"]

            lines.append(
                f"  {label}: {dep} → {arr}  |  {duration}  |  {stop_label}  |  {carrier}"
            )
        lines.append("")

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


# ---------------------------------------------------------------------------
# Task management tools (SQLite)
# ---------------------------------------------------------------------------


@tool(
    "create_trip_task",
    (
        "Create a new trip-planning task and store it in the local database. "
        "Use this to track things to do, book, research, or pack for a trip. "
        "Returns the new task ID."
    ),
    {"title": str, "description": str, "destination": str},
)
async def create_trip_task(args: dict) -> dict:
    title: str = args["title"]
    description: str = args.get("description", "")
    destination: str = args.get("destination", "")
    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (title, description, destination, created_at) VALUES (?, ?, ?, ?)",
            (title, description, destination, created_at),
        )
        task_id = cursor.lastrowid
        conn.commit()

    text = f"Task #{task_id} created: '{title}'"
    if destination:
        text += f" (destination: {destination})"
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "list_trip_tasks",
    (
        "List trip-planning tasks from the local database. "
        "Pass a destination to filter results, or leave it empty to list all tasks. "
        "Shows task IDs, titles, destinations, and status (pending / done)."
    ),
    {"destination": str},
)
async def list_trip_tasks(args: dict) -> dict:
    destination: str = args.get("destination", "").strip()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if destination:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE destination LIKE ? ORDER BY id",
                (f"%{destination}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()

    if not rows:
        msg = "No tasks found"
        if destination:
            msg += f" for destination '{destination}'"
        return {"content": [{"type": "text", "text": msg + "."}]}

    lines = ["Trip planning tasks:\n"]
    for row in rows:
        status_icon = "✓" if row["status"] == "done" else "○"
        dest_tag = f" [{row['destination']}]" if row["destination"] else ""
        lines.append(
            f"  {status_icon} #{row['id']} {row['title']}{dest_tag}"
        )
        if row["description"]:
            lines.append(f"       {row['description']}")

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@tool(
    "complete_trip_task",
    (
        "Mark a trip-planning task as done. "
        "Provide the numeric task ID (visible in list_trip_tasks output)."
    ),
    {"task_id": int},
)
async def complete_trip_task(args: dict) -> dict:
    task_id: int = int(args["task_id"])

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return {
                "content": [{"type": "text", "text": f"Task #{task_id} not found."}]
            }
        conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
        conn.commit()

    return {
        "content": [
            {"type": "text", "text": f"Task #{task_id} '{row['title']}' marked as done."}
        ]
    }


@tool(
    "delete_trip_task",
    (
        "Permanently delete a trip-planning task by its numeric ID. "
        "Provide the task ID visible in list_trip_tasks output."
    ),
    {"task_id": int},
)
async def delete_trip_task(args: dict) -> dict:
    task_id: int = int(args["task_id"])

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT title FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return {
                "content": [{"type": "text", "text": f"Task #{task_id} not found."}]
            }
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()

    return {
        "content": [
            {"type": "text", "text": f"Task #{task_id} '{row['title']}' deleted."}
        ]
    }


# ---------------------------------------------------------------------------
# MCP server bundle
# ---------------------------------------------------------------------------

travel_tools_server = create_sdk_mcp_server(
    "travel-tools",
    tools=[
        get_weather,
        search_flights,
        create_trip_task,
        list_trip_tasks,
        complete_trip_task,
        delete_trip_task,
    ],
)
