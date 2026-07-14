"""
world_state.py

The shared world state for the office simulation.
This is the single source of truth for everything happening in the startup:
- Current sprint (name, deadline, status)
- Tasks (assigned, blocked, done)
- Bugs (found, fixed, in prod)
- Relationships between agents (-1.0 to 1.0)
- Morale per agent (0.0 to 1.0)
- Pending office events (crises, surprises, deadlines)

Everything reads from and writes to MongoDB.
The world state changes every tick based on agent actions and random events.
"""

from datetime import datetime
from app.database import db

world_collection     = db["world_state"]
tasks_collection     = db["tasks"]
bugs_collection      = db["bugs"]
events_collection_ws = db["office_events"]

AGENTS = ["vex", "niblet", "pim", "riko"]

# ── Default world state ───────────────────────────────────────
DEFAULT_STATE = {
    "sprint": {
        "name": "Sprint 1 — MVP Launch",
        "deadline_tick": 15,
        "status": "active",   # active | shipped | failed
        "started_tick": 1,
    },
    "sprint_number": 1,
    "quit_agents": [],
    "relationships": {
        "vex-niblet": 0.5,
        "vex-pim":    0.6,
        "vex-riko":   0.2,    # Vex already distrusts Riko
        "niblet-pim": 0.7,
        "niblet-riko": 0.5,
        "pim-riko":   0.4,
    },
    "morale": {
        "vex":    0.65,
        "niblet": 0.85,
        "pim":    0.50,
        "riko":   0.75,
    },
    "office_mood": "normal",   # normal | tense | crisis | celebrating
    "current_tick": 0,
    "updated_at": None,
}

DEFAULT_TASKS = [
    { "id": "T001", "title": "Build auth system",         "assigned_to": "vex",    "status": "in_progress", "priority": "high",   "blocker": None },
    { "id": "T002", "title": "Set up CI/CD pipeline",     "assigned_to": "vex",    "status": "todo",        "priority": "medium", "blocker": None },
    { "id": "T003", "title": "Write sprint report",       "assigned_to": "pim",    "status": "todo",        "priority": "low",    "blocker": None },
    { "id": "T004", "title": "Design onboarding flow",    "assigned_to": "niblet", "status": "in_progress", "priority": "high",   "blocker": None },
    { "id": "T005", "title": "Fix payment integration",   "assigned_to": "riko",   "status": "in_progress", "priority": "high",   "blocker": None },
    { "id": "T006", "title": "Deploy to staging",         "assigned_to": "pim",    "status": "todo",        "priority": "high",   "blocker": "T001" },
    { "id": "T007", "title": "Write API docs",            "assigned_to": "riko",   "status": "todo",        "priority": "low",    "blocker": None },
    { "id": "T008", "title": "Performance testing",       "assigned_to": "vex",    "status": "todo",        "priority": "medium", "blocker": "T001" },
]


# ── Read / Write ──────────────────────────────────────────────
async def get_world_state() -> dict:
    """Get current world state, initializing if it doesn't exist."""
    state = await world_collection.find_one({"_id": "main"})
    if not state:
        await init_world_state()
        state = await world_collection.find_one({"_id": "main"})
    return state


async def update_world_state(updates: dict):
    """Partial update world state fields."""
    updates["updated_at"] = datetime.now()
    await world_collection.update_one(
        {"_id": "main"},
        {"$set": updates},
        upsert=True
    )


async def init_world_state():
    """Initialize fresh world state and tasks."""
    state = DEFAULT_STATE.copy()
    state["_id"] = "main"
    state["updated_at"] = datetime.now()
    await world_collection.replace_one({"_id": "main"}, state, upsert=True)

    # Clear and re-seed tasks
    await tasks_collection.delete_many({})
    await tasks_collection.insert_many([t.copy() for t in DEFAULT_TASKS])

    # Clear bugs
    await bugs_collection.delete_many({})


# ── Task helpers ──────────────────────────────────────────────
async def get_tasks(assigned_to: str = None, status: str = None) -> list:
    query = {}
    if assigned_to: query["assigned_to"] = assigned_to
    if status:      query["status"] = status
    return await tasks_collection.find(query).to_list(None)


