"""Pipeline orchestrator - coordinates the 4-step content generation process."""
import json
import traceback
from pathlib import Path
from datetime import datetime

from app.config import get_settings
from app.database import get_db
from app.models.job import JobStatus
from app.services.transcription import transcribe_file, cleanup_transcript, extract_document_content
from app.services.atomization import atomize_content
from app.services.drafting import draft_linkedin_posts, draft_blog_post, draft_email, draft_linkedin_quick
from app.services.editing import batch_edit_content
from app.services.factcheck import batch_factcheck_content
from app.services.library_manager import (
    add_atoms_to_library,
    save_atoms_to_db,
    save_outputs_to_db,
)
from app.services.persona_manager import resolve_personas, combine_personas_for_prompt
from app.services.scoring import batch_score_content
from app.services.hook_generator import generate_hook_variations, split_hook_and_body, combine_hooks_with_body
from app.api.brand_voice import get_user_brand_context
from app.utils.error_messages import format_pipeline_error, detect_error_type, get_error_message

settings = get_settings()


def select_top_atoms(atoms: list[dict], count: int = 3) -> list[dict]:
    """
    Select the top atoms by persona relevance for preview generation.

    Args:
        atoms: List of atoms with persona_relevance scores
        count: Number of atoms to select

    Returns:
        Top N atoms sorted by highest persona relevance
    """
    def get_relevance_score(atom: dict) -> float:
        """Extract maximum relevance score from persona_relevance dict."""
        relevance = atom.get("persona_relevance", {})
        if isinstance(relevance, dict) and relevance:
            return max(relevance.values())
        return 0.5  # Default middle score

    # Sort by relevance score descending
    sorted_atoms = sorted(atoms, key=get_relevance_score, reverse=True)
    return sorted_atoms[:count]


async def save_preview_output(job_id: str, preview_content: str):
    """Save preview output to the job record."""
    async with get_db() as db:
        await db.execute(
            "UPDATE jobs SET preview_output = ? WHERE id = ?",
            (preview_content, job_id)
        )
        await db.commit()


async def update_job_status(
    job_id: str,
    status: JobStatus,
    current_step: str,
    progress: int,
    cost_to_add: float = 0.0,
    error_message: str = None,
):
    """Update job status in database and track user costs."""
    async with get_db() as db:
        if error_message:
            await db.execute(
                """
                UPDATE jobs SET
                    status = ?, current_step = ?, progress = ?,
                    cost_incurred = cost_incurred + ?, error_message = ?
                WHERE id = ?
                """,
                (status.value, current_step, progress, cost_to_add, error_message, job_id)
            )
        else:
            await db.execute(
                """
                UPDATE jobs SET
                    status = ?, current_step = ?, progress = ?,
                    cost_incurred = cost_incurred + ?
                WHERE id = ?
                """,
                (status.value, current_step, progress, cost_to_add, job_id)
            )

        # Update user's total cost if cost was added
        if cost_to_add > 0:
            await db.execute(
                """
                UPDATE users SET total_cost_incurred = total_cost_incurred + ?
                WHERE id = (SELECT user_id FROM jobs WHERE id = ?)
                """,
                (cost_to_add, job_id)
            )

        await db.commit()


