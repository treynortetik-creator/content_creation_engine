"""Transcription service using Gemini."""
import os
import json
import aiofiles
from pathlib import Path
from typing import Optional, Tuple
import google.generativeai as genai

from app.config import get_settings, calculate_cost
from app.utils.retry import retry_async, gemini_circuit_breaker
from app.services import settings_manager

settings = get_settings()


class GeminiAPIKeyMissingError(Exception):
    """Raised when Gemini API key is not configured."""

    def __init__(self):
        super().__init__(
            "GEMINI_API_KEY is not configured. "
            "Video and audio transcription requires a Google AI Studio API key. "
            "Get one at: https://aistudio.google.com/ and add it to your .env file. "
            "Text files (.txt, .md) can still be processed without this key."
        )


def init_gemini():
    """Initialize Gemini API client."""
    api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiAPIKeyMissingError()
    genai.configure(api_key=api_key)


def is_gemini_available() -> bool:
    """Check if Gemini API is configured and available."""
    api_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    return bool(api_key)


async def _call_gemini_transcribe(model, content, prompt) -> tuple:
    """Helper to call Gemini for transcription with retry support."""
    response = await gemini_circuit_breaker.call(
        lambda: model.generate_content([content, prompt])
    )
    return response


async def transcribe_file(
    file_path: Path,
    file_type: str,
    job_id: str = None,
    user_id: int = None,
    magic_words: str = None,
) -> Tuple[str, float]:
    """
    Transcribe audio/video file or extract text from document.

    Args:
        magic_words: Optional comma-separated list of domain vocabulary
                    (brand names, acronyms, technical terms) to recognize accurately.

    Returns (transcript, cost) tuple.
    """
    init_gemini()

    file_size = file_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)

    # Read file content
    if file_type == "document":
        # For text files, just read the content
        if file_path.suffix.lower() in [".txt", ".md"]:
            async with aiofiles.open(file_path, "r", errors="ignore") as f:
                transcript = await f.read()
            return transcript, 0.0

    # Prepare file for Gemini
    mime_types = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/x-m4a",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    ext = file_path.suffix.lower()
    mime_type = mime_types.get(ext, "application/octet-stream")

    model_name = settings_manager.get_model_for_step("transcription")
    model = genai.GenerativeModel(model_name)

    # Build prompt with optional magic words
    vocabulary_section = ""
    if magic_words and magic_words.strip():
        vocabulary_section = f"""
IMPORTANT VOCABULARY TO RECOGNIZE ACCURATELY:
{magic_words}

These are domain-specific terms, brand names, acronyms, or technical terms.
Make sure to transcribe them correctly as written above.

"""

    prompt = f"""{vocabulary_section}Transcribe this content completely and accurately.

Instructions:
1. Transcribe all spoken words exactly as said
2. Include speaker labels if there are multiple speakers (e.g., "Speaker 1:", "Speaker 2:")
3. Note any significant non-verbal sounds in [brackets] (e.g., [applause], [laughter])
4. For documents/PDFs, extract all readable text content
5. Preserve paragraph breaks where natural
6. If there are timestamps visible, include them

Output the full transcript only, no additional commentary."""

    async def do_transcribe():
        if file_size_mb > 20:
            # Use file upload for large files
            uploaded_file = genai.upload_file(path=str(file_path))
            return model.generate_content([uploaded_file, prompt])
        else:
            # Read file directly for smaller files
            async with aiofiles.open(file_path, "rb") as f:
                file_content = await f.read()

            return model.generate_content([
                {"mime_type": mime_type, "data": file_content},
                prompt
            ])

    # Use retry logic for API call
    response = await retry_async(
        do_transcribe,
        max_retries=3,
        base_delay=2.0,
        job_id=job_id,
        user_id=user_id,
        context="transcribe_file",
    )

    transcript = response.text

    # Calculate cost
    input_tokens = response.usage_metadata.prompt_token_count
    output_tokens = response.usage_metadata.candidates_token_count
    cost = calculate_cost(model_name, input_tokens, output_tokens)

    return transcript, cost


