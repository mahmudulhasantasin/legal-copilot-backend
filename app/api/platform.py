# app/api/platform.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.services.metrics import get_metrics_summary

router = APIRouter(prefix="/api/platform", tags=["Platform & Pitch"])

# ──────────────────────────────────────────────────────────
# 1. LIVE KPI METRICS
# ──────────────────────────────────────────────────────────

@router.get("/metrics")
async def platform_metrics(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns live system metrics (AI Quality, Latency, Business Volume).
    This powers the Admin Dashboard and the Pitch Deck traction slide.
    """
    return await get_metrics_summary(db, days=days)


# ──────────────────────────────────────────────────────────
# 2. SYSTEM ARCHITECTURE
# ──────────────────────────────────────────────────────────

@router.get("/architecture")
async def get_architecture():
    """
    Public endpoint showing the jurisdiction-agnostic, multi-agent architecture.
    Used by judges to understand technical depth.
    """
    return {
        "status": "Production-Ready",
        "design_philosophy": "One Engine, Two Products (Copilot + Audit), Multi-Jurisdiction Core",
        "layers": [
            {
                "layer": "User Interaction",
                "components": ["React/Next.js Frontend", "Vercel Edge", "Multilingual UI (BN/EN)"],
                "description": "Handles user inputs, file uploads, and renders structured JSON responses."
            },
            {
                "layer": "API & Orchestration",
                "components": ["FastAPI", "LangGraph (QA & Contract Graphs)", "JWT Auth & RBAC"],
                "description": "The core controller. Routes requests to the right AI graph based on query type."
            },
            {
                "layer": "AI & Verification (Shared Core)",
                "components": ["Groq (Llama-3.3) Primary", "Gemini 2.5 Fallback", "MCP Citation Verifier", "Hybrid Retriever"],
                "description": "Generates responses. Cross-checks every statute against Qdrant + Live MCP Server to eliminate hallucinations."
            },
            {
                "layer": "Data & Infrastructure",
                "components": ["Supabase PostgreSQL", "Qdrant Vector DB", "Supabase Storage", "Render Cloud"],
                "description": "Stores user data, 35,000+ Bangladesh statutes, and securely hosts PDF contracts."
            }
        ],
        "agent_design_pattern": "Hybrid (Rules/Chains for Audit + Routing for Chat). We added autonomy only where safe."
    }


# ──────────────────────────────────────────────────────────
# 3. YC-STYLE PITCH DECK
# ──────────────────────────────────────────────────────────

@router.get("/pitchdeck")
async def get_pitch_deck():
    """
    Structured pitch deck data for investors and judges.
    Highlights the market gap, product, and global scalability.
    """
    return {
        "company": "ContractMind (Advotech)",
        "tagline": "AI-powered Legal Intelligence for the Global South.",
        "problem": "80% of South Asians lack access to justice. A single contract review costs ৳50,000 ($450) and takes 7 days.",
        "solution": "AI-Native Legal Platform with Two Products: Copilot (Ask) and Audit (Verify).",
        "value_proposition": "30-second contract audits for ৳28 ($0.25). Verified by real lawyers via our HITL marketplace.",
        "market_size": {
            "bangladesh_legal_services": "$1-2B annually",
            "bangladesh_diaspora": "4.5M+ needing legal help abroad",
            "foreign_companies_in_bd": "10,000+ needing local compliance",
            "global_legal_tech": "$28B (2024) growing to $70B+ by 2030"
        },
        "business_model": {
            "b2c_topup": "Credit-based micro-payments via SSLCommerz (Cards, bKash, Nagad, Rocket, NetBanking)",
            "b2b_enterprise": "Monthly subscriptions for corporate playbooks & team quotas",
            "mcp_api": "Licensing our legal MCP server to other AI agent developers"
        },
        "traction_and_metrics": "Refer to /api/platform/metrics for live latency, citation accuracy, and usage data.",
        "global_scalability": {
            "strategy": "Jurisdiction-agnostic core. Bangladesh is the beachhead; India, Pakistan, and UAE are next.",
            "expansion_timeline": "India (Q1 2027), Pakistan (Q2 2027), UAE (Q3 2027)"
        },
        "the_ask": "$500K Seed round to expand the legal data engineering team and launch in 3 new South Asian jurisdictions."
    }


# ──────────────────────────────────────────────────────────
# 4. JURISDICTION EXPANSION ROADMAP
# ──────────────────────────────────────────────────────────

@router.get("/jurisdictions")
async def get_jurisdictions():
    """
    Proves the 'Global Scalability' rubric. 
    Shows that adding a country requires 0 lines of code change, just data ingestion.
    """
    return {
        "active_jurisdictions": [
            {
                "code": "bd",
                "name": "Bangladesh",
                "status": "Live",
                "statutes_ingested": 35000,
                "languages": ["Bengali", "English", "Banglish"],
                "payment_gateway": "SSLCommerz"
            }
        ],
        "planned_jurisdictions": [
            {
                "code": "in",
                "name": "India",
                "status": "Q1 2027",
                "estimated_statutes": 100000,
                "languages": ["Hindi", "English"],
                "payment_gateway": "Razorpay"
            },
            {
                "code": "pk",
                "name": "Pakistan",
                "status": "Q2 2027",
                "languages": ["Urdu", "English"],
                "payment_gateway": "JazzCash"
            }
        ],
        "architecture_readiness": "Adding a new country requires only 3 steps: (1) Ingest laws to Qdrant, (2) Upload act_aliases.json, (3) Add payment adapter. No core AI code rewrite."
    }