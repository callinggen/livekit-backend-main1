from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from app.database import Base


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    source_type = Column(String(50), nullable=False, default="raw")  # 'url', 'pdf', 'docx', 'txt', 'csv', 'xlsx', 'sheet', 'faq', 'raw', 'image'
    source_url = Column(String(1024), nullable=True)
    file_path = Column(String(512), nullable=True)
    raw_content = Column(Text, nullable=True)
    meta_info = Column(Text, nullable=True)  # JSON metadata (e.g. sheet_url, file_size, headers)
    status = Column(String(50), nullable=False, default="indexed")  # 'indexed', 'syncing', 'error'
    chunk_count = Column(Integer, nullable=False, default=0)
    error_message = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    chunks = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    content = Column(Text, nullable=False)
    embedding_json = Column(Text, nullable=True)  # Vector embedding floats serialised as JSON list
    token_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    document = relationship("KnowledgeDocument", back_populates="chunks")


# Compound index for fast tenant filtering
Index("ix_knowledge_chunks_user_doc", KnowledgeChunk.user_id, KnowledgeChunk.document_id)
