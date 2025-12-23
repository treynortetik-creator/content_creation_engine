"""Swipe File API - Save and learn from favorite content."""
import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user_id
from app.services.ai_client import generate_content
from app.services.swipe_analyzer import (
    deep_analyze_swipe,
    analyze_swipe_collection,
    generate_style_guide,
    get_swipe_style_context,
)

router = APIRouter()


class SwipeCreate(BaseModel):
    output_id: Optional[int] = None
    content_type: str
    content: str
    source_title: Optional[str] = None
    notes: Optional[str] = None


class SwipeResponse(BaseModel):
    id: int
    output_id: Optional[int]
    content_type: str
    content: str
    source_title: Optional[str]
    notes: Optional[str]
    patterns_extracted: Optional[dict]
    created_at: str


@router.post("/swipe")
async def add_to_swipe_file(
    swipe: SwipeCreate,
    user_id: int = Depends(get_current_user_id),
):
    """
    Add content to the swipe file.

    Automatically extracts patterns from the content for future learning.
    """
    if not swipe.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    # Extract patterns from the content
    patterns = await extract_patterns(swipe.content, swipe.content_type)

    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO swipe_file (
                user_id, output_id, content_type, content,
                source_title, notes, patterns_extracted
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                swipe.output_id,
                swipe.content_type,
                swipe.content.strip(),
                swipe.source_title,
                swipe.notes,
                json.dumps(patterns) if patterns else None,
            )
        )
        await db.commit()
        swipe_id = cursor.lastrowid

    return {
        "success": True,
        "swipe_id": swipe_id,
        "message": "Saved to swipe file",
        "patterns_found": len(patterns.get("patterns", [])) if patterns else 0,
    }


