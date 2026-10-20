import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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
)

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
    start_scheduler()
    
    yield  # Application is running
    
    logger.info("Shutting down ContractMind API...")
    shutdown_scheduler()


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
        "https://advotech-copilot.vercel.app",  # ✅ স্পেসিফিক লিংক (স্ল্যাশ ছাড়া)
        "https://outliers-omega.vercel.app"     # ✅ স্পেসিফিক লিংক (স্ল্যাশ ছাড়া)
    ],
    # ✅ এই ম্যাজিক লাইনটি vercel.app-এর যেকোনো সাবডোমেইনকে অনুমতি দেবে
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
async def health_check():
    """
    Basic health check. 
    In a full production setup, this should also ping the DB and Qdrant.
    """
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "service": "ContractMind API"
        }
    )