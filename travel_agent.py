"""
Travel Agent - Step 3: Tools enabled (weather + task management)

New capabilities:
  - get_weather         — live forecast from yr.no for any destination
  - create_trip_task    — add a planning task to a local SQLite database
  - list_trip_tasks     — list tasks, optionally filtered by destination
  - complete_trip_task  — mark a task done
  - delete_trip_task    — remove a task permanently

Tools are served via an in-process SDK MCP server (ClaudeSDKClient required).
"""

import anyio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock

from tools import travel_tools_server

SYSTEM_PROMPT = """You are an expert travel consultant with deep knowledge of destinations worldwide.
You help travelers plan memorable trips by providing:
- Destination overviews and highlights
- Best times to visit
- Must-see attractions and hidden gems
- Practical travel tips (transport, accommodation, local customs)
- Sample itineraries

You have access to real tools:
  • get_weather          — check live weather for any destination before recommending it
  • search_flights       — search for flights between two cities (accepts city names or IATA codes)
  • create_trip_task     — add tasks to the traveler's planning list
  • list_trip_tasks      — review what the traveler still needs to do
  • complete_trip_task   — mark tasks done when the traveler confirms
  • delete_trip_task     — remove tasks the traveler no longer needs

Use get_weather proactively when discussing destinations or timing.
Use search_flights whenever the traveler asks about flights or transport options.
Use the task tools to help the traveler build and track a concrete action plan.
Be specific, enthusiastic, and helpful. Tailor advice to the traveler's interests.
Remember details the traveler shares (budget, interests, travel dates) and refer back to them."""


class TravelAgent:
    """Travel agent backed by a persistent ClaudeSDKClient session with custom tools."""

    # MCP tool names follow the pattern mcp__<server-name>__<tool-name>
    ALLOWED_TOOLS = [
        "mcp__travel-tools__get_weather",
        "mcp__travel-tools__search_flights",
        "mcp__travel-tools__create_trip_task",
        "mcp__travel-tools__list_trip_tasks",
        "mcp__travel-tools__complete_trip_task",
        "mcp__travel-tools__delete_trip_task",
    ]

    def __init__(self):
        self.options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            model="claude-opus-4-6",
            mcp_servers={"travel-tools": travel_tools_server},
            allowed_tools=self.ALLOWED_TOOLS,
            permission_mode="bypassPermissions",
        )

    async def run_conversation(self, turns: list[str]) -> None:
        """Open one client session and send all turns through it."""
        async with ClaudeSDKClient(options=self.options) as client:
            for i, question in enumerate(turns, 1):
                print(f"\n{'='*60}")
                print(f"Turn {i}:")
                print(f"Q: {question}")
                print(f"{'-'*60}")

                await client.query(question)

                async for message in client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                print(f"A: {block.text}")
                    elif isinstance(message, ResultMessage):
                        # ResultMessage signals end of turn; result is the final text
                        if not any(
                            isinstance(block, TextBlock)
                            for block in (message.content if hasattr(message, "content") else [])
                        ):
                            print(f"A: {message.result}")

        print(f"\n{'='*60}")


async def main():
    agent = TravelAgent()

    # A multi-turn conversation that exercises all three tool groups.
    conversation = [
        # Turn 1: Set the scene and check weather
        "I'm planning a 5-day trip to Kyrgyzstan in July with a medium budget. "
        "I love hiking, nomadic culture, and off-the-beaten-path experiences. "
        "Can you check the current weather in Bishkek and suggest which regions to focus on?",

        # Turn 2: Flight search
        "I'll be flying from Bengaluru. Can you search for flights from Bengaluru to Bishkek "
        "departing 2025-07-10 and returning 2025-07-17, 1 adult?",

        # Turn 3: Create planning tasks
        "Great. Based on the flight options and the trip we've discussed, please create "
        "a few planning tasks for me — booking the flight, researching yurt camps, "
        "packing for trekking, and checking visa requirements. Tag them all 'Kyrgyzstan'.",

        # Turn 4: Review and close out
        "Show me the task list, mark the visa task as done, "
        "then give me a final 5-day itinerary.",
    ]

    await agent.run_conversation(conversation)


if __name__ == "__main__":
    anyio.run(main)