async def update_task(task_id: str, updates: dict):
    await tasks_collection.update_one({"id": task_id}, {"$set": updates})


async def get_task(task_id: str) -> dict:
    return await tasks_collection.find_one({"id": task_id})


# ── Bug helpers ───────────────────────────────────────────────
async def add_bug(reported_by: str, description: str, severity: str = "medium", caused_by: str = None) -> dict:
    bug_count = await bugs_collection.count_documents({})
    bug = {
        "id": f"BUG{bug_count+1:03d}",
        "description": description,
        "severity": severity,        # low | medium | high | critical
        "reported_by": reported_by,
        "caused_by": caused_by,      # agent who introduced it (if known)
        "assigned_to": None,
        "status": "open",            # open | in_progress | fixed | in_prod
        "found_in_prod": False,
        "created_tick": None,
        "fixed_tick": None,
    }
    await bugs_collection.insert_one(bug)
    return bug


async def get_bugs(status: str = None) -> list:
    query = {}
    if status: query["status"] = status
    return await bugs_collection.find(query).to_list(None)


async def update_bug(bug_id: str, updates: dict):
    await bugs_collection.update_one({"id": bug_id}, {"$set": updates})


# ── Relationship helpers ──────────────────────────────────────
def _rel_key(a: str, b: str) -> str:
    """Canonical key: alphabetical order."""
    return f"{min(a,b)}-{max(a,b)}"


async def get_relationship(a: str, b: str) -> float:
    state = await get_world_state()
    key = _rel_key(a, b)
    return state.get("relationships", {}).get(key, 0.5)


async def update_relationship(a: str, b: str, delta: float):
    """Nudge relationship score, clamped to [-1, 1]."""
    state = await get_world_state()
    key = _rel_key(a, b)
    current = state.get("relationships", {}).get(key, 0.5)
    new_val = max(-1.0, min(1.0, current + delta))
    await update_world_state({f"relationships.{key}": new_val})


# ── Morale helpers ────────────────────────────────────────────
async def update_morale(agent: str, delta: float):
    """Nudge morale, clamped to [0, 1]."""
    state = await get_world_state()
    current = state.get("morale", {}).get(agent, 0.5)
    new_val = max(0.0, min(1.0, current + delta))
    await update_world_state({f"morale.{agent}": new_val})


async def get_morale(agent: str) -> float:
    state = await get_world_state()
    return state.get("morale", {}).get(agent, 0.5)


# ── Office event helpers ──────────────────────────────────────
async def add_office_event(tick: int, event_type: str, description: str, affects: list = None):
    """Log a world event (crisis, deadline, surprise)."""
    await events_collection_ws.insert_one({
        "tick": tick,
        "type": event_type,
        "description": description,
        "affects": affects or AGENTS,
        "created_at": datetime.now(),
    })


async def get_recent_office_events(limit: int = 5) -> list:
    return await events_collection_ws.find().sort("tick", -1).limit(limit).to_list(None)


# ── World summary for agent prompts ──────────────────────────
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


