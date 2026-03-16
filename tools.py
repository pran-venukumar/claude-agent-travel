"""
Travel agent tools:
  - get_weather:    fetches a forecast from yr.no (MET Norway) for any location name
  - search_flights: searches flight offers via Duffel API (free test environment)
                    set DUFFEL_API_KEY env var (get one at duffel.com/developers)
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
# Flight search tool (Duffel API — free test environment)
# Sign up at duffel.com/developers and create an access token.
# Set DUFFEL_API_KEY in your environment.
# ---------------------------------------------------------------------------

_DUFFEL_BASE = "https://api.duffel.com"


def _duffel_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ.get('DUFFEL_API_KEY', '')}",
        "Duffel-Version": "v2",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip",
    }


async def _resolve_iata(client: httpx.AsyncClient, query: str) -> tuple[str, str]:
    """
    Resolve a city/airport name or bare IATA code to (iata_code, display_name).
    Uses Duffel's places/suggestions endpoint; bare 3-letter codes are returned as-is.
    """
    query = query.strip()
    if len(query) == 3 and query.isalpha():
        return query.upper(), query.upper()

    resp = await client.get(
        f"{_DUFFEL_BASE}/places/suggestions",
        params={"query": query},
        headers=_duffel_headers(),
    )
    resp.raise_for_status()
    suggestions = resp.json().get("data", [])
    for s in suggestions:
        if s.get("iata_code"):
            name = s.get("name") or s.get("city_name") or query
            return s["iata_code"], name
    raise ValueError(f"Could not find an airport/city matching '{query}'")


@tool(
    "search_flights",
    (
        "Search for available flights between two cities. "
        "Accepts city names, airport names, or IATA codes for origin and destination. "
        "departure_date must be YYYY-MM-DD. "
        "Set return_date (YYYY-MM-DD) for a round trip, or leave it empty for one-way. "
        "passengers is the number of adults (default 1). "
        "Returns up to 5 options with airline, stops, duration, and price. "
        "Requires the DUFFEL_API_KEY environment variable (duffel.com/developers)."
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

    if not os.environ.get("DUFFEL_API_KEY"):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Flight search is not configured.\n"
                        "Sign up at duffel.com/developers, create an access token, "
                        "then set DUFFEL_API_KEY in your environment."
                    ),
                }
            ]
        }

    async with httpx.AsyncClient(timeout=30) as client:
        origin_iata, origin_name = await _resolve_iata(client, origin_q)
        dest_iata, dest_name = await _resolve_iata(client, dest_q)

        # Build slices — two slices for round trip, one for one-way
        slices = [
            {"origin": origin_iata, "destination": dest_iata, "departure_date": departure_date}
        ]
        if return_date:
            slices.append(
                {"origin": dest_iata, "destination": origin_iata, "departure_date": return_date}
            )

        body = {
            "data": {
                "slices": slices,
                "passengers": [{"type": "adult"} for _ in range(passengers)],
                "cabin_class": "economy",
            }
        }

        resp = await client.post(
            f"{_DUFFEL_BASE}/air/offer_requests",
            params={"return_offers": "true"},
            json=body,
            headers=_duffel_headers(),
        )
        resp.raise_for_status()
        offers = resp.json().get("data", {}).get("offers", [])

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
        f"  Date: {departure_date}  |  {trip_type}  |  {passengers} adult(s)\n",
    ]

    for i, offer in enumerate(offers[:5], 1):
        price = offer["total_amount"]
        currency = offer["total_currency"]
        airline = offer["owner"]["name"]

        lines.append(f"Option {i}  —  {currency} {price}  ({airline})")

        for leg_idx, slc in enumerate(offer["slices"]):
            label = "Outbound" if leg_idx == 0 else "Return  "
            segments = slc["segments"]
            first_seg = segments[0]
            last_seg = segments[-1]

            dep = first_seg["departing_at"].replace("T", " ")
            arr = last_seg["arriving_at"].replace("T", " ")
            origin_code = first_seg["origin"]["iata_code"]
            dest_code = last_seg["destination"]["iata_code"]

            # Total stops = intermediate stops within all segments
            total_stops = sum(len(seg.get("stops", [])) for seg in segments)
            extra_segments = len(segments) - 1  # connections
            total_stops += extra_segments
            stop_label = "non-stop" if total_stops == 0 else f"{total_stops} stop(s)"

            # Slice duration from first segment (slice-level duration not always present)
            duration = slc.get("duration", first_seg.get("duration", ""))
            duration = duration.replace("PT", "").lower() if duration else "?"

            lines.append(
                f"  {label}: {origin_code} {dep} → {dest_code} {arr}"
                f"  |  {duration}  |  {stop_label}"
            )
        lines.append("")

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


# ---------------------------------------------------------------------------
# Cheapest-week search (Duffel — searches every Saturday in a given month)
# ---------------------------------------------------------------------------


@tool(
    "find_cheapest_week",
    (
        "Find the cheapest 7-night round trip within a given month. "
        "Searches every Saturday in the month as a potential departure date "
        "and returns the 3 cheapest options with their exact dates and prices. "
        "Use this instead of search_flights when the traveller has only specified "
        "a travel month rather than exact dates. "
        "travel_month must be YYYY-MM format (e.g. '2026-07'). "
        "Requires the DUFFEL_API_KEY environment variable."
    ),
    {"origin": str, "destination": str, "travel_month": str, "passengers": int},
)
async def find_cheapest_week(args: dict) -> dict:
    import calendar
    from datetime import date, timedelta

    origin_q: str = args["origin"]
    dest_q: str = args["destination"]
    travel_month: str = args["travel_month"]   # YYYY-MM
    passengers: int = int(args.get("passengers") or 1)

    if not os.environ.get("DUFFEL_API_KEY"):
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Flight search is not configured. "
                        "Set DUFFEL_API_KEY in your environment."
                    ),
                }
            ]
        }

    try:
        year, month = map(int, travel_month.split("-"))
    except ValueError:
        return {
            "content": [{"type": "text", "text": f"Invalid travel_month '{travel_month}'. Use YYYY-MM format."}]
        }

    _, days_in_month = calendar.monthrange(year, month)
    saturdays = [
        date(year, month, day)
        for day in range(1, days_in_month + 1)
        if date(year, month, day).weekday() == 5  # Saturday
    ]

    async with httpx.AsyncClient(timeout=30) as client:
        origin_iata, origin_name = await _resolve_iata(client, origin_q)
        dest_iata, dest_name = await _resolve_iata(client, dest_q)

        results = []
        for dep_date in saturdays:
            ret_date = dep_date + timedelta(days=7)
            body = {
                "data": {
                    "slices": [
                        {
                            "origin": origin_iata,
                            "destination": dest_iata,
                            "departure_date": dep_date.isoformat(),
                        },
                        {
                            "origin": dest_iata,
                            "destination": origin_iata,
                            "departure_date": ret_date.isoformat(),
                        },
                    ],
                    "passengers": [{"type": "adult"} for _ in range(passengers)],
                    "cabin_class": "economy",
                }
            }
            try:
                resp = await client.post(
                    f"{_DUFFEL_BASE}/air/offer_requests",
                    params={"return_offers": "true"},
                    json=body,
                    headers=_duffel_headers(),
                )
                resp.raise_for_status()
                offers = resp.json().get("data", {}).get("offers", [])
                if offers:
                    cheapest = min(offers, key=lambda o: float(o["total_amount"]))
                    results.append(
                        {
                            "departure": dep_date.isoformat(),
                            "return": ret_date.isoformat(),
                            "price": float(cheapest["total_amount"]),
                            "currency": cheapest["total_currency"],
                            "airline": cheapest["owner"]["name"],
                        }
                    )
            except Exception:
                continue  # skip dates with no results

    if not results:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"No flights found for {origin_name} ({origin_iata}) → "
                        f"{dest_name} ({dest_iata}) in {travel_month}."
                    ),
                }
            ]
        }

    results.sort(key=lambda r: r["price"])

    lines = [
        f"Cheapest week-long trips: {origin_name} ({origin_iata}) → {dest_name} ({dest_iata})",
        f"  Month: {travel_month}  |  7 nights  |  {passengers} adult(s)\n",
    ]
    for i, r in enumerate(results[:3], 1):
        lines.append(
            f"Option {i}  —  {r['currency']} {r['price']:.2f}  ({r['airline']})"
        )
        lines.append(f"  Depart: {r['departure']}   Return: {r['return']}")
        lines.append("")

    lines.append(f"Cheapest option: depart {results[0]['departure']}, return {results[0]['return']}")

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
        find_cheapest_week,
        create_trip_task,
        list_trip_tasks,
        complete_trip_task,
        delete_trip_task,
    ],
)
