"""Admin API endpoints."""
import json
import os
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query, Body
from typing import Optional, Dict
from pydantic import BaseModel
import httpx

from app.database import get_db
from app.services import settings_manager

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard():
    """
    Get admin dashboard overview.
    """
    async with get_db() as db:
        # Active jobs
        cursor = await db.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE status NOT IN ('complete', 'failed')
            """
        )
        active_jobs = (await cursor.fetchone())[0]

        # Jobs completed today
        today = datetime.now().strftime("%Y-%m-%d")
        cursor = await db.execute(
            """
            SELECT COUNT(*) FROM jobs
            WHERE status = 'complete'
            AND date(completed_at) = ?
            """,
            (today,)
        )
        completed_today = (await cursor.fetchone())[0]

        # Total cost today
        cursor = await db.execute(
            """
            SELECT COALESCE(SUM(cost_incurred), 0) FROM jobs
            WHERE date(created_at) = ?
            """,
            (today,)
        )
        cost_today = (await cursor.fetchone())[0]

        # Total cost this week
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        cursor = await db.execute(
            """
            SELECT COALESCE(SUM(cost_incurred), 0) FROM jobs
            WHERE date(created_at) >= ?
            """,
            (week_ago,)
        )
        cost_week = (await cursor.fetchone())[0]

        # Total cost this month
        month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
        cursor = await db.execute(
            """
            SELECT COALESCE(SUM(cost_incurred), 0) FROM jobs
            WHERE date(created_at) >= ?
            """,
            (month_start,)
        )
        cost_month = (await cursor.fetchone())[0]

        # Library size
        cursor = await db.execute("SELECT COUNT(*) FROM content_library")
        library_size = (await cursor.fetchone())[0]

        # Recent activity
        cursor = await db.execute(
            """
            SELECT id, status, original_filename, created_at, completed_at
            FROM jobs
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        recent_jobs = [
            {
                "id": row["id"],
                "status": row["status"],
                "filename": row["original_filename"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
            for row in await cursor.fetchall()
        ]

        return {
            "active_jobs": active_jobs,
            "completed_today": completed_today,
            "costs": {
                "today": round(cost_today, 4),
                "this_week": round(cost_week, 4),
                "this_month": round(cost_month, 4),
            },
            "library_size": library_size,
            "recent_activity": recent_jobs,
        }


@router.get("/prompts")
async def list_prompts():
    """
    List all prompt templates.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, template_name, model, max_tokens, variables, version, updated_at
            FROM prompt_templates
            ORDER BY template_name
            """
        )
        prompts = [
            {
                "id": row["id"],
                "template_name": row["template_name"],
                "model": row["model"],
                "max_tokens": row["max_tokens"],
                "variables": json.loads(row["variables"]) if row["variables"] else [],
                "version": row["version"],
                "updated_at": row["updated_at"],
            }
            for row in await cursor.fetchall()
        ]

        return {"prompts": prompts}


@router.get("/prompts/{template_name}")
async def get_prompt(template_name: str):
    """
    Get a specific prompt template.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM prompt_templates WHERE template_name = ?
            """,
            (template_name,)
        )
        row = await cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Template not found")

        return {
            "id": row["id"],
            "template_name": row["template_name"],
            "model": row["model"],
            "max_tokens": row["max_tokens"],
            "prompt_content": row["prompt_content"],
            "variables": json.loads(row["variables"]) if row["variables"] else [],
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


@router.put("/prompts/{template_name}")
async def update_prompt(
    template_name: str,
    prompt_content: str,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
):
    """
    Update a prompt template.
    """
    async with get_db() as db:
        # Check if exists
        cursor = await db.execute(
            "SELECT id, version FROM prompt_templates WHERE template_name = ?",
            (template_name,)
        )
        row = await cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Template not found")

        # Update with version increment
        new_version = row["version"] + 1
        update_fields = ["prompt_content = ?", "version = ?", "updated_at = CURRENT_TIMESTAMP"]
        params = [prompt_content, new_version]

        if model:
            update_fields.append("model = ?")
            params.append(model)

        if max_tokens:
            update_fields.append("max_tokens = ?")
            params.append(max_tokens)

        params.append(template_name)

        await db.execute(
            f"""
            UPDATE prompt_templates
            SET {", ".join(update_fields)}
            WHERE template_name = ?
            """,
            params
        )
        await db.commit()

        return {"message": "Template updated", "version": new_version}


@router.get("/clients")
async def list_clients():
    """
    List all clients/users.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT u.id, u.email, u.subscription_tier, u.created_at,
                   COUNT(j.id) as job_count,
                   COALESCE(SUM(j.cost_incurred), 0) as total_cost
            FROM users u
            LEFT JOIN jobs j ON u.id = j.user_id
            GROUP BY u.id
            ORDER BY u.created_at DESC
            """
        )
        clients = [
            {
                "id": row["id"],
                "email": row["email"],
                "subscription_tier": row["subscription_tier"],
                "created_at": row["created_at"],
                "job_count": row["job_count"],
                "total_cost": round(row["total_cost"], 4),
            }
            for row in await cursor.fetchall()
        ]

        return {"clients": clients}


