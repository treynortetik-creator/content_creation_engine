"""AI-powered brand voice extraction from content samples."""
import json
import re
from typing import Optional

from app.services.openrouter import call_openrouter_simple


async def extract_brand_voice_patterns(
    samples: list[str],
    content_types: list[str],
) -> dict:
    """
    Use AI to extract brand voice patterns from sample content.

    Args:
        samples: List of content samples (posts, blogs, emails)
        content_types: List of content types for each sample (linkedin, blog, email)

    Returns:
        Extracted brand voice patterns as a dict
    """
    # Format samples for the prompt
    samples_text = "\n\n---\n\n".join([
        f"[SAMPLE {i+1} - {content_types[i].upper()}]\n{sample}"
        for i, sample in enumerate(samples)
    ])

    prompt = f"""Analyze these content samples and extract the brand voice patterns. Be specific and actionable based on what you observe in the actual samples.

CONTENT SAMPLES:
{samples_text}

---

Carefully analyze the writing style, tone, vocabulary, and structure across all samples. Extract patterns and return a JSON object with this exact structure:

{{
    "tone_by_format": {{
        "linkedin": "Describe the LinkedIn tone based on samples (if present), or suggest based on overall voice",
        "blog": "Describe the blog tone based on samples (if present), or suggest based on overall voice",
        "email": "Describe the email tone based on samples (if present), or suggest based on overall voice"
    }},
    "core_principles": [
        "Principle 1 - include specific example from the samples that demonstrates this",
        "Principle 2 - include specific example from the samples",
        "Principle 3 - include specific example from the samples"
    ],
    "hook_patterns": [
        "Pattern 1: Describe how they typically start content (with example)",
        "Pattern 2: Another common opening approach (with example)"
    ],
    "vocabulary_patterns": {{
        "preferred_words": ["list", "of", "words", "they", "use", "frequently"],
        "avoided_words": ["words", "they", "seem", "to", "avoid"],
        "industry_terms": ["domain", "specific", "terminology", "used"]
    }},
    "structural_patterns": {{
        "paragraph_length": "short/medium/long with typical sentence count",
        "uses_contractions": true or false,
        "uses_questions": true or false,
        "uses_lists": true or false,
        "uses_emojis": true or false,
        "typical_post_length": "estimate in words"
    }},
    "signature_phrases": [
        "Exact phrases they use repeatedly across samples",
        "Catchphrases or recurring expressions"
    ],
    "cta_patterns": [
        "How they typically end posts or ask for engagement"
    ],
    "red_flags": [
        "Things they clearly avoid based on analyzing the samples"
    ],
    "overall_voice_summary": "A 2-3 sentence summary of the overall brand voice"
}}

Important instructions:
1. Base ALL observations on actual evidence from the samples provided
2. Quote specific examples when possible
3. If a pattern isn't clearly visible, note it as "Not enough samples to determine"
4. Be specific rather than generic - these patterns should be unique to THIS brand

Return ONLY the JSON object, no other text.
"""

    response, cost = await call_openrouter_simple(
        task_name="brand_voice_analysis",
        prompt=prompt,
        temperature=0.3,
        max_tokens=2000,
    )

    # Parse JSON from response
    try:
        # Handle potential markdown code blocks
        cleaned = response.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0]
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0]

        return json.loads(cleaned.strip())

    except json.JSONDecodeError as e:
        # Try to find JSON object in response
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Return error structure
        return {
            "error": f"Failed to parse AI response: {str(e)}",
            "raw_response": response[:1000],
        }


