"""
Autonomous Travel Planning Agent

Run the script and answer three prompts:
    Origin city      — where you're flying from
    Destination      — where you want to go
    Travel month     — MM/YYYY (optional; press Enter to skip for a specific date)

If you supply a month, the agent finds the cheapest Saturday-departure week in
that month. If you supply exact dates instead, it searches those directly.

The agent then works autonomously — weather, flights, task creation, full plan —
without asking any further questions.

NOTE: Run from a regular terminal, not inside an active Claude Code session.
"""

import anyio
from dataclasses import dataclass, field
from datetime import date

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from tools import travel_tools_server

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a fully autonomous travel planning agent.

You receive a traveller's preferences once and produce a complete, actionable trip plan
entirely on your own. You do NOT ask clarifying questions under any circumstances.
If information is missing or ambiguous, make a reasonable assumption and state it briefly.

━━━ YOUR TOOLS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  get_weather        — live forecast for any destination
  find_cheapest_week — find the cheapest 7-night round trip in a given month
  search_flights     — search flights for specific departure and return dates
  create_trip_task   — add a concrete action item to the planning database
  list_trip_tasks    — review all logged tasks
  complete_trip_task — mark a task as done
  delete_trip_task   — remove a task

━━━ YOUR MANDATORY WORKFLOW ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Execute these steps in order — do not skip any:

  STEP 1 · WEATHER
    Call get_weather for the main destination city.
    If the trip spans multiple distinct regions, call it for each.

  STEP 2 · FLIGHTS
    • If a travel_month is provided (and no exact dates): call find_cheapest_week
      to find the cheapest Saturday-departure week in that month.
    • If exact departure_date and return_date are provided: call search_flights.
    Use the cheapest result as the recommended flight throughout the plan.

  STEP 3 · TASKS
    Call create_trip_task for every concrete action item. Cover at minimum:
      - Book the specific flight identified in Step 2 (include price and dates)
      - Visa / entry requirements research and application
      - Travel insurance
      - Accommodation booking for each segment
      - Internal transport (airport transfers, intercity, car hire, etc.)
      - Any activities or tours that require advance booking
      - Packing reminders tailored to the destination and weather
    Tag every task with the destination field.

  STEP 4 · SYNTHESIZE
    Produce the final formatted travel plan (see output contract below).

━━━ OUTPUT CONTRACT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Your final answer MUST include all of the following sections:

  1. TRIP SUMMARY         — one-paragraph overview
  2. RECOMMENDED FLIGHT   — cheapest option found, with exact dates and price
  3. WEATHER BRIEFING     — what to expect and how it shapes the plan
  4. DAY-BY-DAY ITINERARY — concrete schedule for every day of the trip
  5. ACCOMMODATION PLAN   — where to stay each segment and why
  6. BUDGET BREAKDOWN     — estimated costs (flights, accommodation, food, activities)
  7. TASK CHECKLIST       — all tasks created, grouped by category
  8. PACKING LIST         — tailored to the destination, weather, and activities

Ground every section in real data from your tool results. Be specific, not generic.
"""

# ---------------------------------------------------------------------------
# Trip preferences dataclass
# ---------------------------------------------------------------------------


@dataclass
class TripPreferences:
    """
    Everything the autonomous agent needs to plan a trip without asking questions.
    Either supply travel_month (YYYY-MM) for cheapest-week search,
    or supply departure_date + return_date for a fixed itinerary.
    """

    # Core logistics
    origin_city: str
    destination: str

    # Date options — supply travel_month OR exact dates (not both)
    travel_month: str = ""       # YYYY-MM  →  agent finds cheapest week
    departure_date: str = ""     # YYYY-MM-DD  →  used with return_date
    return_date: str = ""        # YYYY-MM-DD

    travelers: int = 1
    budget_usd: int = 2000

    # Preferences (edit these defaults to personalise the plan)
    interests: list[str] = field(default_factory=list)
    accommodation_pref: str = "mid-range guesthouses or hotels"
    fitness_level: str = "moderate"   # low | moderate | high
    must_haves: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    extra_notes: str = ""


# ---------------------------------------------------------------------------
# Goal prompt builder
# ---------------------------------------------------------------------------


def _build_goal_prompt(prefs: TripPreferences) -> str:
    interests_str = ", ".join(prefs.interests) if prefs.interests else "general sightseeing"
    must_haves_str = (
        "\n".join(f"    • {m}" for m in prefs.must_haves)
        if prefs.must_haves
        else "    • None specified"
    )
    avoid_str = (
        "\n".join(f"    • {a}" for a in prefs.avoid)
        if prefs.avoid
        else "    • None specified"
    )

    # Build the dates line depending on what was supplied
    if prefs.travel_month:
        try:
            year, month = map(int, prefs.travel_month.split("-"))
            from calendar import month_name
            month_label = f"{month_name[month]} {year}"
        except Exception:
            month_label = prefs.travel_month
        dates_line = (
            f"  Travel month:       {prefs.travel_month} ({month_label})\n"
            f"  Duration:           7 nights (cheapest Saturday departure)\n"
            f"  Exact dates:        to be determined by find_cheapest_week tool"
        )
    else:
        try:
            d1 = date.fromisoformat(prefs.departure_date)
            d2 = date.fromisoformat(prefs.return_date)
            nights = (d2 - d1).days
            dates_line = (
                f"  Departure:          {prefs.departure_date}\n"
                f"  Return:             {prefs.return_date}  ({nights} nights)"
            )
        except Exception:
            dates_line = (
                f"  Departure:          {prefs.departure_date}\n"
                f"  Return:             {prefs.return_date}"
            )

    return f"""Plan this trip autonomously. Follow your mandatory workflow exactly.
