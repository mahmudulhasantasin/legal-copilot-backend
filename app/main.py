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

    # Ensure new tables and newly added columns are migrated
    try:
        from app.core.database import engine, Base, sync_postgres_enums

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Auto-migrate missing columns on existing PostgreSQL tables
            migration_sqls = [
                "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS suggested_follow_ups JSON;",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS gateway_session_key VARCHAR(120);",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS val_id VARCHAR(120);",
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_payments_val_id ON payments (val_id) WHERE val_id IS NOT NULL;",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS bank_tran_id VARCHAR(120);",
                "CREATE INDEX IF NOT EXISTS ix_payments_bank_tran_id ON payments (bank_tran_id);",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS card_type VARCHAR(80);",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS card_issuer VARCHAR(120);",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS card_brand VARCHAR(60);",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS store_amount_bdt NUMERIC(10, 2);",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS gateway_status VARCHAR(40);",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS gateway_validated_at TIMESTAMPTZ;",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS gateway_raw_response JSON;",
                "ALTER TABLE withdrawal_requests ADD COLUMN IF NOT EXISTS payout_method VARCHAR(30) DEFAULT 'BKASH' NOT NULL;",
                "ALTER TABLE withdrawal_requests ADD COLUMN IF NOT EXISTS account_number VARCHAR(60);",
                "ALTER TABLE withdrawal_requests ADD COLUMN IF NOT EXISTS bank_name VARCHAR(120);",
                "ALTER TABLE withdrawal_requests ADD COLUMN IF NOT EXISTS branch_name VARCHAR(120);",
                "ALTER TABLE withdrawal_requests ALTER COLUMN bkash_number TYPE VARCHAR(60);",
                "ALTER TABLE lawyer_profiles ADD COLUMN IF NOT EXISTS auto_accept_offers BOOLEAN DEFAULT FALSE;",
                "ALTER TABLE lawyer_profiles ADD COLUMN IF NOT EXISTS max_concurrent_tasks INTEGER DEFAULT 5;",
                "ALTER TABLE enterprises ADD COLUMN IF NOT EXISTS pending_admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS enterprise_id INTEGER REFERENCES enterprises(id) ON DELETE SET NULL;",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS company_role VARCHAR(50);",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS member_approval_status VARCHAR(50);",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS member_credit_quota INTEGER;",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS member_credit_used INTEGER;",
                "UPDATE users SET member_approval_status = 'APPROVED', company_role = 'ADMIN' WHERE role = 'ENTERPRISE_ADMIN' AND (member_approval_status IS NULL OR member_approval_status != 'APPROVED' OR company_role IS NULL OR company_role != 'ADMIN');",
                "INSERT INTO credit_packages (name, tier, description, price_bdt, base_credits, bonus_credits, applies_to, is_active, display_order, badge_text, created_at) SELECT 'Enterprise Growth', 'ENTERPRISE', 'For growing legal teams (up to 5 members).', 5000.00, 5000, 500, 'ENTERPRISE', TRUE, 4, 'Team Choice', NOW() WHERE NOT EXISTS (SELECT 1 FROM credit_packages WHERE name = 'Enterprise Growth');",
                "INSERT INTO credit_packages (name, tier, description, price_bdt, base_credits, bonus_credits, applies_to, is_active, display_order, badge_text, created_at) SELECT 'Enterprise Scale', 'ENTERPRISE', 'For corporate legal teams (5-10 members).', 10000.00, 8000, 3500, 'ENTERPRISE', TRUE, 5, 'Most Popular', NOW() WHERE NOT EXISTS (SELECT 1 FROM credit_packages WHERE name = 'Enterprise Scale' OR name = 'Enterprise Pack');",
                "UPDATE credit_packages SET name = 'Enterprise Scale', description = 'For corporate legal teams (5-10 members).', price_bdt = 10000.00, base_credits = 8000, bonus_credits = 3500, badge_text = 'Most Popular' WHERE name = 'Enterprise Pack';",
            ]
            for sql in migration_sqls:
                try:
                    async with conn.begin_nested():
                        await conn.execute(text(sql))
                except Exception as sql_err:
                    logger.warning(f"Migration note ({sql}): {sql_err}")

        await sync_postgres_enums(engine, log=logger)
        logger.info("✅ Database tables and columns verified/migrated.")
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
    allow_origin_regex=r"https://.*\.vercel\.app",
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

# Payments (SSLCommerz Gateway)
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

@app.api_route("/", methods=["GET", "HEAD"], tags=["System"])
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