@router.get("/clients/{client_id}")
async def get_client(client_id: int):
    """
    Get detailed client information.
    """
    async with get_db() as db:
        # Get user info
        cursor = await db.execute(
            "SELECT * FROM users WHERE id = ?",
            (client_id,)
        )
        user = await cursor.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Client not found")

        # Get job history
        cursor = await db.execute(
            """
            SELECT id, status, original_filename, created_at, completed_at, cost_incurred
            FROM jobs
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (client_id,)
        )
        jobs = [dict(row) for row in await cursor.fetchall()]

        # Get library stats
        cursor = await db.execute(
            """
            SELECT entry_type, COUNT(*) as count
            FROM content_library
            WHERE user_id = ?
            GROUP BY entry_type
            """,
            (client_id,)
        )
        library_stats = {row["entry_type"]: row["count"] for row in await cursor.fetchall()}

        # Total cost
        cursor = await db.execute(
            "SELECT COALESCE(SUM(cost_incurred), 0) FROM jobs WHERE user_id = ?",
            (client_id,)
        )
        total_cost = (await cursor.fetchone())[0]

        return {
            "id": user["id"],
            "email": user["email"],
            "subscription_tier": user["subscription_tier"],
            "created_at": user["created_at"],
            "total_cost": round(total_cost, 4),
            "library_stats": library_stats,
            "recent_jobs": jobs,
        }


@router.get("/costs")
async def get_costs(
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    group_by: str = Query("day", description="Group by: day, user, or job"),
):
    """
    Get cost breakdown.
    """
    async with get_db() as db:
        if group_by == "day":
            query = """
                SELECT date(created_at) as date, SUM(cost_incurred) as cost
                FROM jobs
                WHERE 1=1
            """
            params = []

            if start_date:
                query += " AND date(created_at) >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date(created_at) <= ?"
                params.append(end_date)

            query += " GROUP BY date(created_at) ORDER BY date DESC"

            cursor = await db.execute(query, params)
            results = [
                {"date": row["date"], "cost": round(row["cost"], 4)}
                for row in await cursor.fetchall()
            ]

        elif group_by == "user":
            query = """
                SELECT u.email, SUM(j.cost_incurred) as cost
                FROM jobs j
                JOIN users u ON j.user_id = u.id
                WHERE 1=1
            """
            params = []

            if start_date:
                query += " AND date(j.created_at) >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date(j.created_at) <= ?"
                params.append(end_date)

            query += " GROUP BY u.id ORDER BY cost DESC"

            cursor = await db.execute(query, params)
            results = [
                {"user": row["email"], "cost": round(row["cost"], 4)}
                for row in await cursor.fetchall()
            ]

        else:  # group_by == "job"
            query = """
                SELECT id, original_filename, cost_incurred, created_at
                FROM jobs
                WHERE 1=1
            """
            params = []

            if start_date:
                query += " AND date(created_at) >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date(created_at) <= ?"
                params.append(end_date)

            query += " ORDER BY created_at DESC LIMIT 100"

            cursor = await db.execute(query, params)
            results = [
                {
                    "job_id": row["id"],
                    "filename": row["original_filename"],
                    "cost": round(row["cost_incurred"], 4),
                    "created_at": row["created_at"],
                }
                for row in await cursor.fetchall()
            ]

        return {"costs": results, "group_by": group_by}


@router.get("/logs")
async def get_logs(
    level: Optional[str] = Query(None, description="Filter by level (error, warning, info)"),
    start_date: Optional[str] = Query(None),
    limit: int = Query(100),
):
    """
    Get job logs/errors.

    Note: For MVP, we extract errors from job records. A proper logging
    system would be implemented in production.
    """
    async with get_db() as db:
        query = """
            SELECT id, status, error_message, created_at, completed_at
            FROM jobs
            WHERE 1=1
        """
        params = []

        if level == "error":
            query += " AND status = 'failed'"

        if start_date:
            query += " AND date(created_at) >= ?"
            params.append(start_date)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await db.execute(query, params)
        logs = [
            {
                "job_id": row["id"],
                "status": row["status"],
                "error_message": row["error_message"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
            for row in await cursor.fetchall()
        ]

        return {"logs": logs}


# Pydantic models for settings endpoints
class ApiKeyRequest(BaseModel):
    provider: str
    key: str


class OpenRouterToggle(BaseModel):
    enabled: bool


class ModelConfig(BaseModel):
    transcription: str
    atomization: str
    drafting: str
    editing: str
    factcheck: str


@router.get("/settings")
async def get_settings():
    """Get current settings including API key status and model config."""
    settings = settings_manager.get_settings()
    api_keys = settings_manager.get_api_key_status()
    
    return {
        "api_keys": api_keys,
        "use_openrouter": settings.get("use_openrouter", False),
        "models": settings.get("models", {})
    }


@router.post("/settings/apikey")
async def save_api_key(request: ApiKeyRequest):
    """
    Save API key - stores in environment for current session.
    Note: For permanent storage, keys should be set in Replit Secrets.
    """
    provider = request.provider.lower()
    key = request.key
    
    if provider == "openrouter":
        os.environ["OPENROUTER_API_KEY"] = key
    elif provider == "gemini":
        os.environ["GEMINI_API_KEY"] = key
    elif provider == "anthropic":
        os.environ["ANTHROPIC_API_KEY"] = key
    else:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    
    return {"status": "ok", "message": f"{provider} API key saved for this session"}


@router.post("/settings/openrouter")
async def toggle_openrouter(request: OpenRouterToggle):
    """Toggle OpenRouter usage."""
    settings_manager.set_openrouter_enabled(request.enabled)
    return {"status": "ok", "use_openrouter": request.enabled}


@router.post("/settings/models")
async def save_model_config(config: ModelConfig):
    """Save model configuration for each pipeline step."""
    settings_manager.set_model_config({
        "transcription": config.transcription,
        "atomization": config.atomization,
        "drafting": config.drafting,
        "editing": config.editing,
        "factcheck": config.factcheck
    })
    return {"status": "ok", "models": config.model_dump()}


@router.get("/openrouter-models")
async def get_openrouter_models():
    """Fetch available models from OpenRouter."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    
    if not openrouter_key:
        raise HTTPException(status_code=400, detail="OpenRouter API key not configured")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {openrouter_key}"}
            )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to fetch OpenRouter models"
                )
            
            data = response.json()
            models = data.get("data", [])
            
            # Format models for frontend (id and name)
            formatted_models = [
                {
                    "id": model.get("id"),
                    "name": model.get("name", model.get("id")),
                    "pricing": model.get("pricing", {})
                }
                for model in models
                if model.get("id")
            ]
            
            # Sort by name
            formatted_models.sort(key=lambda x: x["name"])
            
            return {"models": formatted_models}
            
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect to OpenRouter: {str(e)}"
        )


