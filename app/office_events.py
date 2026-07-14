"""
office_events.py

Random event generator — fires crises, surprises, and deadlines
into the world state to keep things dramatic and unpredictable.

Events fire based on tick number and probability.
Some are scheduled (deadline approaching), some are random (prod is down).

FIXES APPLIED:
- Sprint ending (shipped or failed) now actually calls start_new_sprint(),
  instead of leaving the world frozen at "shipped"/"failed" forever with
  the same stale tasks and bugs. This was the main reason agents kept
  circling the same handful of facts after a sprint ended -- nothing ever
  refreshed the goalposts.
- Added a "conflict_resolved" random event: previously relationships could
  only decay (raise_concern, blame from report_bug) with no mechanical way
  to repair, so any agent pair that drifted hostile (e.g. vex-riko starts
  at 0.2) had nothing to talk about except that tension, forever. This
  event gives a real, randomly-firing path back toward trust.
"""
import random
from app.world_state import (
    update_world_state, add_office_event, add_bug,
    update_morale, update_relationship, get_world_state,
    AGENTS,
)


# Event definitions: (probability_per_tick, generator_fn)
# Each generator returns (event_type, description, world_state_changes)

async def maybe_fire_event(tick: int) -> dict | None:
    """
    Check if a random event fires this tick.
    Returns event dict if one fired, None otherwise.
    """
    state = await get_world_state()
    sprint = state.get("sprint", {})
    deadline = sprint.get("deadline_tick", 999)
    ticks_left = deadline - tick
    mood = state.get("office_mood", "normal")

    # Don't fire events if already in crisis
    if mood == "crisis" and random.random() > 0.3:
        return None

    # ── Scheduled events ──────────────────────────────────────

    # Deadline warning at 5 ticks out
    if ticks_left == 5 and sprint.get("status") == "active":
        await update_world_state({"office_mood": "tense"})
        await add_office_event(tick, "deadline_warning",
            "5 ticks until sprint deadline. Pressure is mounting.")
        return {
            "type": "deadline_warning",
            "description": "⏰ 5 ticks until sprint deadline. The pressure is real.",
            "mood_change": "tense"
        }

    # Final deadline push at 2 ticks out
    if ticks_left == 2 and sprint.get("status") == "active":
        await update_world_state({"office_mood": "crisis"})
        await add_office_event(tick, "final_push",
            "2 ticks until deadline. All hands on deck.")
        return {
            "type": "final_push",
            "description": "🚨 2 ticks until deadline. This is the final push.",
            "mood_change": "crisis"
        }

    # Sprint deadline tick — ship or fail, THEN start the next sprint so
    # the world doesn't freeze in a "shipped"/"failed" state forever.
    if ticks_left <= 0 and sprint.get("status") == "active":
        from app.world_state import get_tasks
        tasks = await get_tasks()
        high_priority = [t for t in tasks if t["priority"] == "high"]
        done = [t for t in high_priority if t["status"] == "done"]
        success = len(done) >= len(high_priority) * 0.6 if high_priority else True

        sprint_number = state.get("sprint_number", 1)

        if success:
            await update_world_state({
                "sprint.status": "shipped",
                "office_mood": "celebrating"
            })
            for agent in AGENTS:
                await update_morale(agent, +0.2)
            await add_office_event(tick, "sprint_shipped",
                f"Sprint shipped! {len(done)}/{len(high_priority)} high-priority tasks done.")
            event = {
                "type": "sprint_shipped",
                "description": f"🎉 Sprint shipped! {len(done)}/{len(high_priority)} high-priority tasks completed.",
                "mood_change": "celebrating"
            }
        else:
            await update_world_state({
                "sprint.status": "failed",
                "office_mood": "crisis"
            })
            for agent in AGENTS:
                await update_morale(agent, -0.25)
            await add_office_event(tick, "sprint_failed",
                f"Sprint failed. Only {len(done)}/{len(high_priority)} high-priority tasks done.")
            event = {
                "type": "sprint_failed",
                "description": f"💥 Sprint FAILED. Only {len(done)}/{len(high_priority)} high-priority tasks shipped.",
                "mood_change": "crisis"
            }

        # Kick off the next sprint immediately so there's fresh material
        # for agents to react to instead of stale, resolved tasks/bugs.
        await start_new_sprint(tick, sprint_number + 1)

        return event

    # ── Random events (probability based) ─────────────────────
    roll = random.random()

    # Prod is down (8% chance per tick after tick 5)
    if tick > 5 and roll < 0.08:
        bug = await add_bug(
            reported_by="system",
            description="Production is down — users getting 500 errors on login",
            severity="critical",
            caused_by=random.choice(["riko", "unknown"])
        )
        await update_world_state({"office_mood": "crisis"})
        for agent in AGENTS:
            await update_morale(agent, -0.15)
        await add_office_event(tick, "prod_down",
            f"PROD IS DOWN. {bug['id']}: {bug['description']}")
        return {
            "type": "prod_down",
            "description": f"🔥 PROD IS DOWN. {bug['id']} logged: {bug['description']}",
            "mood_change": "crisis"
        }

    # Investor demo announced (5% chance, only once)
    elif roll < 0.13 and tick > 3:
        demo_tick = tick + 4
        await update_world_state({"office_mood": "tense", "investor_demo_tick": demo_tick})
        await add_office_event(tick, "investor_demo",
            f"Investor demo scheduled for tick {demo_tick}. Everything needs to work.")
        return {
            "type": "investor_demo",
            "description": f"💼 Investor demo in 4 ticks. Everything needs to be perfect.",
            "mood_change": "tense"
        }

    # Riko accidentally breaks something (12% chance)
    elif roll < 0.25 and tick > 2:
        bug = await add_bug(
            reported_by="system",
            description=random.choice([
                "Payment flow broken after recent commit",
                "API returning 404 on /users endpoint",
                "Auth tokens not invalidating on logout",
                "Database connection pool exhausted",
                "CSS completely broken on mobile",
            ]),
            severity=random.choice(["medium", "high"]),
            caused_by="riko"
        )
        await update_morale("riko", -0.05)
        await add_office_event(tick, "riko_broke_something",
            f"New bug introduced: {bug['id']} — {bug['description']}")
        return {
            "type": "bug_introduced",
            "description": f"⚠️ New bug: {bug['id']} — {bug['description']} (looks like Riko's commit)",
            "mood_change": None
        }

    # Client emails asking for a feature by tomorrow (6% chance)
    elif roll < 0.31 and tick > 4:
        feature = random.choice([
            "dark mode",
            "CSV export",
            "Slack integration",
            "2FA",
            "mobile app"
        ])
        await update_world_state({"office_mood": "tense"})
        await add_office_event(tick, "client_request",
            f"Client urgently requesting {feature} before launch.")
        return {
            "type": "client_request",
            "description": f"📧 Client just emailed: they need {feature} before launch. 'It's a dealbreaker.'",
            "mood_change": "tense"
        }

    # Something actually goes right (8% chance)
    elif roll < 0.39 and tick > 3:
        good_thing = random.choice([
            "Performance benchmarks came back great — 40% faster than target",
            "A major competitor just had a data breach. Good timing for our launch.",
            "New developer wants to join — saw the repo on GitHub",
            "The auth system passed security audit on first try",
            "Client loved the onboarding mockups",
        ])
        for agent in AGENTS:
            await update_morale(agent, +0.08)
        if mood == "tense":
            await update_world_state({"office_mood": "normal"})
        await add_office_event(tick, "good_news", good_thing)
        return {
            "type": "good_news",
            "description": f"✅ {good_thing}",
            "mood_change": None
        }

    # Conflict resolves between two tense/hostile agents (7% chance).
    # NEW: gives relationships a real repair path. Without this, any pair
    # that drifted negative (e.g. vex-riko, which starts at 0.2) had no
    # mechanism to ever improve -- only raise_concern/report_bug blame
    # could move the score, and both only push it down.
    elif roll < 0.46 and tick > 4:
        rels = state.get("relationships", {})
        tense_pairs = [k for k, v in rels.items() if v < 0.4]
        if tense_pairs:
            pair_key = random.choice(tense_pairs)
            a, b = pair_key.split("-")
            await update_relationship(a, b, +0.25)
            for agent in (a, b):
                await update_morale(agent, +0.05)
            description = f"{a.capitalize()} and {b.capitalize()} had a real conversation and cleared the air."
            await add_office_event(tick, "conflict_resolved", description)
            return {
                "type": "conflict_resolved",
                "description": f"🤝 {description}",
                "mood_change": None
            }

    return None  # No event this tick


