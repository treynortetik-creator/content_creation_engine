"""Autopilot monitor service - checks sources and processes new content."""
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional

from app.database import get_db
from app.services.autopilot.fetchers import (
    fetch_youtube_channel_videos,
    fetch_rss_feed,
    fetch_podcast_episodes,
    fetch_article_content,
)
from app.models.job import JobStatus
from app.config import get_settings

settings = get_settings()


async def get_monitor_by_id(monitor_id: int) -> Optional[dict]:
    """Get a monitor by ID."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM autopilot_monitors WHERE id = ?
            """,
            (monitor_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_active_monitors() -> list[dict]:
    """Get all enabled monitors."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM autopilot_monitors WHERE enabled = TRUE
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def update_monitor_last_checked(
    monitor_id: int,
    last_content_id: Optional[str] = None,
):
    """Update monitor's last checked timestamp and optionally content ID."""
    async with get_db() as db:
        if last_content_id:
            await db.execute(
                """
                UPDATE autopilot_monitors
                SET last_checked_at = CURRENT_TIMESTAMP, last_content_id = ?
                WHERE id = ?
                """,
                (last_content_id, monitor_id)
            )
        else:
            await db.execute(
                """
                UPDATE autopilot_monitors
                SET last_checked_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (monitor_id,)
            )
        await db.commit()


async def save_autopilot_run(
    monitor_id: int,
    status: str,
    content_found: int,
    jobs_created: int,
    error_message: Optional[str] = None,
):
    """Save an autopilot run record."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO autopilot_runs
                (monitor_id, run_status, content_found, jobs_created, error_message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (monitor_id, status, content_found, jobs_created, error_message)
        )
        await db.commit()


async def create_autopilot_job(
    user_id: int,
    content_url: str,
    content_title: str,
    persona_id: str,
    magic_words: Optional[str],
    source_type: str,
    monitor_id: int,
) -> str:
    """
    Create a processing job from autopilot-discovered content.

    Args:
        user_id: User ID
        content_url: URL of the content
        content_title: Title of the content
        persona_id: Target persona ID
        magic_words: Optional domain vocabulary
        source_type: Type of source (youtube, podcast, rss)
        monitor_id: ID of the monitor that found this content

    Returns:
        Job ID
    """
    job_id = str(uuid.uuid4())

    # Create job directory
    job_dir = settings.upload_dir / str(user_id) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Determine file type based on source
    if source_type == "youtube":
        file_type = "youtube_url"
        original_filename = f"youtube_{content_title[:50]}.url"
    elif source_type == "podcast":
        file_type = "audio_url"
        original_filename = f"podcast_{content_title[:50]}.url"
    else:  # RSS text content
        file_type = "text"
        original_filename = f"article_{content_title[:50]}.txt"

    # For RSS, fetch the article content now
    transcript = None
    if source_type == "rss":
        try:
            transcript = await fetch_article_content(content_url)
        except Exception as e:
            print(f"[Autopilot] Failed to fetch article content: {e}")
            transcript = f"Failed to fetch article from {content_url}"

    # Default asset types
    asset_types = ["linkedin"]
    asset_quantities = {"linkedin": 3}

    # Store persona as JSON array format
    personas_list = [{"type": "preset", "id": persona_id}]

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO jobs (
                id, user_id, status, original_filename, file_type,
                target_persona, asset_types, asset_quantities,
                processing_mode, magic_words, current_step, progress,
                transcript, autopilot_monitor_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user_id,
                JobStatus.QUEUED.value,
                original_filename,
                file_type,
                json.dumps(personas_list),
                json.dumps(asset_types),
                json.dumps(asset_quantities),
                "autopilot",
                magic_words,
                "Queued from Autopilot",
                0,
                transcript,
                monitor_id,
            )
        )
        await db.commit()

    # Save the content URL to a file for processing
    url_file = job_dir / "source_url.txt"
    with open(url_file, "w") as f:
        f.write(f"{content_url}\n{content_title}")

    return job_id


async def check_monitor(monitor_id: int) -> dict:
    """
    Check a single monitor for new content and process if found.

    Args:
        monitor_id: ID of the monitor to check

    Returns:
        Dict with status and results
    """
    monitor = await get_monitor_by_id(monitor_id)

    if not monitor:
        return {"status": "error", "error": "Monitor not found"}

    if not monitor["enabled"]:
        return {"status": "skipped", "reason": "disabled"}

    try:
        # Fetch based on source type
        if monitor["source_type"] == "youtube_channel":
            items = await fetch_youtube_channel_videos(
                monitor["source_url"],
                monitor["last_content_id"],
            )
        elif monitor["source_type"] == "rss":
            items = await fetch_rss_feed(
                monitor["source_url"],
                monitor["last_content_id"],
            )
        elif monitor["source_type"] == "podcast":
            items = await fetch_podcast_episodes(
                monitor["source_url"],
                monitor["last_content_id"],
            )
        else:
            raise ValueError(f"Unknown source type: {monitor['source_type']}")

        if not items:
            await save_autopilot_run(monitor_id, "no_new_content", 0, 0)
            await update_monitor_last_checked(monitor_id)
            return {
                "status": "no_new_content",
                "monitor": monitor["monitor_name"],
            }

        # Process each new item (reverse order so oldest first)
        jobs_created = 0
        for item in reversed(items):
            job_id = await create_autopilot_job(
                user_id=monitor["user_id"],
                content_url=item["url"],
                content_title=item["title"],
                persona_id=monitor["persona_id"],
                magic_words=monitor["magic_words"],
                source_type=item["source_type"],
                monitor_id=monitor_id,
            )
            jobs_created += 1

            # Queue for processing (import here to avoid circular imports)
            from app.services.pipeline import process_job
            asyncio.create_task(process_job(job_id))

        # Update monitor with latest content ID (first item is most recent)
        await update_monitor_last_checked(monitor_id, items[0]["content_id"])
        await save_autopilot_run(monitor_id, "success", len(items), jobs_created)

        return {
            "status": "success",
            "monitor": monitor["monitor_name"],
            "new_items": len(items),
            "jobs_created": jobs_created,
        }

    except Exception as e:
        error_msg = str(e)
        await save_autopilot_run(monitor_id, "error", 0, 0, error_msg)
        return {
            "status": "error",
            "monitor": monitor["monitor_name"],
            "error": error_msg,
        }


async def run_all_due_monitors() -> list[dict]:
    """
    Check all monitors that are due for checking.

    Returns:
        List of results for each checked monitor
    """
    monitors = await get_active_monitors()
    results = []

    for monitor in monitors:
        # Check if due
        if monitor["last_checked_at"]:
            # Parse the timestamp
            try:
                last_checked = datetime.fromisoformat(
                    monitor["last_checked_at"].replace("Z", "+00:00")
                )
            except ValueError:
                # Try parsing without timezone
                last_checked = datetime.strptime(
                    monitor["last_checked_at"],
                    "%Y-%m-%d %H:%M:%S"
                )

            next_check = last_checked + timedelta(hours=monitor["check_interval_hours"])
            if datetime.now() < next_check:
                continue

        result = await check_monitor(monitor["id"])
        results.append(result)

    return results


async def get_user_monitors(user_id: int) -> list[dict]:
    """Get all monitors for a user."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM autopilot_monitors
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_monitor_runs(monitor_id: int, limit: int = 20) -> list[dict]:
    """Get run history for a monitor."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM autopilot_runs
            WHERE monitor_id = ?
            ORDER BY run_at DESC
            LIMIT ?
            """,
            (monitor_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def create_monitor(
    user_id: int,
    monitor_name: str,
    source_type: str,
    source_url: str,
    persona_id: str,
    magic_words: Optional[str] = None,
    check_interval_hours: int = 24,
) -> int:
    """
    Create a new autopilot monitor.

    Args:
        user_id: User ID
        monitor_name: Name for the monitor
        source_type: Type of source (youtube_channel, rss, podcast)
        source_url: URL of the source
        persona_id: Target persona ID
        magic_words: Optional domain vocabulary
        check_interval_hours: How often to check (1-168)

    Returns:
        Monitor ID
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO autopilot_monitors
                (user_id, monitor_name, source_type, source_url, persona_id,
                 magic_words, check_interval_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                monitor_name,
                source_type,
                source_url,
                persona_id,
                magic_words,
                check_interval_hours,
            )
        )
        await db.commit()
        return cursor.lastrowid


