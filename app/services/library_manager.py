"""Content library management service."""
import json
from typing import Optional

from app.database import get_db


async def add_atoms_to_library(
    atoms: list[dict],
    user_id: int,
    source_file: Optional[str] = None,
) -> int:
    """
    Add extracted atoms to the content library.

    Returns the number of entries added.
    """
    async with get_db() as db:
        count = 0

        for atom in atoms:
            await db.execute(
                """
                INSERT INTO content_library (
                    user_id, entry_type, content, source, source_timestamp,
                    tags, persona_relevance
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    atom.get("atom_type", "insight"),
                    atom.get("content", ""),
                    source_file or atom.get("source_file"),
                    atom.get("source_location"),
                    json.dumps(atom.get("tags", [])),
                    json.dumps(atom.get("persona_relevance", {})),
                )
            )
            count += 1

        await db.commit()

    return count


async def save_atoms_to_db(atoms: list[dict]) -> int:
    """
    Save atoms to the atoms table (job-specific tracking).

    Returns the number of atoms saved.
    """
    async with get_db() as db:
        count = 0

        for atom in atoms:
            await db.execute(
                """
                INSERT INTO atoms (
                    id, job_id, user_id, atom_type, content,
                    source_location, source_file, tags, persona_relevance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    atom.get("id"),
                    atom.get("job_id"),
                    atom.get("user_id"),
                    atom.get("atom_type"),
                    atom.get("content"),
                    atom.get("source_location"),
                    atom.get("source_file"),
                    json.dumps(atom.get("tags", [])),
                    json.dumps(atom.get("persona_relevance", {})),
                )
            )
            count += 1

        await db.commit()

    return count


async def save_outputs_to_db(outputs: list[dict], job_id: str) -> int:
    """
    Save generated outputs to the database.

    Returns the number of outputs saved.
    """
    async with get_db() as db:
        count = 0

        for output in outputs:
            await db.execute(
                """
                INSERT INTO outputs (
                    job_id, content_type, variation_number,
                    step1_draft, step2_edited, step3_final,
                    atoms_used, citations, warnings, quality_scores, hook_variations,
                    subject_line, preview_text, send_day, email_type, cta_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    output.get("content_type"),
                    output.get("variation_number"),
                    output.get("content") or output.get("step1_draft"),
                    output.get("step2_edited"),
                    output.get("step3_final"),
                    json.dumps(output.get("atoms_used", [])),
                    json.dumps(output.get("citations", [])),
                    json.dumps(output.get("warnings", [])),
                    json.dumps(output.get("quality_scores")) if output.get("quality_scores") else None,
                    json.dumps(output.get("hook_variations")) if output.get("hook_variations") else None,
                    output.get("subject") or output.get("subject_line"),
                    output.get("preview_text"),
                    output.get("send_day"),
                    output.get("email_type"),
                    output.get("cta_text"),
                )
            )
            count += 1

        await db.commit()

    return count


async def get_library_entries_by_ids(entry_ids: list[int], user_id: int) -> list[dict]:
    """
    Get library entries by their IDs.
    """
    if not entry_ids:
        return []

    async with get_db() as db:
        placeholders = ",".join("?" * len(entry_ids))
        cursor = await db.execute(
            f"""
            SELECT * FROM content_library
            WHERE id IN ({placeholders}) AND user_id = ?
            """,
            (*entry_ids, user_id)
        )

        entries = []
        for row in await cursor.fetchall():
            entries.append({
                "id": row["id"],
                "entry_type": row["entry_type"],
                "content": row["content"],
                "source": row["source"],
                "source_timestamp": row["source_timestamp"],
                "tags": json.loads(row["tags"]) if row["tags"] else [],
                "persona_relevance": json.loads(row["persona_relevance"]) if row["persona_relevance"] else {},
            })

        return entries