# ============================================================================
# AI Model Configuration (Database-backed)
# ============================================================================

class TaskModelUpdate(BaseModel):
    model_id: str


@router.get("/ai-models")
async def get_ai_model_configs():
    """
    Get all AI model configurations from database.
    Returns task-level model assignments.
    """
    from app.services.openrouter import get_all_model_configs, get_available_models

    configs = await get_all_model_configs()
    available = get_available_models()

    return {
        "configs": configs,
        "available_models": available,
    }


@router.put("/ai-models/{task_name}")
async def update_ai_model_config(task_name: str, update: TaskModelUpdate):
    """
    Update the model for a specific AI task.

    Valid task names:
    - transcription, atomization, drafting, editing, fact_checking, scoring
    - brand_voice_analysis, hook_generation, swipe_analysis
    """
    from app.services.openrouter import update_model_config, get_available_models

    # Validate model exists
    available_ids = [m["id"] for m in get_available_models()]
    if update.model_id not in available_ids:
        # Allow any model ID (user might use one not in our curated list)
        pass

    success = await update_model_config(task_name, update.model_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update model config")

    return {
        "success": True,
        "task_name": task_name,
        "model_id": update.model_id,
    }


@router.get("/ai-models/available")
async def get_available_ai_models():
    """
    Get list of available AI models for selection.
    Returns curated list plus option to fetch from OpenRouter.
    """
    from app.services.openrouter import get_available_models, get_model_tier

    models = get_available_models()

    return {
        "models": models,
        "note": "Use /api/admin/openrouter-models to fetch full list from OpenRouter API"
    }