async def get_job_data(job_id: str) -> dict:
    """Get job data from database."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def process_job(job_id: str):
    """
    Main pipeline orchestrator.

    Steps:
    0. Transcribe (if needed) + Clean
    1. Atomize content
    2. Draft content
    3. Edit for audience
    4. Fact-check
    5. Save results
    """
    total_cost = 0.0

    try:
        # Get job data
        job_data = await get_job_data(job_id)
        if not job_data:
            return

        user_id = job_data["user_id"]
        # Parse personas (now stored as JSON array)
        target_persona_raw = job_data["target_persona"]
        try:
            personas_list = json.loads(target_persona_raw) if target_persona_raw else []
        except json.JSONDecodeError:
            # Backwards compatibility: treat as single persona ID
            personas_list = [{"type": "preset", "id": target_persona_raw}]

        # Resolve persona references to full persona objects
        resolved_personas = await resolve_personas(personas_list)
        combined_persona = combine_personas_for_prompt(resolved_personas)

        asset_types = json.loads(job_data["asset_types"]) if job_data["asset_types"] else ["linkedin"]
        asset_quantities = json.loads(job_data["asset_quantities"]) if job_data["asset_quantities"] else {}
        original_filename = job_data["original_filename"]
        file_type = job_data["file_type"]
        magic_words = job_data.get("magic_words")

        # Check if we already have transcript (text upload)
        transcript = job_data.get("transcript")
        cleaned_transcript = job_data.get("cleaned_transcript")

        file_path = settings.upload_dir / str(user_id) / job_id / original_filename

        # Determine processing path based on file type
        # Documents (PDF, DOCX, TXT, MD) go directly to extraction → atomization
        # Audio/Video go through transcription → cleanup → atomization
        is_document = file_type == "document"
        is_audio_video = file_type in ["audio", "video"]

        if is_document:
            # ======== DOCUMENT PATH: Extract content directly ========
            if not cleaned_transcript:
                await update_job_status(
                    job_id, JobStatus.TRANSCRIBING,
                    "Extracting document content", 15
                )

                if not file_path.exists():
                    raise FileNotFoundError(f"Upload file not found: {file_path}")

                # Extract document content with image/graph analysis
                # This goes directly to cleaned_transcript (no cleanup needed for documents)
                extracted_content, extract_cost = await extract_document_content(
                    file_path, job_id, user_id
                )
                total_cost += extract_cost

                # For documents, the extracted content IS the cleaned transcript
                # No cleanup step needed since it's not spoken content
                cleaned_transcript = extracted_content
                transcript = extracted_content

                # Save both transcript and cleaned_transcript
                async with get_db() as db:
                    await db.execute(
                        "UPDATE jobs SET transcript = ?, cleaned_transcript = ? WHERE id = ?",
                        (transcript, cleaned_transcript, job_id)
                    )
                    await db.commit()

        elif is_audio_video:
            # ======== AUDIO/VIDEO PATH: Transcribe → Cleanup ========
            # Step 0a: Transcription
            if not transcript:
                await update_job_status(
                    job_id, JobStatus.TRANSCRIBING,
                    "Step 0a: Transcribing content", 10
                )

                if not file_path.exists():
                    raise FileNotFoundError(f"Upload file not found: {file_path}")

                transcript, trans_cost = await transcribe_file(
                    file_path, file_type, job_id, user_id, magic_words
                )
                total_cost += trans_cost

                # Save transcript to database
                async with get_db() as db:
                    await db.execute(
                        "UPDATE jobs SET transcript = ? WHERE id = ?",
                        (transcript, job_id)
                    )
                    await db.commit()

            # Step 0b: Cleanup (for spoken content with filler words, etc.)
            if not cleaned_transcript:
                await update_job_status(
                    job_id, JobStatus.CLEANING,
                    "Step 0b: Cleaning transcript", 20, total_cost
                )
                total_cost = 0  # Reset after update

                # Use transcript or the text that was uploaded
                source_text = transcript or job_data.get("transcript", "")

                cleaned_transcript, clean_cost = await cleanup_transcript(source_text)
                total_cost += clean_cost

                # Save cleaned transcript
                async with get_db() as db:
                    await db.execute(
                        "UPDATE jobs SET cleaned_transcript = ? WHERE id = ?",
                        (cleaned_transcript, job_id)
                    )
                    await db.commit()

        else:
            # ======== TEXT UPLOAD PATH (file_type == "text") ========
            # Text was already saved to transcript, just need cleanup
            if not cleaned_transcript:
                await update_job_status(
                    job_id, JobStatus.CLEANING,
                    "Processing text content", 20, total_cost
                )
                total_cost = 0

                source_text = transcript or job_data.get("transcript", "")

                # For pasted text, do a light cleanup
                cleaned_transcript, clean_cost = await cleanup_transcript(source_text)
                total_cost += clean_cost

                async with get_db() as db:
                    await db.execute(
                        "UPDATE jobs SET cleaned_transcript = ? WHERE id = ?",
                        (cleaned_transcript, job_id)
                    )
                    await db.commit()

        # ======== STEP 1: ATOMIZATION ========
        await update_job_status(
            job_id, JobStatus.ATOMIZING,
            "Step 1: Extracting content atoms", 30, total_cost
        )
        total_cost = 0

        atoms, atom_cost = await atomize_content(
            cleaned_transcript, combined_persona, job_id, user_id
        )
        total_cost += atom_cost

        # Save atoms to database and library
        await save_atoms_to_db(atoms)
        await add_atoms_to_library(atoms, user_id, original_filename)

        # ======== QUICK WIN PREVIEW ========
        # Generate a preview LinkedIn post immediately so users see output fast
        if "linkedin" in asset_types:
            try:
                await update_job_status(
                    job_id, JobStatus.ATOMIZING,
                    "Generating quick preview...", 40, total_cost
                )
                total_cost = 0

                # Select top 3 atoms by persona relevance
                best_atoms = select_top_atoms(atoms, count=3)

                # Generate quick preview
                preview_post, preview_cost = await draft_linkedin_quick(
                    best_atoms, combined_persona, user_id=user_id
                )
                total_cost += preview_cost

                # Save preview
                await save_preview_output(job_id, preview_post)

            except Exception as preview_error:
                # Don't fail the job if preview fails, just log and continue
                print(f"Preview generation failed for job {job_id}: {preview_error}")

        # ======== STEP 2: DRAFTING ========
        await update_job_status(
            job_id, JobStatus.DRAFTING,
            "Step 2: Drafting content", 50, total_cost
        )
        total_cost = 0

        all_drafts = []

        # Generate LinkedIn posts if requested
        if "linkedin" in asset_types:
            count = asset_quantities.get("linkedin", 3)
            linkedin_drafts, li_cost = await draft_linkedin_posts(
                atoms, combined_persona, count, user_id=user_id
            )
            total_cost += li_cost

            for i, draft in enumerate(linkedin_drafts):
                all_drafts.append({
                    "content_type": "linkedin",
                    "variation_number": i + 1,
                    "content": draft.get("content", ""),
                    "atoms_used": draft.get("atoms_used", []),
                })

        # Generate blog post if requested
        if "blog" in asset_types:
            blog_draft, blog_cost = await draft_blog_post(atoms, combined_persona, user_id=user_id)
            total_cost += blog_cost

            all_drafts.append({
                "content_type": "blog",
                "variation_number": 1,
                "content": blog_draft.get("content", ""),
                "title": blog_draft.get("title", ""),
                "atoms_used": blog_draft.get("atoms_used", []),
            })

        # Generate email if requested
        if "email" in asset_types:
            email_draft, email_cost = await draft_email(atoms, combined_persona, user_id=user_id)
            total_cost += email_cost

            all_drafts.append({
                "content_type": "email",
                "variation_number": 1,
                "content": email_draft.get("body", ""),
                "subject": email_draft.get("subject", ""),
                "atoms_used": email_draft.get("atoms_used", []),
            })

        # ======== STEP 3: EDITING ========
        await update_job_status(
            job_id, JobStatus.EDITING,
            "Step 3: Editing for audience", 70, total_cost
        )
        total_cost = 0

        edited_drafts, edit_cost = await batch_edit_content(all_drafts, combined_persona)
        total_cost += edit_cost

        # ======== STEP 4: FACT-CHECKING ========
        await update_job_status(
            job_id, JobStatus.FACTCHECKING,
            "Step 4: Fact-checking content", 85, total_cost
        )
        total_cost = 0

        factchecked_drafts, fc_cost = await batch_factcheck_content(
            edited_drafts, cleaned_transcript
        )
        total_cost += fc_cost

        # ======== STEP 5: QUALITY SCORING ========
        await update_job_status(
            job_id, JobStatus.FACTCHECKING,
            "Step 5: Scoring content quality", 92, total_cost
        )
        total_cost = 0

        # Get persona title for context (use combined_persona)
        persona_title = combined_persona.get("title", "Target Audience")

        scored_drafts, score_cost = await batch_score_content(
            factchecked_drafts, persona_title
        )
        total_cost += score_cost

        # ======== STEP 6: HOOK VARIATIONS (LinkedIn only) ========
        if "linkedin" in asset_types:
            try:
                await update_job_status(
                    job_id, JobStatus.FACTCHECKING,
                    "Step 6: Generating hook variations", 96, total_cost
                )
                total_cost = 0

                # Get brand context for hook generation
                brand_context = await get_user_brand_context(user_id)

                # Generate hook variations for each LinkedIn post
                for draft in scored_drafts:
                    if draft.get("content_type") == "linkedin":
                        final_content = draft.get("step3_final") or draft.get("step2_edited") or draft.get("content", "")
                        if final_content:
                            # Split into hook and body
                            original_hook, body = split_hook_and_body(final_content)

                            # Generate 4 additional hooks
                            additional_hooks, hook_cost = await generate_hook_variations(
                                atoms=atoms[:5],  # Use first 5 atoms for context
                                post_body=body,
                                persona=combined_persona,
                                brand_context=brand_context,
                                count=4
                            )
                            total_cost += hook_cost

                            # Combine original + new hooks with body
                            all_hooks = [original_hook] + additional_hooks
                            all_variations = combine_hooks_with_body(all_hooks, body)

                            # Store hook variations
                            draft["hook_variations"] = {
                                "hooks": all_hooks,
                                "body": body,
                                "full_variations": all_variations,
                                "selected_index": 0
                            }

            except Exception as hook_error:
                # Don't fail the job if hook generation fails
                print(f"Hook variation generation failed for job {job_id}: {hook_error}")

        # ======== SAVE RESULTS ========
        await save_outputs_to_db(scored_drafts, job_id)

        # Mark job complete
        await update_job_status(
            job_id, JobStatus.COMPLETE,
            "Complete", 100, total_cost
        )

        # Update completed_at timestamp
        async with get_db() as db:
            await db.execute(
                "UPDATE jobs SET completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job_id,)
            )
            await db.commit()

    except Exception as e:
        # Log detailed error for debugging
        error_detail = f"{type(e).__name__}: {str(e)}\n{traceback.format_exc()}"
        print(f"Job {job_id} failed: {error_detail}")

        # Determine which step failed for context
        step_context = "unknown"
        job_data_check = await get_job_data(job_id)
        if job_data_check:
            current = job_data_check.get("current_step", "")
            if "transcrib" in current.lower():
                step_context = "transcription"
            elif "atom" in current.lower():
                step_context = "atomization"
            elif "draft" in current.lower():
                step_context = "drafting"
            elif "edit" in current.lower():
                step_context = "editing"
            elif "fact" in current.lower():
                step_context = "factchecking"

        # Get user-friendly error message
        user_error = format_pipeline_error(e, step_context, job_id)

        await update_job_status(
            job_id, JobStatus.FAILED,
            "Failed", 0, total_cost, user_error
        )


async def process_job_from_library(job_id: str, atom_content: list[dict]):
    """
    Process a job generated from library atoms.

    Skips transcription and atomization steps.
    """
    total_cost = 0.0

    try:
        # Get job data
        job_data = await get_job_data(job_id)
        if not job_data:
            return

        user_id = job_data["user_id"]
        target_persona = job_data["target_persona"]
        asset_types = json.loads(job_data["asset_types"]) if job_data["asset_types"] else ["linkedin"]
        asset_quantities = json.loads(job_data["asset_quantities"]) if job_data["asset_quantities"] else {}

        # Convert library entries to atom format
        atoms = []
        for entry in atom_content:
            atoms.append({
                "id": str(entry.get("id")),
                "atom_type": entry.get("type", "insight"),
                "content": entry.get("content", ""),
                "persona_relevance": entry.get("persona_relevance", {}),
            })

        # ======== STEP 2: DRAFTING ========
        await update_job_status(
            job_id, JobStatus.DRAFTING,
            "Drafting content from library", 50, 0
        )

        all_drafts = []

        if "linkedin" in asset_types:
            count = asset_quantities.get("linkedin", 2)
            linkedin_drafts, li_cost = await draft_linkedin_posts(
                atoms, target_persona, count, user_id=user_id
            )
            total_cost += li_cost

            for i, draft in enumerate(linkedin_drafts):
                all_drafts.append({
                    "content_type": "linkedin",
                    "variation_number": i + 1,
                    "content": draft.get("content", ""),
                    "atoms_used": draft.get("atoms_used", []),
                })

        if "blog" in asset_types:
            blog_draft, blog_cost = await draft_blog_post(atoms, target_persona, user_id=user_id)
            total_cost += blog_cost

            all_drafts.append({
                "content_type": "blog",
                "variation_number": 1,
                "content": blog_draft.get("content", ""),
                "title": blog_draft.get("title", ""),
                "atoms_used": blog_draft.get("atoms_used", []),
            })

        # ======== STEP 3: EDITING ========
        await update_job_status(
            job_id, JobStatus.EDITING,
            "Editing for audience", 70, total_cost
        )
        total_cost = 0

        edited_drafts, edit_cost = await batch_edit_content(all_drafts, target_persona)
        total_cost += edit_cost

        # ======== STEP 4: FACT-CHECKING ========
        # For library generation, we do a lighter fact-check
        await update_job_status(
            job_id, JobStatus.FACTCHECKING,
            "Final review", 85, total_cost
        )

        # Use atom content as "transcript" for fact-checking
        atom_text = "\n\n".join([a["content"] for a in atoms])
        factchecked_drafts, fc_cost = await batch_factcheck_content(
            edited_drafts, atom_text
        )
        total_cost += fc_cost

        # Save results
        await save_outputs_to_db(factchecked_drafts, job_id)

        # Mark complete
        await update_job_status(
            job_id, JobStatus.COMPLETE,
            "Complete", 100, total_cost
        )

        async with get_db() as db:
            await db.execute(
                "UPDATE jobs SET completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job_id,)
            )
            await db.commit()

    except Exception as e:
        error_detail = f"{type(e).__name__}: {str(e)}"
        print(f"Library job {job_id} failed: {error_detail}")

        # Get user-friendly error message
        user_error = format_pipeline_error(e, "generation", job_id)

        await update_job_status(
            job_id, JobStatus.FAILED,
            "Failed", 0, total_cost, user_error
        )
