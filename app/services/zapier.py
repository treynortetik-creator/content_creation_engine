"""Zapier integration service - Send data to user webhooks."""
import json
from datetime import datetime
from typing import Optional

import httpx

from app.database import get_db


async def get_user_webhooks(user_id: int, event: str) -> list[dict]:
    """Get all enabled webhooks for a user and event type."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, webhook_url, webhook_name, trigger_event
            FROM zapier_webhooks
            WHERE user_id = ? AND trigger_event = ? AND enabled = TRUE
            """,
            (user_id, event)
        )
        rows = await cursor.fetchall()

        return [
            {
                "id": row["id"],
                "webhook_url": row["webhook_url"],
                "webhook_name": row["webhook_name"],
                "trigger_event": row["trigger_event"],
            }
            for row in rows
        ]


async def update_webhook_last_triggered(webhook_id: int, error: Optional[str] = None):
    """Update webhook's last triggered timestamp and error status."""
    async with get_db() as db:
        if error:
            await db.execute(
                """
                UPDATE zapier_webhooks
                SET last_triggered_at = CURRENT_TIMESTAMP, last_error = ?
                WHERE id = ?
                """,
                (error, webhook_id)
            )
        else:
            await db.execute(
                """
                UPDATE zapier_webhooks
                SET last_triggered_at = CURRENT_TIMESTAMP, last_error = NULL
                WHERE id = ?
                """,
                (webhook_id,)
            )
        await db.commit()


async def trigger_zapier_webhooks(user_id: int, event: str, data: dict):
    """
    Send data to all user's webhooks for the given event type.

    Events:
    - 'job_complete': When a job finishes processing
    - 'output_generated': When each output is generated (not used yet)
    """
    webhooks = await get_user_webhooks(user_id, event)

    if not webhooks:
        return

    for webhook in webhooks:
        try:
            async with httpx.AsyncClient() as client:
                payload = {
                    "event": event,
                    "timestamp": datetime.now().isoformat(),
                    "data": data
                }

                response = await client.post(
                    webhook["webhook_url"],
                    json=payload,
                    timeout=10.0,
                    headers={"Content-Type": "application/json"}
                )

                if response.status_code == 200:
                    await update_webhook_last_triggered(webhook["id"])
                else:
                    await update_webhook_last_triggered(
                        webhook["id"],
                        f"HTTP {response.status_code}: {response.text[:200]}"
                    )

        except httpx.TimeoutException:
            await update_webhook_last_triggered(webhook["id"], "Request timed out")
        except Exception as e:
            await update_webhook_last_triggered(webhook["id"], str(e)[:200])


async def trigger_job_complete_webhook(job_id: str, user_id: int):
    """
    Trigger webhooks when a job completes.

    Sends all outputs and atoms to Zapier for distribution.
    """
    async with get_db() as db:
        # Get job details
        cursor = await db.execute(
            """
            SELECT id, original_filename, target_persona, created_at, completed_at, cost_incurred
            FROM jobs WHERE id = ?
            """,
            (job_id,)
        )
        job = await cursor.fetchone()

        if not job:
            return

        # Get outputs
        cursor = await db.execute(
            """
            SELECT id, content_type, variation_number,
                   step3_final, step2_edited, step1_draft,
                   quality_scores, hook_variations,
                   subject_line, preview_text, send_day, email_type, cta_text
            FROM outputs WHERE job_id = ?
            ORDER BY content_type, variation_number
            """,
            (job_id,)
        )

        outputs = []
        for row in await cursor.fetchall():
            content = row["step3_final"] or row["step2_edited"] or row["step1_draft"] or ""
            quality = json.loads(row["quality_scores"]) if row["quality_scores"] else None
            hooks = json.loads(row["hook_variations"]) if row["hook_variations"] else None

            output_data = {
                "id": row["id"],
                "content_type": row["content_type"],
                "variation_number": row["variation_number"],
                "content": content,
                "quality_score": quality.get("overall_score") if quality else None,
            }

            # Add hook variations if available
            if hooks and hooks.get("full_variations"):
                output_data["hook_variations"] = hooks["full_variations"]

            # Add email-specific fields
            if row["content_type"] == "email_sequence":
                output_data["subject_line"] = row["subject_line"]
                output_data["preview_text"] = row["preview_text"]
                output_data["send_day"] = row["send_day"]
                output_data["email_type"] = row["email_type"]
                output_data["cta_text"] = row["cta_text"]

            outputs.append(output_data)

        # Get atom count
        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM atoms WHERE job_id = ?",
            (job_id,)
        )
        atom_row = await cursor.fetchone()
        atoms_count = atom_row["count"] if atom_row else 0

    # Build webhook payload
    data = {
        "job_id": job["id"],
        "filename": job["original_filename"],
        "persona": job["target_persona"],
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
        "cost": job["cost_incurred"],
        "outputs_count": len(outputs),
        "atoms_extracted": atoms_count,
        "outputs": outputs,
    }

    await trigger_zapier_webhooks(user_id, "job_complete", data)
