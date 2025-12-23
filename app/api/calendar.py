"""Content Calendar API - Schedule and visualize content publishing."""
import json
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user_id

router = APIRouter()


class ScheduleContent(BaseModel):
    """Request to schedule content."""
    output_id: int
    scheduled_date: str  # YYYY-MM-DD format
    notes: Optional[str] = None


class UpdateSchedule(BaseModel):
    """Request to update scheduled content."""
    scheduled_date: Optional[str] = None
    notes: Optional[str] = None


@router.post("/calendar/schedule")
async def schedule_content(
    schedule: ScheduleContent,
    user_id: int = Depends(get_current_user_id),
):
    """
    Schedule content for a specific date.

    Only one piece of content can be scheduled per date per user.
    """
    # Validate date format
    try:
        datetime.strptime(schedule.scheduled_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    async with get_db() as db:
        # Verify output exists and belongs to user
        cursor = await db.execute(
            """
            SELECT o.id FROM outputs o
            JOIN jobs j ON o.job_id = j.id
            WHERE o.id = ? AND j.user_id = ?
            """,
            (schedule.output_id, user_id)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Output not found")

        # Check if already scheduled
        cursor = await db.execute(
            "SELECT id FROM content_schedule WHERE output_id = ?",
            (schedule.output_id,)
        )
        existing = await cursor.fetchone()

        if existing:
            # Update existing schedule
            await db.execute(
                """
                UPDATE content_schedule
                SET scheduled_date = ?, notes = ?
                WHERE output_id = ?
                """,
                (schedule.scheduled_date, schedule.notes, schedule.output_id)
            )
        else:
            # Create new schedule
            await db.execute(
                """
                INSERT INTO content_schedule (user_id, output_id, scheduled_date, notes)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, schedule.output_id, schedule.scheduled_date, schedule.notes)
            )

        await db.commit()

    return {
        "success": True,
        "message": f"Content scheduled for {schedule.scheduled_date}"
    }


@router.delete("/calendar/schedule/{output_id}")
async def unschedule_content(
    output_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Remove content from the schedule."""
    async with get_db() as db:
        # Verify ownership
        cursor = await db.execute(
            """
            SELECT cs.id FROM content_schedule cs
            WHERE cs.output_id = ? AND cs.user_id = ?
            """,
            (output_id, user_id)
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Scheduled content not found")

        await db.execute(
            "DELETE FROM content_schedule WHERE output_id = ? AND user_id = ?",
            (output_id, user_id)
        )
        await db.commit()

    return {"success": True, "message": "Content unscheduled"}


@router.get("/calendar")
async def get_calendar(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_id: int = Depends(get_current_user_id),
):
    """
    Get calendar view of scheduled content.

    Default: Current month if no dates specified.
    """
    # Default to current month
    if not start_date:
        today = datetime.now()
        start_date = today.replace(day=1).strftime("%Y-%m-%d")
    if not end_date:
        today = datetime.now()
        # Last day of next month
        next_month = today.replace(day=28) + timedelta(days=4)
        end_date = (next_month.replace(day=1) + timedelta(days=31)).replace(day=1).strftime("%Y-%m-%d")

    async with get_db() as db:
        # Get scheduled content
        cursor = await db.execute(
            """
            SELECT
                cs.id, cs.output_id, cs.scheduled_date, cs.notes,
                o.content_type, o.step3_final, o.step2_edited, o.step1_draft,
                j.original_filename
            FROM content_schedule cs
            JOIN outputs o ON cs.output_id = o.id
            JOIN jobs j ON o.job_id = j.id
            WHERE cs.user_id = ?
              AND cs.scheduled_date >= ?
              AND cs.scheduled_date <= ?
            ORDER BY cs.scheduled_date
            """,
            (user_id, start_date, end_date)
        )
        rows = await cursor.fetchall()

        scheduled = []
        for row in rows:
            content = row["step3_final"] or row["step2_edited"] or row["step1_draft"] or ""
            scheduled.append({
                "id": row["id"],
                "output_id": row["output_id"],
                "scheduled_date": row["scheduled_date"],
                "notes": row["notes"],
                "content_type": row["content_type"],
                "content_preview": content[:150] + "..." if len(content) > 150 else content,
                "source": row["original_filename"],
            })

        # Get unscheduled content (recent outputs not scheduled)
        cursor = await db.execute(
            """
            SELECT
                o.id, o.content_type, o.step3_final, o.step2_edited, o.step1_draft,
                j.original_filename, j.created_at
            FROM outputs o
            JOIN jobs j ON o.job_id = j.id
            LEFT JOIN content_schedule cs ON o.id = cs.output_id
            WHERE j.user_id = ? AND cs.id IS NULL
            ORDER BY j.created_at DESC
            LIMIT 20
            """,
            (user_id,)
        )
        unscheduled_rows = await cursor.fetchall()

        unscheduled = []
        for row in unscheduled_rows:
            content = row["step3_final"] or row["step2_edited"] or row["step1_draft"] or ""
            unscheduled.append({
                "output_id": row["id"],
                "content_type": row["content_type"],
                "content_preview": content[:100] + "..." if len(content) > 100 else content,
                "source": row["original_filename"],
            })

        return {
            "start_date": start_date,
            "end_date": end_date,
            "scheduled": scheduled,
            "unscheduled": unscheduled,
        }


@router.get("/calendar/upcoming")
async def get_upcoming_content(
    days: int = 7,
    user_id: int = Depends(get_current_user_id),
):
    """Get content scheduled for the upcoming days."""
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT
                cs.scheduled_date, cs.notes,
                o.id as output_id, o.content_type,
                o.step3_final, o.step2_edited, o.step1_draft
            FROM content_schedule cs
            JOIN outputs o ON cs.output_id = o.id
            WHERE cs.user_id = ?
              AND cs.scheduled_date >= ?
              AND cs.scheduled_date <= ?
            ORDER BY cs.scheduled_date
            """,
            (user_id, today, end_date)
        )
        rows = await cursor.fetchall()

        upcoming = []
        for row in rows:
            content = row["step3_final"] or row["step2_edited"] or row["step1_draft"] or ""
            upcoming.append({
                "output_id": row["output_id"],
                "scheduled_date": row["scheduled_date"],
                "content_type": row["content_type"],
                "content_preview": content[:200],
                "notes": row["notes"],
            })

        return {"upcoming": upcoming, "days": days}