@router.get("/swipe")
async def get_swipe_file(
    content_type: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """
    Get all items in the user's swipe file.

    Optionally filter by content type (linkedin, blog, email, etc.)
    """
    async with get_db() as db:
        if content_type:
            cursor = await db.execute(
                """
                SELECT id, output_id, content_type, content,
                       source_title, notes, patterns_extracted, created_at
                FROM swipe_file
                WHERE user_id = ? AND content_type = ?
                ORDER BY created_at DESC
                """,
                (user_id, content_type)
            )
        else:
            cursor = await db.execute(
                """
                SELECT id, output_id, content_type, content,
                       source_title, notes, patterns_extracted, created_at
                FROM swipe_file
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,)
            )

        rows = await cursor.fetchall()

        swipes = []
        for row in rows:
            swipes.append({
                "id": row["id"],
                "output_id": row["output_id"],
                "content_type": row["content_type"],
                "content": row["content"],
                "source_title": row["source_title"],
                "notes": row["notes"],
                "patterns_extracted": json.loads(row["patterns_extracted"]) if row["patterns_extracted"] else None,
                "created_at": row["created_at"],
            })

        return {"swipes": swipes, "count": len(swipes)}


@router.delete("/swipe/{swipe_id}")
async def delete_from_swipe_file(
    swipe_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Delete an item from the swipe file."""
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            "SELECT id FROM swipe_file WHERE id = ? AND user_id = ?",
            (swipe_id, user_id)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Swipe not found")

        await db.execute(
            "DELETE FROM swipe_file WHERE id = ?",
            (swipe_id,)
        )
        await db.commit()

    return {"success": True, "message": "Removed from swipe file"}


@router.get("/swipe/patterns")
async def get_learned_patterns(
    content_type: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """
    Get aggregated patterns learned from the user's swipe file.

    Returns common patterns that can be used to guide future content generation.
    """
    async with get_db() as db:
        if content_type:
            cursor = await db.execute(
                """
                SELECT patterns_extracted
                FROM swipe_file
                WHERE user_id = ? AND content_type = ? AND patterns_extracted IS NOT NULL
                """,
                (user_id, content_type)
            )
        else:
            cursor = await db.execute(
                """
                SELECT patterns_extracted, content_type
                FROM swipe_file
                WHERE user_id = ? AND patterns_extracted IS NOT NULL
                """,
                (user_id,)
            )

        rows = await cursor.fetchall()

        # Aggregate patterns
        all_hooks = []
        all_structures = []
        all_techniques = []
        all_tones = []

        for row in rows:
            patterns = json.loads(row["patterns_extracted"]) if row["patterns_extracted"] else {}
            if patterns.get("hook_style"):
                all_hooks.append(patterns["hook_style"])
            if patterns.get("structure"):
                all_structures.append(patterns["structure"])
            if patterns.get("techniques"):
                all_techniques.extend(patterns["techniques"])
            if patterns.get("tone"):
                all_tones.append(patterns["tone"])

        # Count frequency of each pattern
        from collections import Counter
        hook_counts = Counter(all_hooks).most_common(5)
        structure_counts = Counter(all_structures).most_common(5)
        technique_counts = Counter(all_techniques).most_common(10)
        tone_counts = Counter(all_tones).most_common(5)

        return {
            "total_swipes": len(rows),
            "preferred_hooks": [{"style": h, "count": c} for h, c in hook_counts],
            "preferred_structures": [{"structure": s, "count": c} for s, c in structure_counts],
            "common_techniques": [{"technique": t, "count": c} for t, c in technique_counts],
            "preferred_tones": [{"tone": t, "count": c} for t, c in tone_counts],
        }


@router.post("/swipe/{swipe_id}/deep-analyze")
async def deep_analyze_swipe_entry(
    swipe_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """
    Perform deep AI analysis on a single swipe file entry.

    Extracts detailed patterns including hooks, structure, language,
    engagement triggers, formatting, and generates a replication guide.
    """
    async with get_db() as db:
        # Get the swipe entry
        cursor = await db.execute(
            """
            SELECT id, content, content_type
            FROM swipe_file
            WHERE id = ? AND user_id = ?
            """,
            (swipe_id, user_id)
        )
        row = await cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Swipe not found")

        content = row["content"]
        content_type = row["content_type"]

        # Check for existing analysis
        cursor = await db.execute(
            "SELECT analysis_data FROM swipe_analysis WHERE swipe_id = ?",
            (swipe_id,)
        )
        existing = await cursor.fetchone()

        if existing:
            return {
                "success": True,
                "swipe_id": swipe_id,
                "analysis": json.loads(existing["analysis_data"]),
                "cached": True,
            }

    # Run deep analysis
    analysis = await deep_analyze_swipe(content, content_type)

    if "error" in analysis:
        raise HTTPException(status_code=500, detail=analysis["error"])

    # Save the analysis
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO swipe_analysis (swipe_id, user_id, analysis_data)
            VALUES (?, ?, ?)
            ON CONFLICT(swipe_id) DO UPDATE SET
                analysis_data = excluded.analysis_data,
                created_at = CURRENT_TIMESTAMP
            """,
            (swipe_id, user_id, json.dumps(analysis))
        )
        await db.commit()

    return {
        "success": True,
        "swipe_id": swipe_id,
        "analysis": analysis,
        "cached": False,
    }


@router.post("/swipe/analyze-collection")
async def analyze_swipe_collection_endpoint(
    content_type: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """
    Analyze patterns across all swipes in the collection.

    Identifies dominant patterns, vocabulary profile, formatting preferences,
    and generates a comprehensive style guide.
    """
    async with get_db() as db:
        if content_type:
            cursor = await db.execute(
                """
                SELECT content, content_type
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
                SELECT content, content_type
                FROM swipe_file
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id,)
            )

        rows = await cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No swipes found to analyze")

    swipes = [
        {"content": row["content"], "content_type": row["content_type"]}
        for row in rows
    ]

    # Run collection analysis
    analysis = await analyze_swipe_collection(swipes, content_type)

    if "error" in analysis:
        raise HTTPException(status_code=500, detail=analysis["error"])

    # Cache the collection analysis
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO swipe_collection_analysis
                (user_id, content_type, analysis_data, swipe_count)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, content_type, json.dumps(analysis), len(swipes))
        )
        await db.commit()

    return {
        "success": True,
        "content_type": content_type or "all",
        "swipes_analyzed": len(swipes),
        "analysis": analysis,
    }


@router.get("/swipe/collection-analysis")
async def get_collection_analysis(
    content_type: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """
    Get the most recent collection analysis for the user.

    Returns cached analysis if available, otherwise returns empty.
    """
    async with get_db() as db:
        if content_type:
            cursor = await db.execute(
                """
                SELECT analysis_data, swipe_count, created_at
                FROM swipe_collection_analysis
                WHERE user_id = ? AND content_type = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id, content_type)
            )
        else:
            cursor = await db.execute(
                """
                SELECT analysis_data, swipe_count, content_type, created_at
                FROM swipe_collection_analysis
                WHERE user_id = ? AND content_type IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,)
            )

        row = await cursor.fetchone()

    if not row:
        return {
            "success": True,
            "has_analysis": False,
            "message": "No collection analysis found. Run /swipe/analyze-collection first.",
        }

    return {
        "success": True,
        "has_analysis": True,
        "content_type": content_type or row.get("content_type") or "all",
        "swipe_count": row["swipe_count"],
        "created_at": row["created_at"],
        "analysis": json.loads(row["analysis_data"]),
    }


