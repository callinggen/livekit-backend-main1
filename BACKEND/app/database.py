import os
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase

from typing import Any, Dict

DB_PATH = (Path(__file__).resolve().parent.parent / "callinggen.db").as_posix()
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite+aiosqlite:///{DB_PATH}"

class Base(DeclarativeBase):
    pass

engine_kwargs: dict[str, Any] = {"echo": False}
if "postgresql" in DATABASE_URL:
    engine_kwargs.update({
        "pool_size": 20,
        "max_overflow": 10,
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    })

engine = create_async_engine(
    DATABASE_URL,
    **engine_kwargs
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