import os
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.knowledge import KnowledgeDocument, KnowledgeChunk
from app.services.knowledge_service import KnowledgeService

logger = logging.getLogger("callinggen.knowledge")

router = APIRouter(prefix="/knowledge", tags=["Knowledge Base"])


# ── Pydantic Request & Response Schemas ─────────────────────────────────────

class TextIngestRequest(BaseModel):
    title: str = Field(..., description="Document or Topic Title (e.g. 'Company Overview', 'Return Policy')")
    content: str = Field(..., description="Full text content describing services, pricing, business hours, etc.")
    source_type: str = Field("raw", description="Source type tag ('raw', 'policy', 'service', 'product')")


class UrlIngestRequest(BaseModel):
    url: str = Field(..., description="Webpage or article link (e.g. https://yourcompany.com/about)")
    title: Optional[str] = Field(None, description="Optional custom title override")


class GoogleSheetIngestRequest(BaseModel):
    sheet_url: str = Field(..., description="Public or viewable Google Sheet link")
    title: Optional[str] = Field(None, description="Optional title (e.g. 'Inventory Catalog', 'Pricing Sheet')")


class FaqIngestRequest(BaseModel):
    question: str = Field(..., description="Customer question (e.g. 'What are your working hours?')")
    answer: str = Field(..., description="Accurate company answer")
    category: Optional[str] = Field(None, description="Optional category (e.g. 'Billing', 'Services')")


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., description="Question or keyword to search against the company knowledge base")
    top_k: int = Field(5, ge=1, le=20, description="Max number of matching chunks to retrieve")
    threshold: float = Field(0.20, ge=0.0, le=1.0, description="Minimum confidence score threshold")


class KnowledgeSearchResultItem(BaseModel):
    chunk_id: int
    document_id: int
    title: str
    source_type: str
    source_url: Optional[str] = None
    content: str
    score: float
    meta: Dict[str, Any] = {}


class KnowledgeSearchResponse(BaseModel):
    query: str
    total_matches: int
    results: List[KnowledgeSearchResultItem]


class KnowledgeChunkItem(BaseModel):
    id: int
    chunk_index: int
    content: str
    word_count: int


class KnowledgeDocumentResponse(BaseModel):
    id: int
    user_id: int
    title: str
    source_type: str
    source_url: Optional[str] = None
    status: str
    chunk_count: int
    error_message: Optional[str] = None
    meta_info: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class KnowledgeDocumentDetailResponse(KnowledgeDocumentResponse):
    extracted_text: Optional[str] = None
    chunks: List[KnowledgeChunkItem] = []