async def update_monitor(
    monitor_id: int,
    user_id: int,
    enabled: Optional[bool] = None,
    check_interval_hours: Optional[int] = None,
    persona_id: Optional[str] = None,
    magic_words: Optional[str] = None,
) -> bool:
    """Update a monitor's settings."""
    async with get_db() as db:
        # Build update query dynamically
        updates = []
        values = []

        if enabled is not None:
            updates.append("enabled = ?")
            values.append(enabled)

        if check_interval_hours is not None:
            updates.append("check_interval_hours = ?")
            values.append(check_interval_hours)

        if persona_id is not None:
            updates.append("persona_id = ?")
            values.append(persona_id)

        if magic_words is not None:
            updates.append("magic_words = ?")
            values.append(magic_words)

        if not updates:
            return True

        values.extend([monitor_id, user_id])

        await db.execute(
            f"""
            UPDATE autopilot_monitors
            SET {', '.join(updates)}
            WHERE id = ? AND user_id = ?
            """,
            tuple(values)
        )
        await db.commit()
        return True


async def delete_monitor(monitor_id: int, user_id: int) -> bool:
    """Delete a monitor."""
    async with get_db() as db:
        await db.execute(
            """
            DELETE FROM autopilot_monitors
            WHERE id = ? AND user_id = ?
            """,
            (monitor_id, user_id)
        )
        await db.commit()
        return True
