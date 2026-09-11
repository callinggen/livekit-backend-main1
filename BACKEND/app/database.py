import os
from pathlib import Path

from datetime import datetime
from sqlalchemy import event, DateTime, TypeDecorator
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase

DB_PATH = (Path(__file__).resolve().parent.parent / "callinggen.db").as_posix()
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite+aiosqlite:///{DB_PATH}"

class SafeDateTime(TypeDecorator):
    """
    Guarantees that timezone-aware datetimes are safely stripped of tzinfo
    before being passed to PostgreSQL/asyncpg, preventing:
    'can't subtract offset-naive and offset-aware datetimes'
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and isinstance(value, datetime) and getattr(value, "tzinfo", None) is not None:
            return value.replace(tzinfo=None)
        return value

class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Disabled verbose SQL engine log spam
)


if "sqlite" in DATABASE_URL:
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


print("-" * 50)
print("DATABASE INIT")
print(f"PID: {os.getpid()}")
print(f"Absolute DB Path: {DB_PATH}")
print(f"Engine URL: {DATABASE_URL}")
print("-" * 50)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session