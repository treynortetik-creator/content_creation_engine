"""Memory rules API - Persistent user preferences for content generation."""
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user_id

router = APIRouter()


class MemoryRuleCreate(BaseModel):
    rule_type: str  # 'never' or 'always'
    rule_content: str


class MemoryRuleResponse(BaseModel):
    id: int
    rule_type: str
    rule_content: str
    created_at: str


@router.post("/memory/rules")
async def add_memory_rule(
    rule: MemoryRuleCreate,
    user_id: int = Depends(get_current_user_id),
):
    """
    Add a new memory rule.

    Rules can be:
    - 'never': Things to never do (e.g., "use rhetorical questions")
    - 'always': Things to always do (e.g., "mention SafelyYou by name")
    """
    # Validate rule type
    if rule.rule_type not in ('never', 'always'):
        raise HTTPException(
            status_code=400,
            detail="rule_type must be 'never' or 'always'"
        )

    if not rule.rule_content.strip():
        raise HTTPException(
            status_code=400,
            detail="rule_content cannot be empty"
        )

    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO memory_rules (user_id, rule_type, rule_content)
            VALUES (?, ?, ?)
            """,
            (user_id, rule.rule_type, rule.rule_content.strip())
        )
        await db.commit()

        rule_id = cursor.lastrowid

    return {
        "success": True,
        "rule_id": rule_id,
        "message": f"Rule added: {rule.rule_type} {rule.rule_content}"
    }


@router.get("/memory/rules")
async def get_memory_rules(
    user_id: int = Depends(get_current_user_id),
):
    """
    Get all memory rules for the current user.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, rule_type, rule_content, created_at
            FROM memory_rules
            WHERE user_id = ?
            ORDER BY rule_type, created_at DESC
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

        rules = []
        for row in rows:
            rules.append({
                "id": row["id"],
                "rule_type": row["rule_type"],
                "rule_content": row["rule_content"],
                "created_at": row["created_at"],
            })

        return {"rules": rules}


@router.delete("/memory/rules/{rule_id}")
async def delete_memory_rule(
    rule_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """
    Delete a memory rule.
    """
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            "SELECT id FROM memory_rules WHERE id = ? AND user_id = ?",
            (rule_id, user_id)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Rule not found")

        await db.execute(
            "DELETE FROM memory_rules WHERE id = ?",
            (rule_id,)
        )
        await db.commit()

    return {"success": True, "message": "Rule deleted"}


async def get_user_memory_rules(user_id: int) -> dict:
    """
    Get memory rules for a user, formatted for prompt injection.

    Returns:
        {
            "never": ["rule1", "rule2"],
            "always": ["rule3", "rule4"]
        }
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT rule_type, rule_content
            FROM memory_rules
            WHERE user_id = ?
            ORDER BY rule_type, created_at
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

        rules = {"never": [], "always": []}
        for row in rows:
            rules[row["rule_type"]].append(row["rule_content"])

        return rules


def format_memory_rules_for_prompt(rules: dict) -> str:
    """
    Format memory rules for injection into prompts.

    Args:
        rules: Dict with 'never' and 'always' lists

    Returns:
        Formatted string for prompt injection, or empty string if no rules.
    """
    if not rules or (not rules.get("never") and not rules.get("always")):
        return ""

    sections = [
        "=" * 50,
        "USER PREFERENCES (MEMORY RULES)",
        "=" * 50,
        "",
    ]

    if rules.get("never"):
        sections.append("NEVER DO THESE (strict):")
        for rule in rules["never"]:
            sections.append(f"  ❌ {rule}")
        sections.append("")

    if rules.get("always"):
        sections.append("ALWAYS DO THESE:")
        for rule in rules["always"]:
            sections.append(f"  ✓ {rule}")
        sections.append("")

    sections.extend([
        "Follow these rules strictly in all content generation.",
        "=" * 50,
        "",
    ])

    return "\n".join(sections)
