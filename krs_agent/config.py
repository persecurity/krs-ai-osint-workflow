"""Configuration loaded from environment / .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "KRS-Investigator")

DEFAULT_MODELS = [
    "google/gemini-2.5-flash-lite",
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.5",
]
MODEL_LADDER = [
    m.strip()
    for m in os.getenv("OPENROUTER_MODELS", ",".join(DEFAULT_MODELS)).split(",")
    if m.strip()
]

KRS_API_BASE = "https://api-krs.ms.gov.pl/api/krs"
CASES_DIR = PROJECT_ROOT / "cases"
