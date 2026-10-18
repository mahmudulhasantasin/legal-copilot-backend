import asyncio
import logging
from sqlalchemy import text
from app.core.database import engine, Base
import app.models # ⚠️ Required to load all models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def reset_database():
    logger.warning("⚠️ Dropping all tables via SQL CASCADE...")
    
    async with engine.begin() as conn:
        # Fetch all table names in the public schema
        result = await conn.execute(text("""
            SELECT tablename FROM pg_tables WHERE schemaname = 'public';
        """))
        tables = [row[0] for row in result]
        
        if tables:
            # Drop all tables with CASCADE to bypass FK circular dependencies
            # Wrap table names in double quotes to handle case sensitivity
            drop_query = f"DROP TABLE IF EXISTS {', '.join(['\"' + t + '\"' for t in tables])} CASCADE;"
            await conn.execute(text(drop_query))
            logger.info(f"✅ Dropped {len(tables)} tables.")
        else:
            logger.info("✅ No tables to drop.")
            
    logger.info("🛠️ Creating all tables from new models...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    logger.info("🎉 Database reset and recreated successfully!")

if __name__ == "__main__":
    asyncio.run(reset_database())