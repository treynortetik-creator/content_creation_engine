"""Batch processing API - Upload and process multiple files at once."""
import uuid
import json
import asyncio
import aiofiles
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Depends, Request
from pydantic import BaseModel

from app.config import get_settings
from app.database import get_db
from app.models.job import JobStatus
from app.api.auth import get_current_user_id
from app.api.upload import get_file_type

router = APIRouter()
settings = get_settings()

# Maximum concurrent jobs in a batch
MAX_CONCURRENT_JOBS = 3


class BatchResponse(BaseModel):
    """Response for batch upload."""
    batch_id: str
    total_jobs: int
    job_ids: list[str]
    message: str


class BatchStatusResponse(BaseModel):
    """Response for batch status."""
    batch_id: str
    status: str
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    jobs: list[dict]


@router.post("/batch/upload", response_model=BatchResponse)
async def batch_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    target_personas: str = Form(...),
    asset_types: str = Form(default='["linkedin"]'),
    asset_quantities: str = Form(default='{"linkedin": 3}'),
    processing_mode: str = Form(default="autopilot"),
    user_id: int = Depends(get_current_user_id),
):
    """
    Upload multiple files for batch processing.

    All files will share the same persona and asset settings.
    Files are processed in parallel (up to MAX_CONCURRENT_JOBS at a time).
    """
    if len(files) > 10:
        raise HTTPException(
            status_code=400,
            detail="Maximum 10 files per batch"
        )

    if len(files) == 0:
        raise HTTPException(
            status_code=400,
            detail="At least one file is required"
        )

    # Parse JSON fields
    try:
        asset_types_list = json.loads(asset_types)
        asset_quantities_dict = json.loads(asset_quantities)
        personas_list = json.loads(target_personas)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON in form fields"
        )

    # Validate personas
    if not personas_list:
        raise HTTPException(
            status_code=400,
            detail="At least one target persona is required"
        )

    # Create batch ID
    batch_id = str(uuid.uuid4())[:8]
    job_ids = []

    async with get_db() as db:
        # Create batch record
        await db.execute(
            """
            INSERT INTO batches (id, user_id, total_jobs, status)
            VALUES (?, ?, ?, ?)
            """,
            (batch_id, user_id, len(files), "processing")
        )
        await db.commit()

        # Create jobs for each file
        for file in files:
            # Validate file type
            file_type = get_file_type(file.filename, file.content_type)
            if not file_type:
                continue  # Skip unsupported files

            # Check file size
            file.file.seek(0, 2)
            file_size = file.file.tell()
            file.file.seek(0)

            max_size = settings.upload_max_size_mb * 1024 * 1024
            if file_size > max_size:
                continue  # Skip oversized files

            # Create job ID
            job_id = str(uuid.uuid4())
            job_ids.append(job_id)

            # Create job directory
            job_dir = settings.upload_dir / str(user_id) / job_id
            job_dir.mkdir(parents=True, exist_ok=True)

            # Save uploaded file
            file_path = job_dir / file.filename
            content = await file.read()
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(content)

            # Create job record
            await db.execute(
                """
                INSERT INTO jobs (
                    id, user_id, batch_id, status, original_filename, file_type, file_size,
                    target_persona, asset_types, asset_quantities, processing_mode,
                    current_step, progress
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    user_id,
                    batch_id,
                    JobStatus.UPLOADING.value,
                    file.filename,
                    file_type,
                    file_size,
                    json.dumps(personas_list),
                    json.dumps(asset_types_list),
                    json.dumps(asset_quantities_dict),
                    processing_mode,
                    "Queued for batch processing",
                    0,
                )
            )

        # Update batch with actual job count (excluding skipped files)
        await db.execute(
            "UPDATE batches SET total_jobs = ? WHERE id = ?",
            (len(job_ids), batch_id)
        )
        await db.commit()

    if not job_ids:
        raise HTTPException(
            status_code=400,
            detail="No valid files in batch"
        )

    # Start batch processing
    background_tasks.add_task(process_batch, batch_id, job_ids, user_id)

    return BatchResponse(
        batch_id=batch_id,
        total_jobs=len(job_ids),
        job_ids=job_ids,
        message=f"Batch created with {len(job_ids)} jobs. Processing started."
    )


async def process_batch(batch_id: str, job_ids: list[str], user_id: int):
    """Process all jobs in a batch with concurrency control."""
    from app.services.pipeline import process_job

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

    async def process_with_semaphore(job_id: str):
        async with semaphore:
            try:
                await process_job(job_id)
                return job_id, True
            except Exception as e:
                print(f"Batch job {job_id} failed: {e}")
                return job_id, False

    # Process all jobs concurrently (limited by semaphore)
    results = await asyncio.gather(
        *[process_with_semaphore(job_id) for job_id in job_ids],
        return_exceptions=True
    )

    # Count successes and failures
    completed = sum(1 for r in results if isinstance(r, tuple) and r[1])
    failed = len(job_ids) - completed

    # Update batch status
    async with get_db() as db:
        await db.execute(
            """
            UPDATE batches
            SET status = ?, completed_jobs = ?, failed_jobs = ?, completed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("complete" if failed == 0 else "partial", completed, failed, batch_id)
        )
        await db.commit()


@router.get("/batch/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status(
    batch_id: str,
    user_id: int = Depends(get_current_user_id),
):
    """Get status of a batch and all its jobs."""
    async with get_db() as db:
        # Get batch
        cursor = await db.execute(
            """
            SELECT id, total_jobs, completed_jobs, failed_jobs, status, created_at, completed_at
            FROM batches WHERE id = ? AND user_id = ?
            """,
            (batch_id, user_id)
        )
        batch = await cursor.fetchone()

        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Get jobs in batch
        cursor = await db.execute(
            """
            SELECT id, original_filename, status, progress, current_step, error_message
            FROM jobs WHERE batch_id = ?
            ORDER BY created_at
            """,
            (batch_id,)
        )
        job_rows = await cursor.fetchall()

        jobs = []
        for row in job_rows:
            jobs.append({
                "job_id": row["id"],
                "filename": row["original_filename"],
                "status": row["status"],
                "progress": row["progress"],
                "current_step": row["current_step"],
                "error": row["error_message"],
            })

        return BatchStatusResponse(
            batch_id=batch["id"],
            status=batch["status"],
            total_jobs=batch["total_jobs"],
            completed_jobs=batch["completed_jobs"],
            failed_jobs=batch["failed_jobs"],
            jobs=jobs,
        )


@router.get("/batches")
async def list_batches(
    user_id: int = Depends(get_current_user_id),
):
    """List all batches for the current user."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id, total_jobs, completed_jobs, failed_jobs, status, created_at, completed_at
            FROM batches WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (user_id,)
        )
        rows = await cursor.fetchall()

        batches = []
        for row in rows:
            batches.append({
                "batch_id": row["id"],
                "total_jobs": row["total_jobs"],
                "completed_jobs": row["completed_jobs"],
                "failed_jobs": row["failed_jobs"],
                "status": row["status"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            })

        return {"batches": batches}
