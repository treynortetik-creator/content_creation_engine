"""Brand context and persona models."""
from typing import Optional
from pydantic import BaseModel


class ContentPreferences(BaseModel):
    """Content preferences for a persona."""
    length: str = "Medium"
    data_density: str = "Moderate"
    tone: str = "Professional"


class Persona(BaseModel):
    """Target persona for content generation."""
    id: str
    title: str
    company_size: Optional[str] = None
    pain_points: list[str] = []
    language_level: str = "Professional"
    priorities: list[str] = []
    content_preferences: ContentPreferences = ContentPreferences()


class StructuralPreferences(BaseModel):
    """Structural preferences for content."""
    linkedin_structure: str = "Hook (question/stat) → Bullets (3-5) → Mission tie → CTA"
    paragraph_length: str = "2-4 sentences"  # Options: "1-2 sentences", "2-4 sentences", "3-5 sentences"
    contractions: str = "Moderate"  # Options: "Frequent", "Moderate", "Rare"


class ToneByFormat(BaseModel):
    """Tone preferences by content format."""
    linkedin: str = "Conversational, punchy, question-driven"
    blog: str = "Authoritative but accessible, educational"
    email: str = "Warm, brief, helpful without hard selling"


class BrandVoice(BaseModel):
    """Brand voice configuration."""
    tone_by_format: ToneByFormat = ToneByFormat()
    core_principles: list[str] = []  # 5 bullet points
    mission_phrases: list[str] = []  # Taglines and mission phrases
    red_flags: list[str] = []  # Things to never do
    structural_preferences: StructuralPreferences = StructuralPreferences()
    # Legacy fields for backwards compatibility
    tone: str = "Professional and empathetic"
    style: str = "Conversational but authoritative"
    values: list[str] = []
    do_not_say: list[str] = []
    always_include: list[str] = []


class BrandContext(BaseModel):
    """Full brand context for a client."""
    company_name: str = ""
    industry: str = ""
    brand_voice: BrandVoice = BrandVoice()
    mission_statement: Optional[str] = None
    key_differentiators: list[str] = []
    competitor_names: list[str] = []


class BrandContextCreate(BaseModel):
    """Request model for creating/updating brand context."""
    company_name: str = ""
    industry: str = ""
    tone_linkedin: str = "Conversational, punchy, question-driven"
    tone_blog: str = "Authoritative but accessible, educational"
    tone_email: str = "Warm, brief, helpful without hard selling"
    core_principles: str = ""  # Multi-line text, one per line
    mission_phrases: str = ""  # Multi-line text
    red_flags: str = ""  # Multi-line text
    linkedin_structure: str = "Hook (question/stat) → Bullets (3-5) → Mission tie → CTA"
    paragraph_length: str = "2-4 sentences"
    contractions: str = "Moderate"
    mission_statement: Optional[str] = None
    key_differentiators: str = ""  # Multi-line text
    competitor_names: str = ""  # Multi-line text


class BrandContextResponse(BaseModel):
    """Response model for brand context."""
    id: int
    user_id: int
    company_name: str
    industry: str
    brand_voice: BrandVoice
    mission_statement: Optional[str]
    key_differentiators: list[str]
    competitor_names: list[str]
    created_at: str
    updated_at: str
