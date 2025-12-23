"""File upload API endpoints."""
import uuid
import json
import asyncio
import aiofiles
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Depends, Request
from typing import Optional

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings
from app.database import get_db
from app.models.job import JobResponse, JobStatus
from app.api.auth import get_current_user_id

router = APIRouter()
settings = get_settings()

# Rate limiter (uses app state limiter)
limiter = Limiter(key_func=get_remote_address)

# Allowed file types
ALLOWED_EXTENSIONS = {
    "video": [".mp4", ".mov", ".avi", ".webm", ".mkv"],
    "audio": [".mp3", ".wav", ".m4a", ".ogg", ".flac"],
    "document": [".pdf", ".txt", ".md", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".webp"],
}

ALLOWED_MIME_TYPES = {
    "video/mp4", "video/quicktime", "video/x-msvideo", "video/webm",
    "audio/mpeg", "audio/wav", "audio/x-m4a", "audio/ogg", "audio/flac",
    "application/pdf", "text/plain", "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png", "image/jpeg", "image/gif", "image/webp",
}


def get_file_type(filename: str, content_type: str) -> Optional[str]:
    """Determine file type category from filename and content type."""
    ext = Path(filename).suffix.lower()

    for file_type, extensions in ALLOWED_EXTENSIONS.items():
        if ext in extensions:
            return file_type

    return None


@router.post("/upload", response_model=JobResponse)
@limiter.limit("10/hour")  # 10 uploads per hour per user
async def upload_content(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    target_personas: str = Form(...),
    asset_types: str = Form(default='["linkedin", "blog"]'),
    asset_quantities: str = Form(default='{"linkedin": 3, "blog": 1}'),
    processing_mode: str = Form(default="autopilot"),
    campaign_name: Optional[str] = Form(default=None),
    magic_words: Optional[str] = Form(default=None),
    user_id: int = Depends(get_current_user_id),
):
    """
    Upload content for processing.

    Accepts video, audio, PDF, or text files.
    Returns a job ID that can be used to track progress.
    """
    # Validate file type
    file_type = get_file_type(file.filename, file.content_type)
    if not file_type:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {list(ALLOWED_EXTENSIONS.keys())}"
        )

    # Check file size
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    max_size = settings.upload_max_size_mb * 1024 * 1024
    if file_size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {settings.upload_max_size_mb}MB"
        )

    # Parse JSON fields
    try:
        asset_types_list = json.loads(asset_types)
        asset_quantities_dict = json.loads(asset_quantities)
        personas_list = json.loads(target_personas)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON in asset_types, asset_quantities, or target_personas"
        )

    # Validate personas
    if not personas_list or len(personas_list) == 0:
        raise HTTPException(
            status_code=400,
            detail="At least one target persona is required"
        )

    # Create job ID
    job_id = str(uuid.uuid4())

    # Create job directory (scoped by user_id)
    job_dir = settings.upload_dir / str(user_id) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded file
    file_path = job_dir / file.filename
    async with aiofiles.open(file_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    # Create job in database (target_persona now stores JSON array)
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO jobs (
                id, user_id, status, original_filename, file_type, file_size,
                target_persona, asset_types, asset_quantities, processing_mode,
                campaign_name, magic_words, current_step, progress
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user_id,
                JobStatus.UPLOADING.value,
                file.filename,
                file_type,
                file_size,
                json.dumps(personas_list),
                json.dumps(asset_types_list),
                json.dumps(asset_quantities_dict),
                processing_mode,
                campaign_name,
                magic_words,
                "Uploading file",
                5,
            )
        )
        await db.commit()

    # Start background processing
    from app.services.pipeline import process_job
    background_tasks.add_task(process_job, job_id)

    return JobResponse(
        job_id=job_id,
        status=JobStatus.UPLOADING,
        message="File uploaded successfully. Processing started."
    )


@router.post("/upload-text", response_model=JobResponse)
@limiter.limit("10/hour")  # 10 uploads per hour per user
async def upload_text(
    request: Request,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    target_personas: str = Form(...),
    asset_types: str = Form(default='["linkedin", "blog"]'),
    asset_quantities: str = Form(default='{"linkedin": 3, "blog": 1}'),
    processing_mode: str = Form(default="autopilot"),
    campaign_name: Optional[str] = Form(default=None),
    content_name: Optional[str] = Form(default="pasted_content.txt"),
    magic_words: Optional[str] = Form(default=None),
    user_id: int = Depends(get_current_user_id),
):
    """
    Upload text content directly (paste text instead of file).
    """
    # Parse JSON fields
    try:
        asset_types_list = json.loads(asset_types)
        asset_quantities_dict = json.loads(asset_quantities)
        personas_list = json.loads(target_personas)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON in asset_types, asset_quantities, or target_personas"
        )

    # Validate personas
    if not personas_list or len(personas_list) == 0:
        raise HTTPException(
            status_code=400,
            detail="At least one target persona is required"
        )

    # Create job ID
    job_id = str(uuid.uuid4())

    # Create job directory (scoped by user_id)
    job_dir = settings.upload_dir / str(user_id) / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Save text content
    file_path = job_dir / content_name
    async with aiofiles.open(file_path, "w") as f:
        await f.write(content)

    # Create job in database (target_persona now stores JSON array)
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO jobs (
                id, user_id, status, original_filename, file_type, file_size,
                target_persona, asset_types, asset_quantities, processing_mode,
                campaign_name, magic_words, current_step, progress, transcript
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user_id,
                JobStatus.UPLOADING.value,
                content_name,
                "text",
                len(content.encode("utf-8")),
                json.dumps(personas_list),
                json.dumps(asset_types_list),
                json.dumps(asset_quantities_dict),
                processing_mode,
                campaign_name,
                magic_words,
                "Processing text content",
                10,
                content,  # Text content is already the transcript
            )
        )
        await db.commit()

    # Start background processing
    from app.services.pipeline import process_job
    background_tasks.add_task(process_job, job_id)

    return JobResponse(
        job_id=job_id,
        status=JobStatus.UPLOADING,
        message="Text content uploaded successfully. Processing started."
    )
