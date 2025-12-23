"""Advanced swipe file analysis with deep pattern extraction."""
import json
import re
from typing import Optional
from collections import Counter

from app.services.openrouter import call_openrouter_simple
from app.database import get_db


async def deep_analyze_swipe(content: str, content_type: str) -> dict:
    """
    Perform deep AI analysis of a single swipe file entry.

    Extracts detailed patterns including:
    - Hook style and specific techniques
    - Structural patterns and flow
    - Vocabulary and language patterns
    - Engagement triggers
    - Formatting conventions
    - Call-to-action patterns

    Args:
        content: The content to analyze
        content_type: Type of content (linkedin, blog, email, etc.)

    Returns:
        Detailed analysis dict
    """
    prompt = f"""Perform a deep analysis of this {content_type} content to extract actionable writing patterns.

CONTENT:
{content}

---

Analyze every aspect of this content's effectiveness and return a JSON object with this exact structure:

{{
    "hook_analysis": {{
        "type": "question|statistic|bold_claim|story|curiosity_gap|controversial|how_to|pain_point",
        "technique": "Specific technique used (e.g., 'open loop', 'pattern interrupt', 'direct address')",
        "first_line": "The exact first line/hook",
        "hook_strength": "strong|medium|weak",
        "why_it_works": "Explanation of why this hook is effective"
    }},
    "structure_analysis": {{
        "format": "problem_solution|listicle|narrative|comparison|framework|case_study|tips|story_lesson",
        "sections": ["List of content sections in order"],
        "paragraph_count": number,
        "avg_paragraph_length": "short (1-2 sentences)|medium (3-4)|long (5+)",
        "line_breaks_usage": "heavy|moderate|minimal",
        "flow_pattern": "Description of how ideas progress"
    }},
    "language_patterns": {{
        "reading_level": "simple|conversational|professional|academic",
        "sentence_variety": "varied|consistent|mixed",
        "uses_contractions": true|false,
        "uses_first_person": true|false,
        "uses_second_person": true|false,
        "power_words": ["List of impactful words used"],
        "transition_phrases": ["Phrases used to connect ideas"],
        "unique_expressions": ["Distinctive phrases or word choices"]
    }},
    "engagement_triggers": {{
        "emotional_appeals": ["List: curiosity, fear, aspiration, belonging, etc."],
        "credibility_markers": ["How authority/trust is established"],
        "social_proof": "Description of any social proof used",
        "specificity_examples": ["Specific numbers, names, details used"]
    }},
    "formatting_conventions": {{
        "uses_emojis": true|false,
        "emoji_placement": "beginning|end|inline|section_breaks|none",
        "uses_hashtags": true|false,
        "hashtag_count": number,
        "uses_bold_text": true|false,
        "uses_bullet_points": true|false,
        "uses_numbers": true|false,
        "whitespace_style": "dense|balanced|airy"
    }},
    "cta_analysis": {{
        "has_cta": true|false,
        "cta_type": "question|instruction|invitation|soft_nudge|none",
        "cta_text": "The exact CTA if present",
        "cta_placement": "end|middle|throughout|none"
    }},
    "effectiveness_scores": {{
        "hook_score": 1-10,
        "clarity_score": 1-10,
        "engagement_potential": 1-10,
        "uniqueness_score": 1-10,
        "overall_score": 1-10
    }},
    "replication_guide": {{
        "template": "A template/formula based on this content structure",
        "key_elements_to_copy": ["List of specific elements to replicate"],
        "avoid": ["What NOT to do based on this analysis"]
    }},
    "word_count": number,
    "estimated_read_time": "X min read"
}}

Be extremely specific and actionable. Quote exact text from the content where relevant.
Return ONLY the JSON object, no other text.
"""

    response, cost = await call_openrouter_simple(
        task_name="swipe_analysis",
        prompt=prompt,
        temperature=0.3,
        max_tokens=2500,
    )

    # Parse JSON from response
    try:
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

        return {
            "error": f"Failed to parse AI response: {str(e)}",
            "raw_response": response[:1000],
        }


