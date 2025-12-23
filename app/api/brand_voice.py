"""Brand voice configuration API endpoints."""
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user_id
from app.models.brand_context import (
    BrandContext,
    BrandVoice,
    ToneByFormat,
    StructuralPreferences,
    BrandContextCreate,
    BrandContextResponse,
)

router = APIRouter()


def parse_multiline(text: str) -> list[str]:
    """Parse multiline text into list of non-empty lines."""
    if not text:
        return []
    return [line.strip() for line in text.strip().split('\n') if line.strip()]


def format_list_to_multiline(items: list) -> str:
    """Format list to multiline string."""
    if not items:
        return ""
    return '\n'.join(items)


@router.get("/brand-voice")
async def get_brand_voice(
    user_id: int = Depends(get_current_user_id),
):
    """
    Get the user's brand voice configuration.
    Returns null if not configured.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, user_id, company_name, industry, brand_voice_json,
                   mission_statement, key_differentiators, competitor_names,
                   created_at, updated_at
            FROM brand_contexts
            WHERE user_id = ?
            """,
            (user_id,)
        )
        row = await cursor.fetchone()

        if not row:
            return {"brand_context": None, "has_brand_voice": False}

        # Parse JSON fields
        brand_voice_data = json.loads(row["brand_voice_json"]) if row["brand_voice_json"] else {}
        key_differentiators = json.loads(row["key_differentiators"]) if row["key_differentiators"] else []
        competitor_names = json.loads(row["competitor_names"]) if row["competitor_names"] else []

        # Build BrandVoice object
        tone_by_format = ToneByFormat(
            linkedin=brand_voice_data.get("tone_by_format", {}).get("linkedin", "Conversational, punchy, question-driven"),
            blog=brand_voice_data.get("tone_by_format", {}).get("blog", "Authoritative but accessible, educational"),
            email=brand_voice_data.get("tone_by_format", {}).get("email", "Warm, brief, helpful without hard selling"),
        )

        structural_prefs = StructuralPreferences(
            linkedin_structure=brand_voice_data.get("structural_preferences", {}).get("linkedin_structure", "Hook (question/stat) → Bullets (3-5) → Mission tie → CTA"),
            paragraph_length=brand_voice_data.get("structural_preferences", {}).get("paragraph_length", "2-4 sentences"),
            contractions=brand_voice_data.get("structural_preferences", {}).get("contractions", "Moderate"),
        )

        brand_voice = BrandVoice(
            tone_by_format=tone_by_format,
            core_principles=brand_voice_data.get("core_principles", []),
            mission_phrases=brand_voice_data.get("mission_phrases", []),
            red_flags=brand_voice_data.get("red_flags", []),
            structural_preferences=structural_prefs,
        )

        return {
            "brand_context": {
                "id": row["id"],
                "user_id": row["user_id"],
                "company_name": row["company_name"] or "",
                "industry": row["industry"] or "",
                "brand_voice": brand_voice.model_dump(),
                "mission_statement": row["mission_statement"],
                "key_differentiators": key_differentiators,
                "competitor_names": competitor_names,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
            "has_brand_voice": True,
        }


@router.post("/brand-voice")
async def save_brand_voice(
    data: BrandContextCreate,
    user_id: int = Depends(get_current_user_id),
):
    """
    Save or update the user's brand voice configuration.
    """
    # Build brand voice JSON
    brand_voice_data = {
        "tone_by_format": {
            "linkedin": data.tone_linkedin,
            "blog": data.tone_blog,
            "email": data.tone_email,
        },
        "core_principles": parse_multiline(data.core_principles),
        "mission_phrases": parse_multiline(data.mission_phrases),
        "red_flags": parse_multiline(data.red_flags),
        "structural_preferences": {
            "linkedin_structure": data.linkedin_structure,
            "paragraph_length": data.paragraph_length,
            "contractions": data.contractions,
        },
    }

    key_differentiators = parse_multiline(data.key_differentiators)
    competitor_names = parse_multiline(data.competitor_names)

    async with get_db() as db:
        # Check if user already has a brand context
        cursor = await db.execute(
            "SELECT id FROM brand_contexts WHERE user_id = ?",
            (user_id,)
        )
        existing = await cursor.fetchone()

        if existing:
            # Update existing
            await db.execute(
                """
                UPDATE brand_contexts SET
                    company_name = ?,
                    industry = ?,
                    brand_voice_json = ?,
                    mission_statement = ?,
                    key_differentiators = ?,
                    competitor_names = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (
                    data.company_name,
                    data.industry,
                    json.dumps(brand_voice_data),
                    data.mission_statement,
                    json.dumps(key_differentiators),
                    json.dumps(competitor_names),
                    user_id,
                )
            )
        else:
            # Create new
            await db.execute(
                """
                INSERT INTO brand_contexts (
                    user_id, company_name, industry, brand_voice_json,
                    mission_statement, key_differentiators, competitor_names
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    data.company_name,
                    data.industry,
                    json.dumps(brand_voice_data),
                    data.mission_statement,
                    json.dumps(key_differentiators),
                    json.dumps(competitor_names),
                )
            )

        await db.commit()

    return {
        "success": True,
        "message": "Brand voice saved successfully",
    }


@router.get("/brand-voice/form-data")
async def get_brand_voice_form_data(
    user_id: int = Depends(get_current_user_id),
):
    """
    Get brand voice data formatted for form population.
    Returns flat structure suitable for form inputs.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT company_name, industry, brand_voice_json,
                   mission_statement, key_differentiators, competitor_names
            FROM brand_contexts
            WHERE user_id = ?
            """,
            (user_id,)
        )
        row = await cursor.fetchone()

        if not row:
            # Return defaults
            return {
                "company_name": "",
                "industry": "",
                "tone_linkedin": "Conversational, punchy, question-driven",
                "tone_blog": "Authoritative but accessible, educational",
                "tone_email": "Warm, brief, helpful without hard selling",
                "core_principles": "",
                "mission_phrases": "",
                "red_flags": "",
                "linkedin_structure": "Hook (question/stat) → Bullets (3-5) → Mission tie → CTA",
                "paragraph_length": "2-4 sentences",
                "contractions": "Moderate",
                "mission_statement": "",
                "key_differentiators": "",
                "competitor_names": "",
            }

        brand_voice_data = json.loads(row["brand_voice_json"]) if row["brand_voice_json"] else {}
        key_differentiators = json.loads(row["key_differentiators"]) if row["key_differentiators"] else []
        competitor_names = json.loads(row["competitor_names"]) if row["competitor_names"] else []

        tone_by_format = brand_voice_data.get("tone_by_format", {})
        structural_prefs = brand_voice_data.get("structural_preferences", {})

        return {
            "company_name": row["company_name"] or "",
            "industry": row["industry"] or "",
            "tone_linkedin": tone_by_format.get("linkedin", "Conversational, punchy, question-driven"),
            "tone_blog": tone_by_format.get("blog", "Authoritative but accessible, educational"),
            "tone_email": tone_by_format.get("email", "Warm, brief, helpful without hard selling"),
            "core_principles": format_list_to_multiline(brand_voice_data.get("core_principles", [])),
            "mission_phrases": format_list_to_multiline(brand_voice_data.get("mission_phrases", [])),
            "red_flags": format_list_to_multiline(brand_voice_data.get("red_flags", [])),
            "linkedin_structure": structural_prefs.get("linkedin_structure", "Hook (question/stat) → Bullets (3-5) → Mission tie → CTA"),
            "paragraph_length": structural_prefs.get("paragraph_length", "2-4 sentences"),
            "contractions": structural_prefs.get("contractions", "Moderate"),
            "mission_statement": row["mission_statement"] or "",
            "key_differentiators": format_list_to_multiline(key_differentiators),
            "competitor_names": format_list_to_multiline(competitor_names),
        }


