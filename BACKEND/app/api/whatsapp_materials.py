import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, desc

from app.database import get_db
from app.models.whatsapp_material import WhatsAppMaterial
from app.models.user import User
from app.core.security import get_current_user

router = APIRouter()

# Directory for storing uploaded materials
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "uploads", "materials")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Allowed file extensions & MIME types
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB


class TextMaterialCreate(BaseModel):
    title: str
    content: str
    tags: Optional[str] = None


class MaterialUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[str] = None


# ── GET /api/whatsapp/materials ─────────────────────────────────────────────

@router.get("/materials")
async def list_materials(
    type: Optional[str] = Query(None, description="Filter by type: text, image, document"),
    search: Optional[str] = Query(None, description="Search by title, content, or tags"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all WhatsApp materials belonging to the current user or shared."""
    query = select(WhatsAppMaterial).where(
        or_(WhatsAppMaterial.user_id == current_user.id, WhatsAppMaterial.user_id.is_(None))
    )

    if type and type.lower() in ("text", "image", "document"):
        query = query.where(WhatsAppMaterial.type == type.lower())

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.where(
            or_(
                WhatsAppMaterial.title.ilike(term),
                WhatsAppMaterial.content.ilike(term),
                WhatsAppMaterial.tags.ilike(term),
            )
        )

    query = query.order_by(desc(WhatsAppMaterial.updated_at))
    res = await db.execute(query)
    materials = res.scalars().all()

    return [
        {
            "id": m.id,
            "title": m.title,
            "type": m.type,
            "content": m.content,
            "file_path": m.file_path,
            "file_url": m.file_url,
            "mime_type": m.mime_type,
            "file_size": m.file_size,
            "tags": m.tags,
            "created_at": m.created_at.isoformat() if m.created_at else "",
            "updated_at": m.updated_at.isoformat() if m.updated_at else "",
        }
        for m in materials
    ]


# ── POST /api/whatsapp/materials (Text) ─────────────────────────────────────

@router.post("/materials")
async def create_text_material(
    req: TextMaterialCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new text material."""
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Message content cannot be empty")

    material = WhatsAppMaterial(
        user_id=current_user.id,
        title=req.title.strip(),
        type="text",
        content=req.content.strip(),
        tags=req.tags.strip() if req.tags else None,
    )
    db.add(material)
    await db.commit()
    await db.refresh(material)

    return {
        "success": True,
        "message": "Text material created successfully",
        "material": {
            "id": material.id,
            "title": material.title,
            "type": material.type,
            "content": material.content,
            "tags": material.tags,
            "created_at": material.created_at.isoformat() if material.created_at else "",
        },
    }


# ── POST /api/whatsapp/materials/upload (Image/Document) ────────────────────

@router.post("/materials/upload")
async def upload_material(
    file: UploadFile = File(...),
    title: str = Form(...),
    type: str = Form(...),  # "image" or "document"
    tags: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload an image or document material with validation."""
    if not title.strip():
        raise HTTPException(status_code=400, detail="Title cannot be empty")

    mat_type = type.strip().lower()
    if mat_type not in ("image", "document"):
        raise HTTPException(status_code=400, detail="Type must be either 'image' or 'document'")

    # Validate file extension
    filename = file.filename or "file"
    ext = os.path.splitext(filename)[1].lower()

    if mat_type == "image":
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid image format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}",
            )
    else:
        if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid document format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))}",
            )

    # Read content to check size
    file_bytes = await file.read()
    file_size = len(file_bytes)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File size ({round(file_size / (1024*1024), 2)}MB) exceeds maximum limit of 25MB.",
        )
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Generate unique stored filename
    unique_name = f"{uuid.uuid4().hex}_{os.path.basename(filename)}"
    stored_path = os.path.join(UPLOAD_DIR, unique_name)

    # Write file safely to disk
    with open(stored_path, "wb") as f:
        f.write(file_bytes)

    # Base URL for public serving
    file_url = f"/api/whatsapp/materials/file/{unique_name}"

    material = WhatsAppMaterial(
        user_id=current_user.id,
        title=title.strip(),
        type=mat_type,
        file_path=stored_path,
        file_url=file_url,
        mime_type=file.content_type or ("image/png" if mat_type == "image" else "application/pdf"),
        file_size=file_size,
        tags=tags.strip() if tags else None,
    )
    db.add(material)
    await db.commit()
    await db.refresh(material)

    return {
        "success": True,
        "message": f"{mat_type.capitalize()} material uploaded successfully",
        "material": {
            "id": material.id,
            "title": material.title,
            "type": material.type,
            "file_url": material.file_url,
            "file_size": material.file_size,
            "mime_type": material.mime_type,
            "tags": material.tags,
            "created_at": material.created_at.isoformat() if material.created_at else "",
        },
    }


# ── GET /api/whatsapp/materials/file/{filename} ─────────────────────────────

@router.get("/materials/file/{filename}")
async def get_material_file(filename: str):
    """Serve uploaded material file."""
    clean_name = os.path.basename(filename)
    file_path = os.path.join(UPLOAD_DIR, clean_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)


# ── GET /api/whatsapp/materials/{material_id} ───────────────────────────────

@router.get("/materials/{material_id}")
async def get_material(
    material_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get details of a single material."""
    material = await db.get(WhatsAppMaterial, material_id)
    if not material or (material.user_id and material.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=404, detail="Material not found")

    return {
        "id": material.id,
        "title": material.title,
        "type": material.type,
        "content": material.content,
        "file_url": material.file_url,
        "file_size": material.file_size,
        "mime_type": material.mime_type,
        "tags": material.tags,
        "created_at": material.created_at.isoformat() if material.created_at else "",
        "updated_at": material.updated_at.isoformat() if material.updated_at else "",
    }


# ── PUT /api/whatsapp/materials/{material_id} ───────────────────────────────

@router.put("/materials/{material_id}")
async def update_material(
    material_id: int,
    req: MaterialUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update title, content, or tags of a material."""
    material = await db.get(WhatsAppMaterial, material_id)
    if not material or (material.user_id and material.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=404, detail="Material not found")

    if req.title is not None:
        if not req.title.strip():
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        material.title = req.title.strip()

    if req.content is not None:
        material.content = req.content.strip()

    if req.tags is not None:
        material.tags = req.tags.strip() if req.tags else None

    material.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(material)

    return {
        "success": True,
        "message": "Material updated successfully",
        "material": {
            "id": material.id,
            "title": material.title,
            "type": material.type,
            "content": material.content,
            "file_url": material.file_url,
            "tags": material.tags,
            "updated_at": material.updated_at.isoformat() if material.updated_at else "",
        },
    }


# ── DELETE /api/whatsapp/materials/{material_id} ────────────────────────────

@router.delete("/materials/{material_id}")
async def delete_material(
    material_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a material and its associated file on disk."""
    material = await db.get(WhatsAppMaterial, material_id)
    if not material or (material.user_id and material.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=404, detail="Material not found")

    # Clean up file on disk if exists
    if material.file_path and os.path.exists(material.file_path):
        try:
            os.remove(material.file_path)
        except Exception as e:
            print(f"[WhatsAppMaterials] Warning: could not delete file {material.file_path}: {e}")

    await db.delete(material)
    await db.commit()

    return {"success": True, "message": "Material deleted successfully"}