async def cleanup_transcript(
    transcript: str,
    job_id: str = None,
    user_id: int = None,
) -> Tuple[str, float]:
    """
    Clean up transcript by removing filler words, fixing formatting.

    Returns (cleaned_transcript, cost) tuple.
    """
    init_gemini()

    model_name = settings_manager.get_model_for_step("transcription")
    model = genai.GenerativeModel(model_name)

    prompt = f"""Clean up this transcript while preserving all meaningful content.

TRANSCRIPT:
{transcript}

CLEANUP TASKS:
1. Remove filler words (um, uh, like, you know, I mean, basically, actually, sort of, kind of)
2. Remove false starts and repeated words
3. Fix obvious grammatical errors that came from speech-to-text
4. Maintain speaker labels if present
5. Keep all substantive content - do not summarize or remove information
6. Format into clear paragraphs
7. Keep any timestamps or section markers

OUTPUT REQUIREMENTS:
- Return ONLY the cleaned transcript
- Do not add any commentary or notes
- Do not summarize - keep all content
- Maintain the original meaning and flow"""

    async def do_cleanup():
        return model.generate_content(prompt)

    # Use retry logic for API call
    response = await retry_async(
        do_cleanup,
        max_retries=3,
        base_delay=2.0,
        job_id=job_id,
        user_id=user_id,
        context="cleanup_transcript",
    )

    cleaned = response.text

    # Calculate cost
    input_tokens = response.usage_metadata.prompt_token_count
    output_tokens = response.usage_metadata.candidates_token_count
    cost = calculate_cost(model_name, input_tokens, output_tokens)

    return cleaned, cost


async def extract_document_content(
    file_path: Path,
    job_id: str = None,
    user_id: int = None,
) -> Tuple[str, float]:
    """
    Extract text content from documents (PDF, DOCX) with image/graph analysis.

    Unlike transcribe_file, this function:
    - Extracts text directly from documents
    - Analyzes any images, charts, graphs, or visual content
    - Returns content ready for atomization (no cleanup needed)

    Returns (extracted_content, cost) tuple.
    """
    init_gemini()

    file_size = file_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)
    ext = file_path.suffix.lower()

    # For plain text files, just read directly (no API cost)
    if ext in [".txt", ".md"]:
        async with aiofiles.open(file_path, "r", errors="ignore") as f:
            content = await f.read()
        return content, 0.0

    # MIME types for documents
    mime_types = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }

    mime_type = mime_types.get(ext, "application/octet-stream")

    model_name = settings_manager.get_model_for_step("transcription")
    model = genai.GenerativeModel(model_name)

    # Build extraction prompt with image analysis
    prompt = """Extract all content from this document comprehensively.

EXTRACTION INSTRUCTIONS:

1. TEXT CONTENT:
   - Extract all readable text content completely
   - Preserve the logical structure and organization
   - Maintain headings, subheadings, and section breaks
   - Keep bullet points, numbered lists, and formatting cues
   - Preserve any quotes, citations, or references

2. VISUAL CONTENT ANALYSIS (VERY IMPORTANT):
   - For any images, charts, graphs, diagrams, or visual elements:
     * Describe what the visual shows
     * Extract any data points, numbers, or statistics visible
     * Explain the key insights or trends depicted
     * Transcribe any text or labels within the visual
   - Format visual analysis as: [VISUAL: description and analysis]

3. TABLES AND DATA:
   - Extract table contents in a readable format
   - Preserve column relationships and data alignment
   - Note any footnotes or annotations

4. OUTPUT FORMAT:
   - Present the content in a clean, well-organized manner
   - Use clear paragraph breaks
   - Keep the original flow and narrative structure
   - Include visual content analysis inline where the visuals appear

Extract the complete document content now:"""

    async def do_extract():
        if file_size_mb > 20:
            # Use file upload for large files
            uploaded_file = genai.upload_file(path=str(file_path))
            return model.generate_content([uploaded_file, prompt])
        else:
            # Read file directly for smaller files
            async with aiofiles.open(file_path, "rb") as f:
                file_content = await f.read()

            return model.generate_content([
                {"mime_type": mime_type, "data": file_content},
                prompt
            ])

    # Use retry logic for API call
    response = await retry_async(
        do_extract,
        max_retries=3,
        base_delay=2.0,
        job_id=job_id,
        user_id=user_id,
        context="extract_document_content",
    )

    extracted_content = response.text

    # Calculate cost
    input_tokens = response.usage_metadata.prompt_token_count
    output_tokens = response.usage_metadata.candidates_token_count
    cost = calculate_cost(model_name, input_tokens, output_tokens)

    return extracted_content, cost
