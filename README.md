# The Office

All four agents (Vex, Niblet, Pim, Riko) now exist. Each `/tick` call runs
all four, in order, sharing one tick number -- each can post publicly, send
a private message to one coworker (`talk_to_agent`), or idle. Their view of
the event log is filtered to what's actually relevant to them: public posts
and idles are visible to everyone, but a private message only reaches its
sender and its target. Powered by Groq's free tier (llama-3.3-70b-versatile).
Still no frontend, no auto-tick timer, no private journals yet -- those are
the next milestones.

## Setup

### 1. MongoDB

You need a MongoDB instance running. Easiest options:

- **Local install**: install MongoDB Community Server, run `mongod` in the background.
- **Docker** (if you have Docker installed): 
  ```bash
  docker run -d -p 27017:27017 --name office-mongo mongo:latest
  ```
- **MongoDB Atlas** (free tier, no local install needed): create a free cluster
  at mongodb.com/atlas, get your connection string, use that as `MONGO_URI`.

### 2. Environment variables

```bash
cp .env.example .env
```

Then edit `.env` and fill in:
- `GROQ_API_KEY` — get a free key at https://console.groq.com/keys
  (no card required)
- `MONGO_URI` — leave as-is for local Mongo, or paste your Atlas connection string

### 3. Install dependencies

```bash
pip install fastapi uvicorn motor groq python-dotenv
```

### 4. Run the server

```bash
uvicorn app.main:app --reload
```

## A note on the free tier

Groq's free tier is rate-limited (requests per minute, tokens per minute,
requests per day — check current limits at https://console.groq.com/docs/rate-limits
since these change, and they vary by model). For Week 1 manual ticking this
is a non-issue — you're clicking a button occasionally. It becomes relevant
later once auto-tick is running 4 agents continuously; that's exactly why
the spec's cost/rate safeguard (agents only tick while a viewer is connected)
matters just as much for staying inside free-tier limits as it does for cost
on a paid plan.

## Testing it

Open `http://localhost:8000/docs` — FastAPI's interactive API docs.

1. Try **GET /health** first — confirms the server is up and Mongo is reachable.
2. Try **POST /tick** a few times in a row. Each call runs all 4 agents in
   order (vex, niblet, pim, riko) and returns a `tick_results` list showing
   what each one decided -- which tool, who it was aimed at (if private),
   and what was said.
3. Try **GET /events** to see the full log and check the things below.

## What to actually check for (this matters more than "did it run")

- **Does each agent sound distinct?** Read a few ticks' worth of output and
  cover up the `agent` field -- can you guess who said what just from the
  voice? If two agents blur together, that personality prompt needs work.
- **Does privacy actually hold?** If Vex sends a `talk_to_agent` message to
  Pim, does Riko's next decision ever reference it? It shouldn't -- check
  `/events` and trace through whether Riko's choices look like they ignored
  things not addressed to them.
- **Is idle actually being chosen sometimes?** If every single agent posts
  or talks every single tick with nothing to react to, the model isn't
  taking "idle" seriously as an option -- that's a sign to make the idle
  tool's description or the personality prompts more explicit about it
  being a normal, frequent choice.
- **Does same-tick back-and-forth happen?** Since all 4 agents act within
  one tick in a fixed order, check whether (for example) Pim's action
  sometimes reacts to something Vex just said two agents earlier in the
  *same* tick, not just the previous one.

If something feels off, the fix is almost always in `app/personalities.py`
(the system prompts) rather than the architecture -- iterate there before
touching `agent_loop.py`.

## Project structure

```
app/
  config.py        # env var loading
  database.py       # Mongo connection + collection references
  personalities.py   # all 4 agent system prompts + AGENTS lookup dict
  tools.py          # tool schema (post_public_message, talk_to_agent, idle)
  event_log.py      # read/write helpers, including relevance-filtered reads
  agent_loop.py      # the core decide-and-act cycle, run for all 4 agents per tick
  main.py           # FastAPI routes
```