def convert_analysis_to_brand_voice(analysis: dict) -> dict:
    """
    Convert AI analysis output to brand_voice_json format compatible with existing system.

    Args:
        analysis: The raw analysis from extract_brand_voice_patterns

    Returns:
        Brand voice JSON in the format expected by brand_contexts table
    """
    if "error" in analysis:
        return {}

    tone_by_format = analysis.get("tone_by_format", {})
    structural = analysis.get("structural_patterns", {})

    # Convert to existing format
    brand_voice = {
        "tone_by_format": {
            "linkedin": tone_by_format.get("linkedin", "Professional and engaging"),
            "blog": tone_by_format.get("blog", "Informative and authoritative"),
            "email": tone_by_format.get("email", "Friendly and direct"),
        },
        "core_principles": analysis.get("core_principles", []),
        "mission_phrases": analysis.get("signature_phrases", []),
        "red_flags": analysis.get("red_flags", []),
        "structural_preferences": {
            "linkedin_structure": _infer_linkedin_structure(analysis),
            "paragraph_length": structural.get("paragraph_length", "2-4 sentences"),
            "contractions": "Frequent" if structural.get("uses_contractions", True) else "Rare",
        },
        # Extended fields from analysis
        "hook_patterns": analysis.get("hook_patterns", []),
        "vocabulary_patterns": analysis.get("vocabulary_patterns", {}),
        "cta_patterns": analysis.get("cta_patterns", []),
        "overall_voice_summary": analysis.get("overall_voice_summary", ""),
    }

    return brand_voice


def _infer_linkedin_structure(analysis: dict) -> str:
    """Infer LinkedIn structure preference from analysis."""
    structural = analysis.get("structural_patterns", {})
    hooks = analysis.get("hook_patterns", [])

    parts = []

    # Determine hook style
    if hooks:
        first_hook = hooks[0].lower()
        if "question" in first_hook:
            parts.append("Hook (question)")
        elif "stat" in first_hook or "number" in first_hook:
            parts.append("Hook (statistic)")
        elif "story" in first_hook or "anecdote" in first_hook:
            parts.append("Hook (story)")
        else:
            parts.append("Hook")
    else:
        parts.append("Hook")

    # Check for list usage
    if structural.get("uses_lists", False):
        parts.append("Bullets (3-5)")
    else:
        parts.append("Body paragraphs")

    # Add CTA if patterns detected
    if analysis.get("cta_patterns"):
        parts.append("CTA")

    return " → ".join(parts)


async def analyze_and_merge_with_existing(
    samples: list[str],
    content_types: list[str],
    existing_brand_voice: Optional[dict] = None,
) -> dict:
    """
    Analyze samples and optionally merge with existing brand voice.

    Args:
        samples: Content samples to analyze
        content_types: Type of each sample
        existing_brand_voice: Optional existing brand voice to enhance

    Returns:
        Complete brand voice configuration
    """
    # Get fresh analysis
    analysis = await extract_brand_voice_patterns(samples, content_types)

    if "error" in analysis:
        return analysis

    # Convert to brand voice format
    new_voice = convert_analysis_to_brand_voice(analysis)

    if not existing_brand_voice:
        return {
            "brand_voice": new_voice,
            "analysis": analysis,
            "source": "ai_extracted",
        }

    # Merge with existing - prioritize existing manual settings but enhance with AI
    merged = existing_brand_voice.copy()

    # Add AI-detected patterns that don't exist
    if not merged.get("hook_patterns"):
        merged["hook_patterns"] = new_voice.get("hook_patterns", [])

    if not merged.get("vocabulary_patterns"):
        merged["vocabulary_patterns"] = new_voice.get("vocabulary_patterns", {})

    if not merged.get("cta_patterns"):
        merged["cta_patterns"] = new_voice.get("cta_patterns", [])

    # Add signature phrases to mission phrases if not present
    existing_phrases = set(merged.get("mission_phrases", []))
    new_phrases = new_voice.get("mission_phrases", [])
    for phrase in new_phrases:
        if phrase not in existing_phrases:
            if "mission_phrases" not in merged:
                merged["mission_phrases"] = []
            merged["mission_phrases"].append(phrase)

    return {
        "brand_voice": merged,
        "analysis": analysis,
        "source": "merged",
    }