async def get_user_brand_context(user_id: int) -> Optional[dict]:
    """
    Get user's brand context for use in content generation.
    Returns None if not configured.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT company_name, industry, brand_voice_json,
                   mission_statement, key_differentiators, competitor_names
            FROM brand_contexts
            WHERE user_id = ?
            """,
            (user_id,)
        )
        row = await cursor.fetchone()

        if not row:
            return None

        brand_voice_data = json.loads(row["brand_voice_json"]) if row["brand_voice_json"] else {}
        key_differentiators = json.loads(row["key_differentiators"]) if row["key_differentiators"] else []
        competitor_names = json.loads(row["competitor_names"]) if row["competitor_names"] else []

        return {
            "company_name": row["company_name"] or "",
            "industry": row["industry"] or "",
            "brand_voice": brand_voice_data,
            "mission_statement": row["mission_statement"],
            "key_differentiators": key_differentiators,
            "competitor_names": competitor_names,
        }


def format_brand_voice_for_prompt(brand_context: dict, content_type: str) -> str:
    """
    Format brand voice context for injection into prompts.

    Args:
        brand_context: The user's brand context dict
        content_type: The type of content being generated (linkedin, blog, email)

    Returns:
        Formatted string for prompt injection, or empty string if no brand voice.
    """
    if not brand_context:
        return ""

    brand_voice = brand_context.get("brand_voice", {})
    tone_by_format = brand_voice.get("tone_by_format", {})
    structural_prefs = brand_voice.get("structural_preferences", {})

    # Get tone for this content type
    tone = tone_by_format.get(content_type, "Professional")

    # Format core principles
    principles = brand_voice.get("core_principles", [])
    principles_text = "\n".join(f"  - {p}" for p in principles) if principles else "  (Not specified)"

    # Format mission phrases
    mission_phrases = brand_voice.get("mission_phrases", [])
    mission_text = "\n".join(f"  - \"{p}\"" for p in mission_phrases) if mission_phrases else "  (Not specified)"

    # Format red flags
    red_flags = brand_voice.get("red_flags", [])
    red_flags_text = "\n".join(f"  - {r}" for r in red_flags) if red_flags else "  (Not specified)"

    # Format competitor names if present
    competitors = brand_context.get("competitor_names", [])
    competitors_text = ", ".join(competitors) if competitors else None

    # Build the brand voice section
    sections = [
        "=" * 50,
        "BRAND VOICE GUIDELINES",
        "=" * 50,
        "",
        f"Company: {brand_context.get('company_name', 'N/A')}",
        f"Industry: {brand_context.get('industry', 'N/A')}",
        "",
        f"TONE FOR THIS {content_type.upper()} CONTENT:",
        f"  {tone}",
        "",
        "CORE BRAND PRINCIPLES:",
        principles_text,
        "",
        "MISSION PHRASES TO USE (incorporate naturally when relevant):",
        mission_text,
        "",
        "RED FLAGS - NEVER DO THESE:",
        red_flags_text,
    ]

    if competitors_text:
        sections.extend([
            "",
            "COMPETITORS (never mention by name):",
            f"  {competitors_text}",
        ])

    sections.extend([
        "",
        "STRUCTURAL PREFERENCES:",
        f"  - Paragraph length: {structural_prefs.get('paragraph_length', '2-4 sentences')}",
        f"  - Contractions usage: {structural_prefs.get('contractions', 'Moderate')}",
    ])

    if content_type == "linkedin":
        sections.append(f"  - LinkedIn structure: {structural_prefs.get('linkedin_structure', 'Hook → Bullets → CTA')}")

    if brand_context.get("mission_statement"):
        sections.extend([
            "",
            "COMPANY MISSION:",
            f"  {brand_context['mission_statement']}",
        ])

    sections.extend([
        "",
        "=" * 50,
        "",
    ])

    return "\n".join(sections)
