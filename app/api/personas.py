"""Persona management and brand voice preview API."""
import json
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import google.generativeai as genai

from app.config import get_settings, calculate_cost
from app.api.auth import get_current_user_id
from app.database import get_db
from app.services.persona_manager import get_persona, list_personas, invalidate_cache
from app.utils.retry import retry_async

router = APIRouter()
settings = get_settings()


class CustomPersonaCreate(BaseModel):
    """Request to create a custom persona."""
    title: str
    description: Optional[str] = None
    company_size: Optional[str] = None
    pain_points: Optional[list[str]] = None
    priorities: Optional[list[str]] = None
    language_level: Optional[str] = "Professional"
    tone: Optional[str] = "Professional"
    length_preference: Optional[str] = "Medium"
    data_density: Optional[str] = "Moderate"


class CustomPersonaUpdate(BaseModel):
    """Request to update a custom persona."""
    title: Optional[str] = None
    description: Optional[str] = None
    company_size: Optional[str] = None
    pain_points: Optional[list[str]] = None
    priorities: Optional[list[str]] = None
    language_level: Optional[str] = None
    tone: Optional[str] = None
    length_preference: Optional[str] = None
    data_density: Optional[str] = None


class BrandVoicePreviewRequest(BaseModel):
    """Request for brand voice preview."""
    sample_text: str
    persona_id: str


class BrandVoicePreviewResponse(BaseModel):
    """Response from brand voice preview."""
    original_text: str
    transformed_text: str
    changes_summary: list[str]
    persona_applied: dict


@router.get("/personas")
async def get_all_personas(
    user_id: int = Depends(get_current_user_id),
):
    """Get all available personas."""
    personas = await list_personas()
    return {"personas": personas}


