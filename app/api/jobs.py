"""Job management API endpoints."""
import json
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional

from app.database import get_db
from app.models.job import JobStatus, JobStatusResponse
from app.api.auth import get_current_user_id

router = APIRouter()


def estimate_time_remaining(status: str, progress: int) -> Optional[str]:
    """Estimate time remaining based on current progress."""
    if status == JobStatus.COMPLETE.value:
        return None
    if status == JobStatus.FAILED.value:
        return None

    # Rough estimates based on typical processing times
    if progress < 20:
        return "5-10 minutes"
    elif progress < 40:
        return "4-8 minutes"
    elif progress < 60:
        return "3-5 minutes"
    elif progress < 80:
        return "1-3 minutes"
    else:
        return "Less than 1 minute"


@router.get("/job/{job_id}/status")
async def get_job_status(
    job_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """
    Get the current status of a processing job.

    Returns progress percentage, current step, estimated time remaining,
    and partial transcript preview during processing.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, user_id, status, current_step, progress, error_message,
                   transcript, cleaned_transcript, preview_output
            FROM jobs WHERE id = ? AND user_id = ?
            """,
            (job_id, user_id)
        )
        row = await cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        # Get partial transcript preview (first 1000 chars)
        partial_transcript = None
        if row["cleaned_transcript"]:
            partial_transcript = row["cleaned_transcript"][:1000]
        elif row["transcript"]:
            partial_transcript = row["transcript"][:1000]

        # Get preview output if available
        preview_output = row["preview_output"] if "preview_output" in row.keys() else None

        return {
            "job_id": row["id"],
            "status": row["status"],
            "current_step": row["current_step"],
            "progress": row["progress"],
            "estimated_time_remaining": estimate_time_remaining(row["status"], row["progress"]),
            "error_message": row["error_message"],
            "partial_transcript": partial_transcript,
            "preview_output": preview_output,
        }


