"""Content drafting service - Step 1 of the pipeline."""
import json
import os
from typing import Tuple, Union
import anthropic

from app.config import get_settings, calculate_cost
from app.services.prompt_manager import get_rendered_prompt
from app.services.persona_manager import get_persona
from app.services.atomization import select_atoms_for_content_type, group_atoms_by_type
from app.utils.retry import retry_async, claude_circuit_breaker
from app.services import settings_manager
from app.api.brand_voice import get_user_brand_context, format_brand_voice_for_prompt

settings = get_settings()

# Try to import openai for OpenRouter support
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


def use_openrouter() -> bool:
    """Check if we should use OpenRouter instead of Anthropic."""
    # Check settings_manager first (admin-configured toggle)
    if settings_manager.is_openrouter_enabled():
        api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
        return bool(api_key)
    # Fall back to config setting
    if settings.use_openrouter:
        api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
        return bool(api_key)
    # Also auto-detect: if no Anthropic key but OpenRouter key exists
    anthropic_key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    openrouter_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    return not anthropic_key and bool(openrouter_key)


def get_anthropic_client():
    """Get Anthropic client."""
    api_key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=api_key)


def get_openrouter_client():
    """Get OpenRouter client (OpenAI-compatible)."""
    if not OPENAI_AVAILABLE:
        raise ImportError("openai package required for OpenRouter. Run: pip install openai")

    api_key = settings.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not configured")

    return openai.OpenAI(
        api_key=api_key,
        base_url=settings.openrouter_base_url,
    )


async def call_llm(prompt: str, model: str, max_tokens: int) -> Tuple[str, int, int, str]:
    """
    Call LLM (Anthropic or OpenRouter) and return response.

    Returns (response_text, input_tokens, output_tokens, actual_model_used)
    """
    # Override model with admin settings if available
    admin_model = settings_manager.get_model_for_step("drafting")
    if admin_model:
        model = admin_model

    if use_openrouter():
        # Use OpenRouter (OpenAI-compatible API)
        client = get_openrouter_client()

        # Map Anthropic model names to OpenRouter format
        openrouter_model = model
        if "claude" in model.lower():
            # OpenRouter uses format like "anthropic/claude-3.5-sonnet"
            if "opus-4" in model or "opus-4-5" in model:
                openrouter_model = "anthropic/claude-sonnet-4"  # closest available
            elif "sonnet" in model:
                openrouter_model = "anthropic/claude-3.5-sonnet"
            else:
                openrouter_model = "anthropic/claude-3.5-sonnet"

        async def do_call():
            return client.chat.completions.create(
                model=openrouter_model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                extra_headers={
                    "HTTP-Referer": "https://contentmultiplier.com",
                    "X-Title": "ContentMultiplier",
                }
            )

        response = await retry_async(
            do_call,
            max_retries=3,
            base_delay=2.0,
            context="openrouter_call",
        )

        response_text = response.choices[0].message.content
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        return response_text, input_tokens, output_tokens, openrouter_model

    else:
        # Use Anthropic directly
        client = get_anthropic_client()

        async def do_call():
            return client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )

        message = await retry_async(
            do_call,
            max_retries=3,
            base_delay=2.0,
            context="anthropic_call",
        )

        response_text = message.content[0].text
        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens

        return response_text, input_tokens, output_tokens, model


