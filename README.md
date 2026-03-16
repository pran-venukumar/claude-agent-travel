# Travel Planning Agent

A travel planning agent built with the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python), available in two modes: a **conversational assistant** for back-and-forth trip exploration, and an **autonomous planner** that takes your preferences and delivers a complete trip plan without asking any questions.

## Features

- **Weather** — live forecasts from [yr.no](https://yr.no) (MET Norway) for any destination, resolved via OpenStreetMap geocoding. No API key required.
- **Flight search** — searches for flight offers via the [Duffel API](https://duffel.com/developers) (free test environment). Finds the cheapest week in a given month, or searches specific dates.
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

```bash
python3 autonomous_agent.py
```

The agent prompts for three inputs, then works entirely on its own:

```
Origin city: Bengaluru
Destination: Kyrgyzstan
Travel month MM/YYYY (optional, press Enter to skip): 07/2026
```

- **Supply a month** — the agent finds the cheapest Saturday-departure week in that month and builds the plan around it.
- **Skip the month** — you'll be prompted for exact departure and return dates instead.

The agent then runs autonomously:
1. Checks the weather at the destination
2. Finds the cheapest flights (or searches specific dates)
3. Creates a full task list in the database
4. Delivers a complete formatted plan (itinerary, accommodation, budget breakdown, packing list)

### Conversational assistant

```bash
python3 travel_agent.py
```

A multi-turn session where you drive the conversation turn by turn.

## Project structure

```
autonomous_agent.py  — autonomous planner (interactive prompts → complete plan)
travel_agent.py      — conversational assistant (multi-turn)
tools.py             — MCP tool definitions shared by both agents
trip_tasks.db        — SQLite task database, created automatically on first run
```

## Tools

| Tool | Description |
|---|---|
| `get_weather` | Current conditions and 12h forecast for any location |
| `find_cheapest_week` | Cheapest 7-night round trip across all Saturdays in a given month |
| `search_flights` | Flight offers for specific departure and return dates |
| `create_trip_task` | Add a planning task to the local database |
| `list_trip_tasks` | List all tasks, optionally filtered by destination |
| `complete_trip_task` | Mark a task as done by ID |
| `delete_trip_task` | Permanently remove a task by ID |
