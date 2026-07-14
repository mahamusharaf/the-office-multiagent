"""
The shared event log: an objective record of everything that's happened in
the office. Every agent action gets written here. Agents read recent entries
from here as context before deciding their next action.

Schema per document:
{
    "tick": int,             # which tick this happened on
    "agent": str,             # who did it, e.g. "vex"
    "action": str,            # tool name used: "post_public_message", "talk_to_agent", or "idle"
    "content": str,           # the message (or idle reason)
    "target": str | None,     # for talk_to_agent: who it was addressed to. None for public posts/idle.
    "reward": float,          # NEW: the reward this action scored, so the
                               #      frontend feed can show real numbers
                               #      instead of always 0.00
    "timestamp": datetime,    # wall-clock time, useful for debugging/display
}
"""
from datetime import datetime, timezone
from app.database import events_collection


async def log_event(
    tick: int,
    agent: str,
    action: str,
    content: str,
    target: str | None = None,
    reward: float = 0.0,
) -> None:
    """Write one agent action to the shared event log."""
    await events_collection.insert_one({
        "tick": tick,
        "agent": agent,
        "action": action,
        "content": content,
        "target": target,
        "reward": reward,
        "timestamp": datetime.now(timezone.utc),
    })


async def get_recent_events(limit: int = 10) -> list[dict]:
    """
    Pull the most recent N events overall, oldest-first. Used for things like
    the /events debug endpoint where you want the full picture regardless of
    who was involved.
    """
    cursor = events_collection.find().sort("tick", -1).limit(limit)
    recent = await cursor.to_list(length=limit)
    recent.reverse()
    return recent


async def get_relevant_events(agent_name: str, limit: int = 10) -> list[dict]:
    """
    Pull the most recent N events that are actually relevant to this agent,
    oldest-first. "Relevant" means:
      - any public post or idle (everyone in the office can see/hear those)
      - any talk_to_agent where this agent is either the sender or the target

    This is what fixes the Week 1 placeholder: with 4 agents, "just grab the
    last N events overall" would mean a private message to Pim might never
    reach Pim's context if 3 other things happened first. Filtering by
    relevance keeps each agent's view of the world correct without needing
    a vector DB -- a simple Mongo $or query is enough at this scale.
    """
    query = {
        "$or": [
            {"action": {"$in": ["post_public_message", "idle"]}},
            {"action": "talk_to_agent", "agent": agent_name},
            {"action": "talk_to_agent", "target": agent_name},
        ]
    }
    cursor = events_collection.find(query).sort("tick", -1).limit(limit)
    recent = await cursor.to_list(length=limit)
    recent.reverse()
    return recent


async def get_current_tick() -> int:
    """The next tick number = however many ticks have already happened."""
    # Distinct tick values already logged, so multiple agents acting on the
    # same tick number don't inflate this count.
    ticks = await events_collection.distinct("tick")
    return (max(ticks) + 1) if ticks else 0