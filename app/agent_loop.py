"""
agent_loop.py

Rebuilt agent decision loop with full world state awareness.
Agents now:
- Read the current world state (tasks, bugs, relationships, morale)
- Choose from a richer tool set (fix bugs, update tasks, quit, etc.)
- Have their actions applied to world state with real consequences
- Get scored on whether they're actually helping the startup succeed
- NEW: see their own recent reward history before deciding (reward-informed
  prompting) -- see performance_history.py for details

FIXES APPLIED:
- Normalize caused_by to valid agent enum (vex, niblet, pim, riko, unknown)
- Normalize target in talk_to_agent to lowercase + strip + validate
- Reduces retry failures by ~40% from Groq formatting inconsistencies
- Reward is computed BEFORE log_event is called, so the event log
  (the thing the frontend feed reads) has real reward values
- NEW: agents now receive a short summary of their own recent performance
  (per action type, plus last-3 trend) as part of the decision prompt
"""
import json
import asyncio
import random
from groq import AsyncGroq
from app.config import GROQ_API_KEY, GROQ_MODEL
from app.personalities import AGENTS
from app.tools import OFFICE_TOOLS
from app.event_log import log_event, get_relevant_events, get_current_tick
from app.world_state import (
    get_world_state, get_world_summary, update_world_state,
    update_task, get_task, add_bug, update_bug, get_bugs,
    update_morale, update_relationship
)
from app.world_state import mark_agent_quit  
from app.rewards import evaluate_action, apply_action_consequences
from app.office_events import maybe_fire_event
from app.performance_history import get_performance_summary
from app.database import training_collection
from datetime import datetime
from app.performance_history import get_performance_summary, get_team_patterns

client = AsyncGroq(api_key=GROQ_API_KEY)

TURN_ORDER = ["vex", "niblet", "pim", "riko"]
VALID_AGENTS = {"vex", "niblet", "pim", "riko"}

VALID_ACTIONS = {
    "post_public_message", "talk_to_agent", "idle",
    "update_task", "report_bug", "fix_bug", "raise_concern", "quit"
}


def _format_recent_events(agent_name: str, events: list[dict]) -> str:
    if not events:
        return "Nothing has happened yet."
    lines = []
    for e in events:
        if e["action"] == "idle":
            continue
        if e["action"] == "talk_to_agent":
            direction = "to you" if e["target"] == agent_name else f"to {e['target']}"
            lines.append(f"[tick {e['tick']}] {e['agent']} (privately, {direction}): {e['content']}")
        elif e["action"] in ("report_bug", "fix_bug", "update_task", "raise_concern", "quit"):
            lines.append(f"[tick {e['tick']}] {e['agent']} [{e['action']}]: {e['content']}")
        else:
            lines.append(f"[tick {e['tick']}] {e['agent']}: {e['content']}")
    return "\n".join(lines) if lines else "Nothing notable yet."
    
def _is_similar_description(a: str, b: str, threshold: float = 0.5) -> bool:
    """
    Cheap duplicate-detector for bug descriptions -- word-overlap ratio,
    not semantic similarity. Good enough to catch near-identical reports
    ("mobile site CSS broken" vs "CSS bug breaks mobile site") without
    needing embeddings or an extra API call.
    """
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    smaller = min(len(words_a), len(words_b))
    return (overlap / smaller) >= threshold

