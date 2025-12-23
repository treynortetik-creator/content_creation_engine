"""Image prompt generation service for external AI image tools."""
import json
from typing import Optional

from app.services.openrouter import call_openrouter_simple


# Image format specifications for different platforms/purposes
IMAGE_STYLES = {
    "linkedin_header": {
        "aspect": "1200x627 (1.91:1)",
        "style_hints": "Professional, clean, corporate-friendly, minimal text overlay space",
        "avoid": "Busy backgrounds, too many elements, unprofessional imagery"
    },
    "linkedin_post": {
        "aspect": "1200x1200 (1:1) or 1200x627",
        "style_hints": "Eye-catching, scroll-stopping, professional but engaging",
        "avoid": "Stock photo clichés, generic business imagery"
    },
    "blog_hero": {
        "aspect": "1200x630 or 16:9",
        "style_hints": "Editorial quality, supports the headline, draws readers in",
        "avoid": "Cluttered compositions, text-heavy images"
    },
    "blog_inline": {
        "aspect": "Flexible, usually 16:9 or 4:3",
        "style_hints": "Illustrative, supports the content point, can be more conceptual",
        "avoid": "Distracting from the text"
    },
    "email_header": {
        "aspect": "600x200 or 600x300",
        "style_hints": "Simple, fast-loading, brand-consistent",
        "avoid": "Complex details that get lost at small sizes"
    },
    "social_square": {
        "aspect": "1080x1080 (1:1)",
        "style_hints": "Bold, attention-grabbing, works as thumbnail",
        "avoid": "Fine details, small text"
    },
    "twitter_card": {
        "aspect": "1200x628",
        "style_hints": "Clean, impactful, works with text overlay",
        "avoid": "Busy compositions"
    }
}

# Visual style presets with prompt additions
VISUAL_STYLES = [
    {
        "id": "photorealistic",
        "name": "Photorealistic",
        "prompt_addition": "photorealistic, 8k, professional photography, sharp focus"
    },
    {
        "id": "editorial",
        "name": "Editorial/Magazine",
        "prompt_addition": "editorial photography style, magazine quality, sophisticated lighting"
    },
    {
        "id": "minimal",
        "name": "Minimalist",
        "prompt_addition": "minimalist design, clean composition, negative space, simple"
    },
    {
        "id": "abstract",
        "name": "Abstract/Conceptual",
        "prompt_addition": "abstract representation, conceptual art, symbolic imagery"
    },
    {
        "id": "illustration",
        "name": "Illustration",
        "prompt_addition": "digital illustration, vector art style, clean lines"
    },
    {
        "id": "3d_render",
        "name": "3D Render",
        "prompt_addition": "3D render, octane render, professional lighting, depth of field"
    },
    {
        "id": "isometric",
        "name": "Isometric",
        "prompt_addition": "isometric view, 3D isometric illustration, clean geometric"
    },
    {
        "id": "watercolor",
        "name": "Watercolor",
        "prompt_addition": "watercolor painting style, soft edges, artistic"
    },
    {
        "id": "corporate",
        "name": "Corporate/Business",
        "prompt_addition": "professional corporate imagery, business context, polished"
    },
    {
        "id": "tech",
        "name": "Tech/Futuristic",
        "prompt_addition": "futuristic technology aesthetic, digital, modern tech visualization"
    }
]


