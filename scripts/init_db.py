import asyncio
import os
import sys

# Ensure root directory is present in sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import engine, Base
import app.models  # Registers all declarative models

async def init_models():
    """Initializes and synchronizes database tables with Supabase PostgreSQL."""
    print("Connecting to database and generating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Database tables initialized successfully!")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(init_models())