@router.get("/swipe/style-guide")
async def get_style_guide(
    content_type: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """
    Generate a comprehensive style guide from the user's swipe file.

    Returns actionable do's, don'ts, templates, and a prompt injection string.
    """
    result = await generate_style_guide(user_id, content_type)

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return {
        "success": True,
        "content_type": content_type or "all",
        **result,
    }


@router.get("/swipe/style-context")
async def get_style_context(
    content_type: str,
    user_id: int = Depends(get_current_user_id),
):
    """
    Get style context string for prompt injection.

    This endpoint returns a formatted string that can be injected into
    content generation prompts to help AI match the user's preferred style.
    """
    context = await get_swipe_style_context(user_id, content_type)

    return {
        "success": True,
        "content_type": content_type,
        "context": context,
        "has_context": bool(context),
    }


async def extract_patterns(content: str, content_type: str) -> dict:
    """
    Use AI to extract writing patterns from content.

    Analyzes:
    - Hook style (question, stat, story, bold claim, etc.)
    - Structure (problem-solution, listicle, narrative, etc.)
    - Writing techniques (metaphors, specific numbers, social proof, etc.)
    - Tone (professional, conversational, urgent, etc.)
    """
    prompt = f"""Analyze this {content_type} content and extract writing patterns.

CONTENT:
{content}

Extract the following patterns and return as JSON:
{{
    "hook_style": "one of: question, statistic, bold_claim, story, curiosity_gap, controversial, how_to",
    "structure": "one of: problem_solution, listicle, narrative, comparison, framework, case_study",
    "techniques": ["list of techniques used, e.g.: specific_numbers, social_proof, metaphor, call_to_action, urgency, personal_story, data_driven"],
    "tone": "one of: professional, conversational, urgent, inspirational, educational, provocative",
    "estimated_word_count": number,
    "key_elements": ["list of what makes this content effective"]
}}

Return ONLY the JSON object, no other text."""

    try:
        result = await generate_content(prompt, model="gpt-4o-mini")

        # Parse JSON from result
        import re
        json_match = re.search(r'\{[\s\S]*\}', result)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        print(f"Pattern extraction error: {e}")

    return {}


async def get_user_swipe_patterns(user_id: int, content_type: str = None) -> str:
    """
    Get formatted swipe patterns for prompt injection.

    Returns a string describing the user's preferred writing patterns
    based on their swipe file.
    """
    async with get_db() as db:
        if content_type:
            cursor = await db.execute(
                """
                SELECT patterns_extracted
                FROM swipe_file
                WHERE user_id = ? AND content_type = ? AND patterns_extracted IS NOT NULL
                LIMIT 10
                """,
                (user_id, content_type)
            )
        else:
            cursor = await db.execute(
                """
                SELECT patterns_extracted
                FROM swipe_file
                WHERE user_id = ? AND patterns_extracted IS NOT NULL
                LIMIT 10
                """,
                (user_id,)
            )

        rows = await cursor.fetchall()

        if not rows:
            return ""

        # Aggregate patterns
        from collections import Counter
        all_hooks = []
        all_structures = []
        all_techniques = []
        all_tones = []

        for row in rows:
            patterns = json.loads(row["patterns_extracted"]) if row["patterns_extracted"] else {}
            if patterns.get("hook_style"):
                all_hooks.append(patterns["hook_style"])
            if patterns.get("structure"):
                all_structures.append(patterns["structure"])
            if patterns.get("techniques"):
                all_techniques.extend(patterns["techniques"])
            if patterns.get("tone"):
                all_tones.append(patterns["tone"])

        # Get most common patterns
        top_hooks = Counter(all_hooks).most_common(2)
        top_structures = Counter(all_structures).most_common(2)
        top_techniques = Counter(all_techniques).most_common(5)
        top_tones = Counter(all_tones).most_common(2)

        if not (top_hooks or top_structures or top_techniques or top_tones):
            return ""

        sections = [
            "=" * 50,
            "USER'S PREFERRED WRITING PATTERNS (from swipe file)",
            "=" * 50,
            "",
        ]

        if top_hooks:
            hooks = [h[0].replace("_", " ") for h in top_hooks]
            sections.append(f"Preferred hook styles: {', '.join(hooks)}")

        if top_structures:
            structures = [s[0].replace("_", " ") for s in top_structures]
            sections.append(f"Preferred structures: {', '.join(structures)}")

        if top_techniques:
            techniques = [t[0].replace("_", " ") for t in top_techniques]
            sections.append(f"Commonly used techniques: {', '.join(techniques)}")

        if top_tones:
            tones = [t[0] for t in top_tones]
            sections.append(f"Preferred tones: {', '.join(tones)}")

        sections.extend([
            "",
            "Use these patterns to match the user's writing style preferences.",
            "=" * 50,
            "",
        ])

        return "\n".join(sections)
