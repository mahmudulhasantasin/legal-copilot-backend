import logging
import asyncio  # ✅ নতুন ইমপোর্ট (Render Port Binding Fix)
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ⚠️ CRITICAL: Import all models FIRST so SQLAlchemy resolves all relationships
import app.models  # noqa: F401

# ── Import Routers ──
from app.api import (
    auth,
    admin,
    company,
    payments,
    lawyer,
    chat,
    contract,
    settings,
    platform,
    inquiries,
)

# ── Import Database & Services ──
from app.core.database import get_db
from app.services.vector_store import vector_manager

# ── Import Cron Scheduler ──
from app.services.cron import start_scheduler, shutdown_scheduler

# ── Configure Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# LIFESPAN EVENTS (Startup & Shutdown)
# ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events.
    Starts the background scheduler for HITL SLAs and payment expiries.
    """
    logger.info("Starting ContractMind API...")
    
    # ✅ FIX: Render Port Binding & Event Loop Blocking Fix
    # We run the scheduler in a background task after a 5-second delay.
    # This allows Uvicorn to bind to Render's dynamic $PORT first.
    async def start_cron_safely():
        await asyncio.sleep(5) 
        try:
            start_scheduler()
            logger.info("✅ Background Cron Scheduler started successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to start scheduler: {e}")

    # Ensure new tables (e.g. feedback_suggestions, newsletter_subscribers) are created
    try:
        from app.core.database import engine, Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables verified/initialized.")
    except Exception as db_err:
        logger.warning(f"Database schema auto-sync note: {db_err}")

    asyncio.create_task(start_cron_safely())
    
    yield  # Application is running
    
    logger.info("Shutting down ContractMind API...")
    try:
        shutdown_scheduler()
        logger.info("🛑 Background Cron Scheduler stopped.")
    except Exception as e:
        logger.error(f"Error during scheduler shutdown: {e}")


# ──────────────────────────────────────────────────────────
# APP INITIALIZATION
# ──────────────────────────────────────────────────────────

app = FastAPI(
    title="ContractMind API",
    description="AI-powered Legal Assistant & Contract Auditor for Bangladesh Law",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",       # Swagger UI
    redoc_url="/api/redoc",     # ReDoc
    openapi_url="/api/openapi.json"
)


# ──────────────────────────────────────────────────────────
# MIDDLEWARE (CORS)
# ──────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",     
        "http://localhost:5173",     
        "http://localhost:3001",     
        "https://advotech-copilot.vercel.app",  # ✅ স্পেসিফিক লিংক
        "https://outliers-omega.vercel.app"     # ✅ স্পেসিফিক লিংক
    ],
    # ✅ FIX: allow_origin_regex রিমুভ করা হলো (যাতে যেকোনো ভের্সেল অ্যাপ এক্সেস না পায়)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────
# REGISTER ROUTERS
# ──────────────────────────────────────────────────────────

# Authentication & Onboarding
app.include_router(auth.router)

# System Admin
app.include_router(admin.router)

# Enterprise Admin
app.include_router(company.router)

# Payments (bKash Manual Flow)
app.include_router(payments.router)

# Lawyer HITL (Offers, Submissions, Withdrawals)
app.include_router(lawyer.router)

# Legal Chat (RAG)
app.include_router(chat.router)

# Contract Audit (LangGraph)
app.include_router(contract.router)

# User Settings
app.include_router(settings.router)

# ✅ Platform Metrics, Architecture & Pitch Deck (NEW)
app.include_router(platform.router)
app.include_router(inquiries.router)


# ──────────────────────────────────────────────────────────
# SYSTEM HEALTH & ROOT
# ──────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
async def root():
    return {
        "app": "ContractMind API",
        "status": "online",
        "docs": "/api/docs"
    }

@app.get("/health", tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    ✅ FIX: Real-time health check.
    Pings the PostgreSQL database and Qdrant Cloud to verify connectivity.
    """
    health_status = {
        "status": "healthy",
        "service": "ContractMind API",
        "components": {
            "database": "unknown",
            "qdrant": "unknown"
        }
    }
    
    is_healthy = True

    # 1. Check PostgreSQL Database
    try:
        await db.execute(text("SELECT 1"))
        health_status["components"]["database"] = "connected"
    except Exception as e:
        logger.error(f"[HealthCheck] DB connection failed: {e}")
        health_status["components"]["database"] = "error"
        is_healthy = False

    # 2. Check Qdrant Vector Database
    try:
        vector_manager.client.get_collection(vector_manager.collection_name)
        health_status["components"]["qdrant"] = "connected"
    except Exception as e:
        logger.error(f"[HealthCheck] Qdrant connection failed: {e}")
        health_status["components"]["qdrant"] = "error"
        is_healthy = False

    # 3. Update Overall Status
    if not is_healthy:
        health_status["status"] = "degraded"

    return JSONResponse(status_code=200, content=health_status)