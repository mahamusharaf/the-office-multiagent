"""
MongoDB connection, set up once and reused across the app.

Using motor (the async Mongo driver) instead of plain pymongo because FastAPI
is async end-to-end -- a blocking pymongo call inside an async route would
stall the whole event loop while waiting on the DB.
"""
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import MONGO_URI, MONGO_DB_NAME

_client = AsyncIOMotorClient(MONGO_URI)
db = _client[MONGO_DB_NAME]

# Collections we'll use. Defined here as the single source of truth for names,
# so nothing typos "events" vs "event_log" in different files later.
events_collection = db["events"]       # shared, objective event log (Week 1)
journals_collection = db["journals"]   # private per-agent memory (added later)
agents_collection = db["agents"]       # agent definitions/state (added later)
training_collection = db["training_episodes"]  # RL: {state, action, reward}



async def ping_database() -> bool:
    """Quick connectivity check, used by the /health endpoint."""
    try:
        await db.command("ping")
        return True
    except Exception:
        return False