async def start_new_sprint(tick: int, sprint_number: int):
    """
    Start a new sprint after the previous one ends.

    FIX: this function already existed but was never called anywhere --
    after a sprint shipped or failed, the world just sat in that terminal
    state forever with the same (now resolved or irrelevant) tasks and
    bugs. maybe_fire_event() now calls this automatically right after a
    sprint concludes.
    """
    sprint_names = [
        "Sprint 2 — Stability & Polish",
        "Sprint 3 — Scale & Performance",
        "Sprint 4 — Feature Expansion",
    ]
    name = sprint_names[min(sprint_number - 2, len(sprint_names) - 1)] if sprint_number >= 2 else "Sprint 1 — MVP Launch"

    from app.world_state import tasks_collection

    new_tasks = [
        {"id": "T101", "title": "Fix all sprint 1 bugs",      "assigned_to": "vex",    "status": "todo", "priority": "high",   "blocker": None},
        {"id": "T102", "title": "Add rate limiting",           "assigned_to": "vex",    "status": "todo", "priority": "medium", "blocker": None},
        {"id": "T103", "title": "Customer onboarding emails",  "assigned_to": "niblet", "status": "todo", "priority": "high",   "blocker": None},
        {"id": "T104", "title": "Set up monitoring/alerts",    "assigned_to": "pim",    "status": "todo", "priority": "high",   "blocker": None},
        {"id": "T105", "title": "Optimize payment flow",       "assigned_to": "riko",   "status": "todo", "priority": "high",   "blocker": None},
        {"id": "T106", "title": "Write postmortem",            "assigned_to": "pim",    "status": "todo", "priority": "low",    "blocker": None},
    ]

    await tasks_collection.delete_many({})
    await tasks_collection.insert_many(new_tasks)
    await update_world_state({
        "sprint": {
            "name": name,
            "deadline_tick": tick + 15,
            "status": "active",
            "started_tick": tick,
        },
        "sprint_number": sprint_number,
        "office_mood": "normal",
    })
    await add_office_event(tick, "new_sprint", f"New sprint started: {name}")