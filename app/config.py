"""
Central config. Loads from .env so secrets never get hardcoded or committed.
"""
import os
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "the_office")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Free tier, fast inference, solid tool-use support.
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Create a .env file (see .env.example) "
        "and add your key before starting the server."
    )
