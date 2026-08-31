from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from typing import List, Optional

from app.database import get_db
from app.models.email_template import EmailMarketingTemplate
from app.models.user import User
from app.schemas.email_template import (
    EmailTemplateCreate,
    EmailTemplateUpdate,
    EmailTemplateOut,
)
from app.core.security import get_current_user
from app.services.default_marketing_templates import DEFAULT_TEMPLATES

router = APIRouter(prefix="/email-templates", tags=["Email Marketing Templates"])


async def ensure_default_templates_seeded(db: AsyncSession):
    """Seed / synchronize the 4 curated core system templates (preserving user custom templates)."""
    # Delete old system templates to sync with 4 core templates
    from sqlalchemy import delete
    await db.execute(delete(EmailMarketingTemplate).where(EmailMarketingTemplate.is_system == True))
    
    for tpl in DEFAULT_TEMPLATES:
        item = EmailMarketingTemplate(
            user_id=None,
            name=tpl["name"],
            category=tpl["category"],
            description=tpl.get("description", ""),
            subject=tpl["subject"],
            html_body=tpl["html_body"],
            preview_text=tpl.get("preview_text", ""),
            is_system=True,
            status="active",
        )
        db.add(item)
    await db.commit()


@router.get("", response_model=List[EmailTemplateOut])
async def list_templates(
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search in name, description, or subject"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List all accessible marketing email templates:
    Includes global curated system templates + current user's custom templates.
    """
    await ensure_default_templates_seeded(db)

    query = select(EmailMarketingTemplate).where(
        EmailMarketingTemplate.status == "active",
        or_(
            EmailMarketingTemplate.is_system == True,
            EmailMarketingTemplate.user_id == current_user.id,
        )
    )

    if category and category.lower() != "all":
        query = query.where(func.lower(EmailMarketingTemplate.category) == category.lower())

    if search:
        term = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(EmailMarketingTemplate.name).like(term),
                func.lower(EmailMarketingTemplate.description).like(term),
                func.lower(EmailMarketingTemplate.subject).like(term),
            )
        )

    query = query.order_by(
        EmailMarketingTemplate.is_system.desc(),
        EmailMarketingTemplate.category.asc(),
        EmailMarketingTemplate.id.asc()
    )
    result = await db.execute(query)
    templates = result.scalars().all()
    return templates


@router.get("/{template_id}", response_model=EmailTemplateOut)
async def get_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch details of a single marketing email template."""
    await ensure_default_templates_seeded(db)
    template = await db.get(EmailMarketingTemplate, template_id)
    if not template or template.status == "archived":
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Check access permission
    if not template.is_system and template.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this template")

    return template


@router.post("", response_model=EmailTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_custom_template(
    data: EmailTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new custom user marketing template."""
    template = EmailMarketingTemplate(
        user_id=current_user.id,
        name=data.name.strip(),
        category=data.category.strip() or "Custom",
        description=data.description.strip() if data.description else "",
        subject=data.subject.strip(),
        html_body=data.html_body,
        preview_text=data.preview_text.strip() if data.preview_text else None,
        is_system=False,
        status="active",
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return template


@router.put("/{template_id}", response_model=EmailTemplateOut)
async def update_custom_template(
    template_id: int,
    data: EmailTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a custom user marketing template (System templates cannot be edited directly)."""
    template = await db.get(EmailMarketingTemplate, template_id)
    if not template or template.status == "archived":
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="System templates cannot be modified. Create a custom template or use this template for your campaign.",
        )

    if template.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to edit this template")

    if data.name is not None:
        template.name = data.name.strip()
    if data.category is not None:
        template.category = data.category.strip()
    if data.description is not None:
        template.description = data.description.strip()
    if data.subject is not None:
        template.subject = data.subject.strip()
    if data.html_body is not None:
        template.html_body = data.html_body
    if data.preview_text is not None:
        template.preview_text = data.preview_text.strip()
    if data.status is not None:
        template.status = data.status

    await db.commit()
    await db.refresh(template)
    return template


@router.delete("/{template_id}")
async def delete_custom_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete or archive a custom user template."""
    template = await db.get(EmailMarketingTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if template.is_system:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="System templates cannot be deleted.",
        )

    if template.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this template")

    await db.delete(template)
    await db.commit()
    return {"message": "Template deleted successfully"}
