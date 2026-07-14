# The Office — Multi-Agent Startup Simulator

A simulated startup office where four AI agents — each with a distinct role and
personality — make autonomous decisions every tick based on live world state,
their own past performance, and team-wide behavior patterns. Built with
FastAPI, MongoDB, and Groq (Llama 3.3 70B) for agent decision-making.

Watch four coworkers (Vex, Niblet, Pim, and Riko) build a product, fix bugs,
argue, panic near deadlines, and sometimes quit — driven entirely by an LLM
choosing from a fixed toolset each turn, scored by a hand-written reward
function, and reflecting on its own track record before deciding what to do
next.

---

## What this actually is

This is **not** reinforcement learning in the technical sense — no weights
are updated, no policy is trained to replace the LLM's decision-making.
Instead it uses two lighter-weight techniques:

1. **Reward-informed prompting** — each agent sees a summary of its own
   recent actions and how they scored, injected as plain text into its
   prompt before it decides what to do next (`performance_history.py`).
2. **An optional, manually-trained classifier** — a small RandomForest
   trained on all collected episodes, used to surface a loose "team-wide
   pattern" hint (`get_team_patterns()`). It has no visibility into world
   state and is explicitly framed to agents as a hint, not a rule — its
   value is generalizing across action/agent combinations a specific agent
   hasn't tried yet, not precision.

Both are additive context. The LLM is always free to ignore them.

---

## The agents

| Agent | Role | Personality |
|---|---|---|
| **Vex** | Lead engineer | Blunt, brilliant, zero tolerance for sloppiness |
| **Niblet** | PM / sprint owner | Warm, relentlessly optimistic, slightly chaotic |
| **Pim** | Ops & infra | Anxious, hyper-competent, chronically underestimated |
| **Riko** | Full-stack wildcard | Chaotic, brilliant, easily bored — most likely to quit under pressure |

Each tick, every active agent:
1. Reads the current world state (their tasks, open bugs, relationships, morale, sprint deadline)
2. Reads their own recent performance history and any team-wide pattern hint
3. Reads recent office activity
4. Picks exactly one action via tool calling: `fix_bug`, `report_bug`, `update_task`, `talk_to_agent`, `post_public_message`, `raise_concern`, `idle`, or `quit`
5. That action is scored and applied to world state — morale shifts, relationships change, bugs get fixed or introduced, tasks progress

Agents who quit are permanently removed from the turn rotation.

## Tech stack

- **Backend:** FastAPI, Motor (async MongoDB driver), asyncio
- **Agent decisions:** Groq API, `llama-3.3-70b-versatile`, tool calling
- **ML:** scikit-learn (RandomForestClassifier), joblib
- **Frontend:** Vanilla HTML/CSS/JS, canvas-based isometric rendering, no build step

## Known limitations

- The classifier's features are shallow; `message_length` tends to dominate its predictions more than `action_type` or `agent`, which is a
  real but not especially deep signal.
- `context_length` (derived from a 500-char-truncated world summary) carries little variance across episodes and is usually ignored by the
  model.
- Task-ID matching is normalized (`.strip().upper()`) but not fully bulletproof — occasional "task not found" responses can still occur if
  the LLM sends a malformed ID that normalization doesn't catch.
- Riko's personality and low starting relationship score with Vex make an early quit fairly common — this is an intentional character trait, not a
  bug, but tunable via `world_state.py`'s `DEFAULT_STATE` if you want longer average runs.

