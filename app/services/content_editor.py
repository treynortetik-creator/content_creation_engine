"""Content editor service for post-output tone adjustments and AI editing."""
from typing import Optional

from app.services.openrouter import call_openrouter_simple
from app.api.brand_voice import get_user_brand_context


# Tone presets with specific instructions
TONE_PRESETS = {
    "more_formal": (
        "Rewrite this to be more formal and professional. "
        "Use complete sentences, avoid contractions, and maintain a business-appropriate tone."
    ),
    "more_casual": (
        "Rewrite this to be more casual and conversational. "
        "Use contractions, shorter sentences, and a friendly tone like you're talking to a colleague."
    ),
    "more_punchy": (
        "Rewrite this to be more punchy and impactful. "
        "Use shorter sentences, stronger verbs, and cut any fluff. Make every word count."
    ),
    "more_authoritative": (
        "Rewrite this to be more authoritative and expert-sounding. "
        "Add confidence, use definitive language, and position the author as a thought leader."
    ),
    "more_empathetic": (
        "Rewrite this to be more empathetic and understanding. "
        "Acknowledge challenges, use inclusive language, and show you understand the reader's perspective."
    ),
    "more_urgent": (
        "Rewrite this to create more urgency. "
        "Add time-sensitive language, emphasize consequences of inaction, and make the reader feel they need to act now."
    ),
    "shorter": (
        "Rewrite this to be significantly shorter while keeping the core message. "
        "Cut redundancy, combine sentences, and remove anything non-essential."
    ),
    "longer": (
        "Expand this with more detail, examples, and context. "
        "Add supporting points and flesh out the ideas more fully."
    ),
    "simpler": (
        "Rewrite this using simpler language. "
        "Reduce jargon, use shorter words, and make it accessible to a general audience."
    ),
    "more_technical": (
        "Rewrite this with more technical depth. "
        "Add industry terminology, specific details, and assume the reader has domain expertise."
    ),
}


async def adjust_tone(
    content: str,
    content_type: str,
    tone_preset: Optional[str] = None,
    custom_instruction: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    """
    Adjust the tone of existing content.

    Args:
        content: The content to adjust
        content_type: Type of content (linkedin, blog, email, etc.)
        tone_preset: One of the TONE_PRESETS keys
        custom_instruction: Custom instruction from user
        user_id: User ID for fetching brand voice

    Returns:
        Dict with original, edited, instruction, and preset_used
    """
    if not tone_preset and not custom_instruction:
        raise ValueError("Must provide tone_preset or custom_instruction")

    # Build instruction
    if custom_instruction:
        instruction = custom_instruction
    else:
        instruction = TONE_PRESETS.get(tone_preset)
        if not instruction:
            raise ValueError(f"Unknown tone preset: {tone_preset}")

    # Get brand voice if available
    brand_context = ""
    if user_id:
        brand_voice = await get_user_brand_context(user_id)
        if brand_voice:
            tone_by_format = brand_voice.get("brand_voice_json", {}).get("tone_by_format", {})
            core_tone = tone_by_format.get(content_type, "professional")
            red_flags = brand_voice.get("brand_voice_json", {}).get("red_flags", [])
            company_name = brand_voice.get("company_name", "")

            brand_context = f"""
Maintain these brand voice guidelines while making adjustments:
- Company: {company_name}
- Core tone: {core_tone}
- Red flags to avoid: {', '.join(red_flags) if red_flags else 'None specified'}
"""

    prompt = f"""You are an expert content editor. Adjust the following {content_type} content according to the instruction.

INSTRUCTION:
{instruction}
{brand_context}
ORIGINAL CONTENT:
{content}

Return ONLY the edited content. Do not include any explanation, preamble, or meta-commentary. Just the edited content ready to use."""

    edited_content, cost = await call_openrouter_simple(
        task_name="editing",
        prompt=prompt,
        temperature=0.4,
        max_tokens=2000,
    )

    # Clean up any accidental quotes or meta text
    edited_content = edited_content.strip()
    if edited_content.startswith('"') and edited_content.endswith('"'):
        edited_content = edited_content[1:-1]

    return {
        "original": content,
        "edited": edited_content,
        "instruction": instruction,
        "preset_used": tone_preset,
        "cost": cost,
    }


async def apply_manual_edits(
    content: str,
    user_edits: str,
    edit_instruction: str,
    content_type: str,
) -> dict:
    """
    User made manual edits, AI polishes/integrates them.

    Args:
        content: Original AI-generated content
        user_edits: User's edited version
        edit_instruction: What the user wants done with their edits
        content_type: Type of content

    Returns:
        Dict with polished content and cost
    """
    prompt = f"""The user has edited some content and wants you to polish it.

ORIGINAL AI-GENERATED CONTENT:
{content}

USER'S EDITED VERSION:
{user_edits}

USER'S INSTRUCTION:
{edit_instruction}

Your job:
1. Incorporate the user's edits
2. Ensure the content flows naturally
3. Fix any grammar or awkward phrasing introduced
4. Maintain the overall structure unless the user changed it
5. Keep the user's voice and changes - don't revert to AI-style writing

Return ONLY the polished content. No explanation."""

    polished, cost = await call_openrouter_simple(
        task_name="editing",
        prompt=prompt,
        temperature=0.3,
        max_tokens=2000,
    )

    return {
        "polished": polished.strip(),
        "cost": cost,
    }


async def regenerate_section(
    full_content: str,
    section_to_change: str,
    instruction: str,
    content_type: str,
) -> dict:
    """
    Regenerate just one section of the content.

    Args:
        full_content: The complete content
        section_to_change: The specific section to change
        instruction: What to do with that section
        content_type: Type of content

    Returns:
        Dict with updated full content and cost
    """
    prompt = f"""Regenerate a specific section of this {content_type} content.

FULL CONTENT:
{full_content}

SECTION TO CHANGE:
{section_to_change}

INSTRUCTION FOR THIS SECTION:
{instruction}

Return the FULL content with the specified section changed according to the instruction. Keep everything else the same."""

    result, cost = await call_openrouter_simple(
        task_name="editing",
        prompt=prompt,
        temperature=0.5,
        max_tokens=2500,
    )

    return {
        "updated_content": result.strip(),
        "cost": cost,
    }


def get_tone_presets_list() -> list[dict]:
    """Get list of available tone presets with metadata."""
    return [
        {"id": "more_formal", "label": "More Formal", "description": "Professional, business-appropriate"},
        {"id": "more_casual", "label": "More Casual", "description": "Conversational, friendly"},
        {"id": "more_punchy", "label": "More Punchy", "description": "Impactful, concise"},
        {"id": "more_authoritative", "label": "More Authoritative", "description": "Expert, confident"},
        {"id": "more_empathetic", "label": "More Empathetic", "description": "Understanding, inclusive"},
        {"id": "more_urgent", "label": "More Urgent", "description": "Time-sensitive, action-oriented"},
        {"id": "shorter", "label": "Shorter", "description": "Cut length, keep message"},
        {"id": "longer", "label": "Longer", "description": "Expand with detail"},
        {"id": "simpler", "label": "Simpler", "description": "Accessible, less jargon"},
        {"id": "more_technical", "label": "More Technical", "description": "Industry depth, expertise"},
    ]