async def draft_linkedin_posts(
    atoms: list[dict],
    target_persona: Union[str, dict],
    count: int = 3,
    user_id: int = None,
) -> Tuple[list[dict], float]:
    """
    Generate LinkedIn post drafts using Claude.

    Args:
        atoms: List of content atoms to use
        target_persona: Either a persona ID string (legacy) or a combined persona dict
        count: Number of variations to generate
        user_id: User ID for fetching brand voice context

    Returns (drafts_list, cost) tuple.
    """
    # Handle both legacy (persona_id string) and new format (combined persona dict)
    if isinstance(target_persona, str):
        persona = await get_persona(target_persona)
        if not persona:
            raise ValueError(f"Persona not found: {target_persona}")
        persona_title = persona["title"]
        pain_points = persona.get("pain_points", [])
        priorities = persona.get("priorities", [])
        descriptions = []
        persona_key = target_persona
    else:
        persona_title = target_persona.get("title", "Target Audience")
        pain_points = target_persona.get("pain_points", [])
        priorities = target_persona.get("priorities", [])
        descriptions = target_persona.get("descriptions", [])
        persona_key = "combined"

    # Select best atoms for LinkedIn
    selected_atoms = select_atoms_for_content_type(atoms, "linkedin", persona_key, count=count * 2)

    # Format atoms for prompt
    atoms_text = "\n".join([
        f"- [{a['atom_type'].upper()}] {a['content']}"
        for a in selected_atoms
    ])

    # Build pain points and priorities with custom descriptions
    pain_points_str = ", ".join(pain_points) if pain_points else "Not specified"
    priorities_str = ", ".join(priorities) if priorities else "Not specified"
    if descriptions:
        pain_points_str += "\n\nAdditional context: " + " | ".join(descriptions)

    variables = {
        "selected_atoms_for_linkedin": atoms_text,
        "persona_title": persona_title,
        "persona_priorities": priorities_str,
        "persona_pain_points": pain_points_str,
    }

    prompt, config = await get_rendered_prompt("linkedin_draft", variables)

    # Fetch and inject brand voice if user_id provided
    brand_voice_section = ""
    if user_id:
        brand_context = await get_user_brand_context(user_id)
        if brand_context:
            brand_voice_section = format_brand_voice_for_prompt(brand_context, "linkedin")

    # Add JSON output instruction
    full_prompt = brand_voice_section + prompt + f"""

Generate exactly {count} LinkedIn post variations.

OUTPUT FORMAT (valid JSON):
{{
  "posts": [
    {{
      "variation": 1,
      "hook_type": "question|stat|problem|benefit",
      "content": "Full post text here...",
      "atoms_used": ["atom content snippets used"],
      "cta": "Call to action text"
    }}
  ]
}}"""

    # Call LLM (supports both Anthropic and OpenRouter)
    response_text, input_tokens, output_tokens, actual_model = await call_llm(
        full_prompt, config["model"], config["max_tokens"]
    )

    # Parse response
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        # Try to extract JSON
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(response_text[start:end])
        else:
            raise ValueError("Failed to parse LinkedIn drafts as JSON")

    drafts = result.get("posts", [])

    # Calculate cost using actual model invoked
    cost = calculate_cost(actual_model, input_tokens, output_tokens)

    return drafts, cost


async def draft_blog_post(
    atoms: list[dict],
    target_persona: Union[str, dict],
    user_id: int = None,
) -> Tuple[dict, float]:
    """
    Generate a blog post draft using Claude.

    Args:
        atoms: List of content atoms to use
        target_persona: Either a persona ID string (legacy) or a combined persona dict
        user_id: User ID for fetching brand voice context

    Returns (draft, cost) tuple.
    """
    # Handle both legacy (persona_id string) and new format (combined persona dict)
    if isinstance(target_persona, str):
        persona = await get_persona(target_persona)
        if not persona:
            raise ValueError(f"Persona not found: {target_persona}")
        persona_title = persona["title"]
    else:
        persona_title = target_persona.get("title", "Target Audience")
        # Add custom descriptions context if present
        descriptions = target_persona.get("descriptions", [])
        if descriptions:
            persona_title += f" ({', '.join(descriptions[:2])})"

    # Group atoms by type
    grouped = group_atoms_by_type(atoms)

    # Format atoms for each category
    def format_atoms(atom_list):
        if not atom_list:
            return "None available"
        return "\n".join([f"- {a['content']}" for a in atom_list[:5]])

    variables = {
        "problem_atoms": format_atoms(grouped["problem"]),
        "insight_atoms": format_atoms(grouped["insight"]),
        "solution_atoms": format_atoms(grouped["solution"]),
        "data_atoms": format_atoms(grouped["data"]),
        "story_atoms": format_atoms(grouped["story"]),
        "persona_title": persona_title,
    }

    prompt, config = await get_rendered_prompt("blog_draft", variables)

    # Fetch and inject brand voice if user_id provided
    brand_voice_section = ""
    if user_id:
        brand_context = await get_user_brand_context(user_id)
        if brand_context:
            brand_voice_section = format_brand_voice_for_prompt(brand_context, "blog")

    # Add JSON output instruction
    full_prompt = brand_voice_section + prompt + """

OUTPUT FORMAT (valid JSON):
{
  "title": "Blog post title",
  "content": "Full blog post content with markdown formatting (## for H2 headings)",
  "atoms_used": ["atom content snippets used"],
  "word_count": 0,
  "sections": ["Section 1 title", "Section 2 title", "Section 3 title"]
}"""

    # Call LLM (supports both Anthropic and OpenRouter)
    response_text, input_tokens, output_tokens, actual_model = await call_llm(
        full_prompt, config["model"], config["max_tokens"]
    )

    # Parse response
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(response_text[start:end])
        else:
            raise ValueError("Failed to parse blog draft as JSON")

    # Calculate cost using actual model invoked
    cost = calculate_cost(actual_model, input_tokens, output_tokens)

    return result, cost