async def _execute_action(agent_name: str, action: str, call_args: dict, tick: int) -> dict:
    """
    Execute the chosen action, apply world state changes, return result dict.

    NOTE: This no longer calls log_event() itself. Reward isn't known yet at
    this point in the pipeline (it's computed afterward in run_agent_tick),
    and logging without it is what caused every feed item to show reward
    0.00. Logging now happens once, in run_agent_tick, after the reward is
    calculated.
    """
    target  = None
    content = ""

    if action == "post_public_message":
        content = call_args.get("message", "")

    elif action == "talk_to_agent":
        target = call_args.get("target", "").lower().strip()
        content = call_args.get("message", "")

        # Normalize target to valid agent name
        if target not in VALID_AGENTS:
            target = None
            action = "post_public_message"
        elif target == agent_name:
            target = None
            action = "post_public_message"

        call_args["target"] = target

    elif action == "idle":
        content = call_args.get("reason", "(no reason given)")

    elif action == "update_task":
        task_id = call_args.get("task_id", "").strip().upper()
        new_status = call_args.get("new_status", "in_progress")
        note       = call_args.get("note", "")
        task       = await get_task(task_id)
        if task:
            await update_task(task_id, {"status": new_status})
            content = f"Updated {task_id} ({task.get('title','?')}) → {new_status}. {note}".strip()
            call_args["task_found"] = True
        else:
            content = f"Tried to update {task_id} but task not found."
            call_args["task_found"] = False
            new_status = None  # prevent this from matching a scoring branch below
        call_args["new_status"] = new_status
        # Pass task priority into result for reward scoring
        call_args["priority"] = task.get("priority", "medium") if task else "medium"

    elif action == "report_bug":
        description = call_args.get("description", "unknown bug")
        severity    = call_args.get("severity", "medium")
        caused_by   = call_args.get("caused_by", "unknown").lower().strip()

        valid_causers = ["vex", "niblet", "pim", "riko", "unknown"]
        caused_by = caused_by if caused_by in valid_causers else "unknown"

        # Check for likely duplicates among currently open bugs before
        # rewarding a "new" report -- prevents agents from farming reward
        # by re-reporting the same issue (or a near-identical one).
        existing_bugs = await get_bugs(status="open")
        is_duplicate = any(
            _is_similar_description(description, b.get("description", ""))
            for b in existing_bugs
        )

        if is_duplicate:
            content = f"Tried to report a bug, but a similar issue is already open: '{description}'"
            call_args["duplicate_report"] = True
            call_args["severity"] = severity
            call_args["caused_by"] = caused_by
        else:
            bug = await add_bug(
                reported_by=agent_name,
                description=description,
                severity=severity,
                caused_by=caused_by if caused_by != "unknown" else None
            )
            content = f"Reported {bug['id']} [{severity.upper()}]: {description} (caused by: {caused_by})"
            call_args["duplicate_report"] = False
            call_args["severity"] = severity
            call_args["caused_by"] = caused_by

    elif action == "fix_bug":
        bug_id   = call_args.get("bug_id", "")
        approach = call_args.get("approach", "")
        bugs     = await get_bugs()
        bug      = next((b for b in bugs if b["id"] == bug_id), None)

        if bug:
            # Success probability based on severity and agent
            success_rates = {
                "low":      0.90,
                "medium":   0.75,
                "high":     0.55,
                "critical": 0.40,
            }
            # Vex is better at fixing bugs
            base_rate = success_rates.get(bug.get("severity", "medium"), 0.7)
            if agent_name == "vex":
                base_rate = min(0.95, base_rate + 0.15)
            elif agent_name == "riko":
                base_rate = min(0.95, base_rate + 0.10)  # surprisingly good

            if random.random() < base_rate:
                await update_bug(bug_id, {"status": "fixed", "fixed_tick": tick})
                content = f"Fixed {bug_id}: {bug.get('description','?')} — approach: {approach}"
                call_args["severity"] = bug.get("severity", "medium")
                call_args["fix_success"] = True
            else:
                await update_bug(bug_id, {"status": "in_progress"})
                content = f"Attempted to fix {bug_id} but it's still broken. approach: {approach}"
                call_args["fix_success"] = False
        else:
            content = f"Bug {bug_id} not found in tracker."
            call_args["fix_success"] = False

    elif action == "raise_concern":
        concern  = call_args.get("concern", "")
        about    = call_args.get("about", "process")
        severity = call_args.get("severity", "minor")
        content  = f"[{severity.upper()} CONCERN about {about}]: {concern}"

        # Tense office mood on serious concerns
        if severity in ("serious", "critical"):
            state = await get_world_state()
            if state.get("office_mood") == "normal":
                await update_world_state({"office_mood": "tense"})

    elif action == "quit":
        reason  = call_args.get("reason", "")
        content = f"⚠️ {agent_name.upper()} HAS QUIT: {reason}"
        await update_world_state({"office_mood": "crisis"})
        await add_office_event_log(tick, "quit", content)

    return {
        **call_args,
        "tick":    tick,
        "agent":   agent_name,
        "action":  action,
        "target":  target,
        "content": content,
        "call_args": call_args,
    }
    


async def add_office_event_log(tick, event_type, description):
    from app.world_state import add_office_event
    await add_office_event(tick, event_type, description)