class KnowledgeStatsResponse(BaseModel):
    total_documents: int
    total_chunks: int
    source_breakdown: Dict[str, int]
    last_synced_at: Optional[datetime] = None


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/stats", response_model=KnowledgeStatsResponse)
async def get_knowledge_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get high-level summary statistics of the user's isolated Knowledge Base.
    """
    doc_count_stmt = select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.user_id == user.id)
    doc_count = (await db.execute(doc_count_stmt)).scalar() or 0

    chunk_count_stmt = select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.user_id == user.id)
    chunk_count = (await db.execute(chunk_count_stmt)).scalar() or 0

    # Source breakdown
    sources_stmt = (
        select(KnowledgeDocument.source_type, func.count(KnowledgeDocument.id))
        .where(KnowledgeDocument.user_id == user.id)
        .group_by(KnowledgeDocument.source_type)
    )
    sources_res = (await db.execute(sources_stmt)).all()
    source_map = {row[0]: row[1] for row in sources_res}

    # Latest sync time
    latest_stmt = (
        select(func.max(KnowledgeDocument.updated_at))
        .where(KnowledgeDocument.user_id == user.id)
    )
    latest_sync = (await db.execute(latest_stmt)).scalar()

    return KnowledgeStatsResponse(
        total_documents=doc_count,
        total_chunks=chunk_count,
        source_breakdown=source_map,
        last_synced_at=latest_sync,
    )


@router.get("/documents", response_model=List[KnowledgeDocumentResponse])
async def list_knowledge_documents(
    source_type: Optional[str] = Query(None, description="Filter by source type ('url', 'pdf', 'sheet', etc.)"),
    q: Optional[str] = Query(None, description="Search document titles"),
    search: Optional[str] = Query(None, description="Search document titles (alias)"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List all knowledge documents ingested for the current tenant.
    """
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.user_id == user.id)
    if source_type and source_type != "all":
        st = source_type.lower()
        if st in ("sheet", "sheets", "csv"):
            stmt = stmt.where(KnowledgeDocument.source_type.in_(["sheet", "csv"]))
        elif st in ("word", "docx", "doc"):
            stmt = stmt.where(KnowledgeDocument.source_type.in_(["docx", "doc"]))
        elif st in ("pdf", "pdfs"):
            stmt = stmt.where(KnowledgeDocument.source_type == "pdf")
        elif st in ("url", "link", "web"):
            stmt = stmt.where(KnowledgeDocument.source_type.in_(["url", "link"]))
        elif st in ("faq", "faqs"):
            stmt = stmt.where(KnowledgeDocument.source_type.in_(["faq", "qna"]))
        elif st in ("raw", "text", "service", "services"):
            stmt = stmt.where(KnowledgeDocument.source_type.notin_(["pdf", "docx", "doc", "sheet", "csv", "url", "link", "faq"]))
        else:
            stmt = stmt.where(KnowledgeDocument.source_type == source_type)

    search_term = (q or search or "").strip()
    if search_term:
        stmt = stmt.where(KnowledgeDocument.title.ilike(f"%{search_term}%"))

    stmt = stmt.order_by(desc(KnowledgeDocument.updated_at))
    result = await db.execute(stmt)
    docs = result.scalars().all()

    out = []
    for d in docs:
        meta = {}
        try:
            if d.meta_info:
                meta = json.loads(d.meta_info)
        except Exception:
            pass

        out.append(
            KnowledgeDocumentResponse(
                id=d.id,
                user_id=d.user_id,
                title=d.title,
                source_type=d.source_type,
                source_url=d.source_url,
                status=d.status,
                chunk_count=d.chunk_count,
                error_message=d.error_message,
                meta_info=meta,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
        )
    return out


@router.get("/documents/{doc_id}", response_model=KnowledgeDocumentDetailResponse)
async def get_knowledge_document_detail(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get full details, extracted text, and chunks of a single knowledge document.
    """
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id, KnowledgeDocument.user_id == user.id)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")

    chunks_stmt = select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id).order_by(KnowledgeChunk.chunk_index)
    chunks_res = await db.execute(chunks_stmt)
    chunks = chunks_res.scalars().all()

    meta = {}
    try:
        if doc.meta_info:
            meta = json.loads(doc.meta_info)
    except Exception:
        pass

    return KnowledgeDocumentDetailResponse(
        id=doc.id,
        user_id=doc.user_id,
        title=doc.title,
        source_type=doc.source_type,
        source_url=doc.source_url,
        status=doc.status,
        chunk_count=doc.chunk_count,
        error_message=doc.error_message,
        meta_info=meta,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        extracted_text=doc.extracted_text,
        chunks=[
            KnowledgeChunkItem(
                id=c.id,
                chunk_index=c.chunk_index,
                content=c.content,
                word_count=c.word_count,
            )
            for c in chunks
        ],
    )


@router.post("/ingest/text", response_model=KnowledgeDocumentResponse)
async def ingest_raw_text(
    payload: TextIngestRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ingest raw text (Company Overview, Pricing, Policy, Warranty, Services).
    """
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty.")

    try:
        doc = await KnowledgeService.ingest_document(
            db=db,
            user_id=user.id,
            source_type=payload.source_type or "raw",
            title=payload.title.strip() or "Company Information",
            content=payload.content,
            meta_info={"word_count": len(payload.content.split())},
        )
        return KnowledgeDocumentResponse(
            id=doc.id,
            user_id=doc.user_id,
            title=doc.title,
            source_type=doc.source_type,
            status=doc.status,
            chunk_count=doc.chunk_count,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except Exception as e:
        logger.error(f"Error ingesting text for user {user.id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to ingest text: {str(e)}")


@router.post("/ingest/url", response_model=KnowledgeDocumentResponse)
async def ingest_url(
    payload: UrlIngestRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Scrape, extract, and chunk content from a public web page link.
    """
    url = payload.url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    try:
        page_title, body_text, meta = await KnowledgeService.extract_from_url(url)
        doc_title = payload.title.strip() if payload.title and payload.title.strip() else page_title

        doc = await KnowledgeService.ingest_document(
            db=db,
            user_id=user.id,
            source_type="url",
            title=doc_title,
            content=body_text,
            source_url=url,
            meta_info=meta,
        )
        return KnowledgeDocumentResponse(
            id=doc.id,
            user_id=doc.user_id,
            title=doc.title,
            source_type=doc.source_type,
            source_url=doc.source_url,
            status=doc.status,
            chunk_count=doc.chunk_count,
            meta_info=meta,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except Exception as e:
        logger.error(f"Error scraping URL '{url}' for user {user.id}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to scrape URL: {str(e)}")


@router.post("/ingest/google-sheet", response_model=KnowledgeDocumentResponse)
async def ingest_google_sheet(
    payload: GoogleSheetIngestRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ingest a Google Sheet into structured knowledge records with auto-sync capability.
    """
    sheet_url = payload.sheet_url.strip()
    try:
        title, text_content, meta = await KnowledgeService.extract_from_google_sheet(sheet_url)
        doc_title = payload.title.strip() if payload.title and payload.title.strip() else title

        doc = await KnowledgeService.ingest_document(
            db=db,
            user_id=user.id,
            source_type="sheet",
            title=doc_title,
            content=text_content,
            source_url=sheet_url,
            meta_info=meta,
        )
        return KnowledgeDocumentResponse(
            id=doc.id,
            user_id=doc.user_id,
            title=doc.title,
            source_type=doc.source_type,
            source_url=doc.source_url,
            status=doc.status,
            chunk_count=doc.chunk_count,
            meta_info=meta,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except Exception as e:
        logger.error(f"Error ingesting Google Sheet for user {user.id}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to ingest Google Sheet: {str(e)}")


@router.post("/ingest/faq", response_model=KnowledgeDocumentResponse)
async def ingest_faq(
    payload: FaqIngestRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Ingest a single Q&A pair into the knowledge base.
    """
    if not payload.question.strip() or not payload.answer.strip():
        raise HTTPException(status_code=400, detail="Question and answer are both required.")

    formatted_content = f"Question: {payload.question.strip()}\nAnswer: {payload.answer.strip()}"
    title = f"FAQ: {payload.question.strip()[:60]}"

    try:
        doc = await KnowledgeService.ingest_document(
            db=db,
            user_id=user.id,
            source_type="faq",
            title=title,
            content=formatted_content,
            meta_info={"category": payload.category or "General"},
        )
        return KnowledgeDocumentResponse(
            id=doc.id,
            user_id=doc.user_id,
            title=doc.title,
            source_type=doc.source_type,
            status=doc.status,
            chunk_count=doc.chunk_count,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except Exception as e:
        logger.error(f"Error ingesting FAQ for user {user.id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to ingest FAQ: {str(e)}")


@router.post("/ingest/file", response_model=KnowledgeDocumentResponse)
async def ingest_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload and ingest PDF, DOCX, TXT, CSV, or XLSX document files.
    """
    filename = file.filename or "uploaded_doc"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_bytes = await file.read()

    if not content_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        if ext == "pdf":
            extracted_title, text_content, meta = KnowledgeService.extract_from_pdf(content_bytes, filename)
            source_type = "pdf"
        elif ext in ["docx", "doc"]:
            extracted_title, text_content, meta = KnowledgeService.extract_from_docx(content_bytes, filename)
            source_type = "docx"
        elif ext in ["csv", "txt"]:
            extracted_title, text_content, meta = KnowledgeService.extract_from_csv_or_excel(content_bytes, filename)
            source_type = "csv" if ext == "csv" else "txt"
        elif ext in ["png", "jpg", "jpeg", "webp"]:
            # Image / Poster file upload
            extracted_title = filename.rsplit(".", 1)[0].replace("_", " ").title()
            text_content = f"Company Poster / Image Asset: {extracted_title}\nFile Name: {filename}"
            meta = {"filename": filename, "file_size": len(content_bytes), "image_type": ext}
            source_type = "image"
        else:
            # General text fallback
            text_content = content_bytes.decode("utf-8", errors="ignore")
            extracted_title = filename
            meta = {"filename": filename, "file_size": len(content_bytes)}
            source_type = "file"

        final_title = title.strip() if title and title.strip() else extracted_title

        doc = await KnowledgeService.ingest_document(
            db=db,
            user_id=user.id,
            source_type=source_type,
            title=final_title,
            content=text_content,
            file_path=filename,
            meta_info=meta,
        )
        return KnowledgeDocumentResponse(
            id=doc.id,
            user_id=doc.user_id,
            title=doc.title,
            source_type=doc.source_type,
            status=doc.status,
            chunk_count=doc.chunk_count,
            meta_info=meta,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except Exception as e:
        logger.error(f"Error parsing file '{filename}' for user {user.id}: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to process file: {str(e)}")


@router.post("/documents/{doc_id}/sync", response_model=KnowledgeDocumentResponse)
async def sync_knowledge_document(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Re-sync a Google Sheet or URL document to refresh latest updates.
    """
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id, KnowledgeDocument.user_id == user.id)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")

    if not doc.source_url:
        raise HTTPException(status_code=400, detail="Only URL or Google Sheet documents with a linked source can be auto-synced.")

    try:
        doc.status = "syncing"
        await db.commit()

        if doc.source_type == "sheet":
            _, text_content, meta = await KnowledgeService.extract_from_google_sheet(doc.source_url)
        else:
            _, text_content, meta = await KnowledgeService.extract_from_url(doc.source_url)

        # Remove old chunks
        await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id))

        # Re-chunk and re-embed
        chunks = KnowledgeService.chunk_text(text_content)
        chunk_objects = []
        for idx, c_text in enumerate(chunks):
            emb = await KnowledgeService.generate_embedding(c_text)
            chunk_obj = KnowledgeChunk(
                document_id=doc.id,
                user_id=user.id,
                chunk_index=idx,
                content=c_text,
                embedding_json=json.dumps(emb) if emb else None,
                token_count=len(c_text.split()),
            )
            chunk_objects.append(chunk_obj)

        db.add_all(chunk_objects)
        doc.raw_content = text_content
        doc.meta_info = json.dumps(meta)
        doc.chunk_count = len(chunk_objects)
        doc.status = "indexed"
        doc.error_message = None
        doc.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(doc)

        return KnowledgeDocumentResponse(
            id=doc.id,
            user_id=doc.user_id,
            title=doc.title,
            source_type=doc.source_type,
            source_url=doc.source_url,
            status=doc.status,
            chunk_count=doc.chunk_count,
            meta_info=meta,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    except Exception as e:
        logger.error(f"Error re-syncing doc {doc_id}: {e}")
        doc.status = "error"
        doc.error_message = str(e)[:500]
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to sync document: {str(e)}")


@router.delete("/documents/{doc_id}")
async def delete_knowledge_document(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a knowledge document and all its indexed chunks.
    """
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id, KnowledgeDocument.user_id == user.id)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Knowledge document not found.")

    await db.delete(doc)
    await db.commit()
    return {"success": True, "message": f"Deleted knowledge document '{doc.title}'."}


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Search the authenticated user's isolated Knowledge Base using hybrid semantic & keyword retrieval.
    """
    results = await KnowledgeService.search_knowledge(
        db=db,
        user_id=user.id,
        query=payload.query,
        top_k=payload.top_k,
        threshold=payload.threshold,
    )

    items = [
        KnowledgeSearchResultItem(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            title=r["title"],
            source_type=r["source_type"],
            source_url=r.get("source_url"),
            content=r["content"],
            score=r["score"],
            meta=r.get("meta", {}),
        )
        for r in results
    ]

    return KnowledgeSearchResponse(
        query=payload.query,
        total_matches=len(items),
        results=items,
    )


class TenantSearchRequest(BaseModel):
    user_id: int = Field(..., description="Tenant / User ID")
    query: str = Field(..., description="Query to search")
    top_k: int = Field(4, ge=1, le=10)


@router.post("/tenant-search", response_model=KnowledgeSearchResponse)
async def tenant_search_knowledge(
    payload: TenantSearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Internal endpoint for LiveKit agent voice workers to search tenant knowledge base during active calls.
    """
    results = await KnowledgeService.search_knowledge(
        db=db,
        user_id=payload.user_id,
        query=payload.query,
        top_k=payload.top_k,
        threshold=0.15,
    )

    items = [
        KnowledgeSearchResultItem(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            title=r["title"],
            source_type=r["source_type"],
            source_url=r.get("source_url"),
            content=r["content"],
            score=r["score"],
            meta=r.get("meta", {}),
        )
        for r in results
    ]

    return KnowledgeSearchResponse(
        query=payload.query,
        total_matches=len(items),
        results=items,
    )
