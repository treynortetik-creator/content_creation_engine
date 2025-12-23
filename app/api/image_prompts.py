"""Image Prompts API - Generate prompts for external AI image tools."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user_id
from app.api.brand_voice import get_user_brand_context
from app.services.image_prompts import (
    generate_image_prompts,
    enhance_image_prompt,
    get_image_styles_list,
    get_visual_styles_list,
)

router = APIRouter()


class ImagePromptRequest(BaseModel):
    output_id: Optional[int] = None
    content: Optional[str] = None  # Can provide content directly
    image_purpose: str = "linkedin_post"
    visual_style: str = "photorealistic"
    num_variations: int = 3


class EnhancePromptRequest(BaseModel):
    prompt: str
    enhancement_type: str


async def get_output_content(output_id: int, user_id: int) -> Optional[dict]:
    """Get output content by ID, verifying user ownership."""
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
        if row:
            row_dict = dict(row)
            # Get the best available content
            content = (
                row_dict.get("step3_final")
                or row_dict.get("step2_edited")
                or row_dict.get("step1_draft")
                or ""
            )
            return {
                "content": content,
                "content_type": row_dict.get("content_type", "general")
            }
        return None


@router.get("/image-prompts/options")
async def get_image_prompt_options():
    """Get available image purposes and visual styles."""
    return {
        "purposes": get_image_styles_list(),
        "styles": get_visual_styles_list()
    }


@router.post("/image-prompts/generate")
async def generate_image_prompts_endpoint(
    request: ImagePromptRequest,
    user_id: int = Depends(get_current_user_id)
):
    """Generate image prompts for content."""
    # Get content from output or use provided content
    if request.output_id:
        output_data = await get_output_content(request.output_id, user_id)
        if not output_data:
            raise HTTPException(404, "Output not found")
        content = output_data["content"]
        content_type = output_data["content_type"]
    elif request.content:
        content = request.content
        content_type = "general"
    else:
        raise HTTPException(400, "Provide output_id or content")

    if not content or len(content.strip()) < 10:
        raise HTTPException(400, "Content too short to generate image prompts")

    # Get brand context if available
    brand_context = await get_user_brand_context(user_id)
    brand_dict = None
    if brand_context:
        brand_dict = {
            "company_name": brand_context.get("company_name"),
            "industry": brand_context.get("industry"),
            "colors": brand_context.get("brand_voice_json", {}).get("colors")
        }

    # Validate variations count
    num_variations = min(max(request.num_variations, 1), 5)

    prompts = await generate_image_prompts(
        content=content,
        content_type=content_type,
        image_purpose=request.image_purpose,
        visual_style=request.visual_style,
        brand_context=brand_dict,
        num_variations=num_variations
    )

    return {"success": True, "prompts": prompts}


@router.post("/image-prompts/enhance")
async def enhance_prompt_endpoint(
    request: EnhancePromptRequest,
    user_id: int = Depends(get_current_user_id)
):
    """Enhance an existing image prompt."""
    if not request.prompt or len(request.prompt.strip()) < 10:
        raise HTTPException(400, "Prompt too short to enhance")

    enhanced = await enhance_image_prompt(
        base_prompt=request.prompt,
        enhancement_type=request.enhancement_type
    )

    return {"success": True, "enhanced_prompt": enhanced}