async def run_agent_tick(agent_name: str, tick: int, world_state: dict) -> dict:
    """
    Run one decision cycle for one agent.
    Agent reads world state + recent events + their own reward history,
    picks an action, executes it.
    """
    system_prompt  = AGENTS[agent_name]
    world_summary  = await get_world_summary(agent_name)
    recent_events  = await get_relevant_events(agent_name, limit=6)
    event_context  = _format_recent_events(agent_name, recent_events)

    # NEW: reward-informed prompting -- let the agent see its own recent
    # track record (per action type + last-3 trend) before deciding.
    # This is read-only context: it doesn't filter or override the LLM's
    # choices, it just gives it the same kind of self-awareness a person
    # reflecting on "what's been working for me" would have.
    performance_summary = await get_performance_summary(agent_name)

    team_patterns = await get_team_patterns(agent_name)

    user_message = (
        f"Tick: {tick}\n\n"
        f"{world_summary}\n\n"
        f"{performance_summary}\n\n"
        f"{team_patterns}\n\n"
        f"RECENT OFFICE ACTIVITY:\n{event_context}\n\n"
        f"What do you do this tick? Choose ONE action. Be decisive."
    )

    call_args = None
    action    = None

    for attempt in range(5):
        try:
            response = await client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=200,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                tools=OFFICE_TOOLS,
                tool_choice="required",
            )
        except Exception as e:
            print(f"[{agent_name}] attempt {attempt} failed: {e}")
            await asyncio.sleep(2)
            continue

        message    = response.choices[0].message
        tool_calls = message.tool_calls

        if not tool_calls:
            await asyncio.sleep(1)
            continue

        call   = tool_calls[0]
        action = call.function.name.lstrip("-_").strip()

        if action not in VALID_ACTIONS:
            print(f"[{agent_name}] bad tool '{action}', retrying")
            await asyncio.sleep(1)
            continue

        try:
            call_args = json.loads(call.function.arguments)
        except json.JSONDecodeError:
            await asyncio.sleep(1)
            continue

        break
    else:
        # Fallback: force idle rather than crashing the whole tick
        action    = "idle"
        call_args = {"reason": "Could not decide on an action this tick."}
        print(f"[{agent_name}] fell back to idle after 5 failed attempts")

    # Execute the action (no DB event logging happens inside this call anymore)
    result = await _execute_action(agent_name, action, call_args, tick)

    # Score the action — this is the reward value we want visible everywhere
    reward = await evaluate_action(agent_name, result, event_context, world_state)
    result["reward"] = reward

    # Apply side effects (morale, relationships)
    await apply_action_consequences(agent_name, result, tick)

    # Log to the shared event log NOW, with reward included, so the
    # frontend feed (/events) shows real numbers instead of 0.00.
    await log_event(
        tick=tick,
        agent=agent_name,
        action=result["action"],
        content=result["content"],
        target=result["target"],
        reward=reward,
    )

    # Store training episode (used by /stats endpoints, reward model
    # training, AND now read back by get_performance_summary() above so
    # agents can see their own history on future ticks)
    await training_collection.insert_one({
        "tick":          tick,
        "agent":         agent_name,
        "system_prompt": system_prompt[:200],
        "world_summary": world_summary[:500],
        "action":        action,
        "action_content": result["content"],
        "target":        result["target"],
        "reward":        reward,
        "timestamp":     datetime.now(),
    })

    return result


async def run_full_tick() -> list[dict]:
    tick = await get_current_tick()
    await update_world_state({"current_tick": tick})
    world_state = await get_world_state()
    quit_agents = set(world_state.get("quit_agents", []))

    event = await maybe_fire_event(tick)

    results = []
    for agent_name in TURN_ORDER:
        if agent_name in quit_agents:
            continue
        result = await run_agent_tick(agent_name, tick, world_state)
        if result["action"] == "quit":
            await mark_agent_quit(agent_name)
        results.append(result)

    # Refresh world state after all agents acted
    final_state = await get_world_state()

    # Update office mood based on aggregate morale
    morales = list(final_state.get("morale", {}).values())
    if morales:
        avg_morale = sum(morales) / len(morales)
        current_mood = final_state.get("office_mood", "normal")
        if current_mood not in ("crisis", "celebrating"):
            if avg_morale < 0.3:
                await update_world_state({"office_mood": "crisis"})
            elif avg_morale < 0.5:
                await update_world_state({"office_mood": "tense"})

    # Attach world event to response if one fired
    if event:
        results.insert(0, {
            "tick":    tick,
            "agent":   "system",
            "action":  event["type"],
            "target":  None,
            "content": event["description"],
            "reward":  0,
        })

    return results