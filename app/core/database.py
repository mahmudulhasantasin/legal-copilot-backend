import os
from typing import AsyncGenerator
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine
)
from sqlalchemy.orm import DeclarativeBase

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set. Please check your .env file!")

# Convert synchronous connection schemes to asyncpg/aiosqlite drivers
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("sqlite://"):
    DATABASE_URL = DATABASE_URL.replace("sqlite://", "sqlite+aiosqlite://", 1)

# Configure connect_args for asyncpg to prevent Supabase/PgBouncer duplicate prepared statement errors
connect_args = {}
if "asyncpg" in DATABASE_URL:
    connect_args = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    }

# Production Async Engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=15,
    max_overflow=10,
    connect_args=connect_args
)

# Async Session Factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for injecting async database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def collect_postgres_enum_values(metadata=None) -> list[tuple[str, str]]:
    """Collect unique (enum_type_name, value) pairs for native SQLAlchemy Enum columns."""
    import app.models  # noqa: F401
    from sqlalchemy import Enum as SQLEnum

    target_metadata = metadata or Base.metadata
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for table in target_metadata.tables.values():
        for column in table.columns:
            col_type = column.type
            if not isinstance(col_type, SQLEnum) or not col_type.native_enum:
                continue
            enum_name = col_type.name
            if not enum_name or not enum_name.isidentifier():
                continue
            for value in col_type.enums:
                key = (enum_name, str(value))
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)
    return pairs


async def sync_postgres_enums(target_engine=engine, log=None) -> int:
    """Idempotently add any missing Python Enum values to PostgreSQL enum types."""
    from sqlalchemy import text

    enum_pairs = collect_postgres_enum_values()
    if not enum_pairs:
        return 0

    added_count = 0
    try:
        async with target_engine.connect() as auto_conn:
            await auto_conn.execution_options(isolation_level="AUTOCOMMIT")
            existing_pairs: set[tuple[str, str]] = set()
            try:
                rows = await auto_conn.execute(text(
                    "SELECT t.typname, e.enumlabel "
                    "FROM pg_type t "
                    "JOIN pg_enum e ON t.oid = e.enumtypid "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = ANY(current_schemas(false));"
                ))
                existing_pairs = {(row[0], row[1]) for row in rows}
            except Exception:
                existing_pairs = set()

            for enum_name, enum_val in enum_pairs:
                if existing_pairs and (enum_name, enum_val) in existing_pairs:
                    continue
                escaped_val = enum_val.replace("'", "''")
                enum_stmt = f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{escaped_val}';"
                try:
                    await auto_conn.execute(text(enum_stmt))
                    added_count += 1
                except Exception as inner_enum_err:
                    if log:
                        log.warning(f"Enum migration note ({enum_stmt}): {inner_enum_err}")
    except Exception as enum_err:
        if log:
            log.warning(f"Enum migration note: {enum_err}")
    return added_count