Do NOT ask me any questions — work with what I've given you and state any assumptions clearly.

━━━ TRIP PREFERENCES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Origin:             {prefs.origin_city}
  Destination:        {prefs.destination}
{dates_line}
  Travellers:         {prefs.travelers} adult(s)
  Total budget:       ${prefs.budget_usd:,} USD

  Interests:          {interests_str}
  Accommodation:      {prefs.accommodation_pref}
  Fitness level:      {prefs.fitness_level}

  Must-haves:
{must_haves_str}

  Things to avoid:
{avoid_str}

  Extra notes:        {prefs.extra_notes or "None"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Now execute your workflow: weather → {"find_cheapest_week" if prefs.travel_month else "search_flights"} → tasks → complete plan.
"""


# ---------------------------------------------------------------------------
# Autonomous agent
# ---------------------------------------------------------------------------


class AutonomousTravelAgent:
    ALLOWED_TOOLS = [
        "mcp__travel-tools__get_weather",
        "mcp__travel-tools__find_cheapest_week",
        "mcp__travel-tools__search_flights",
        "mcp__travel-tools__create_trip_task",
        "mcp__travel-tools__list_trip_tasks",
        "mcp__travel-tools__complete_trip_task",
        "mcp__travel-tools__delete_trip_task",
    ]

    def __init__(self) -> None:
        self.options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            model="claude-opus-4-6",
            mcp_servers={"travel-tools": travel_tools_server},
            allowed_tools=self.ALLOWED_TOOLS,
            permission_mode="bypassPermissions",
            max_turns=50,
        )

    async def plan(self, prefs: TripPreferences) -> None:
        goal_prompt = _build_goal_prompt(prefs)

        date_label = (
            f"month of {prefs.travel_month}" if prefs.travel_month
            else f"{prefs.departure_date} → {prefs.return_date}"
        )
        print("\n" + "=" * 70)
        print("  AUTONOMOUS TRAVEL PLANNER")
        print(f"  {prefs.origin_city}  →  {prefs.destination}")
        print(f"  {date_label}  ·  {prefs.travelers} traveller(s)  ·  ${prefs.budget_usd:,} budget")
        print("=" * 70)
        print("\nAgent is working...\n")
        print("-" * 70)

        final_plan: str = ""

        async with ClaudeSDKClient(options=self.options) as client:
            await client.query(goal_prompt)

            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            print(block.text)
                elif isinstance(message, ResultMessage):
                    final_plan = message.result or ""

        if final_plan and final_plan.strip() not in ("", "Task complete."):
            print("\n" + "-" * 70)
            print(final_plan)

        print("\n" + "=" * 70)
        print("  Planning complete. Check trip_tasks.db for your task list.")
        print("=" * 70)


# ---------------------------------------------------------------------------
# Interactive entry point
# ---------------------------------------------------------------------------


def _prompt(label: str, optional: bool = False) -> str:
    suffix = " (optional, press Enter to skip)" if optional else ""
    return input(f"{label}{suffix}: ").strip()


def _parse_travel_month(raw: str) -> str:
    """Accept MM/YYYY or YYYY-MM and normalise to YYYY-MM."""
    raw = raw.strip()
    if not raw:
        return ""
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) == 2:
            mm, yyyy = parts
            return f"{yyyy.strip()}-{mm.strip().zfill(2)}"
    if "-" in raw and len(raw) == 7:
        return raw   # already YYYY-MM
    raise ValueError(f"Unrecognised month format: '{raw}'. Use MM/YYYY.")


async def main() -> None:
    print("\n" + "=" * 70)
    print("  TRAVEL PLANNER  —  answer a few questions to get started")
    print("=" * 70 + "\n")

    origin = _prompt("Origin city")
    destination = _prompt("Destination")

    travel_month = ""
    departure_date = ""
    return_date = ""

    raw_month = _prompt("Travel month MM/YYYY", optional=True)
    if raw_month:
        try:
            travel_month = _parse_travel_month(raw_month)
        except ValueError as e:
            print(f"  ⚠  {e} — falling back to next month.")
            today = date.today()
            # default to next month
            next_month = today.replace(day=1)
            if today.month == 12:
                next_month = next_month.replace(year=today.year + 1, month=1)
            else:
                next_month = next_month.replace(month=today.month + 1)
            travel_month = next_month.strftime("%Y-%m")
    else:
        # No month given — ask for exact dates
        departure_date = _prompt("Departure date YYYY-MM-DD")
        return_date = _prompt("Return date YYYY-MM-DD")

    prefs = TripPreferences(
        origin_city=origin,
        destination=destination,
        travel_month=travel_month,
        departure_date=departure_date,
        return_date=return_date,
        travelers=1,
        budget_usd=2000,
        interests=[
            "nature and mountains",
            "local culture",
            "off-the-beaten-path experiences",
        ],
        accommodation_pref="mid-range guesthouses or local stays",
        fitness_level="moderate",
    )

    agent = AutonomousTravelAgent()
    await agent.plan(prefs)


if __name__ == "__main__":
    anyio.run(main)
