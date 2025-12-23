"""Output Feedback API - Binary feedback (thumbs up/down) on generated content."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user_id

router = APIRouter()


class FeedbackCreate(BaseModel):
    output_id: int
    feedback_type: str  # 'up' or 'down'
    reason: Optional[str] = None


@router.post("/feedback")
async def submit_feedback(
    feedback: FeedbackCreate,
    user_id: int = Depends(get_current_user_id),
):
    """
    Submit feedback (thumbs up or down) on an output.

    Only one feedback per output per user is allowed.
    Submitting again will update the existing feedback.
    """
    if feedback.feedback_type not in ('up', 'down'):
        raise HTTPException(
            status_code=400,
            detail="feedback_type must be 'up' or 'down'"
        )

    async with get_db() as db:
        # Check if output exists
        cursor = await db.execute(
            "SELECT id FROM outputs WHERE id = ?",
            (feedback.output_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Output not found")

        # Upsert feedback (replace if exists)
        await db.execute(
            """
            INSERT INTO output_feedback (user_id, output_id, feedback_type, reason)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, output_id) DO UPDATE SET
                feedback_type = excluded.feedback_type,
                reason = excluded.reason,
                created_at = CURRENT_TIMESTAMP
            """,
            (user_id, feedback.output_id, feedback.feedback_type, feedback.reason)
        )
        await db.commit()

    return {
        "success": True,
        "message": f"Feedback recorded: {feedback.feedback_type}",
    }


@router.get("/feedback/{output_id}")
async def get_feedback(
    output_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get the current user's feedback on an output."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT feedback_type, reason, created_at
            FROM output_feedback
            WHERE user_id = ? AND output_id = ?
            """,
            (user_id, output_id)
        )
        row = await cursor.fetchone()

        if not row:
            return {"feedback": None}

        return {
            "feedback": {
                "type": row["feedback_type"],
                "reason": row["reason"],
                "created_at": row["created_at"],
            }
        }


@router.delete("/feedback/{output_id}")
async def delete_feedback(
    output_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Remove feedback from an output."""
    async with get_db() as db:
        await db.execute(
            "DELETE FROM output_feedback WHERE user_id = ? AND output_id = ?",
            (user_id, output_id)
        )
        await db.commit()

    return {"success": True, "message": "Feedback removed"}


@router.get("/feedback/summary")
async def get_feedback_summary(
    user_id: int = Depends(get_current_user_id),
):
    """
    Get summary of all user's feedback.

    Returns counts of thumbs up and down by content type.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                o.content_type,
                f.feedback_type,
                COUNT(*) as count
            FROM output_feedback f
            JOIN outputs o ON f.output_id = o.id
            WHERE f.user_id = ?
            GROUP BY o.content_type, f.feedback_type
            ORDER BY o.content_type, f.feedback_type
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

        summary = {}
        total_up = 0
        total_down = 0

        for row in rows:
            content_type = row["content_type"]
            if content_type not in summary:
                summary[content_type] = {"up": 0, "down": 0}

            summary[content_type][row["feedback_type"]] = row["count"]

            if row["feedback_type"] == "up":
                total_up += row["count"]
            else:
                total_down += row["count"]

        return {
            "by_content_type": summary,
            "totals": {
                "up": total_up,
                "down": total_down,
                "total": total_up + total_down,
            }
        }
