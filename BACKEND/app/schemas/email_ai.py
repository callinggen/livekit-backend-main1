from pydantic import BaseModel, Field
from typing import Optional


class EmailAIGenerateRequest(BaseModel):
    prompt: str = Field(..., description="User description or context for the email")
    tone: Optional[str] = Field("Professional", description="Tone of the email (e.g. Professional, Friendly, Persuasive, Urgent, Casual)")
    category: Optional[str] = Field("Marketing", description="Email category (e.g. Promotional, Follow-up, Announcement, Newsletter)")
    action: Optional[str] = Field("generate", description="Action type: generate, improve, shorten, persuasive, friendly, professional")
    current_subject: Optional[str] = Field(None, description="Existing subject line if refining")
    current_heading: Optional[str] = Field(None, description="Existing heading if refining")
    current_body: Optional[str] = Field(None, description="Existing HTML or text body if refining")
    current_cta_text: Optional[str] = Field(None, description="Existing CTA text if refining")
    current_cta_link: Optional[str] = Field(None, description="Existing CTA link if refining")


class EmailAIGenerateResponse(BaseModel):
    subject: str
    heading: str
    body: str
    cta_text: Optional[str] = "Get Started &rarr;"
    cta_link: Optional[str] = "#"
    tone: Optional[str] = "Professional"
