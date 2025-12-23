"""Content Edit API - Post-output tone adjustments and AI editing."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user_id
from app.services.content_editor import (
    adjust_tone,
    apply_manual_edits,
    regenerate_section,
    get_tone_presets_list,
)

router = APIRouter()


class ToneAdjustRequest(BaseModel):
    output_id: int
    tone_preset: Optional[str] = None
    custom_instruction: Optional[str] = None


class ManualEditRequest(BaseModel):
    output_id: int
    user_edits: str
    edit_instruction: str = "Polish and integrate my edits"


class SectionEditRequest(BaseModel):
    output_id: int
    section_text: str
    instruction: str


class SaveContentRequest(BaseModel):
    new_content: str


async def get_output_by_id(output_id: int, user_id: int) -> Optional[dict]:
    """Get an output by ID, verifying user ownership."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT o.*, j.user_id
            FROM outputs o
            JOIN jobs j ON o.job_id = j.id
            WHERE o.id = ? AND j.user_id = ?
            """,
            (output_id, user_id)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def save_output_edit(
    output_id: int,
    edit_type: str,
    instruction: str,
    original_content: str,
    edited_content: str,
):
    """Save an edit to the history."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO output_edits
                (output_id, edit_type, instruction, original_content, edited_content)
            VALUES (?, ?, ?, ?, ?)
            """,
            (output_id, edit_type, instruction, original_content, edited_content)
        )
        await db.commit()


async def update_output_content(output_id: int, new_content: str):
    """Update the output content."""
    async with get_db() as db:
        # Update the step3_final field (or step2_edited if step3 doesn't exist)
        await db.execute(
            """
            UPDATE outputs
            SET step3_final = ?,
                user_edits = COALESCE(user_edits, 0) + 1
            WHERE id = ?
            """,
            (new_content, output_id)
        )
        await db.commit()


async def get_output_edit_history(output_id: int) -> list[dict]:
    """Get edit history for an output."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM output_edits
            WHERE output_id = ?
            ORDER BY created_at DESC
            """,
            (output_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


@router.get("/edit/tone-presets")
async def get_tone_presets():
    """Get available tone adjustment presets."""
    return {"presets": get_tone_presets_list()}


@router.post("/edit/adjust-tone")
async def adjust_tone_endpoint(
    request: ToneAdjustRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Adjust tone of an existing output.

    Provide either tone_preset or custom_instruction.
    """
    # Get the output
    output = await get_output_by_id(request.output_id, user_id)
    if not output:
        raise HTTPException(404, "Output not found")

    if not request.tone_preset and not request.custom_instruction:
        raise HTTPException(400, "Provide tone_preset or custom_instruction")

    # Get the current content (prefer step3_final, then step2, then step1)
    content = (
        output.get("step3_final")
        or output.get("step2_edited")
        or output.get("step1_draft")
    )

    if not content:
        raise HTTPException(400, "No content found in output")

    # Adjust tone
    try:
        result = await adjust_tone(
            content=content,
            content_type=output["content_type"],
            tone_preset=request.tone_preset,
            custom_instruction=request.custom_instruction,
            user_id=user_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Save edit history
    await save_output_edit(
        output_id=request.output_id,
        edit_type="tone_adjustment" if request.tone_preset else "custom_instruction",
        instruction=result["instruction"],
        original_content=result["original"],
        edited_content=result["edited"],
    )

    return {
        "success": True,
        "original": result["original"],
        "edited": result["edited"],
        "instruction": result["instruction"],
        "preset_used": result.get("preset_used"),
    }


@router.post("/edit/apply-changes")
async def apply_changes_endpoint(
    request: ManualEditRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    User made manual edits, AI polishes them.

    Send the user's edited version and the AI will polish it.
    """
    output = await get_output_by_id(request.output_id, user_id)
    if not output:
        raise HTTPException(404, "Output not found")

    content = (
        output.get("step3_final")
        or output.get("step2_edited")
        or output.get("step1_draft")
    )

    if not content:
        raise HTTPException(400, "No content found in output")

    result = await apply_manual_edits(
        content=content,
        user_edits=request.user_edits,
        edit_instruction=request.edit_instruction,
        content_type=output["content_type"],
    )

    await save_output_edit(
        output_id=request.output_id,
        edit_type="manual_edit",
        instruction=request.edit_instruction,
        original_content=content,
        edited_content=result["polished"],
    )

    return {"success": True, "polished": result["polished"]}


@router.post("/edit/regenerate-section")
async def regenerate_section_endpoint(
    request: SectionEditRequest,
    user_id: int = Depends(get_current_user_id),
):
    """
    Regenerate just a section of the content.

    Select a section and provide instructions for how to change it.
    """
    output = await get_output_by_id(request.output_id, user_id)
    if not output:
        raise HTTPException(404, "Output not found")

    content = (
        output.get("step3_final")
        or output.get("step2_edited")
        or output.get("step1_draft")
    )

    if not content:
        raise HTTPException(400, "No content found in output")

    result = await regenerate_section(
        full_content=content,
        section_to_change=request.section_text,
        instruction=request.instruction,
        content_type=output["content_type"],
    )

    await save_output_edit(
        output_id=request.output_id,
        edit_type="section_regenerate",
        instruction=request.instruction,
        original_content=content,
        edited_content=result["updated_content"],
    )

    return {"success": True, "updated_content": result["updated_content"]}


@router.post("/edit/{output_id}/save")
async def save_edited_content(
    output_id: int,
    request: SaveContentRequest,
    user_id: int = Depends(get_current_user_id),
):
    """Save edited content as the new version."""
    output = await get_output_by_id(output_id, user_id)
    if not output:
        raise HTTPException(404, "Output not found")

    await update_output_content(output_id, request.new_content)

    return {"success": True, "message": "Content saved"}


@router.get("/edit/{output_id}/history")
async def get_edit_history(
    output_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Get edit history for an output."""
    # Verify ownership
    output = await get_output_by_id(output_id, user_id)
    if not output:
        raise HTTPException(404, "Output not found")

    history = await get_output_edit_history(output_id)

    return {"history": history}


@router.post("/edit/{output_id}/revert")
async def revert_to_version(
    output_id: int,
    edit_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Revert an output to a previous version from edit history."""
    output = await get_output_by_id(output_id, user_id)
    if not output:
        raise HTTPException(404, "Output not found")

    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT edited_content FROM output_edits
            WHERE id = ? AND output_id = ?
            """,
            (edit_id, output_id)
        )
        row = await cursor.fetchone()

        if not row:
            raise HTTPException(404, "Edit version not found")

        # Get current content for history
        current_content = (
            output.get("step3_final")
            or output.get("step2_edited")
            or output.get("step1_draft")
        )

        # Save the revert as a new edit
        await save_output_edit(
            output_id=output_id,
            edit_type="revert",
            instruction=f"Reverted to version from edit #{edit_id}",
            original_content=current_content,
            edited_content=row["edited_content"],
        )

        # Update the output
        await update_output_content(output_id, row["edited_content"])

    return {"success": True, "message": "Reverted to previous version"}
