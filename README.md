# Travel Planning Agent

A multi-turn AI travel consultant built with the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python). The agent holds a persistent session across conversation turns and has access to live tools for weather, flight search, and trip task management.

## Features

- **Weather** — live forecasts from [yr.no](https://yr.no) (MET Norway) for any destination, resolved via OpenStreetMap geocoding. No API key required.
- **Flight search** — searches for flight offers via the [Amadeus for Developers](https://developers.amadeus.com) free sandbox. Accepts city names, airport names, or IATA codes.
- **Trip task manager** — create, list, complete, and delete planning tasks stored in a local SQLite database.

## Setup

```bash
pip install claude-agent-sdk anyio httpx
```

For flight search, register for free at [developers.amadeus.com](https://developers.amadeus.com/register) and export your sandbox credentials:

```bash
export AMADEUS_CLIENT_ID=your_client_id
export AMADEUS_CLIENT_SECRET=your_client_secret
```

## Usage

> **Note:** The Claude Agent SDK spawns a Claude Code subprocess internally. Run this from a regular terminal — not inside an active Claude Code session.

```bash
python3 travel_agent.py
```

## Project structure

```
travel_agent.py   — agent entry point and demo conversation
tools.py          — MCP tool definitions (weather, flights, tasks)
trip_tasks.db     — SQLite database, created automatically on first run
```

## Tools

| Tool | Description |
|---|---|
| `get_weather` | Current conditions and 12h forecast for any location |
| `search_flights` | Flight offers between two cities (requires Amadeus credentials) |
| `create_trip_task` | Add a planning task to the local database |
| `list_trip_tasks` | List all tasks, optionally filtered by destination |
| `complete_trip_task` | Mark a task as done by ID |
| `delete_trip_task` | Permanently remove a task by ID |
