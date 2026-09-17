from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, or_, and_, desc, asc, delete, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.saved_contact import SavedContact
from app.models.user import User
from app.core.security import get_current_user
from app.schemas.saved_contact import (
    SavedContactCreate,
    SavedContactUpdate,
    SavedContactResponse,
    SavedContactBatchCreate,
    SavedContactBatchDelete,
    SavedContactStats,
    SavedContactListResponse,
    SavedContactListSummary,
    SavedContactRenameList,
    SavedContactSyncRequest,
)
from app.services.google_sheet_service import sync_google_sheet_for_tag

router = APIRouter(prefix="/contacts-book", tags=["Contacts Book"])




def normalize_phone(phone: str) -> str:
    """Normalize phone number to standard format."""
    p = "".join(c for c in phone if c.isdigit() or c == "+").strip()
    if p.startswith("0") and len(p) == 11:
        p = f"+91{p[1:]}"
    elif not p.startswith("+"):
        if len(p) == 10:
            p = f"+91{p}"
        elif len(p) == 12 and p.startswith("91"):
            p = f"+{p}"
    return p


# ── GET /api/contacts-book ──────────────────────────────────────────────────
@router.get("", response_model=SavedContactListResponse)
async def list_saved_contacts(
    q: Optional[str] = Query(None, description="Search by name, phone, email, or tag"),
    tag: Optional[str] = Query(None, description="Filter by tag"),
    source: Optional[str] = Query(None, description="Filter by source"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=500),
    sort_by: str = Query("created_at", pattern="^(created_at|name|phone|call_count)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),

    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List saved contacts for the current user with search, filter, and pagination."""
    query = select(SavedContact).where(SavedContact.user_id == current_user.id)

    # Search filter
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.where(
            or_(
                SavedContact.name.ilike(term),
                SavedContact.phone.ilike(term),
                SavedContact.email.ilike(term),
                SavedContact.tag.ilike(term),
                SavedContact.source.ilike(term),
            )
        )

    # Tag filter
    if tag and tag.strip() and tag.lower() != "all":
        query = query.where(SavedContact.tag == tag.strip())

    # Source filter
    if source and source.strip() and source.lower() != "all":
        query = query.where(SavedContact.source == source.strip())

    # Count total matching
    count_query = select(func.count()).select_from(query.subquery())
    total_count_res = await db.execute(count_query)
    total_count = total_count_res.scalar() or 0

    # Sorting
    sort_col = getattr(SavedContact, sort_by, SavedContact.created_at)
    if sort_order == "asc":
        query = query.order_by(asc(sort_col))
    else:
        query = query.order_by(desc(sort_col))

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    items = result.scalars().all()

    total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1

    return SavedContactListResponse(
        items=[SavedContactResponse.model_validate(item) for item in items],
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ── GET /api/contacts-book/all ──────────────────────────────────────────────
@router.get("/all", response_model=List[SavedContactResponse])
async def get_all_saved_contacts(
    tag: Optional[str] = Query(None, description="Filter by tag (optional)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieve all contacts (or all for a tag) for direct campaign loading."""
    query = select(SavedContact).where(SavedContact.user_id == current_user.id)
    if tag and tag.strip() and tag.lower() != "all":
        query = query.where(SavedContact.tag == tag.strip())
    
    query = query.order_by(asc(SavedContact.name))
    result = await db.execute(query)
    items = result.scalars().all()
    return [SavedContactResponse.model_validate(item) for item in items]


# ── GET /api/contacts-book/stats ───────────────────────────────────────────
@router.get("/stats", response_model=SavedContactStats)
async def get_contacts_book_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get aggregate statistics for the user's Contact Book."""
    # Total contacts
    total_res = await db.execute(
        select(func.count(SavedContact.id)).where(SavedContact.user_id == current_user.id)
    )
    total_contacts = total_res.scalar() or 0

    # With email
    email_res = await db.execute(
        select(func.count(SavedContact.id)).where(
            SavedContact.user_id == current_user.id,
            SavedContact.email.isnot(None),
            SavedContact.email != "",
        )
    )
    with_email = email_res.scalar() or 0

    # Valid phones (length >= 10)
    phones_res = await db.execute(
        select(func.count(SavedContact.id)).where(
            SavedContact.user_id == current_user.id,
            func.length(SavedContact.phone) >= 10,
        )
    )
    valid_phones = phones_res.scalar() or 0

    # Distinct tags count
    tags_res = await db.execute(
        select(func.count(func.distinct(SavedContact.tag))).where(
            SavedContact.user_id == current_user.id,
            SavedContact.tag.isnot(None),
            SavedContact.tag != "",
        )
    )
    total_tags = tags_res.scalar() or 0

    # Added in last 7 days
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent_res = await db.execute(
        select(func.count(SavedContact.id)).where(
            SavedContact.user_id == current_user.id,
            SavedContact.created_at >= one_week_ago,
        )
    )
    recent_added_this_week = recent_res.scalar() or 0

    return SavedContactStats(
        total_contacts=total_contacts,
        valid_phones=valid_phones,
        with_email=with_email,
        total_tags=total_tags,
        recent_added_this_week=recent_added_this_week,
    )


# ── GET /api/contacts-book/tags ─────────────────────────────────────────────
@router.get("/tags", response_model=List[str])
async def get_contacts_book_tags(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List unique tags used across the user's contacts."""
    stmt = (
        select(SavedContact.tag)
        .where(
            SavedContact.user_id == current_user.id,
            SavedContact.tag.isnot(None),
            SavedContact.tag != "",
        )
        .distinct()
        .order_by(asc(SavedContact.tag))
    )
    res = await db.execute(stmt)
    tags = [r for r in res.scalars().all() if r]
    return tags


# ── GET /api/contacts-book/lists ───────────────────────────────────────────
@router.get("/lists", response_model=List[SavedContactListSummary])
async def list_contact_lists_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List summary statistics for each contact list (tag) owned by the user."""
    stmt = (
        select(
            func.coalesce(SavedContact.tag, "General").label("tag"),
            func.count(SavedContact.id).label("total_contacts"),
            func.sum(
                case((func.length(SavedContact.phone) >= 10, 1), else_=0)
            ).label("valid_phones"),
            func.sum(
                case((and_(SavedContact.email.isnot(None), SavedContact.email != ""), 1), else_=0)
            ).label("with_email"),
            func.max(SavedContact.created_at).label("created_at"),
            func.max(SavedContact.updated_at).label("updated_at"),
        )
        .where(SavedContact.user_id == current_user.id)
        .group_by(func.coalesce(SavedContact.tag, "General"))
        .order_by(desc(func.max(SavedContact.updated_at)))
    )

    result = await db.execute(stmt)
    rows = result.all()

    # Also fetch distinct sources and Google Sheet metadata per tag
    metadata_stmt = (
        select(
            func.coalesce(SavedContact.tag, "General").label("tag"),
            SavedContact.source,
            SavedContact.metadata_fields,
        )
        .where(SavedContact.user_id == current_user.id)
    )
    metadata_res = await db.execute(metadata_stmt)
    sources_by_tag: Dict[str, List[str]] = {}
    sheet_url_by_tag: Dict[str, str] = {}
    last_synced_by_tag: Dict[str, datetime] = {}

    for r in metadata_res.all():
        t = r[0]
        s = r[1] or "Manual"
        mf = r[2] or {}

        if t not in sources_by_tag:
            sources_by_tag[t] = []
        if s not in sources_by_tag[t]:
            sources_by_tag[t].append(s)

        if isinstance(mf, dict):
            url = mf.get("google_sheet_url") or mf.get("sheet_url")
            if url and t not in sheet_url_by_tag:
                sheet_url_by_tag[t] = str(url)
            synced_str = mf.get("last_synced_at")
            if synced_str and t not in last_synced_by_tag:
                try:
                    last_synced_by_tag[t] = datetime.fromisoformat(str(synced_str).replace("Z", "+00:00"))
                except Exception:
                    pass

    summaries = []
    for r in rows:
        tag_name = r.tag
        sheet_url = sheet_url_by_tag.get(tag_name)
        is_sheet = bool(sheet_url or "Google Sheet" in sources_by_tag.get(tag_name, []))

        summaries.append(
            SavedContactListSummary(
                tag=tag_name,
                total_contacts=r.total_contacts or 0,
                valid_phones=int(r.valid_phones or 0),
                with_email=int(r.with_email or 0),
                sources=sources_by_tag.get(tag_name, ["Manual"]),
                source_url=sheet_url,
                is_google_sheet=is_sheet,
                last_synced_at=last_synced_by_tag.get(tag_name),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )
    return summaries


# ── POST /api/contacts-book/lists/{tag_name}/sync ──────────────────────────
@router.post("/lists/{tag_name}/sync", response_model=Dict[str, Any])
async def sync_contact_list_from_google_sheet(
    tag_name: str,
    payload: Optional[SavedContactSyncRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Auto-refresh/sync a contact list directly from its linked Google Sheet.
    Fetches the latest rows, updates modified records, and inserts newly added rows.
    """
    sheet_url = payload.google_sheet_url.strip() if payload and payload.google_sheet_url else None

    if not sheet_url:
        c_stmt = (
            select(SavedContact.metadata_fields)
            .where(
                SavedContact.user_id == current_user.id,
                SavedContact.tag == tag_name,
                SavedContact.metadata_fields.isnot(None),
            )
            .limit(10)
        )
        c_res = await db.execute(c_stmt)
        for row in c_res.scalars().all():
            if isinstance(row, dict) and (row.get("google_sheet_url") or row.get("sheet_url")):
                sheet_url = row.get("google_sheet_url") or row.get("sheet_url")
                break

    if not sheet_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No Google Sheet URL found for list '{tag_name}'. Please provide a valid Google Sheet link.",
        )

    try:
        result = await sync_google_sheet_for_tag(
            db=db,
            user_id=current_user.id,
            tag=tag_name,
            sheet_url=sheet_url,
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google Sheet sync error: {str(e)}",
        )



# ── PUT /api/contacts-book/lists/rename ────────────────────────────────────
@router.put("/lists/rename", response_model=Dict[str, Any])
async def rename_contact_list(
    payload: SavedContactRenameList,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rename a contact list tag for all contacts belonging to the user."""
    old_tag = payload.old_tag.strip()
    new_tag = payload.new_tag.strip()
    if not old_tag or not new_tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both old_tag and new_tag must be provided.",
        )

    stmt = (
        SavedContact.__table__.update()
        .where(
            SavedContact.user_id == current_user.id,
            SavedContact.tag == old_tag,
        )
        .values(
            tag=new_tag,
            updated_at=datetime.now(timezone.utc),
        )
    )
    result = await db.execute(stmt)
    await db.commit()
    return {
        "success": True,
        "old_tag": old_tag,
        "new_tag": new_tag,
        "updated_count": result.rowcount,
    }


# ── DELETE /api/contacts-book/lists/{tag_name} ─────────────────────────────
@router.delete("/lists/{tag_name}", response_model=Dict[str, Any])
async def delete_contact_list(
    tag_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete all contacts belonging to a specific list/tag."""
    stmt = delete(SavedContact).where(
        SavedContact.user_id == current_user.id,
        SavedContact.tag == tag_name,
    )
    result = await db.execute(stmt)
    await db.commit()
    return {
        "success": True,
        "tag": tag_name,
        "deleted_count": result.rowcount,
    }



# ── POST /api/contacts-book ────────────────────────────────────────────────
@router.post("", response_model=SavedContactResponse, status_code=status.HTTP_201_CREATED)
async def create_saved_contact(
    data: SavedContactCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add or upsert a single contact into the user's Contact Book."""
    norm_phone = normalize_phone(data.phone)
    if not norm_phone or len(norm_phone) < 7:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone number provided.",
        )

    # Check if contact with same phone already exists for this user
    existing_res = await db.execute(
        select(SavedContact).where(
            SavedContact.user_id == current_user.id,
            SavedContact.phone == norm_phone,
        )
    )
    existing = existing_res.scalars().first()

    now = datetime.now(timezone.utc)
    if existing:
        # Update existing contact
        existing.name = data.name.strip()
        if data.email is not None:
            existing.email = data.email.strip() if data.email else None
        if data.tag:
            existing.tag = data.tag.strip()
        if data.source:
            existing.source = data.source
        if data.metadata_fields:
            existing.metadata_fields = {**(existing.metadata_fields or {}), **data.metadata_fields}
        if data.notes:
            existing.notes = data.notes
        existing.updated_at = now
        await db.commit()
        await db.refresh(existing)
        return SavedContactResponse.model_validate(existing)

    new_contact = SavedContact(
        user_id=current_user.id,
        name=data.name.strip(),
        phone=norm_phone,
        email=data.email.strip() if data.email else None,
        source=data.source or "Manual",
        tag=data.tag.strip() if data.tag else "General",
        metadata_fields=data.metadata_fields or {},
        notes=data.notes,
        created_at=now,
        updated_at=now,
    )
    db.add(new_contact)
    await db.commit()
    await db.refresh(new_contact)
    return SavedContactResponse.model_validate(new_contact)


# ── POST /api/contacts-book/batch ──────────────────────────────────────────
@router.post("/batch", response_model=Dict[str, Any], status_code=status.HTTP_200_OK)
async def batch_create_saved_contacts(
    payload: SavedContactBatchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk import contacts into Contact Book with deduplication by phone number.
    Returns count of added vs updated records.
    """
    if not payload.contacts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contact list is empty.",
        )

    # Fetch all existing phone numbers for this user in one query
    existing_res = await db.execute(
        select(SavedContact).where(SavedContact.user_id == current_user.id)
    )
    existing_map = {c.phone: c for c in existing_res.scalars().all()}

    added_count = 0
    updated_count = 0
    now = datetime.now(timezone.utc)
    new_contacts = []

    # Map to track in-batch duplicates
    seen_in_batch = set()

    for item in payload.contacts:
        name = (item.name or "").strip()
        phone_raw = (item.phone or "").strip()
        if not phone_raw:
            continue
        
        norm_phone = normalize_phone(phone_raw)
        if len(norm_phone) < 7 or norm_phone in seen_in_batch:
            continue
        seen_in_batch.add(norm_phone)

        email = item.email.strip() if item.email else None
        tag = item.tag.strip() if item.tag else (payload.default_tag or "General")
        source = item.source or payload.source or "Batch Upload"

        if norm_phone in existing_map:
            # Update existing
            c = existing_map[norm_phone]
            if name and name != "Unknown":
                c.name = name
            if email:
                c.email = email
            if tag and tag != "General":
                c.tag = tag
            c.source = source
            if item.metadata_fields:
                c.metadata_fields = {**(c.metadata_fields or {}), **item.metadata_fields}
            if item.notes:
                c.notes = item.notes
            c.updated_at = now
            updated_count += 1
        else:
            # Create new
            new_c = SavedContact(
                user_id=current_user.id,
                name=name or "Unknown",
                phone=norm_phone,
                email=email,
                source=source,
                tag=tag,
                metadata_fields=item.metadata_fields or {},
                notes=item.notes,
                created_at=now,
                updated_at=now,
            )
            new_contacts.append(new_c)
            existing_map[norm_phone] = new_c
            added_count += 1

    if new_contacts:
        db.add_all(new_contacts)

    await db.commit()

    return {
        "success": True,
        "total_processed": len(payload.contacts),
        "added": added_count,
        "updated": updated_count,
        "tag": payload.default_tag,
    }


# ── PUT /api/contacts-book/{id} ────────────────────────────────────────────
@router.put("/{contact_id}", response_model=SavedContactResponse)
async def update_saved_contact(
    contact_id: int,
    data: SavedContactUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a specific contact."""
    contact = await db.get(SavedContact, contact_id)
    if not contact or contact.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found.",
        )

    if data.name is not None:
        contact.name = data.name.strip()
    if data.phone is not None:
        norm = normalize_phone(data.phone)
        if len(norm) < 7:
            raise HTTPException(status_code=400, detail="Invalid phone number.")
        contact.phone = norm
    if data.email is not None:
        contact.email = data.email.strip() if data.email else None
    if data.tag is not None:
        contact.tag = data.tag.strip() if data.tag else "General"
    if data.source is not None:
        contact.source = data.source
    if data.metadata_fields is not None:
        contact.metadata_fields = data.metadata_fields
    if data.notes is not None:
        contact.notes = data.notes

    contact.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(contact)
    return SavedContactResponse.model_validate(contact)


# ── DELETE /api/contacts-book/{id} ─────────────────────────────────────────
@router.delete("/{contact_id}")
async def delete_saved_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a single contact."""
    contact = await db.get(SavedContact, contact_id)
    if not contact or contact.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact not found.",
        )

    await db.delete(contact)
    await db.commit()
    return {"success": True, "deleted_id": contact_id}


# ── DELETE /api/contacts-book/batch ────────────────────────────────────────
@router.delete("/batch/delete", response_model=Dict[str, Any])
async def batch_delete_saved_contacts(
    payload: SavedContactBatchDelete,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete multiple contacts by their IDs."""
    if not payload.contact_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No contact IDs provided.",
        )

    stmt = delete(SavedContact).where(
        SavedContact.user_id == current_user.id,
        SavedContact.id.in_(payload.contact_ids),
    )
    result = await db.execute(stmt)
    await db.commit()

    return {
        "success": True,
        "deleted_count": result.rowcount,
    }
