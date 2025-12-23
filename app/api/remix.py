"""Smart Content Remix API - Generate new content from library atoms."""
import json
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
import google.generativeai as genai

from app.config import get_settings
from app.database import get_db
from app.api.auth import get_current_user_id
from app.models.job import JobStatus
from app.utils.retry import retry_async

router = APIRouter()
settings = get_settings()


class RemixRequest(BaseModel):
    """Request to remix content from atoms."""
    atom_ids: list[int]
    content_type: str  # 'linkedin', 'blog', 'email'
    persona_id: Optional[str] = None
    theme: Optional[str] = None  # Optional theme/angle to guide remix


class RemixSuggestion(BaseModel):
    """AI-suggested atom combinations."""
    atoms: list[dict]
    theme: str
    rationale: str


@router.post("/remix/suggest")
async def suggest_remix_combinations(
    user_id: int = Depends(get_current_user_id),
):
    """
    AI suggests interesting atom combinations from the user's library.

    Returns 3 suggestions with different themes/angles.
    """
    async with get_db() as db:
        # Get user's atoms from library
        cursor = await db.execute(
            """
            SELECT id, entry_type, content, source, tags, persona_relevance
            FROM content_library
            WHERE user_id = ?
            ORDER BY times_used DESC, date_added DESC
            LIMIT 50
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

        if len(rows) < 3:
            return {
                "suggestions": [],
                "message": "Need at least 3 atoms in your library to generate suggestions"
            }

        atoms = []
        for row in rows:
            atoms.append({
                "id": row["id"],
                "type": row["entry_type"],
                "content": row["content"][:500],  # Truncate for prompt
                "source": row["source"],
            })

    # Ask AI to suggest combinations
    prompt = f"""You are a content strategist. Analyze these content atoms from a user's library and suggest 3 interesting combinations that could make compelling new content.

ATOMS:
{json.dumps(atoms, indent=2)}

For each suggestion, pick 2-4 atoms that work well together and explain why.

OUTPUT FORMAT (valid JSON):
{{
  "suggestions": [
    {{
      "atom_ids": [1, 5, 12],
      "theme": "The hidden cost of X",
      "rationale": "These atoms combine to tell a compelling story about...",
      "content_types": ["linkedin", "blog"]
    }},
    {{
      "atom_ids": [3, 7],
      "theme": "Why leaders should Y",
      "rationale": "These insights pair well because...",
      "content_types": ["linkedin", "email"]
    }},
    {{
      "atom_ids": [2, 8, 15],
      "theme": "The surprising truth about Z",
      "rationale": "Combining these creates a counterintuitive angle...",
      "content_types": ["blog"]
    }}
  ]
}}"""

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    async def do_suggest():
        return model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=1500,
            )
        )

    try:
        response = await retry_async(
            do_suggest,
            max_retries=2,
            base_delay=1.0,
            context="remix_suggest",
        )

        result = json.loads(response.text)
        suggestions = result.get("suggestions", [])

        # Enrich suggestions with full atom data
        for suggestion in suggestions:
            atom_ids = suggestion.get("atom_ids", [])
            suggestion["atoms"] = [a for a in atoms if a["id"] in atom_ids]

        return {"suggestions": suggestions}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate suggestions: {str(e)}"
        )


@router.post("/remix/generate")
async def generate_remix(
    request: RemixRequest,
    background_tasks: BackgroundTasks,
    user_id: int = Depends(get_current_user_id),
):
    """
    Generate new content from selected atoms.

    Creates a new job that processes the selected atoms into new content.
    """
    if len(request.atom_ids) < 1:
        raise HTTPException(status_code=400, detail="Select at least one atom")

    if len(request.atom_ids) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 atoms per remix")

    if request.content_type not in ['linkedin', 'blog', 'email']:
        raise HTTPException(status_code=400, detail="Invalid content type")

    async with get_db() as db:
        # Fetch selected atoms
        placeholders = ','.join(['?' for _ in request.atom_ids])
        cursor = await db.execute(
            f"""
            SELECT id, entry_type, content, source, tags, persona_relevance
            FROM content_library
            WHERE user_id = ? AND id IN ({placeholders})
            """,
            [user_id] + request.atom_ids
        )
        rows = await cursor.fetchall()

        if len(rows) == 0:
            raise HTTPException(status_code=404, detail="No valid atoms found")

        atoms = []
        for row in rows:
            atoms.append({
                "id": str(row["id"]),
                "atom_type": row["entry_type"],
                "content": row["content"],
                "persona_relevance": json.loads(row["persona_relevance"]) if row["persona_relevance"] else {},
            })

        # Create job for remix
        job_id = str(uuid.uuid4())

        # Determine persona
        persona = request.persona_id or "general_audience"
        if request.persona_id and request.persona_id.startswith("custom_"):
            # Load custom persona
            cursor = await db.execute(
                "SELECT persona_id, title, description FROM custom_personas WHERE user_id = ? AND persona_id = ?",
                (user_id, request.persona_id)
            )
            custom = await cursor.fetchone()
            if custom:
                persona = json.dumps([{"type": "custom", "description": custom["description"] or custom["title"]}])
            else:
                persona = json.dumps([{"type": "preset", "id": "ceo_longterm_care"}])
        else:
            persona = json.dumps([{"type": "preset", "id": request.persona_id or "ceo_longterm_care"}])

        await db.execute(
            """
            INSERT INTO jobs (
                id, user_id, status, original_filename, file_type,
                target_persona, asset_types, asset_quantities,
                processing_mode, current_step, progress
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user_id,
                JobStatus.DRAFTING.value,
                f"Remix: {request.theme or 'Custom Selection'}",
                "remix",
                persona,
                json.dumps([request.content_type]),
                json.dumps({request.content_type: 1}),
                "autopilot",
                "Generating remixed content",
                10,
            )
        )
        await db.commit()

    # Process in background
    from app.services.pipeline import process_job_from_library
    background_tasks.add_task(process_job_from_library, job_id, atoms)

    return {
        "success": True,
        "job_id": job_id,
        "message": f"Remix started! Generating {request.content_type} from {len(atoms)} atoms."
    }


@router.get("/remix/recent")
async def get_recent_remixes(
    user_id: int = Depends(get_current_user_id),
):
    """Get user's recent remix jobs."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, original_filename, status, created_at, completed_at
            FROM jobs
            WHERE user_id = ? AND file_type = 'remix'
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

        remixes = []
        for row in rows:
            remixes.append({
                "job_id": row["id"],
                "name": row["original_filename"],
                "status": row["status"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            })

        return {"remixes": remixes}