@router.get("/personas/{persona_id}")
async def get_persona_details(
    persona_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """Get details for a specific persona."""
    persona = await get_persona(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@router.post("/personas/preview-voice", response_model=BrandVoicePreviewResponse)
async def preview_brand_voice(
    request: BrandVoicePreviewRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Preview how content will sound with a specific persona's brand voice applied.

    This is a quick preview that doesn't run the full pipeline - just transforms
    sample text to demonstrate the brand voice transformation.
    """
    if not request.sample_text or len(request.sample_text) < 10:
        raise HTTPException(status_code=400, detail="Sample text must be at least 10 characters")

    if len(request.sample_text) > 2000:
        raise HTTPException(status_code=400, detail="Sample text must be less than 2000 characters")

    persona = await get_persona(request.persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    # Build a focused prompt for quick brand voice transformation
    prompt = f"""Transform this text to match the following brand voice profile.

TARGET AUDIENCE:
- Title: {persona['title']}
- Language Level: {persona.get('language_level', 'Professional')}
- Priorities: {', '.join(persona.get('priorities', []))}
- Content Tone: {persona.get('content_preferences', {}).get('tone', 'Professional')}
- Preferred Length: {persona.get('content_preferences', {}).get('length', 'Medium')}

TRANSFORMATION GUIDELINES:
1. Adjust vocabulary and complexity for the target audience
2. Emphasize their priorities and pain points
3. Match the preferred tone and length
4. Keep the core message intact
5. Make it actionable for this specific audience

ORIGINAL TEXT:
{request.sample_text}

OUTPUT FORMAT (valid JSON):
{{
  "transformed_text": "The transformed text matching the brand voice",
  "changes_summary": [
    "Simplified technical jargon to executive-friendly language",
    "Added ROI-focused framing",
    "Shortened for time-constrained readers"
  ]
}}"""

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    async def do_transform():
        return model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=1000,
            )
        )

    try:
        response = await retry_async(
            do_transform,
            max_retries=2,
            base_delay=1.0,
            context="preview_brand_voice",
        )

        result = json.loads(response.text)

        return BrandVoicePreviewResponse(
            original_text=request.sample_text,
            transformed_text=result.get("transformed_text", request.sample_text),
            changes_summary=result.get("changes_summary", []),
            persona_applied={
                "id": persona["id"],
                "title": persona["title"],
                "tone": persona.get("content_preferences", {}).get("tone", "Professional"),
            }
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to transform text: {str(e)}"
        )


# ==================== Custom Persona CRUD ====================

def generate_persona_id(title: str) -> str:
    """Generate a URL-friendly persona ID from title."""
    # Convert to lowercase, replace spaces with underscores, remove special chars
    persona_id = title.lower().strip()
    persona_id = re.sub(r'[^a-z0-9\s]', '', persona_id)
    persona_id = re.sub(r'\s+', '_', persona_id)
    return f"custom_{persona_id[:30]}"


@router.post("/personas/custom")
async def create_custom_persona(
    persona: CustomPersonaCreate,
    user_id: int = Depends(get_current_user_id),
):
    """
    Create a new custom persona.

    Custom personas are user-specific and available for all their content generation.
    """
    if not persona.title or len(persona.title) < 3:
        raise HTTPException(status_code=400, detail="Title must be at least 3 characters")

    persona_id = generate_persona_id(persona.title)

    content_preferences = json.dumps({
        "tone": persona.tone or "Professional",
        "length": persona.length_preference or "Medium",
        "data_density": persona.data_density or "Moderate"
    })

    async with get_db() as db:
        # Check for duplicate
        cursor = await db.execute(
            "SELECT id FROM custom_personas WHERE user_id = ? AND persona_id = ?",
            (user_id, persona_id)
        )
        if await cursor.fetchone():
            raise HTTPException(status_code=400, detail="A persona with this name already exists")

        await db.execute(
            """
            INSERT INTO custom_personas
            (user_id, persona_id, title, description, company_size, pain_points,
             priorities, language_level, content_preferences)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, persona_id, persona.title, persona.description,
                persona.company_size, json.dumps(persona.pain_points or []),
                json.dumps(persona.priorities or []), persona.language_level,
                content_preferences
            )
        )
        await db.commit()

    return {
        "success": True,
        "persona_id": persona_id,
        "message": f"Custom persona '{persona.title}' created successfully"
    }


@router.get("/personas/custom")
async def get_custom_personas(
    user_id: int = Depends(get_current_user_id),
):
    """Get all custom personas for the current user."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, persona_id, title, description, company_size,
                   pain_points, priorities, language_level, content_preferences,
                   is_active, created_at
            FROM custom_personas
            WHERE user_id = ? AND is_active = TRUE
            ORDER BY created_at DESC
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

        personas = []
        for row in rows:
            personas.append({
                "id": row["id"],
                "persona_id": row["persona_id"],
                "title": row["title"],
                "description": row["description"],
                "company_size": row["company_size"],
                "pain_points": json.loads(row["pain_points"]) if row["pain_points"] else [],
                "priorities": json.loads(row["priorities"]) if row["priorities"] else [],
                "language_level": row["language_level"],
                "content_preferences": json.loads(row["content_preferences"]) if row["content_preferences"] else {},
                "is_custom": True,
                "created_at": row["created_at"],
            })

        return {"custom_personas": personas}


@router.put("/personas/custom/{persona_db_id}")
async def update_custom_persona(
    persona_db_id: int,
    updates: CustomPersonaUpdate,
    user_id: int = Depends(get_current_user_id),
):
    """Update an existing custom persona."""
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            "SELECT id FROM custom_personas WHERE id = ? AND user_id = ?",
            (persona_db_id, user_id)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Persona not found")

        # Build update query dynamically
        update_fields = []
        values = []

        if updates.title is not None:
            update_fields.append("title = ?")
            values.append(updates.title)
        if updates.description is not None:
            update_fields.append("description = ?")
            values.append(updates.description)
        if updates.company_size is not None:
            update_fields.append("company_size = ?")
            values.append(updates.company_size)
        if updates.pain_points is not None:
            update_fields.append("pain_points = ?")
            values.append(json.dumps(updates.pain_points))
        if updates.priorities is not None:
            update_fields.append("priorities = ?")
            values.append(json.dumps(updates.priorities))
        if updates.language_level is not None:
            update_fields.append("language_level = ?")
            values.append(updates.language_level)

        # Handle content preferences
        if any([updates.tone, updates.length_preference, updates.data_density]):
            # Get current preferences
            cursor = await db.execute(
                "SELECT content_preferences FROM custom_personas WHERE id = ?",
                (persona_db_id,)
            )
            row = await cursor.fetchone()
            prefs = json.loads(row["content_preferences"]) if row["content_preferences"] else {}

            if updates.tone is not None:
                prefs["tone"] = updates.tone
            if updates.length_preference is not None:
                prefs["length"] = updates.length_preference
            if updates.data_density is not None:
                prefs["data_density"] = updates.data_density

            update_fields.append("content_preferences = ?")
            values.append(json.dumps(prefs))

        if not update_fields:
            return {"success": True, "message": "No changes made"}

        update_fields.append("updated_at = CURRENT_TIMESTAMP")
        values.append(persona_db_id)

        await db.execute(
            f"UPDATE custom_personas SET {', '.join(update_fields)} WHERE id = ?",
            values
        )
        await db.commit()

    return {"success": True, "message": "Persona updated successfully"}


@router.delete("/personas/custom/{persona_db_id}")
async def delete_custom_persona(
    persona_db_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Delete a custom persona (soft delete)."""
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            "SELECT id FROM custom_personas WHERE id = ? AND user_id = ?",
            (persona_db_id, user_id)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Persona not found")

        # Soft delete
        await db.execute(
            "UPDATE custom_personas SET is_active = FALSE WHERE id = ?",
            (persona_db_id,)
        )
        await db.commit()

    return {"success": True, "message": "Persona deleted"}
