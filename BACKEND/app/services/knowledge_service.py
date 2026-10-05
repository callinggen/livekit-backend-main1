import io
import os
import re
import json
import math
import csv
import logging
import asyncio
from typing import List, Dict, Any, Optional, Tuple
import httpx
from bs4 import BeautifulSoup

from openai import AsyncOpenAI
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeDocument, KnowledgeChunk

logger = logging.getLogger("callinggen.knowledge")

# Optional OpenAI client for real vector embeddings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


class KnowledgeService:
    """
    Multi-tenant RAG Knowledge Ingestion, Chunking & Hybrid Search Service for CallingGen.
    Supports URLs, PDFs, Word DOCX, CSV/Excel Spreadsheets, Google Sheets, FAQs, and Raw Text.
    """

    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean and normalize whitespace and unicode characters."""
        if not text:
            return ""
        text = re.sub(r"\r\n", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @classmethod
    def chunk_text(cls, text: str, max_chunk_chars: int = 700, overlap_chars: int = 100) -> List[str]:
        """
        Smart text chunker with paragraph and sentence preservation.
        Ensures chunks stay concise and context-rich for voice agents.
        """
        cleaned = cls._clean_text(text)
        if not cleaned:
            return []

        if len(cleaned) <= max_chunk_chars:
            return [cleaned]

        paragraphs = cleaned.split("\n\n")
        chunks: List[str] = []
        current_chunk = ""

        for p in paragraphs:
            p = p.strip()
            if not p:
                continue

            if len(current_chunk) + len(p) + 2 <= max_chunk_chars:
                current_chunk = f"{current_chunk}\n\n{p}" if current_chunk else p
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                
                # If a single paragraph is larger than max_chunk_chars, split by sentences
                if len(p) > max_chunk_chars:
                    sentences = re.split(r"(?<=[.?!])\s+", p)
                    sub_chunk = ""
                    for s in sentences:
                        if len(sub_chunk) + len(s) + 1 <= max_chunk_chars:
                            sub_chunk = f"{sub_chunk} {s}" if sub_chunk else s
                        else:
                            if sub_chunk:
                                chunks.append(sub_chunk.strip())
                            sub_chunk = s
                    if sub_chunk:
                        current_chunk = sub_chunk.strip()
                else:
                    current_chunk = p

        if current_chunk:
            chunks.append(current_chunk.strip())

        return [c for c in chunks if len(c.strip()) > 10]

    # ── Source Extractors ──────────────────────────────────────────────────

    @classmethod
    async def extract_from_url(cls, url: str) -> Tuple[str, str, Dict[str, Any]]:
        """
        Scrape and extract clean readable text and metadata from a web page link.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 CallingGenBot/1.0"
        }
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        soup = BeautifulSoup(html, "html.parser")
        
        # Remove noisy elements
        for tag in soup(["script", "style", "nav", "footer", "noscript", "svg", "header", "aside"]):
            tag.decompose()

        # Extract title
        title = soup.title.string.strip() if soup.title and soup.title.string else url

        # Extract body text
        body_text = soup.get_text(separator="\n")
        cleaned = cls._clean_text(body_text)

        meta = {
            "url": url,
            "title": title,
            "status_code": resp.status_code,
            "content_length": len(cleaned),
        }
        return title, cleaned, meta

    @classmethod
    async def extract_from_google_sheet(cls, sheet_url: str) -> Tuple[str, str, Dict[str, Any]]:
        """
        Fetch and parse public or exportable Google Sheet URL into structured knowledge cards.
        """
        # Convert standard Google Sheet URL to CSV export link if needed
        csv_url = sheet_url
        if "/edit" in sheet_url:
            csv_url = re.sub(r"/edit.*", "/export?format=csv", sheet_url)
        elif "docs.google.com/spreadsheets" in sheet_url and "/export" not in sheet_url:
            csv_url = sheet_url.rstrip("/") + "/export?format=csv"

        headers = {
            "User-Agent": "CallingGen-GoogleSheet-Sync/1.0"
        }
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(csv_url)
            resp.raise_for_status()
            csv_content = resp.text

        reader = csv.reader(io.StringIO(csv_content))
        rows = list(reader)
        if not rows:
            raise ValueError("Google Sheet is empty or could not be read.")

        header_row = [h.strip() for h in rows[0]]
        card_items = []
        for idx, r in enumerate(rows[1:], start=1):
            row_dict = {}
            for col_idx, val in enumerate(r):
                col_name = header_row[col_idx] if col_idx < len(header_row) and header_row[col_idx] else f"Column_{col_idx+1}"
                if val.strip():
                    row_dict[col_name] = val.strip()
            
            if row_dict:
                details = ", ".join([f"{k}: {v}" for k, v in row_dict.items()])
                card_items.append(f"[Record #{idx}] {details}")

        title = f"Google Sheet Data ({len(rows)-1} rows)"
        full_text = "\n\n".join(card_items)
        meta = {
            "sheet_url": sheet_url,
            "csv_export_url": csv_url,
            "headers": header_row,
            "total_rows": len(rows) - 1,
        }
        return title, full_text, meta

    @classmethod
    def extract_from_pdf(cls, file_bytes: bytes, filename: str) -> Tuple[str, str, Dict[str, Any]]:
        """Extract text from PDF file bytes."""
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    pages_text.append(f"--- Page {i+1} ---\n{text.strip()}")

            full_text = cls._clean_text("\n\n".join(pages_text))
            title = filename.replace(".pdf", "").replace("_", " ").title()
            meta = {
                "filename": filename,
                "pages": len(reader.pages),
                "file_size": len(file_bytes),
            }
            return title, full_text, meta
        except Exception as e:
            logger.error(f"Error parsing PDF '{filename}': {e}")
            raise ValueError(f"Failed to extract text from PDF: {e}")

    @classmethod
    def extract_from_docx(cls, file_bytes: bytes, filename: str) -> Tuple[str, str, Dict[str, Any]]:
        """Extract text from Microsoft Word DOCX bytes."""
        try:
            import docx
            doc = docx.Document(io.BytesIO(file_bytes))
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            
            # Extract tables as well
            table_rows = []
            for table in doc.tables:
                for row in table.rows:
                    row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if row_cells:
                        table_rows.append(" | ".join(row_cells))

            all_content = paragraphs + (["\n--- Tables ---"] + table_rows if table_rows else [])
            full_text = cls._clean_text("\n\n".join(all_content))
            title = filename.replace(".docx", "").replace(".doc", "").replace("_", " ").title()
            meta = {
                "filename": filename,
                "paragraphs": len(paragraphs),
                "file_size": len(file_bytes),
            }
            return title, full_text, meta
        except Exception as e:
            logger.error(f"Error parsing DOCX '{filename}': {e}")
            raise ValueError(f"Failed to extract text from Word document: {e}")

    @classmethod
    def extract_from_csv_or_excel(cls, file_bytes: bytes, filename: str) -> Tuple[str, str, Dict[str, Any]]:
        """Extract structured records from CSV or XLSX files."""
        if filename.endswith(".csv") or filename.endswith(".txt"):
            text_data = file_bytes.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text_data))
            rows = list(reader)
        else:
            # Fallback text extract
            text_data = file_bytes.decode("utf-8", errors="ignore")
            return filename, text_data, {"filename": filename}

        if not rows:
            return filename, "", {"filename": filename, "total_rows": 0}

        headers = [h.strip() for h in rows[0]]
        records = []
        for idx, r in enumerate(rows[1:], start=1):
            items = []
            for c_idx, val in enumerate(r):
                h_name = headers[c_idx] if c_idx < len(headers) and headers[c_idx] else f"Col_{c_idx+1}"
                if val.strip():
                    items.append(f"{h_name}: {val.strip()}")
            if items:
                records.append(f"Record {idx}: {', '.join(items)}")

        title = filename.rsplit(".", 1)[0].replace("_", " ").title()
        full_text = "\n\n".join(records)
        meta = {
            "filename": filename,
            "headers": headers,
            "total_rows": len(rows) - 1,
            "file_size": len(file_bytes),
        }
        return title, full_text, meta

    # ── Embedding Generator ────────────────────────────────────────────────

    @classmethod
    async def generate_embedding(cls, text: str) -> Optional[List[float]]:
        """
        Generate embedding vector using OpenAI if configured,
        or compute lightweight normalized term frequency fallback.
        """
        api_key = os.getenv("OPENAI_API_KEY", "")
        if api_key:
            try:
                client = AsyncOpenAI(api_key=api_key)
                # Truncate text to avoid token limits
                truncated = text[:8000]
                resp = await client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=truncated,
                )
                return resp.data[0].embedding
            except Exception as e:
                logger.warning(f"OpenAI embedding generation failed, using local fallback: {e}")

        # Fallback deterministic pseudo-embedding (128-dim normalized hash vector for cosine similarity)
        import zlib
        words = re.findall(r"\w+", text.lower())
        vec = [0.0] * 128
        if not words:
            return vec
        for w in words:
            # Deterministic CRC32 across all Python processes
            h = zlib.crc32(w.encode("utf-8")) % 128
            vec[h] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    # ── Ingest & Save Pipeline ─────────────────────────────────────────────

    @classmethod
    async def ingest_document(
        cls,
        db: AsyncSession,
        user_id: int,
        source_type: str,
        title: str,
        content: str,
        source_url: Optional[str] = None,
        file_path: Optional[str] = None,
        meta_info: Optional[Dict[str, Any]] = None,
    ) -> KnowledgeDocument:
        """
        Ingest a document, chunk it into search vectors, and save to database.
        """
        cleaned_content = cls._clean_text(content)
        if not cleaned_content:
            raise ValueError("Cannot ingest empty content.")

        # Create Document Record
        doc = KnowledgeDocument(
            user_id=user_id,
            title=title.strip() or "Untitled Document",
            source_type=source_type,
            source_url=source_url,
            file_path=file_path,
            raw_content=cleaned_content,
            meta_info=json.dumps(meta_info or {}),
            status="syncing",
            chunk_count=0,
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)

        try:
            # Chunk the content
            chunks = cls.chunk_text(cleaned_content)
            chunk_objects = []

            for idx, c_text in enumerate(chunks):
                # Generate embedding
                emb = await cls.generate_embedding(c_text)
                chunk_obj = KnowledgeChunk(
                    document_id=doc.id,
                    user_id=user_id,
                    chunk_index=idx,
                    content=c_text,
                    embedding_json=json.dumps(emb) if emb else None,
                    token_count=len(c_text.split()),
                )
                chunk_objects.append(chunk_obj)

            db.add_all(chunk_objects)
            doc.chunk_count = len(chunk_objects)
            doc.status = "indexed"
            await db.commit()
            await db.refresh(doc)
            return doc

        except Exception as e:
            logger.error(f"Error indexing chunks for doc {doc.id}: {e}")
            doc.status = "error"
            doc.error_message = str(e)[:500]
            await db.commit()
            raise

    # ── Hybrid Search Engine ───────────────────────────────────────────────

    @classmethod
    async def search_knowledge(
        cls,
        db: AsyncSession,
        user_id: int,
        query: str,
        document_ids: Optional[List[int]] = None,
        top_k: int = 5,
        threshold: float = 0.20,
    ) -> List[Dict[str, Any]]:
        """
        Multi-tenant Hybrid Knowledge Search:
        Combines semantic embedding similarity + keyword full-text token overlap.
        Guarantees 100% strict user_id isolation and optional document filtering.
        """
        if not query or not query.strip():
            return []

        query_cleaned = query.strip()
        query_words = set(re.findall(r"\w+", query_cleaned.lower()))

        # 1. Fetch user's chunks (optionally scoped to specific document_ids)
        stmt = (
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(KnowledgeChunk.user_id == user_id)
        )
        if document_ids and len(document_ids) > 0:
            valid_ids = [int(i) for i in document_ids if str(i).isdigit()]
            if valid_ids:
                stmt = stmt.where(KnowledgeDocument.id.in_(valid_ids))

        result = await db.execute(stmt)
        rows = result.all()

        if not rows:
            return []

        # 2. Compute query embedding
        query_emb = await cls.generate_embedding(query_cleaned)

        scored_results = []
        for chunk, doc in rows:
            # Vector Cosine Similarity
            vector_score = 0.0
            if query_emb and chunk.embedding_json:
                try:
                    c_vec = json.loads(chunk.embedding_json)
                    if len(c_vec) == len(query_emb):
                        dot = sum(a * b for a, b in zip(query_emb, c_vec))
                        q_norm = math.sqrt(sum(a * a for a in query_emb)) or 1.0
                        c_norm = math.sqrt(sum(b * b for b in c_vec)) or 1.0
                        vector_score = max(0.0, dot / (q_norm * c_norm))
                except Exception:
                    pass

            # Keyword Token Overlap Score (excluding trivial stopwords)
            stop_words = {"what", "is", "are", "do", "does", "did", "you", "your", "the", "a", "an", "in", "on", "at", "for", "to", "of", "and", "or", "can", "how", "why", "we", "i", "me", "my"}
            meaningful_query_words = {w for w in query_words if len(w) > 2 and w not in stop_words} or query_words
            
            chunk_words = set(re.findall(r"\w+", chunk.content.lower()))
            # Exact + stem overlap (e.g. "refund" matches "refunds")
            matched_terms = set()
            for qw in meaningful_query_words:
                if any(qw in cw or cw in qw for cw in chunk_words if len(cw) > 2):
                    matched_terms.add(qw)
            
            keyword_score = len(matched_terms) / max(1, len(meaningful_query_words))

            # Hybrid Weighted Score (60% semantic vector, 40% keyword overlap)
            final_score = (vector_score * 0.6) + (keyword_score * 0.4)

            # Boost exact phrases or document titles
            if doc.title.lower() in query_cleaned.lower() or any(qw in doc.title.lower() for qw in meaningful_query_words):
                final_score += 0.15

            if final_score >= threshold or keyword_score >= 0.25:
                meta = {}
                try:
                    if doc.meta_info:
                        meta = json.loads(doc.meta_info)
                except Exception:
                    pass

                scored_results.append({
                    "chunk_id": chunk.id,
                    "document_id": doc.id,
                    "title": doc.title,
                    "source_type": doc.source_type,
                    "source_url": doc.source_url,
                    "content": chunk.content,
                    "score": round(min(1.0, final_score), 3),
                    "meta": meta,
                })

        # Sort descending by score
        scored_results.sort(key=lambda x: x["score"], reverse=True)
        return scored_results[:top_k]
