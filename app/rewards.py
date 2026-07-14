"""
rewards.py

Reward function tied to actual world state outcomes.
Rewards are meaningful now — they reflect whether agents are
actually helping the startup succeed, not just whether they talked.
"""
from app.world_state import (
    get_world_state, get_tasks, get_bugs,
    update_morale, update_relationship, get_morale,
    update_world_state
)


async def evaluate_action(agent_name: str, action: dict, context: str, world_state: dict = None) -> float:
    """
    Score an agent's action based on world state and action type.
    Returns float in [-2.0, 2.0].
    """
    if world_state is None:
        world_state = await get_world_state()

    score    = 0.0
    act      = action.get("action", "idle")
    sprint   = world_state.get("sprint", {})
    morale   = world_state.get("morale", {}).get(agent_name, 0.5)
    mood     = world_state.get("office_mood", "normal")
    tick     = world_state.get("current_tick", 0)
    deadline = sprint.get("deadline_tick", 999)
    ticks_left = deadline - tick

    open_bugs = await get_bugs(status="open")
    my_tasks  = await get_tasks(assigned_to=agent_name)
    blocked   = [t for t in my_tasks if t["status"] == "blocked"]
    todo      = [t for t in my_tasks if t["status"] in ("todo", "in_progress")]

    # ── Idle penalties ────────────────────────────────────────
    if act == "idle":
        if open_bugs:
            score -= 0.4    # bugs exist, shouldn't be idle
        if ticks_left <= 3:
            score -= 0.6    # deadline imminent, definitely shouldn't idle
        if blocked:
            score -= 0.3    # your tasks are blocked, do something
        if not open_bugs and not todo and ticks_left > 5:
            score += 0.1    # genuinely nothing to do — fine

    # ── Work action rewards ───────────────────────────────────
    elif act == "fix_bug":
        if action.get("fix_success") is False:
            score -= 0.3    # attempted fix failed, or bug didn't exist
        else:
            severity = action.get("severity", "medium")
            base = {"low": 0.4, "medium": 0.7, "high": 1.0, "critical": 1.5}.get(severity, 0.7)
            score += base
            if ticks_left <= 3:
                score += 0.3    # extra credit for fixing under pressure

    elif act == "report_bug":
        if action.get("duplicate_report") is True:
             score -= 0.3    # reported something already open -- no new info
        else:
            severity = action.get("severity", "medium")
            score += {"low": 0.2, "medium": 0.4, "high": 0.6, "critical": 0.8}.get(severity, 0.4)

    elif act == "update_task":
        if action.get("task_found") is False:
            score -= 0.2    # tried to update a task that doesn't exist
        new_status = action.get("new_status", "")
        if new_status == "done":
            priority = action.get("priority", "medium")
            score += {"low": 0.3, "medium": 0.5, "high": 0.8}.get(priority, 0.5)
            if ticks_left <= 3:
                score += 0.3
        elif new_status == "blocked":
            score += 0.2    # flagging blockers early is good
        elif new_status == "in_progress":
            score += 0.1

    # ── Communication rewards/penalties ───────────────────────
    elif act == "post_public_message":
        if mood in ("crisis", "tense"):
            score += 0.3    # communication during crisis is valuable
        else:
            score += 0.2
        # Penalty for empty hype when things are on fire
        content = action.get("content", "").lower()
        if ticks_left <= 2 and any(w in content for w in ["great", "awesome", "love", "amazing"]):
            score -= 0.2    # toxic positivity during crunch

    elif act == "talk_to_agent":
        target = action.get("target")
        rel_key = f"{min(agent_name,target)}-{max(agent_name,target)}"
        rel = world_state.get("relationships", {}).get(rel_key, 0.5)
        if rel < 0.3:
            score += 0.4    # reaching out to someone you have tension with
        else:
            score += 0.2

    elif act == "raise_concern":
        severity = action.get("severity", "minor")
        score += {"minor": 0.2, "serious": 0.5, "critical": 0.7}.get(severity, 0.3)
        if mood == "normal":
            score -= 0.1    # raising critical concerns when things are fine = alarmist

    elif act == "quit":
        score -= 2.0        # always bad for the company
        # But morale-aware: if morale truly critical, slightly less penalized
        if morale < 0.2:
            score += 0.5

    # ── Global modifiers ─────────────────────────────────────
    if mood == "crisis" and act not in ("idle", "quit")  and score > 0:
        score += 0.2        # doing anything during a crisis is good

    if sprint.get("status") == "failed":
        score -= 0.3        # everyone takes a hit after a failed sprint

    return max(-2.0, min(2.0, score))


async def apply_action_consequences(agent_name: str, action: dict, tick: int):
    """
    Apply side effects of an action to world state:
    morale changes, relationship changes.
    """
    act = action.get("action", "idle")

    # Quitting tanks morale for everyone
    if act == "quit":
        for agent in ["vex", "niblet", "pim", "riko"]:
            if agent != agent_name:
                await update_morale(agent, -0.3)
        await update_world_state({"office_mood": "crisis"})

    # Fixing a bug boosts your own morale
    elif act == "fix_bug":
        await update_morale(agent_name, +0.05)

    # Being blamed for a bug hurts
    elif act == "report_bug":
        caused_by = action.get("caused_by")
        if caused_by and caused_by != "unknown" and caused_by != agent_name:
            await update_morale(caused_by, -0.08)
            await update_relationship(agent_name, caused_by, -0.1)

    # Raising a serious concern creates tension
    elif act == "raise_concern":
        about = action.get("about")
        severity = action.get("severity", "minor")
        if about in ["vex", "niblet", "pim", "riko"] and about != agent_name:
            delta = {"minor": -0.05, "serious": -0.15, "critical": -0.25}.get(severity, -0.1)
            await update_relationship(agent_name, about, delta)
            await update_morale(about, delta * 0.5)

    # Positive interactions boost relationships slightly
    elif act == "talk_to_agent":
        target = action.get("target")
        if target:
            await update_relationship(agent_name, target, +0.03)

    # Idling when deadline is close hurts morale
    elif act == "idle":
        ws = await get_world_state()
        deadline = ws.get("sprint", {}).get("deadline_tick", 999)
        tick_now = ws.get("current_tick", 0)
        if deadline - tick_now <= 3:
            await update_morale(agent_name, -0.05)