# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Agent

```bash
python3 travel_agent.py
```

**Important:** The Claude Agent SDK spawns a Claude Code subprocess internally. Running `travel_agent.py` inside an active Claude Code session will fail with a nested-session error. Always run it from a regular terminal outside Claude Code.

## Architecture

This is a multi-step project building a travel planning agent using the **Claude Agent SDK** (`claude-agent-sdk`). Each step adds capability:

- **Step 1** — Stateless query agent using `query()` (no memory, no tools)
- **Step 2 (current)** — Session memory via `resume=session_id`
- **Step 3 (planned)** — Tools (weather, flight search, etc.)

### How memory works

`TravelAgent` wraps the SDK's `query()` function. On the first turn, `resume=None` starts a fresh session; the session ID is captured from the `SystemMessage(subtype="init")` response. On subsequent turns, `resume=session_id` resumes the same session, giving the agent full prior context.

### Key dependencies

- `claude-agent-sdk` — Agent SDK (wraps Claude Code CLI)
- `anyio` — async runtime (`anyio.run(main)` entry point)

Install: `pip3 install claude-agent-sdk anyio`