@router.get("/job/{job_id}/results")
async def get_job_results(
    job_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """
    Get the results of a completed job.

    Returns all generated outputs, extracted atoms, and cost information.
    """
    async with get_db() as db:
        # Get job details (scoped to user)
        cursor = await db.execute(
            """
            SELECT id, user_id, status, original_filename, target_persona,
                   asset_types, asset_quantities, cost_incurred,
                   created_at, completed_at, error_message
            FROM jobs WHERE id = ? AND user_id = ?
            """,
            (job_id, user_id)
        )
        job = await cursor.fetchone()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Get outputs
        cursor = await db.execute(
            """
            SELECT id, content_type, variation_number,
                   step1_draft, step2_edited, step3_final,
                   atoms_used, citations, warnings, quality_scores, hook_variations,
                   subject_line, preview_text, send_day, email_type, cta_text
            FROM outputs WHERE job_id = ?
            ORDER BY content_type, variation_number
            """,
            (job_id,)
        )
        output_rows = await cursor.fetchall()

        outputs = []
        for row in output_rows:
            output_data = {
                "id": row["id"],
                "content_type": row["content_type"],
                "variation_number": row["variation_number"],
                "step1_draft": row["step1_draft"],
                "step2_edited": row["step2_edited"],
                "step3_final": row["step3_final"],
                "atoms_used": json.loads(row["atoms_used"]) if row["atoms_used"] else [],
                "citations": json.loads(row["citations"]) if row["citations"] else [],
                "warnings": json.loads(row["warnings"]) if row["warnings"] else [],
                "quality_scores": json.loads(row["quality_scores"]) if row["quality_scores"] else None,
                "hook_variations": json.loads(row["hook_variations"]) if row["hook_variations"] else None,
            }

            # Add email sequence specific fields if present
            if row["content_type"] == "email_sequence":
                output_data["subject_line"] = row["subject_line"]
                output_data["preview_text"] = row["preview_text"]
                output_data["send_day"] = row["send_day"]
                output_data["email_type"] = row["email_type"]
                output_data["cta_text"] = row["cta_text"]
                # Use step1_draft as body if step3_final not available
                output_data["body"] = row["step3_final"] or row["step2_edited"] or row["step1_draft"]

            outputs.append(output_data)

        # Get atoms
        cursor = await db.execute(
            """
            SELECT id, atom_type, content, source_location,
                   tags, persona_relevance, quote_attribution
            FROM atoms WHERE job_id = ?
            ORDER BY atom_type
            """,
            (job_id,)
        )
        atom_rows = await cursor.fetchall()

        atoms = []
        for row in atom_rows:
            atoms.append({
                "id": row["id"],
                "type": row["atom_type"],
                "content": row["content"],
                "source_location": row["source_location"],
                "tags": json.loads(row["tags"]) if row["tags"] else [],
                "persona_relevance": json.loads(row["persona_relevance"]) if row["persona_relevance"] else {},
                "quote_attribution": row["quote_attribution"],
            })

        return {
            "job_id": job["id"],
            "status": job["status"],
            "original_filename": job["original_filename"],
            "target_persona": job["target_persona"],
            "asset_types": json.loads(job["asset_types"]) if job["asset_types"] else [],
            "asset_quantities": json.loads(job["asset_quantities"]) if job["asset_quantities"] else {},
            "outputs": outputs,
            "atoms": atoms,
            "cost_incurred": job["cost_incurred"],
            "created_at": job["created_at"],
            "completed_at": job["completed_at"],
            "error_message": job["error_message"],
        }


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    user_id: int = Depends(get_current_user_id),
):
    """
    List all jobs for the current user with optional status filter.
    """
    async with get_db() as db:
        query = """
            SELECT id, status, original_filename, target_persona,
                   current_step, progress, cost_incurred,
                   created_at, completed_at
            FROM jobs
            WHERE user_id = ?
        """
        params = [user_id]

        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()

        jobs = []
        for row in rows:
            jobs.append({
                "id": row["id"],
                "status": row["status"],
                "original_filename": row["original_filename"],
                "target_persona": row["target_persona"],
                "current_step": row["current_step"],
                "progress": row["progress"],
                "cost_incurred": row["cost_incurred"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            })

        # Get total count for this user
        count_query = "SELECT COUNT(*) FROM jobs WHERE user_id = ?"
        count_params = [user_id]

        if status:
            count_query += " AND status = ?"
            count_params.append(status)

        cursor = await db.execute(count_query, count_params)
        total = (await cursor.fetchone())[0]

        return {
            "jobs": jobs,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.get("/usage")
async def get_usage_stats(user_id: int = Depends(get_current_user_id)):
    """
    Get usage statistics and costs for the current user.
    """
    async with get_db() as db:
        # Get user's total cost
        cursor = await db.execute(
            "SELECT total_cost_incurred FROM users WHERE id = ?",
            (user_id,)
        )
        user_row = await cursor.fetchone()
        total_cost = user_row["total_cost_incurred"] if user_row else 0.0

        # Get job counts by status
        cursor = await db.execute(
            """
            SELECT status, COUNT(*) as count
            FROM jobs
            WHERE user_id = ?
            GROUP BY status
            """,
            (user_id,)
        )
        status_counts = {row["status"]: row["count"] for row in await cursor.fetchall()}

        # Get total jobs
        cursor = await db.execute(
            "SELECT COUNT(*) FROM jobs WHERE user_id = ?",
            (user_id,)
        )
        total_jobs = (await cursor.fetchone())[0]

        # Get cost breakdown by month (last 6 months)
        cursor = await db.execute(
            """
            SELECT
                strftime('%Y-%m', created_at) as month,
                SUM(cost_incurred) as cost,
                COUNT(*) as job_count
            FROM jobs
            WHERE user_id = ?
                AND created_at >= date('now', '-6 months')
            GROUP BY strftime('%Y-%m', created_at)
            ORDER BY month DESC
            """,
            (user_id,)
        )
        monthly_costs = [
            {"month": row["month"], "cost": row["cost"], "jobs": row["job_count"]}
            for row in await cursor.fetchall()
        ]

        # Get content type breakdown
        cursor = await db.execute(
            """
            SELECT content_type, COUNT(*) as count
            FROM outputs o
            JOIN jobs j ON o.job_id = j.id
            WHERE j.user_id = ?
            GROUP BY content_type
            """,
            (user_id,)
        )
        content_counts = {row["content_type"]: row["count"] for row in await cursor.fetchall()}

        # Get library size
        cursor = await db.execute(
            "SELECT COUNT(*) FROM content_library WHERE user_id = ?",
            (user_id,)
        )
        library_size = (await cursor.fetchone())[0]

        return {
            "total_cost": round(total_cost, 4),
            "total_jobs": total_jobs,
            "jobs_by_status": status_counts,
            "monthly_costs": monthly_costs,
            "content_created": content_counts,
            "library_size": library_size,
        }
