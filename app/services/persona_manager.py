"""Persona management service."""
import json
import aiofiles
from pathlib import Path
from typing import Optional

from app.config import get_settings

settings = get_settings()

# Cache for personas
_personas_cache: Optional[dict] = None


async def load_personas() -> dict:
    """Load personas from the JSON file."""
    global _personas_cache

    if _personas_cache is not None:
        return _personas_cache

    personas_file = settings.data_dir / "personas.json"

    if not personas_file.exists():
        # Return default personas if file doesn't exist
        _personas_cache = get_default_personas()
        # Save to file for future edits
        await save_personas(_personas_cache)
        return _personas_cache

    async with aiofiles.open(personas_file, "r") as f:
        content = await f.read()
        _personas_cache = json.loads(content)

    return _personas_cache


async def save_personas(personas_data: dict):
    """Save personas to the JSON file."""
    global _personas_cache

    personas_file = settings.data_dir / "personas.json"
    personas_file.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(personas_file, "w") as f:
        await f.write(json.dumps(personas_data, indent=2))

    _personas_cache = personas_data


async def get_persona(persona_id: str) -> Optional[dict]:
    """Get a specific persona by ID."""
    personas = await load_personas()

    for persona in personas.get("personas", []):
        if persona.get("id") == persona_id:
            return persona

    return None


def create_custom_persona(description: str) -> dict:
    """Create a persona from a custom description."""
    return {
        "id": "custom",
        "title": "Custom Target Audience",
        "description": description,
        "pain_points": [],  # Will be inferred by AI from description
        "priorities": [],
        "language_level": "Professional",
        "content_preferences": {
            "length": "Medium",
            "data_density": "Moderate",
            "tone": "Professional"
        }
    }


async def resolve_personas(personas_list: list) -> list[dict]:
    """
    Resolve a list of persona references to full persona objects.

    Input format:
    [
        {"type": "preset", "id": "ceo_longterm_care"},
        {"type": "custom", "description": "HR Directors at..."}
    ]

    Returns list of full persona dictionaries.
    """
    resolved = []

    for item in personas_list:
        if item.get("type") == "preset":
            # Look up preset persona by ID
            persona = await get_persona(item.get("id"))
            if persona:
                resolved.append(persona)
        elif item.get("type") == "custom":
            # Create custom persona from description
            description = item.get("description", "")
            if description:
                custom = create_custom_persona(description)
                resolved.append(custom)

    return resolved


def combine_personas_for_prompt(personas: list[dict]) -> dict:
    """
    Combine multiple personas into a single context for the AI prompt.

    Returns a dict with combined information for use in prompts.
    """
    if not personas:
        return {
            "title": "General Audience",
            "pain_points": [],
            "priorities": [],
            "descriptions": []
        }

    if len(personas) == 1:
        persona = personas[0]
        return {
            "title": persona.get("title", "Target Audience"),
            "pain_points": persona.get("pain_points", []),
            "priorities": persona.get("priorities", []),
            "descriptions": [persona.get("description", "")] if persona.get("description") else []
        }

    # Multiple personas - combine them
    titles = [p.get("title", "") for p in personas if p.get("title")]
    all_pain_points = []
    all_priorities = []
    descriptions = []

    for p in personas:
        all_pain_points.extend(p.get("pain_points", []))
        all_priorities.extend(p.get("priorities", []))
        if p.get("description"):
            descriptions.append(p.get("description"))

    # Deduplicate while preserving order
    seen_pain = set()
    unique_pain = []
    for pp in all_pain_points:
        if pp.lower() not in seen_pain:
            seen_pain.add(pp.lower())
            unique_pain.append(pp)

    seen_pri = set()
    unique_pri = []
    for pr in all_priorities:
        if pr.lower() not in seen_pri:
            seen_pri.add(pr.lower())
            unique_pri.append(pr)

    return {
        "title": " & ".join(titles) if titles else "Multiple Target Audiences",
        "pain_points": unique_pain,
        "priorities": unique_pri,
        "descriptions": descriptions
    }


async def list_personas() -> list[dict]:
    """List all available personas."""
    personas = await load_personas()
    return personas.get("personas", [])


def get_default_personas() -> dict:
    """Return the default hardcoded personas for MVP."""
    return {
        "personas": [
            {
                "id": "ceo_longterm_care",
                "title": "CEO of Long-Term Care Facility",
                "company_size": "1000+ beds",
                "pain_points": [
                    "Staffing shortages",
                    "Resident retention",
                    "Regulatory compliance",
                    "Profitability pressure"
                ],
                "language_level": "Executive - high-level ROI focus",
                "priorities": [
                    "Bottom line impact",
                    "Competitive advantage",
                    "Operational efficiency"
                ],
                "content_preferences": {
                    "length": "Short - time-constrained",
                    "data_density": "High - wants metrics immediately",
                    "tone": "Confident and outcome-focused"
                }
            },
            {
                "id": "don_memory_care",
                "title": "Director of Nursing (Memory Care)",
                "company_size": "100-300 beds",
                "pain_points": [
                    "Resident safety",
                    "Staff training and retention",
                    "Family communication",
                    "Care quality documentation"
                ],
                "language_level": "Professional - clinical but accessible",
                "priorities": [
                    "Resident outcomes",
                    "Staff empowerment",
                    "Family trust"
                ],
                "content_preferences": {
                    "length": "Medium - wants actionable detail",
                    "data_density": "Moderate - clinical evidence appreciated",
                    "tone": "Empathetic and supportive"
                }
            },
            {
                "id": "marketing_director",
                "title": "Marketing Director (Senior Living)",
                "company_size": "500+ beds across multiple communities",
                "pain_points": [
                    "Lead generation",
                    "Brand differentiation",
                    "Content production at scale",
                    "ROI measurement"
                ],
                "language_level": "Professional - marketing-savvy",
                "priorities": [
                    "Conversion rates",
                    "Content velocity",
                    "Brand consistency"
                ],
                "content_preferences": {
                    "length": "Long - wants comprehensive insight",
                    "data_density": "Moderate - case studies over raw stats",
                    "tone": "Creative but strategic"
                }
            }
        ]
    }


def invalidate_cache():
    """Invalidate the personas cache to force reload."""
    global _personas_cache
    _personas_cache = None