async def analyze_swipe_collection(
    swipes: list[dict],
    content_type: Optional[str] = None,
) -> dict:
    """
    Analyze patterns across a collection of swipe entries.

    Identifies common patterns, preferences, and generates a unified style guide.

    Args:
        swipes: List of swipe entries with 'content' and 'content_type' keys
        content_type: Optional filter for specific content type

    Returns:
        Collection analysis with aggregated patterns and style guide
    """
    if not swipes:
        return {"error": "No swipes provided for analysis"}

    # Filter by content type if specified
    if content_type:
        swipes = [s for s in swipes if s.get("content_type") == content_type]

    if not swipes:
        return {"error": f"No swipes found for content type: {content_type}"}

    # Format swipes for the prompt
    swipes_text = "\n\n---\n\n".join([
        f"[SWIPE {i+1} - {s.get('content_type', 'unknown').upper()}]\n{s['content'][:1500]}"
        for i, s in enumerate(swipes[:10])  # Limit to 10 for token management
    ])

    prompt = f"""Analyze this collection of {len(swipes)} saved content pieces (swipe file) to identify patterns and preferences.

SWIPE COLLECTION:
{swipes_text}

---

Analyze all pieces collectively to identify patterns, preferences, and generate a comprehensive style guide.

Return a JSON object with this exact structure:

{{
    "collection_stats": {{
        "total_pieces": {len(swipes)},
        "content_types": {{"type": count}},
        "avg_word_count": number,
        "common_lengths": "short|medium|long"
    }},
    "dominant_patterns": {{
        "hook_styles": [
            {{"style": "name", "frequency": "X of Y pieces", "example": "Quote from swipes"}}
        ],
        "structures": [
            {{"structure": "name", "frequency": "X of Y pieces", "description": "How it's used"}}
        ],
        "tones": [
            {{"tone": "name", "frequency": "X of Y pieces"}}
        ]
    }},
    "vocabulary_profile": {{
        "reading_level": "simple|conversational|professional",
        "recurring_words": ["Words that appear frequently"],
        "power_phrases": ["Impactful phrases used across multiple pieces"],
        "avoided_patterns": ["Patterns conspicuously absent"]
    }},
    "formatting_preferences": {{
        "typical_paragraph_length": "description",
        "line_break_usage": "heavy|moderate|minimal",
        "emoji_usage": "frequent|occasional|rare|never",
        "list_preference": "bullets|numbers|none",
        "whitespace_style": "dense|balanced|airy"
    }},
    "engagement_patterns": {{
        "common_hooks": ["Types of openings that appear most"],
        "common_ctas": ["Types of endings/CTAs that appear most"],
        "emotional_triggers": ["Emotions commonly evoked"],
        "credibility_methods": ["How trust is established"]
    }},
    "style_guide": {{
        "voice_summary": "2-3 sentence summary of the overall voice",
        "do_list": [
            "Specific things to DO based on these patterns"
        ],
        "dont_list": [
            "Specific things to AVOID based on these patterns"
        ],
        "hook_templates": [
            "Template 1 based on common patterns",
            "Template 2 based on common patterns"
        ],
        "structure_templates": [
            "Template structure based on common patterns"
        ],
        "cta_templates": [
            "Template CTA based on common patterns"
        ]
    }},
    "content_generation_prompt": "A detailed prompt section that can be injected into content generation to replicate this style"
}}

Be specific and actionable. Base everything on actual patterns observed in the swipes.
Return ONLY the JSON object, no other text.
"""

    response, cost = await call_openrouter_simple(
        task_name="swipe_analysis",
        prompt=prompt,
        temperature=0.3,
        max_tokens=3000,
    )

    # Parse JSON from response
    try:
        cleaned = response.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0]
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0]

        result = json.loads(cleaned.strip())
        result["analyzed_count"] = len(swipes)
        result["cost"] = cost
        return result

    except json.JSONDecodeError as e:
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                result = json.loads(json_match.group())
                result["analyzed_count"] = len(swipes)
                return result
            except json.JSONDecodeError:
                pass

        return {
            "error": f"Failed to parse AI response: {str(e)}",
            "raw_response": response[:1000],
        }


async def generate_style_guide(user_id: int, content_type: Optional[str] = None) -> dict:
    """
    Generate a comprehensive style guide from user's swipe file.

    Args:
        user_id: The user's ID
        content_type: Optional content type filter

    Returns:
        Style guide dict
    """
    async with get_db() as db:
        if content_type:
            cursor = await db.execute(
                """
                SELECT content, content_type, patterns_extracted
                FROM swipe_file
                WHERE user_id = ? AND content_type = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id, content_type)
            )
        else:
            cursor = await db.execute(
                """
                SELECT content, content_type, patterns_extracted
                FROM swipe_file
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id,)
            )

        rows = await cursor.fetchall()

    if not rows:
        return {"error": "No swipes found to generate style guide"}

    swipes = [
        {
            "content": row["content"],
            "content_type": row["content_type"],
            "patterns": json.loads(row["patterns_extracted"]) if row["patterns_extracted"] else None
        }
        for row in rows
    ]

    # Run collection analysis
    analysis = await analyze_swipe_collection(swipes, content_type)

    if "error" in analysis:
        return analysis

    return {
        "style_guide": analysis.get("style_guide", {}),
        "content_generation_prompt": analysis.get("content_generation_prompt", ""),
        "dominant_patterns": analysis.get("dominant_patterns", {}),
        "formatting_preferences": analysis.get("formatting_preferences", {}),
        "analyzed_count": analysis.get("analyzed_count", 0),
    }


