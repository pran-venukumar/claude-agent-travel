"""
Autonomous Travel Planning Agent

The user provides a TripPreferences object with all their requirements.
The agent then works independently — calling tools, reasoning about results,
and building a complete trip plan — without asking any questions.

Usage:
    Edit the `my_trip` preferences at the bottom of this file, then run:
        python3 autonomous_agent.py

The agent will:
    1. Check the weather at the destination
    2. Search for flights from origin to destination
    3. Create a full planning task list in the SQLite database
    4. Deliver a complete, formatted travel plan

NOTE: Run from a regular terminal, not inside an active Claude Code session.
"""

import anyio
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from tools import travel_tools_server

# ---------------------------------------------------------------------------
# System prompt — defines the agent's role, workflow, and output contract
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a fully autonomous travel planning agent.

You receive a traveller's preferences once and produce a complete, actionable trip plan
entirely on your own. You do NOT ask clarifying questions under any circumstances.
If information is missing or ambiguous, make a reasonable assumption and state it briefly.

━━━ YOUR TOOLS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  get_weather        — live forecast for any destination
  search_flights     — find flight options between two cities
  create_trip_task   — add a concrete action item to the planning database
  list_trip_tasks    — review all logged tasks
  complete_trip_task — mark a task as done
  delete_trip_task   — remove a task

━━━ YOUR MANDATORY WORKFLOW ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Execute these steps in order every time — do not skip any:

  STEP 1 · WEATHER
    Call get_weather for the main destination city.
    If the trip involves multiple distinct regions, call it for each.

  STEP 2 · FLIGHTS
    Call search_flights with the exact origin, destination, dates, and
    passenger count from the preferences.

  STEP 3 · TASKS
    Call create_trip_task for every concrete action item the traveller must
    complete before or during the trip. Cover at minimum:
      - Book the specific flight identified in Step 2
      - Visa / entry requirements research and application
      - Travel insurance
      - Accommodation booking for each segment
      - Internal transport (airport transfers, intercity, car hire, etc.)
      - Any specific activities or tours that require advance booking
      - Packing reminders tailored to the destination and weather
      Tag every task with the destination field.

  STEP 4 · SYNTHESIZE
    Produce the final formatted travel plan (see output contract below).

━━━ OUTPUT CONTRACT ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Your final answer MUST include all of the following sections:

  1. TRIP SUMMARY         — one-paragraph overview of the trip
  2. RECOMMENDED FLIGHT   — the best option from your search with price and times
  3. WEATHER BRIEFING     — what to expect, how it affects the plan
  4. DAY-BY-DAY ITINERARY — a concrete schedule for every day of the trip
  5. ACCOMMODATION PLAN   — where to stay each segment and why
  6. BUDGET BREAKDOWN     — estimated costs (flights, accommodation, food, activities)
  7. TASK CHECKLIST       — list all tasks you created, grouped by category
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
    Provide as much detail as possible — the more context, the better the plan.
    """

    # Core logistics (required)
    origin_city: str
    destination: str
    departure_date: str          # YYYY-MM-DD
    return_date: str             # YYYY-MM-DD
    travelers: int = 1

    # Budget
    budget_usd: int = 2000       # total budget for the whole trip

    # Preferences
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
    """Format TripPreferences into a structured, unambiguous goal prompt."""
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

    trip_length = "?"
    try:
        from datetime import date
        d1 = date.fromisoformat(prefs.departure_date)
        d2 = date.fromisoformat(prefs.return_date)
        trip_length = f"{(d2 - d1).days} nights"
    except Exception:
        pass

    return f"""Plan this trip autonomously. Follow your mandatory workflow exactly.
Do NOT ask me any questions — work with what I've given you and state any assumptions clearly.

━━━ TRIP PREFERENCES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Origin:             {prefs.origin_city}
  Destination:        {prefs.destination}
  Departure:          {prefs.departure_date}
  Return:             {prefs.return_date}  ({trip_length})
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

Now execute your workflow: weather → flights → tasks → complete plan.
"""


# ---------------------------------------------------------------------------
# Autonomous agent
# ---------------------------------------------------------------------------


class AutonomousTravelAgent:
    """
    Single-shot autonomous travel planner.
    Call plan(preferences) and it runs to completion without any human interaction.
    """

    ALLOWED_TOOLS = [
        "mcp__travel-tools__get_weather",
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
            max_turns=50,   # generous ceiling for complex multi-tool plans
        )

    async def plan(self, prefs: TripPreferences) -> None:
        """Run the autonomous planning loop and stream output to stdout."""
        goal_prompt = _build_goal_prompt(prefs)

        print("=" * 70)
        print("  AUTONOMOUS TRAVEL PLANNER")
        print(f"  {prefs.origin_city}  →  {prefs.destination}")
        print(f"  {prefs.departure_date}  to  {prefs.return_date}"
              f"  ·  {prefs.travelers} traveller(s)  ·  ${prefs.budget_usd:,} budget")
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
                            # Stream the agent's live reasoning and narration
                            print(block.text)

                elif isinstance(message, ResultMessage):
                    final_plan = message.result or ""

        # The ResultMessage.result is the agent's final turn summary.
        # The full plan is already printed via AssistantMessage blocks above,
        # but if the result contains additional content, print it.
        if final_plan and final_plan.strip() not in ("", "Task complete."):
            print("\n" + "-" * 70)
            print(final_plan)

        print("\n" + "=" * 70)
        print("  Planning complete. Check trip_tasks.db for your task list.")
        print("=" * 70)


# ---------------------------------------------------------------------------
# Entry point — edit TripPreferences here to plan your trip
# ---------------------------------------------------------------------------


async def main() -> None:
    agent = AutonomousTravelAgent()

    my_trip = TripPreferences(
        origin_city="Bengaluru, India",
        destination="Kyrgyzstan",
        departure_date="2026-07-10",
        return_date="2026-07-17",
        travelers=1,
        budget_usd=1000,
        interests=[
            "remote nature, greenery, mountains",
            "nomadic culture and yurt stays",
            "landscape photography",
            "off-the-beaten-path experiences",
            "avoid crowded tourist sites",
        ],
        accommodation_pref="mix of guesthouses in Bishkek and yurt camps in the mountains",
        fitness_level="high",
        must_haves=[
            "overnight stay at Song-Kol lake",
            "a multi-day trek in the Tian Shan mountains",
            "trying traditional Kyrgyz food (kumiss, beshbarmak)",
        ],
        avoid=[
            "luxury hotels",
            "heavily commercialised tourist sites",
            "guided group tours with large crowds",
        ],
        extra_notes=(
            "I have a valid Indian passport. "
            "I prefer early morning starts cto catch good light for photography. "
        ),
    )

    await agent.plan(my_trip)


if __name__ == "__main__":
    anyio.run(main)
