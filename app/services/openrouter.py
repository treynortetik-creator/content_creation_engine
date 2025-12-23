"""Centralized OpenRouter service for AI calls with configurable models."""
import os
import json
import httpx
from typing import Optional, Tuple

from app.database import get_db

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Available models for selection
AVAILABLE_MODELS = [
    {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "tier": "premium"},
    {"id": "anthropic/claude-haiku", "name": "Claude Haiku", "tier": "fast"},
    {"id": "google/gemini-flash-1.5", "name": "Gemini Flash 1.5", "tier": "fast"},
    {"id": "google/gemini-pro-1.5", "name": "Gemini Pro 1.5", "tier": "premium"},
    {"id": "openai/gpt-4o", "name": "GPT-4o", "tier": "premium"},
    {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "tier": "fast"},
    {"id": "meta-llama/llama-3.1-70b-instruct", "name": "Llama 3.1 70B", "tier": "fast"},
    {"id": "google/gemini-2.0-flash-exp", "name": "Gemini 2.0 Flash", "tier": "fast"},
]

# Default models for each task (fallback if DB not configured)
DEFAULT_MODELS = {
    "transcription": "google/gemini-flash-1.5",
    "atomization": "google/gemini-flash-1.5",
    "drafting": "anthropic/claude-sonnet-4",
    "editing": "google/gemini-flash-1.5",
    "fact_checking": "google/gemini-flash-1.5",
    "scoring": "google/gemini-flash-1.5",
    "brand_voice_analysis": "anthropic/claude-sonnet-4",
    "hook_generation": "anthropic/claude-sonnet-4",
    "swipe_analysis": "google/gemini-flash-1.5",
}

# Cost per 1M tokens (approximate - update as needed)
MODEL_COSTS = {
    "anthropic/claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "anthropic/claude-haiku": {"input": 0.25, "output": 1.25},
    "google/gemini-flash-1.5": {"input": 0.075, "output": 0.30},
    "google/gemini-pro-1.5": {"input": 1.25, "output": 5.00},
    "google/gemini-2.0-flash-exp": {"input": 0.10, "output": 0.40},
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "meta-llama/llama-3.1-70b-instruct": {"input": 0.52, "output": 0.75},
}


async def get_model_for_task(task_name: str) -> str:
    """Get the configured model for a specific task from database."""
    try:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT model_id FROM ai_model_config WHERE task_name = ?",
                (task_name,)
            )
            row = await cursor.fetchone()
            if row:
                return row["model_id"]
    except Exception as e:
        print(f"Error fetching model config for {task_name}: {e}")

    # Fallback to default
    return DEFAULT_MODELS.get(task_name, "google/gemini-flash-1.5")


async def get_all_model_configs() -> list[dict]:
    """Get all model configurations from database."""
    try:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT task_name, model_id, description, updated_at FROM ai_model_config ORDER BY task_name"
            )
            rows = await cursor.fetchall()
            return [
                {
                    "task_name": row["task_name"],
                    "model_id": row["model_id"],
                    "description": row["description"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
    except Exception as e:
        print(f"Error fetching model configs: {e}")
        return []


async def update_model_config(task_name: str, model_id: str) -> bool:
    """Update the model for a specific task."""
    try:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO ai_model_config (task_name, model_id, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(task_name) DO UPDATE SET
                    model_id = excluded.model_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (task_name, model_id)
            )
            await db.commit()
            return True
    except Exception as e:
        print(f"Error updating model config for {task_name}: {e}")
        return False


def calculate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate API cost based on model and token usage."""
    costs = MODEL_COSTS.get(model_id, {"input": 0.10, "output": 0.40})
    input_cost = (input_tokens / 1_000_000) * costs["input"]
    output_cost = (output_tokens / 1_000_000) * costs["output"]
    return input_cost + output_cost


async def call_openrouter(
    task_name: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 4000,
    model_override: Optional[str] = None,
) -> Tuple[str, int, int, str, float]:
    """
    Call OpenRouter with the configured model for this task.

    Args:
        task_name: The task type to determine which model to use
        messages: List of message dicts with 'role' and 'content'
        temperature: Sampling temperature (0-1)
        max_tokens: Maximum tokens in response
        model_override: Optional model to use instead of configured one

    Returns:
        Tuple of (response_text, input_tokens, output_tokens, model_used, cost)
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY environment variable not set")

    # Get model for this task
    model_id = model_override or await get_model_for_task(task_name)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            OPENROUTER_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://contentmultiplier.com",
                "X-Title": "ContentMultiplier",
            },
            json={
                "model": model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=120.0
        )

        if response.status_code != 200:
            error_text = response.text
            raise Exception(f"OpenRouter API error {response.status_code}: {error_text}")

        result = response.json()

        # Extract response
        content = result["choices"][0]["message"]["content"]

        # Extract token usage
        usage = result.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        # Calculate cost
        cost = calculate_cost(model_id, input_tokens, output_tokens)

        return content, input_tokens, output_tokens, model_id, cost


async def call_openrouter_simple(
    task_name: str,
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 4000,
) -> Tuple[str, float]:
    """
    Simplified wrapper for single-prompt calls.

    Args:
        task_name: The task type
        prompt: The prompt text
        temperature: Sampling temperature
        max_tokens: Maximum response tokens

    Returns:
        Tuple of (response_text, cost)
    """
    messages = [{"role": "user", "content": prompt}]
    content, _, _, _, cost = await call_openrouter(
        task_name, messages, temperature, max_tokens
    )
    return content, cost


def get_available_models() -> list[dict]:
    """Return list of available OpenRouter models."""
    return AVAILABLE_MODELS


def get_model_tier(model_id: str) -> str:
    """Get the tier (fast/premium) for a model."""
    for model in AVAILABLE_MODELS:
        if model["id"] == model_id:
            return model["tier"]
    return "unknown"