async def get_world_summary(agent_name: str) -> str:
    """
    Generate a compact world state summary tailored for a specific agent.
    This gets injected into every agent's prompt so they know what's happening.

    FIX: previously this always showed open_bugs[:4] with no sorting, so
    whichever bugs happened to come first stayed pinned in every agent's
    view forever (this is why the conversation kept circling BUG001 -- it
    never aged out, and nothing told agents that older/lower-priority bugs
    were stale or that other bugs existed beyond the visible slice).

    Now: bugs are sorted by severity (critical/high first) then by id
    (oldest first within a severity), and if there are more open bugs than
    fit in the summary, a line is added telling the agent how many more
    exist -- so the world doesn't silently look smaller than it is.
    """
    state = await get_world_state()
    tasks = await get_tasks()
    bugs  = await get_bugs()
    events = await get_recent_office_events(limit=5)

    sprint = state.get("sprint", {})
    morale = state.get("morale", {})
    rels   = state.get("relationships", {})

    # Sprint status
    deadline = sprint.get("deadline_tick", "?")
    current_tick = state.get("current_tick", 0)
    ticks_left = deadline - current_tick if isinstance(deadline, int) else "?"
    sprint_line = f"SPRINT: '{sprint.get('name')}' — {sprint.get('status').upper()} — {ticks_left} ticks until deadline"

    # My tasks
    my_tasks = [t for t in tasks if t["assigned_to"] == agent_name]
    my_task_lines = []
    for t in my_tasks:
        blocker_note = f" [BLOCKED by {t['blocker']}]" if t.get("blocker") else ""
        my_task_lines.append(f"  [{t['status'].upper()}] {t['id']}: {t['title']}{blocker_note} ({t['priority']} priority)")

    # Open bugs — sorted by severity then recency, so the same stale bug
    # doesn't permanently crowd out everything else.
    open_bugs = [b for b in bugs if b["status"] in ("open", "in_progress")]
    open_bugs.sort(key=lambda b: (_SEVERITY_RANK.get(b.get("severity", "medium"), 2), b.get("id", "")))

    VISIBLE_BUG_LIMIT = 4
    visible_bugs = open_bugs[:VISIBLE_BUG_LIMIT]
    hidden_bug_count = max(0, len(open_bugs) - VISIBLE_BUG_LIMIT)

    bug_lines = []
    for b in visible_bugs:
        prod_flag = " ⚠️ IN PROD" if b.get("found_in_prod") else ""
        bug_lines.append(f"  [{b['severity'].upper()}] {b['id']}: {b['description']}{prod_flag}")
    if hidden_bug_count > 0:
        bug_lines.append(f"  (+{hidden_bug_count} more open bug{'s' if hidden_bug_count != 1 else ''}, lower priority, not shown)")

    # My relationships
    my_rels = {}
    for key, val in rels.items():
        parts = key.split("-")
        if agent_name in parts:
            other = parts[1] if parts[0] == agent_name else parts[0]
            my_rels[other] = val

    rel_lines = []
    for other, score in my_rels.items():
        if score >= 0.7:   mood = "trust"
        elif score >= 0.4: mood = "neutral"
        elif score >= 0.1: mood = "tension"
        else:              mood = "hostile"
        rel_lines.append(f"  {other}: {mood} ({score:.1f})")

    # My morale
    my_morale = morale.get(agent_name, 0.5)
    if my_morale >= 0.8:   morale_str = "high"
    elif my_morale >= 0.5: morale_str = "okay"
    elif my_morale >= 0.3: morale_str = "low"
    else:                  morale_str = "critical — you're close to quitting"

    # Recent office events
    event_lines = []
    for e in events:
        event_lines.append(f"  [tick {e['tick']}] {e['type'].upper()}: {e['description']}")

    # Office mood
    mood = state.get("office_mood", "normal")

    task_section   = my_task_lines if my_task_lines else ["  (none assigned)"]
    bug_section    = bug_lines if bug_lines else ["  (none — clean codebase)"]
    event_section  = event_lines if event_lines else ["  (nothing notable)"]

    lines = [
        "=== WORLD STATE ===",
        sprint_line,
        f"OFFICE MOOD: {mood.upper()}",
        "",
        "YOUR TASKS:",
    ] + task_section + [
        "",
        "OPEN BUGS:",
    ] + bug_section + [
        "",
        "YOUR RELATIONSHIPS:",
    ] + rel_lines + [
        "",
        f"YOUR MORALE: {morale_str} ({my_morale:.1f})",
        "",
        "RECENT EVENTS:",
    ] + event_section + [
        "==================",
    ]
    return "\n".join(lines)


# ── Quit tracking ─────────────────────────────────────────────
async def mark_agent_quit(agent: str):
    """Permanently remove an agent from active turn rotation."""
    state = await get_world_state()
    quit_agents = state.get("quit_agents", [])
    if agent not in quit_agents:
        quit_agents.append(agent)
    await update_world_state({"quit_agents": quit_agents})
