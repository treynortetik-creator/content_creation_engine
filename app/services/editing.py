"""Content editing service - Step 2 of the pipeline."""
import json
from typing import Tuple, Union
import google.generativeai as genai

from app.config import get_settings, calculate_cost
from app.services.prompt_manager import get_rendered_prompt
from app.services.persona_manager import get_persona
from app.utils.retry import retry_async, gemini_circuit_breaker

settings = get_settings()


async def edit_for_audience(
    draft_content: str,
    target_persona: Union[str, dict],
    content_type: str = "linkedin",
) -> Tuple[dict, float]:
    """
    Edit content for target audience - simplify jargon, check guardrails, improve flow.

    Args:
        draft_content: The draft content to edit
        target_persona: Either a persona ID string (legacy) or a combined persona dict
        content_type: Type of content (linkedin, blog, email)

    Returns (edited_result, cost) tuple.
    """
    # Handle both legacy (persona_id string) and new format (combined persona dict)
    if isinstance(target_persona, str):
        persona = await get_persona(target_persona)
        if not persona:
            raise ValueError(f"Persona not found: {target_persona}")
        persona_title = persona["title"]
        language_level = persona.get("language_level", "Professional")
        priorities = persona.get("priorities", [])
    else:
        persona_title = target_persona.get("title", "Target Audience")
        language_level = "Professional"  # Default for combined
        priorities = target_persona.get("priorities", [])
        # Add custom descriptions context if present
        descriptions = target_persona.get("descriptions", [])
        if descriptions:
            persona_title += f" ({', '.join(descriptions[:1])}...)" if len(descriptions) > 1 else f" ({descriptions[0]})"

    variables = {
        "persona_title": persona_title,
        "persona_language_level": language_level,
        "persona_priorities": ", ".join(priorities) if priorities else "Not specified",
        "draft_from_step1": draft_content,
    }

    prompt, config = await get_rendered_prompt("audience_edit", variables)

    # Add output instruction
    full_prompt = prompt + f"""

CONTENT TYPE: {content_type}

OUTPUT FORMAT (valid JSON):
{{
  "edited_content": "The fully edited content",
  "changes_made": [
    {{"type": "simplification|flow|tone|guardrail", "description": "What was changed"}}
  ],
  "warnings": ["Any warnings about content that needs attention"],
  "guardrails_status": {{
    "active_voice": true,
    "empowerment_close": true,
    "no_competitors": true,
    "appropriate_tone": true,
    "citations_flagged": true
  }},
  "citations_needed": ["List of claims that need citation/verification"]
}}"""

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(config["model"])

    async def do_edit():
        return model.generate_content(
            full_prompt,
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=config["max_tokens"],
            )
        )

    # Use retry logic for API call
    response = await retry_async(
        do_edit,
        max_retries=3,
        base_delay=2.0,
        context="edit_for_audience",
    )

    try:
        result = json.loads(response.text)
    except json.JSONDecodeError:
        text = response.text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
        else:
            raise ValueError("Failed to parse editing response as JSON")

    # Calculate cost
    input_tokens = response.usage_metadata.prompt_token_count
    output_tokens = response.usage_metadata.candidates_token_count
    cost = calculate_cost(config["model"], input_tokens, output_tokens)

    return result, cost


async def batch_edit_content(
    drafts: list[dict],
    target_persona: Union[str, dict],
) -> Tuple[list[dict], float]:
    """
    Edit multiple drafts for audience.

    Args:
        drafts: List of draft content dictionaries
        target_persona: Either a persona ID string (legacy) or a combined persona dict

    Returns (edited_drafts, total_cost) tuple.
    """
    edited_results = []
    total_cost = 0.0

    for draft in drafts:
        content = draft.get("content", "")
        content_type = draft.get("content_type", "linkedin")

        if not content:
            edited_results.append(draft)
            continue

        result, cost = await edit_for_audience(content, target_persona, content_type)
        total_cost += cost

        edited_draft = {
            **draft,
            "step2_edited": result.get("edited_content", content),
            "changes_made": result.get("changes_made", []),
            "warnings": result.get("warnings", []),
            "guardrails_status": result.get("guardrails_status", {}),
            "citations_needed": result.get("citations_needed", []),
        }
        edited_results.append(edited_draft)

    return edited_results, total_cost
