# Travel Planning Agent

A travel planning agent built with the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python), available in two modes: a **conversational assistant** for back-and-forth trip exploration, and an **autonomous planner** that takes your preferences once and delivers a complete trip plan without asking any questions.

## Features

- **Weather** — live forecasts from [yr.no](https://yr.no) (MET Norway) for any destination, resolved via OpenStreetMap geocoding. No API key required.
- **Flight search** — searches for flight offers via the [Duffel API](https://duffel.com/developers) (free test environment). Accepts city names, airport names, or IATA codes.
- **Trip task manager** — create, list, complete, and delete planning tasks stored in a local SQLite database.

## Setup

```bash
pip install claude-agent-sdk anyio httpx
```

For flight search, sign up at [duffel.com/developers](https://duffel.com/developers), create an access token, and export it:

```bash
export DUFFEL_API_KEY=your_access_token
```

> **Note:** The Claude Agent SDK spawns a Claude Code subprocess internally. Always run from a regular terminal — not inside an active Claude Code session.

## Usage

### Autonomous planner (recommended)

Edit the `my_trip` block at the bottom of `autonomous_agent.py` with your preferences, then run:

```bash
python3 autonomous_agent.py
```

The agent works through a fixed workflow without any prompting:
1. Checks the weather at your destination
2. Searches for flights from your origin
3. Creates a full task list in the database
4. Delivers a complete formatted plan (itinerary, accommodation, budget breakdown, packing list)

### Conversational assistant

```bash
python3 travel_agent.py
```

A multi-turn session where you drive the conversation turn by turn.

## Project structure

```
autonomous_agent.py  — autonomous planner (single-shot input → complete plan)
travel_agent.py      — conversational assistant (multi-turn)
tools.py             — MCP tool definitions shared by both agents
trip_tasks.db        — SQLite task database, created automatically on first run
```

## Tools

| Tool | Description |
|---|---|
| `get_weather` | Current conditions and 12h forecast for any location |
| `search_flights` | Flight offers between two cities (requires `DUFFEL_API_KEY`) |
| `create_trip_task` | Add a planning task to the local database |
| `list_trip_tasks` | List all tasks, optionally filtered by destination |
| `complete_trip_task` | Mark a task as done by ID |
| `delete_trip_task` | Permanently remove a task by ID |

## TripPreferences

The autonomous planner is driven by a `TripPreferences` dataclass. Fill in as much detail as possible — the more context, the better the plan.

```python
TripPreferences(
    origin_city="London",
    destination="Kyrgyzstan",
    departure_date="2025-07-10",
    return_date="2025-07-17",
    travelers=1,
    budget_usd=3000,
    interests=["hiking", "nomadic culture", "photography"],
    accommodation_pref="mix of guesthouses and yurt camps",
    fitness_level="high",          # low | moderate | high
    must_haves=["Song-Kol lake", "Tian Shan trek"],
    avoid=["luxury hotels", "large group tours"],
    extra_notes="UK passport holder, carrying own camping gear.",
)
```