async def get_swipe_style_context(
    user_id: int,
    content_type: str,
    max_examples: int = 3,
) -> str:
    """
    Get swipe-based style context for content generation prompts.

    This is designed to be injected into drafting prompts to help
    AI replicate the user's preferred writing patterns.

    Args:
        user_id: The user's ID
        content_type: The content type being generated
        max_examples: Maximum number of example excerpts to include

    Returns:
        Formatted string for prompt injection
    """
    async with get_db() as db:
        # First, check for cached collection analysis
        cursor = await db.execute(
            """
            SELECT analysis_data
            FROM swipe_collection_analysis
            WHERE user_id = ? AND content_type = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, content_type)
        )
        cached = await cursor.fetchone()

        style_guide = None
        if cached:
            try:
                analysis = json.loads(cached["analysis_data"])
                style_guide = analysis.get("style_guide", {})
            except json.JSONDecodeError:
                pass

        # Get recent swipes with deep analysis
        cursor = await db.execute(
            """
            SELECT sf.content, sa.analysis_data
            FROM swipe_file sf
            LEFT JOIN swipe_analysis sa ON sf.id = sa.swipe_id
            WHERE sf.user_id = ? AND sf.content_type = ?
            ORDER BY sf.created_at DESC
            LIMIT ?
            """,
            (user_id, content_type, max_examples)
        )
        rows = await cursor.fetchall()

    if not rows and not style_guide:
        return ""

    sections = [
        "=" * 60,
        f"STYLE REFERENCE (from your {content_type.upper()} swipe file)",
        "=" * 60,
        "",
    ]

    # Add style guide if available
    if style_guide:
        if style_guide.get("voice_summary"):
            sections.append(f"VOICE: {style_guide['voice_summary']}")
            sections.append("")

        if style_guide.get("do_list"):
            sections.append("DO:")
            for item in style_guide["do_list"][:5]:
                sections.append(f"  • {item}")
            sections.append("")

        if style_guide.get("dont_list"):
            sections.append("DON'T:")
            for item in style_guide["dont_list"][:5]:
                sections.append(f"  • {item}")
            sections.append("")

        if style_guide.get("hook_templates"):
            sections.append("HOOK PATTERNS TO USE:")
            for template in style_guide["hook_templates"][:3]:
                sections.append(f"  • {template}")
            sections.append("")

    # Add example excerpts
    if rows:
        sections.append("REFERENCE EXAMPLES (match this style):")
        sections.append("")
        for i, row in enumerate(rows[:max_examples]):
            content = row["content"]
            # Get first 200 chars or first paragraph
            excerpt = content[:200].split("\n\n")[0]
            if len(content) > 200:
                excerpt += "..."
            sections.append(f"Example {i+1}:")
            sections.append(excerpt)
            sections.append("")

    sections.extend([
        "Use these patterns to match the user's preferred writing style.",
        "=" * 60,
        "",
    ])

    return "\n".join(sections)


def aggregate_basic_patterns(swipes: list[dict]) -> dict:
    """
    Aggregate basic patterns from swipe entries without AI.

    Uses pre-extracted patterns stored in the database.

    Args:
        swipes: List of swipe entries with patterns_extracted field

    Returns:
        Aggregated pattern statistics
    """
    all_hooks = []
    all_structures = []
    all_techniques = []
    all_tones = []

    for swipe in swipes:
        patterns = swipe.get("patterns") or {}
        if isinstance(patterns, str):
            try:
                patterns = json.loads(patterns)
            except json.JSONDecodeError:
                continue

        if patterns.get("hook_style"):
            all_hooks.append(patterns["hook_style"])
        if patterns.get("structure"):
            all_structures.append(patterns["structure"])
        if patterns.get("techniques"):
            all_techniques.extend(patterns["techniques"])
        if patterns.get("tone"):
            all_tones.append(patterns["tone"])

    return {
        "hook_distribution": dict(Counter(all_hooks).most_common(5)),
        "structure_distribution": dict(Counter(all_structures).most_common(5)),
        "technique_frequency": dict(Counter(all_techniques).most_common(10)),
        "tone_distribution": dict(Counter(all_tones).most_common(5)),
        "total_analyzed": len(swipes),
    }
