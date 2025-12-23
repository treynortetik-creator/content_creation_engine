"""Hook variation generator for LinkedIn posts."""
import json
from typing import Tuple

from app.config import get_settings, calculate_cost
from app.services import settings_manager

settings = get_settings()

# Try to import Google Generative AI for Gemini
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False


def split_hook_and_body(post_content: str) -> Tuple[str, str]:
    """
    Split a LinkedIn post into hook (first line/paragraph) and body.

    Returns (hook, body) tuple.
    """
    # Try to split at first double newline
    if '\n\n' in post_content:
        parts = post_content.split('\n\n', 1)
        return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""

    # Try single newline
    if '\n' in post_content:
        parts = post_content.split('\n', 1)
        return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""

    # No clear split - use first sentence
    sentences = post_content.split('. ', 1)
    if len(sentences) > 1:
        return sentences[0].strip() + '.', sentences[1].strip()

    return post_content, ""


async def generate_hook_variations(
    atoms: list[dict],
    post_body: str,
    persona: dict,
    brand_context: dict = None,
    count: int = 4,
) -> Tuple[list[str], float]:
    """
    Generate hook variations for a LinkedIn post.

    Args:
        atoms: Content atoms used in the post
        post_body: The body of the post (everything after the hook)
        persona: Target persona dict
        brand_context: Optional brand voice context
        count: Number of additional hooks to generate (default 4)

    Returns (list of hook strings, cost) tuple.
    """
    # Format atoms for prompt
    atoms_text = "\n".join([
        f"- {a['content'][:200]}"  # Limit atom length
        for a in atoms[:5]
    ])

    # Get persona info
    persona_title = persona.get("title", "Target Audience")
    persona_priorities = ", ".join(persona.get("priorities", [])[:3])

    # Get brand voice tone if available
    tone = "Professional but engaging"
    if brand_context:
        brand_voice = brand_context.get("brand_voice", {})
        tone_by_format = brand_voice.get("tone_by_format", {})
        tone = tone_by_format.get("linkedin", tone)

    prompt = f"""Generate {count} different hooks (opening lines) for a LinkedIn post.

CONTENT ATOMS TO REFERENCE:
{atoms_text}

POST BODY (this stays the same for all variations):
{post_body[:500]}...

TARGET AUDIENCE: {persona_title}
AUDIENCE PRIORITIES: {persona_priorities}

BRAND TONE: {tone}

Generate {count} hooks using these different angles:
1. QUESTION - Start with a thought-provoking question
2. STAT/DATA - Lead with a surprising statistic or number
3. STORY - Start with a personal observation or story hook
4. BOLD CLAIM - Make a provocative or contrarian statement

Each hook should:
- Be 1-2 sentences max
- Grab attention in the first 2 seconds
- Connect naturally to the post body
- Match the brand tone
- Reference content from the atoms when relevant

OUTPUT FORMAT (valid JSON array of {count} strings):
["Hook 1 text here...", "Hook 2 text here...", "Hook 3 text here...", "Hook 4 text here..."]

Output ONLY the JSON array, nothing else.
"""

    # Use Gemini for speed/cost if available, otherwise Claude
    if GEMINI_AVAILABLE and settings.gemini_api_key:
        try:
            genai.configure(api_key=settings.gemini_api_key)
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(prompt)
            response_text = response.text

            # Estimate tokens for cost calculation (rough estimate)
            input_tokens = len(prompt) // 4
            output_tokens = len(response_text) // 4
            cost = calculate_cost("gemini-1.5-flash", input_tokens, output_tokens)

        except Exception as e:
            print(f"Gemini hook generation failed, falling back to Claude: {e}")
            # Fall through to Claude
            GEMINI_AVAILABLE = False

    if not GEMINI_AVAILABLE or not settings.gemini_api_key:
        # Use Claude via the existing call_llm infrastructure
        from app.services.drafting import call_llm

        admin_model = settings_manager.get_model_for_step("drafting")
        model = admin_model or "claude-sonnet-4-20250514"

        response_text, input_tokens, output_tokens, actual_model = await call_llm(
            prompt, model, max_tokens=600
        )
        cost = calculate_cost(actual_model, input_tokens, output_tokens)

    # Parse response
    try:
        hooks = json.loads(response_text)
        if isinstance(hooks, list):
            return hooks[:count], cost
    except json.JSONDecodeError:
        # Try to extract JSON array
        start = response_text.find('[')
        end = response_text.rfind(']') + 1
        if start >= 0 and end > start:
            try:
                hooks = json.loads(response_text[start:end])
                if isinstance(hooks, list):
                    return hooks[:count], cost
            except json.JSONDecodeError:
                pass

    # Fallback: return empty list
    print(f"Failed to parse hooks: {response_text[:200]}")
    return [], cost


def combine_hooks_with_body(hooks: list[str], body: str) -> list[str]:
    """
    Combine each hook with the post body to create full variations.

    Args:
        hooks: List of hook strings
        body: The post body

    Returns:
        List of full post texts (hook + body)
    """
    variations = []
    for hook in hooks:
        if body:
            full_post = f"{hook}\n\n{body}"
        else:
            full_post = hook
        variations.append(full_post)
    return variations
