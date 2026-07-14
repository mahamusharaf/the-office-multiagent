from app.database import training_collection
import os
import joblib

# How many of the agent's most recent actions to consider. Kept small and
# recent (not all-time) so behavior can actually shift as the simulation
# progresses, rather than being permanently anchored by early-game numbers.
HISTORY_WINDOW = 15

# Need at least this many past actions before showing a summary at all --
# stats from 1-2 data points are noise, not signal, and would be more
# misleading than helpful early in a run.
MIN_EPISODES_FOR_SUMMARY = 3


async def get_performance_summary(agent_name: str) -> str:
    """
    Build a short performance summary for one agent, based on their most
    recent HISTORY_WINDOW training episodes. Returns a formatted string
    ready to drop into the agent's prompt, or a neutral placeholder if
    there isn't enough history yet.
    """
    cursor = (
        training_collection
        .find({"agent": agent_name})
        .sort("tick", -1)
        .limit(HISTORY_WINDOW)
    )
    episodes = await cursor.to_list(length=HISTORY_WINDOW)

    if len(episodes) < MIN_EPISODES_FOR_SUMMARY:
        return "YOUR RECENT PERFORMANCE: (not enough history yet)"

    # episodes are newest-first from the sort; reverse for "last 3" framing
    episodes_oldest_first = list(reversed(episodes))

    # Group rewards by action type
    by_action: dict[str, list[float]] = {}
    for ep in episodes_oldest_first:
        action = ep.get("action", "idle")
        reward = ep.get("reward", 0.0)
        by_action.setdefault(action, []).append(reward)

    # Sort action types by how often they were used, most-used first --
    # that's usually the most relevant signal for "what should I keep
    # doing / stop doing".
    action_lines = []
    for action, rewards in sorted(by_action.items(), key=lambda kv: -len(kv[1])):
        avg = sum(rewards) / len(rewards)
        sign = "+" if avg >= 0 else ""
        action_lines.append(f"  {action}: avg reward {sign}{avg:.2f} (used {len(rewards)}x)")

    # Last 3 actions, in order, as a quick "recent trend" signal
    last_n = episodes_oldest_first[-3:]
    recent_scores = ", ".join(
        f"{'+' if ep.get('reward', 0) >= 0 else ''}{ep.get('reward', 0):.2f}"
        for ep in last_n
    )

    lines = [
        f"YOUR RECENT PERFORMANCE (last {len(episodes_oldest_first)} actions):",
    ] + action_lines + [
        f"  Your last {len(last_n)} actions scored: {recent_scores}",
    ]
    return "\n".join(lines)


# ── Team-pattern context (from manually-trained reward model) ──
MODEL_PATH = "reward_model.pkl"

_AGENT_MAP  = {"vex": 0, "niblet": 1, "pim": 2, "riko": 3}
_ACTION_MAP = {"idle": 0, "post_public_message": 1, "talk_to_agent": 2}


async def get_team_patterns(agent_name: str) -> str:
    """
    Optional extra context line, derived from the manually-trained
    reward_model.pkl (see POST /train endpoint), not from raw averages.

    Honesty note: this model only sees agent, action, message length,
    context length, and has_target -- it has NO visibility into world
    state (bugs, deadlines, morale). Its value isn't precision, it's
    generalizing across action/agent combos this specific agent hasn't
    tried yet, which raw history (get_performance_summary) can't do.
    Framed as a loose team-wide pattern, not a personalized certainty.

    Returns "" if no model has been trained yet -- this is optional,
    additive context, never a hard dependency.
    """
    if not os.path.exists(MODEL_PATH):
        return ""

    try:
        model = joblib.load(MODEL_PATH)
    except Exception:
        return ""

    if agent_name not in _AGENT_MAP:
        return ""

    agent_id = _AGENT_MAP[agent_name]

    # Only the 3 action types the model actually knows about (see
    # extract_features' action_map) -- fix_bug/report_bug/etc. aren't
    # encoded, so we can't score them meaningfully here.
    best_action, best_prob = None, -1.0
    for action_name, action_id in _ACTION_MAP.items():
        has_target = 1 if action_name == "talk_to_agent" else 0
        # Generic filler values -- this model was never precise about
        # message/context length, so use rough typical values rather
        # than pretending to know the specifics of a hypothetical action.
        features = [[agent_id, action_id, 40, 300, has_target]]
        try:
            prob_positive = model.predict_proba(features)[0][1]
        except Exception:
            return ""
        if prob_positive > best_prob:
            best_prob, best_action = prob_positive, action_name

    if best_action is None or best_prob < 0.5:
        return ""

    return (
        f"TEAM PATTERN (from trained model, team-wide, not personal): "
        f"'{best_action}' has historically scored well across the team "
        f"(~{best_prob*100:.0f}% of similar actions were rewarded positively). "
        f"Take this as a loose hint, not a rule."
    )