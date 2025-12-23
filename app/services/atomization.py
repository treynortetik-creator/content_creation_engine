"""Content atomization service - Step 0 of the pipeline."""
import json
import uuid
from typing import Tuple, Union
import google.generativeai as genai

from app.config import get_settings, calculate_cost
from app.services.prompt_manager import get_rendered_prompt
from app.services.persona_manager import get_persona
from app.utils.retry import retry_async, gemini_circuit_breaker

settings = get_settings()


async def atomize_content(
    cleaned_transcript: str,
    target_persona: Union[str, dict],
    job_id: str,
    user_id: int,
) -> Tuple[list[dict], float]:
    """
    Extract reusable content atoms from transcript.

    Args:
        cleaned_transcript: The content to atomize
        target_persona: Either a persona ID string (legacy) or a combined persona dict
                       with keys: title, pain_points, priorities, descriptions
        job_id: The job ID for tracking
        user_id: The user ID for tracking

    Atoms are categorized as: data, insight, story, problem, solution.
    Each atom is scored for relevance to the target persona.

    Returns (atoms_list, cost) tuple.
    """
    # Handle both legacy (persona_id string) and new format (combined persona dict)
    if isinstance(target_persona, str):
        # Legacy format: look up persona by ID
        persona = await get_persona(target_persona)
        if not persona:
            raise ValueError(f"Persona not found: {target_persona}")
        persona_title = persona["title"]
        pain_points = persona.get("pain_points", [])
        priorities = persona.get("priorities", [])
        descriptions = []
        persona_key = target_persona
    else:
        # New format: combined persona dict
        persona_title = target_persona.get("title", "Target Audience")
        pain_points = target_persona.get("pain_points", [])
        priorities = target_persona.get("priorities", [])
        descriptions = target_persona.get("descriptions", [])
        persona_key = "combined"

    # Build pain points and priorities strings
    pain_points_str = ", ".join(pain_points) if pain_points else "Not specified"
    priorities_str = ", ".join(priorities) if priorities else "Not specified"

    # Add custom descriptions if present
    extra_context = ""
    if descriptions:
        extra_context = "\n\nADDITIONAL AUDIENCE CONTEXT:\n" + "\n".join(f"- {d}" for d in descriptions)

    # Get and render the atomization prompt
    variables = {
        "target_persona_title": persona_title,
        "persona_pain_points": pain_points_str + extra_context,
        "persona_priorities": priorities_str,
        "cleaned_transcript": cleaned_transcript,
    }

    prompt, config = await get_rendered_prompt("atomization", variables)

    # Call Gemini
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(config["model"])

    # Add JSON output instruction
    full_prompt = prompt + """

OUTPUT FORMAT:
Return valid JSON with this structure:
{
  "atoms": [
    {
      "type": "data|insight|story|problem|solution",
      "content": "The actual content extracted",
      "source_location": "timestamp or section reference",
      "relevance_to_persona": 1-5,
      "why_relevant": "Brief explanation",
      "tags": ["tag1", "tag2"]
    }
  ],
  "summary": "Brief summary of what was extracted",
  "recommended_distribution": {
    "linkedin": ["atom indexes best for LinkedIn"],
    "blog": ["atom indexes best for blog"],
    "email": ["atom indexes best for email"]
  }
}"""

    async def do_atomize():
        return model.generate_content(
            full_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=config["max_tokens"],
            )
        )

    # Use retry logic for API call
    response = await retry_async(
        do_atomize,
        max_retries=3,
        base_delay=2.0,
        job_id=job_id,
        user_id=user_id,
        context="atomize_content",
    )

    # Parse response
    try:
        result = json.loads(response.text)
    except json.JSONDecodeError:
        # Try to extract JSON from response
        text = response.text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
        else:
            raise ValueError("Failed to parse atomization response as JSON")

    # Process atoms
    atoms = []
    for atom_data in result.get("atoms", []):
        atom = {
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "user_id": user_id,
            "atom_type": atom_data.get("type", "insight"),
            "content": atom_data.get("content", ""),
            "source_location": atom_data.get("source_location"),
            "tags": atom_data.get("tags", []),
            "persona_relevance": {
                persona_key: atom_data.get("relevance_to_persona", 3)
            },
            "why_relevant": atom_data.get("why_relevant"),
        }
        atoms.append(atom)

    # Calculate cost
    input_tokens = response.usage_metadata.prompt_token_count
    output_tokens = response.usage_metadata.candidates_token_count
    cost = calculate_cost(config["model"], input_tokens, output_tokens)

    return atoms, cost


def select_atoms_for_content_type(
    atoms: list[dict],
    content_type: str,
    persona_id: str,
    count: int = 5,
) -> list[dict]:
    """
    Select the best atoms for a specific content type.

    Prioritizes by persona relevance and atom type appropriateness.
    """
    # Type preferences by content type
    type_preferences = {
        "linkedin": ["data", "insight", "story"],
        "blog": ["problem", "insight", "solution", "data", "story"],
        "email": ["problem", "solution", "data"],
    }

    preferred_types = type_preferences.get(content_type, ["insight", "data"])

    # Score atoms
    scored_atoms = []
    for atom in atoms:
        score = 0

        # Persona relevance (0-5)
        relevance = atom.get("persona_relevance", {}).get(persona_id, 3)
        score += relevance * 2

        # Type preference bonus
        atom_type = atom.get("atom_type", "insight")
        if atom_type in preferred_types:
            score += (len(preferred_types) - preferred_types.index(atom_type))

        scored_atoms.append((score, atom))

    # Sort by score descending and return top N
    scored_atoms.sort(key=lambda x: x[0], reverse=True)

    return [atom for _, atom in scored_atoms[:count]]


def group_atoms_by_type(atoms: list[dict]) -> dict[str, list[dict]]:
    """Group atoms by their type for easier access in prompts."""
    grouped = {
        "data": [],
        "insight": [],
        "story": [],
        "problem": [],
        "solution": [],
    }

    for atom in atoms:
        atom_type = atom.get("atom_type", "insight")
        if atom_type in grouped:
            grouped[atom_type].append(atom)

    return grouped