async def generate_image_prompts(
    content: str,
    content_type: str,
    image_purpose: str = "linkedin_post",
    visual_style: str = "photorealistic",
    brand_context: Optional[dict] = None,
    num_variations: int = 3
) -> list[dict]:
    """
    Generate image prompts based on content.

    Args:
        content: The text content to visualize
        content_type: Type of content (linkedin, blog, email, etc.)
        image_purpose: Purpose/format of the image
        visual_style: Visual style preset to use
        brand_context: Optional brand information
        num_variations: Number of prompt variations to generate

    Returns:
        List of prompt dictionaries with prompt, concept, best_for, negative_prompt
    """
    purpose_info = IMAGE_STYLES.get(image_purpose, IMAGE_STYLES["linkedin_post"])
    style_info = next(
        (s for s in VISUAL_STYLES if s["id"] == visual_style),
        VISUAL_STYLES[0]
    )

    brand_guidance = ""
    if brand_context:
        brand_guidance = f"""
Brand Context:
- Company: {brand_context.get('company_name', 'Not specified')}
- Industry: {brand_context.get('industry', 'Not specified')}
- Brand colors to consider: {brand_context.get('colors', 'Not specified')}
"""

    prompt = f"""Generate {num_variations} image prompts for AI image generators (Midjourney, DALL-E, Ideogram) based on this content.

CONTENT TO VISUALIZE:
{content}

CONTENT TYPE: {content_type}

IMAGE PURPOSE: {image_purpose}
- Aspect Ratio: {purpose_info['aspect']}
- Style Guidelines: {purpose_info['style_hints']}
- Avoid: {purpose_info['avoid']}

VISUAL STYLE: {style_info['name']}
- Style additions: {style_info['prompt_addition']}

{brand_guidance}

---

Generate {num_variations} distinct image prompts. Each should:
1. Capture a different visual interpretation of the content
2. Be specific and detailed (50-100 words each)
3. Include composition guidance
4. Specify lighting, mood, and atmosphere
5. End with technical quality terms

Return a JSON array with this structure:
[
    {{
        "prompt": "The full image generation prompt",
        "concept": "Brief description of the visual concept (10 words max)",
        "best_for": "midjourney|dalle|ideogram|all",
        "negative_prompt": "Things to exclude from the image"
    }}
]

Return ONLY the JSON array, no other text."""

    response, cost = await call_openrouter_simple(
        task_name="drafting",
        prompt=prompt,
        temperature=0.7,
        max_tokens=2000
    )

    try:
        # Clean up response to extract JSON
        cleaned = response.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0]
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0]

        prompts = json.loads(cleaned.strip())

        # Add style suffix to each prompt if not already present
        for p in prompts:
            if style_info["prompt_addition"] not in p.get("prompt", ""):
                p["prompt"] = p["prompt"] + ", " + style_info["prompt_addition"]

        return prompts

    except json.JSONDecodeError:
        # Return raw response if JSON parsing fails
        return [{
            "prompt": response[:500],
            "concept": "Generated prompt",
            "best_for": "all",
            "negative_prompt": "",
            "error": "Could not parse structured response"
        }]


async def enhance_image_prompt(
    base_prompt: str,
    enhancement_type: str
) -> str:
    """
    Enhance an existing image prompt.

    Args:
        base_prompt: The original prompt to enhance
        enhancement_type: Type of enhancement to apply

    Returns:
        Enhanced prompt string
    """
    enhancements = {
        "more_detail": "Add more specific visual details, textures, and elements",
        "simplify": "Simplify the prompt while keeping the core concept",
        "more_dramatic": "Make it more dramatic with bold lighting and composition",
        "more_subtle": "Make it more subtle and understated",
        "add_people": "Add human elements if appropriate",
        "remove_people": "Remove any human elements, focus on objects/concepts",
        "warmer": "Shift to warmer color palette and lighting",
        "cooler": "Shift to cooler color palette and lighting",
        "more_professional": "Make it more professional and business-appropriate",
        "more_creative": "Make it more creative and artistic"
    }

    instruction = enhancements.get(enhancement_type, enhancement_type)

    prompt = f"""Enhance this image generation prompt according to the instruction.

ORIGINAL PROMPT:
{base_prompt}

INSTRUCTION:
{instruction}

Return ONLY the enhanced prompt, nothing else."""

    enhanced, cost = await call_openrouter_simple(
        task_name="editing",
        prompt=prompt,
        temperature=0.5,
        max_tokens=500
    )

    return enhanced.strip()


def get_image_styles_list() -> list[dict]:
    """Get list of available image purposes with metadata."""
    return [
        {"id": k, "name": k.replace("_", " ").title(), **v}
        for k, v in IMAGE_STYLES.items()
    ]


def get_visual_styles_list() -> list[dict]:
    """Get list of available visual styles."""
    return VISUAL_STYLES