async def draft_email(
    atoms: list[dict],
    target_persona: Union[str, dict],
    user_id: int = None,
) -> Tuple[dict, float]:
    """
    Generate an email draft using Claude.

    Args:
        atoms: List of content atoms to use
        target_persona: Either a persona ID string (legacy) or a combined persona dict
        user_id: User ID for fetching brand voice context

    Returns (draft, cost) tuple.
    """
    # Handle both legacy (persona_id string) and new format (combined persona dict)
    if isinstance(target_persona, str):
        persona = await get_persona(target_persona)
        if not persona:
            raise ValueError(f"Persona not found: {target_persona}")
        persona_title = persona["title"]
        pain_points = persona.get("pain_points", [])
        priorities = persona.get("priorities", [])
        descriptions = []
        persona_key = target_persona
    else:
        persona_title = target_persona.get("title", "Target Audience")
        pain_points = target_persona.get("pain_points", [])
        priorities = target_persona.get("priorities", [])
        descriptions = target_persona.get("descriptions", [])
        persona_key = "combined"

    # Select atoms for email
    selected_atoms = select_atoms_for_content_type(atoms, "email", persona_key, count=4)

    atoms_text = "\n".join([
        f"- [{a['atom_type'].upper()}] {a['content']}"
        for a in selected_atoms
    ])

    # Build pain points and priorities with custom descriptions
    pain_points_str = ", ".join(pain_points) if pain_points else "Not specified"
    priorities_str = ", ".join(priorities) if priorities else "Not specified"
    if descriptions:
        pain_points_str += "\n\nAdditional context: " + " | ".join(descriptions)

    variables = {
        "selected_atoms": atoms_text,
        "persona_title": persona_title,
        "persona_priorities": priorities_str,
        "persona_pain_points": pain_points_str,
    }

    prompt, config = await get_rendered_prompt("email_draft", variables)

    # Fetch and inject brand voice if user_id provided
    brand_voice_section = ""
    if user_id:
        brand_context = await get_user_brand_context(user_id)
        if brand_context:
            brand_voice_section = format_brand_voice_for_prompt(brand_context, "email")

    # Add output instruction
    full_prompt = brand_voice_section + prompt + """

OUTPUT FORMAT (valid JSON):
{
  "subject": "Email subject line",
  "preview_text": "Email preview text (first line)",
  "body": "Full email body",
  "cta": "Call to action",
  "atoms_used": ["atom content snippets used"]
}"""

    # Call LLM (supports both Anthropic and OpenRouter)
    response_text, input_tokens, output_tokens, actual_model = await call_llm(
        full_prompt, config["model"], config["max_tokens"]
    )

    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(response_text[start:end])
        else:
            raise ValueError("Failed to parse email draft as JSON")

    # Calculate cost using actual model invoked
    cost = calculate_cost(actual_model, input_tokens, output_tokens)

    return result, cost
