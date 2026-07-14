"""
FastAPI entrypoint with frontend integration.

Routes:
- GET  /health               status + DB check
- POST /tick                 manual tick trigger
- GET  /events               event log
- GET  /stats/all            avg reward per agent (formatted for UI)
- GET  /stats/agent/{name}   one agent's detailed stats
- GET  /world                full world state
- POST /reset                clear all data
- POST /scheduler/start      begin auto-ticking
- POST /scheduler/stop       stop auto-ticking
- GET  /scheduler/status     is auto-tick running?

WebSocket (optional):
- WS   /ws                   real-time event stream
"""
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
import json
from datetime import datetime
from typing import Set

from app.database import ping_database, events_collection, training_collection
from app.agent_loop import run_full_tick, TURN_ORDER
from app.world_state import get_world_state, init_world_state
from app.train_reward_model import train_reward_model

app = FastAPI(title="The Office")
from fastapi.staticfiles import StaticFiles

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CORS — allow your frontend to hit this API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, use ["http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Global state
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TICK_LOCK = asyncio.Lock()
SCHEDULER_RUNNING = False
SCHEDULER_TASK = None
TICK_INTERVAL = 3.0  # seconds between auto-ticks
WEBSOCKET_CLIENTS: Set[WebSocket] = set()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Startup / Shutdown
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.on_event("startup")
async def startup_event():
    """Initialize world on server start."""
    try:
        await init_world_state()
        print("✓ World state initialized")
    except Exception as e:
        print(f"⚠ World init warning: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Health & Status
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.get("/health")
async def health():
    """Server + DB health check."""
    try:
        db_ok = await ping_database()
        if not db_ok:
            raise HTTPException(status_code=503, detail="Database not reachable")
        return {
            "status": "ok",
            "database": "connected",
            "scheduler_running": SCHEDULER_RUNNING,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": str(e)}
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tick Management
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.post("/tick")
async def tick():
    """
    Manual tick: run all 4 agents sequentially.
    Frontend calls this to manually advance the simulation.
    """
    async with TICK_LOCK:
        try:
            results = await run_full_tick()

            # Broadcast to WebSocket clients
            for ws in WEBSOCKET_CLIENTS:
                try:
                    await ws.send_json({
                        "type": "tick_complete",
                        "results": results,
                        "timestamp": datetime.now().isoformat(),
                    })
                except:
                    pass  # Client disconnected

            return {
                "success": True,
                "tick_results": results,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception as e:
            print(f"[TICK ERROR] {e}")
            raise HTTPException(status_code=500, detail=str(e))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Auto-Tick Scheduler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def scheduler_loop():
    """Background loop: runs ticks automatically every N seconds."""
    global SCHEDULER_RUNNING
    SCHEDULER_RUNNING = True
    tick_count = 0

    try:
        while SCHEDULER_RUNNING:
            await asyncio.sleep(TICK_INTERVAL)

            try:
                async with TICK_LOCK:
                    results = await run_full_tick()
                    tick_count += 1

                    # Notify WebSocket clients
                    for ws in WEBSOCKET_CLIENTS:
                        try:
                            await ws.send_json({
                                "type": "auto_tick",
                                "tick_count": tick_count,
                                "results": results,
                                "timestamp": datetime.now().isoformat(),
                            })
                        except:
                            pass

                    print(f"[AUTO-TICK] #{tick_count} complete")
            except Exception as e:
                print(f"[AUTO-TICK ERROR] {e}")
                await asyncio.sleep(1)  # Brief pause before retry
    finally:
        SCHEDULER_RUNNING = False
        print("[SCHEDULER] Stopped")


@app.post("/scheduler/start")
async def start_scheduler(interval: float = 3.0):
    """Start auto-ticking at specified interval (seconds)."""
    global SCHEDULER_TASK, SCHEDULER_RUNNING, TICK_INTERVAL

    if SCHEDULER_RUNNING:
        return {"status": "already_running", "interval": TICK_INTERVAL}

    TICK_INTERVAL = interval
    SCHEDULER_TASK = asyncio.create_task(scheduler_loop())

    return {
        "status": "started",
        "interval": TICK_INTERVAL,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/scheduler/stop")
async def stop_scheduler():
    """Stop auto-ticking."""
    global SCHEDULER_RUNNING, SCHEDULER_TASK

    SCHEDULER_RUNNING = False
    if SCHEDULER_TASK:
        SCHEDULER_TASK.cancel()
        try:
            await SCHEDULER_TASK
        except asyncio.CancelledError:
            pass

    return {
        "status": "stopped",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/scheduler/status")
async def scheduler_status():
    """Check if auto-tick is running."""
    return {
        "running": SCHEDULER_RUNNING,
        "interval": TICK_INTERVAL,
        "timestamp": datetime.now().isoformat(),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Event Log
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.get("/events")
async def list_events(limit: int = 50):
    """
    Get recent events (for the activity feed).
    Formatted for easy frontend consumption.
    """
    try:
        cursor = events_collection.find().sort("tick", -1).limit(limit)
        events = await cursor.to_list(length=limit)

        # Reverse to show oldest first, serialize
        events = list(reversed(events))
        for e in events:
            e["_id"] = str(e["_id"])

        return {
            "count": len(events),
            "events": events,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/train")
async def train():
    """
    Manually trigger reward-model training on all episodes collected so far.
    Not automatic — call this deliberately when you want to refresh the
    model (e.g. after a batch of ticks). Saves reward_model.pkl, which
    get_team_patterns() in performance_history.py will pick up on the
    NEXT tick after training (not retroactively).
    """
    try:
        result = await train_reward_model()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stats / Performance
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.get("/stats/all")
async def all_stats():
    """
    Get aggregated stats for all agents.
    Formatted for the UI stats cards (bottom of screen).

    FIX: training_collection.aggregate() returns an async Motor cursor,
    not a plain list. The previous version called list(...) on it directly,
    which doesn't work on an async cursor and was the cause of the
    500 errors on this route. Must `await` the call and use `.to_list()`,
    the same pattern used everywhere else in this codebase (e.g. /events).
    """
    try:
        stats = {}

        for agent in ["vex", "niblet", "pim", "riko"]:
            pipeline = [
                {"$match": {"agent": agent}},
                {"$group": {
                    "_id": "$agent",
                    "avg_reward": {"$avg": "$reward"},
                    "total_reward": {"$sum": "$reward"},
                    "action_count": {"$sum": 1},
                    "last_tick": {"$max": "$tick"},
                }}
            ]
            cursor = training_collection.aggregate(pipeline)
            result = await cursor.to_list(length=1)

            if result:
                r = result[0]
                stats[agent] = {
                    "avg_reward": round(r.get("avg_reward", 0) or 0, 2),
                    "total_reward": round(r.get("total_reward", 0) or 0, 2),
                    "action_count": r.get("action_count", 0),
                    "last_tick": r.get("last_tick", 0),
                }
            else:
                stats[agent] = {
                    "avg_reward": 0.0,
                    "total_reward": 0.0,
                    "action_count": 0,
                    "last_tick": 0,
                }

        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats/agent/{agent_name}")
async def agent_stats(agent_name: str):
    """
    Detailed stats for one agent.

    FIX: same async-cursor issue as /stats/all — aggregate() must be
    awaited via .to_list(), not wrapped in a synchronous list(...).
    """
    if agent_name not in TURN_ORDER:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")

    try:
        pipeline = [
            {"$match": {"agent": agent_name}},
            {"$group": {
                "_id": "$agent",
                "avg_reward": {"$avg": "$reward"},
                "total_reward": {"$sum": "$reward"},
                "action_count": {"$sum": 1},
                "actions": {"$push": "$action"},
                "last_tick": {"$max": "$tick"},
            }}
        ]
        cursor = training_collection.aggregate(pipeline)
        result = await cursor.to_list(length=1)

        if not result:
            return {
                "agent": agent_name,
                "avg_reward": 0.0,
                "total_reward": 0.0,
                "action_count": 0,
                "actions": {},
                "last_tick": 0,
            }

        r = result[0]

        # Count action types
        action_counts = {}
        for action in r.get("actions", []):
            action_counts[action] = action_counts.get(action, 0) + 1

        return {
            "agent": agent_name,
            "avg_reward": round(r.get("avg_reward", 0) or 0, 2),
            "total_reward": round(r.get("total_reward", 0) or 0, 2),
            "action_count": r.get("action_count", 0),
            "last_tick": r.get("last_tick", 0),
            "action_breakdown": action_counts,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# World State
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.get("/world")
async def get_world():
    """Full world state snapshot."""
    try:
        state = await get_world_state()
        return {
            "world": state,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Admin / Reset
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.post("/reset")
async def reset_world():
    """Nuke everything and start fresh."""
    global SCHEDULER_RUNNING

    # Stop auto-tick if running
    if SCHEDULER_RUNNING:
        await stop_scheduler()

    try:
        await init_world_state()
        await events_collection.delete_many({})
        await training_collection.delete_many({})

        return {
            "status": "reset",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# WebSocket (Real-time Updates)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time event stream.
    Frontend connects here to get live tick updates without polling.

    Usage:
      const ws = new WebSocket("ws://localhost:8000/ws");
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "tick_complete") { ... }
      };
    """
    await websocket.accept()
    WEBSOCKET_CLIENTS.add(websocket)

    try:
        # Keep connection alive; client will disconnect when done
        while True:
            data = await websocket.receive_text()
            # Echo back or ignore client messages for now
            await websocket.send_text(json.dumps({"status": "pong"}))
    except Exception as e:
        print(f"[WS ERROR] {e}")
    finally:
        WEBSOCKET_CLIENTS.discard(websocket)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Root / Documentation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@app.get("/")
async def root():
    """API overview."""
    return {
        "app": "The Office — Multi-Agent Startup Simulator",
        "status": "running",
        "agents": TURN_ORDER,
        "endpoints": {
            "manual_control": {
                "POST /tick": "Run one full tick (all agents act)",
                "POST /reset": "Clear all data, reset world",
            },
            "scheduler": {
                "POST /scheduler/start?interval=3": "Start auto-ticking every N seconds",
                "POST /scheduler/stop": "Stop auto-ticking",
                "GET  /scheduler/status": "Check if auto-tick is running",
            },
            "monitoring": {
                "GET  /health": "Server + DB status",
                "GET  /events?limit=50": "Recent events",
                "GET  /world": "Full world state",
                "GET  /stats/all": "Aggregate stats (UI stats cards)",
                "GET  /stats/agent/{name}": "One agent's detailed stats",
            },
            "realtime": {
                "WS   /ws": "WebSocket for live event stream",
            },
        },
        "docs": "http://localhost:8000/docs (Swagger UI)",
        "timestamp": datetime.now().isoformat(),
    }

app.mount("/", StaticFiles(directory="app/frontend", html=True), name="frontend")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)