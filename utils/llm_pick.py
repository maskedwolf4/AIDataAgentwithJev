import os
from typing import Literal
from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

ModelTier = Literal["low", "medium", "high", "top", "claude"]

GROQ_MODEL_MAP: dict[str, str] = {
    "low": os.getenv("GROQ_MODEL_LOW", "llama-3.1-8b-instant"),
    "medium": os.getenv("GROQ_MODEL_MEDIUM", "openai/gpt-oss-20b"),
    "high": os.getenv("GROQ_MODEL_HIGH", "llama-3.3-70b-versatile"),
    "top": os.getenv("GROQ_MODEL_TOP", "openai/gpt-oss-120b"),
    "claude": os.getenv("GROQ_MODEL_TOP", "openai/gpt-oss-120b"),
}

TIER_ORDER = ["low", "medium", "high", "top"]


def get_model_name(level: str) -> str:
    """Return the Groq model ID for a given tier name."""
    normalized = level.lower().strip()
    if normalized not in GROQ_MODEL_MAP:
        raise ValueError(
            f"Unsupported level: {level}. Valid levels: {list(GROQ_MODEL_MAP.keys())}"
        )
    return GROQ_MODEL_MAP[normalized]


def pick_llm(level: str = "medium", temperature: float = 0.0) -> ChatGroq:
    """
    Return a ChatGroq instance for the requested complexity tier.

    Tier mapping (by Groq model size):
      low    -> llama-3.1-8b-instant   (8B)
      medium -> openai/gpt-oss-20b     (20B)
      high   -> llama-3.3-70b-versatile (70B)
      top    -> openai/gpt-oss-120b    (120B)
      claude -> alias for top
    """
    model_name = get_model_name(level)
    return ChatGroq(
        model=model_name,
        temperature=temperature,
        api_key=os.getenv("GROQ_API_KEY"),
    )


def escalate_tier(current_tier: str) -> str:
    """Escalate to the next larger Groq model tier for Jev cascade retries."""
    norm = "top" if current_tier.lower() == "claude" else current_tier.lower()
    if norm not in TIER_ORDER:
        return "high"
    idx = TIER_ORDER.index(norm)
    return TIER_ORDER[min(idx + 1, len(TIER_ORDER) - 1)]
