# app/services/docs_content.py
"""
Default content for the live /docs page (pitch deck + technical documentation).

Every fact here is sourced from the real system:
  - Market / business model numbers come from app/api/platform.py /pitchdeck.
  - Benchmark numbers are the banked 2026-11-15 benchmark run (real, zero mocks).
  - Changelog entries mirror the actual git history of this repository.

Admins can override any of these via PUT /api/docs/content — the values stored
in docs_config (JSON blobs) always win over these defaults (see merged_content).
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.services.architecture_data import get_architecture_systems

# ──────────────────────────────────────────────────────────
# PITCH DECK (YC-style)
# ──────────────────────────────────────────────────────────

DEFAULT_PITCH_DECK: Dict[str, Any] = {
    "company": "Advotech Copilot",
    "team_name": "Team Advotech",
    "tagline": "AI-powered Legal Intelligence for the Global South",
    "one_liner": "30-second AI contract audits for ৳28 ($0.25) — verified by real lawyers. Legal help that 80% of South Asians can actually afford.",
    "problem": {
        "headline": "80% of South Asians lack access to justice",
        "points": [
            "A single contract review costs ৳50,000 (~$450) and takes 7 days via a traditional lawyer.",
            "Legal language is English-heavy; most citizens think in Bengali (or 'Banglish') — existing tools don't handle that.",
            "Small businesses sign contracts blind: unreliable landlords, vendors, and employers exploit the information gap.",
            "4.5M+ Bangladeshi diaspora need trustworthy legal help abroad with no local representation.",
        ],
    },
    "solution": {
        "headline": "An AI-native legal platform with two products: Copilot (Ask) and Audit (Verify)",
        "points": [
            "Copilot Chat: grounded legal Q&A over 3,547 Bangladesh statutes in Bengali, English and Banglish — every answer cites the exact act & section.",
            "Contract Audit: upload a PDF, get a 30-second clause-by-clause risk audit (missing clauses, unfair terms, severity-ranked) for ৳28.",
            "Human-in-the-loop marketplace: real, verified lawyers review and countersign AI audits when stakes are high.",
            "Lawyer Research Mode: deeper doctrinal retrieval + case-law synthesis for legal professionals.",
        ],
    },
    "why_now": {
        "headline": "Three curves just crossed",
        "points": [
            "LLM reasoning quality crossed the reliability threshold for bilingual legal retrieval (our Bengali RAG is benchmarked, not claimed).",
            "Bangladesh digital payments (SSLCommerz: bKash, Nagad, Rocket, cards) make ৳28 micro-payments finally collectable.",
            "Legal-tech in the Global South is still near-empty: global legal AI spends its budget on US/UK corpora, leaving 1.7B people underserved.",
        ],
    },
    "product_demo": {
        "headline": "Live, judgeable, and real",
        "points": [
            "Contract audit: upload a real PDF → severity-ranked findings with clause citations in ~30 seconds.",
            "Grounded chat: ask in Banglish ('সামান্য চুক্তি ভাঙলে কী হবে?') → answer cites the exact statute.",
            "Every AI claim is verifiable: citation precision and retrieval metrics are published on the live metrics page, not slideware.",
            "Open the app: the deployment is live on Render; /api/health pings the database and vector store in real time.",
        ],
    },
    "market": {
        "headline": "$1-2B Bangladesh legal services → $70B+ global legal tech by 2030",
        "points": [
            "Bangladesh legal services market: $1-2B annually, almost entirely offline.",
            "Beachhead wedge: contracts — high-frequency, high-pain, low-judgment work AI does reliably today.",
            "10,000+ foreign companies operating in Bangladesh need local compliance review.",
            "Global legal-tech: $28B (2024) growing to $70B+ by 2030; South Asia is the unclaimed chunk.",
        ],
    },
    "business_model": {
        "headline": "Three revenue engines, one billing rail",
        "points": [
            "B2C top-up: credit-based micro-payments via SSLCommerz (cards, bKash, Nagad, Rocket, NetBanking) — audited full engine with ledger, fulfilment and expiry cron.",
            "B2B enterprise: monthly subscriptions for corporate playbooks, team quotas and shared workspaces.",
            "MCP licensing: our legal MCP server (act lookup, section retrieval, case-law search) is licensable to other AI agent developers.",
        ],
    },
    "traction": {
        "headline": "Real numbers, zero mocks",
        "points": [
            "Contract Audit benchmark: 33/36 (91.7%) with 100% provision recall on the gold set.",
            "RAG benchmark: 46/55 effective (Router 100%, Act Hit@K 93.9%, MRR 0.864); Bengali accuracy +20pts after gold-set fixes.",
            "Hallucination benchmark: 88.9% (16/18) — out-of-scope and fabricated-section attacks deterministically blocked.",
            "Full backend test suite: 98 unit and integration tests passing, including security regression tests.",
            "35,000+ Bangladesh statutes ingested; payments engine (SSLCommerz + ledger) fully built and deployed.",
        ],
    },
    "competition": {
        "headline": "Global tools don't read Bengali; local lawyers don't scale",
        "points": [
            " Harvey / general LLMs: strong English, weak Bengali grounding, hallucinate statutes — we retrieve then generate, with deterministic guards.",
            "Traditional firms: 7 days + ৳50,000 per review vs our 30 seconds + ৳28.",
            "Local legal aggregators: directories, not AI — no audit engine, no payments, no benchmarks.",
        ],
    },
    "unique_advantage": {
        "headline": "Benchmarked bilingual legal RAG + a lawyer marketplace no pure-AI rival has",
        "points": [
            "3,547-act alias index + hybrid 2-path retrieval: reproducible accuracy numbers, not vibes.",
            "Deterministic safety layer: foreign-jurisdiction and non-existent-section guards make hallucination a test case, not a hope.",
            "HITL marketplace converts AI speed into lawyer-verified trust — a two-sided moat an LLM wrapper cannot copy.",
            "MCP server means our legal data becomes infrastructure for the whole agent ecosystem.",
        ],
    },
    "go_to_market": {
        "headline": "Land on contracts, expand into conversations",
        "points": [
            "Phase 1: young professionals & SMEs signing first rental/employment/vendor contracts (pay-per-audit, zero friction).",
            "Phase 2: law students & junior lawyers via Lawyer Research Mode and the MCP server.",
            "Phase 3: enterprises with playbooks + quotas; diaspora via remittance-friendly pricing.",
        ],
    },
    "vision": {
        "headline": "The default legal layer for the Global South",
        "points": [
            "Jurisdiction-agnostic core: Bangladesh is the beachhead; India (Q1 2027), Pakistan (Q2 2027), UAE (Q3 2027) need only data ingestion — zero code change.",
            "Every statute, every language, every contract type — one AI layer humans trust enough to countersign.",
        ],
    },
    "the_ask": "$500K seed to expand the legal data engineering team and launch 3 new South Asian jurisdictions.",
}

# ──────────────────────────────────────────────────────────
# TECH DOCS
# ──────────────────────────────────────────────────────────

DEFAULT_TECH_DOCS: Dict[str, Any] = {
    "product_overview": {
        "what_it_does": "Backend API for an AI-native legal platform: grounded bilingual legal chat, 30-second contract audits, lawyer marketplace, enterprise workspaces, credits & payments, and an MCP legal server.",
        "target_users": ["General users (Bengali/English/Banglish)", "Lawyers (research mode + marketplace)", "Enterprises (playbooks, quotas, shared audits)", "AI agent developers (MCP server)"],
        "core_use_cases": ["Ask a legal question with statute citations", "Audit a contract PDF for risky/missing clauses", "Get a lawyer to countersign an AI audit", "Buy credits and manage enterprise teams"],
    },
    "feature_matrix": [
        {"name": "AI Contract Audit (PDF → severity-ranked findings)", "status": "LIVE", "note": "91.7% gold-set accuracy, 100% provision recall"},
        {"name": "Grounded Legal Chat (RAG over 3,547 acts)", "status": "LIVE", "note": "Router 100%, Act Hit@K 93.9%, MRR 0.864"},
        {"name": "Lawyer Research Mode (doctrinal persona + case-law search)", "status": "LIVE", "note": "Live A/B verified; doctrine/precedent triggers wired"},
        {"name": "HITL Lawyer Marketplace (audit countersigning)", "status": "LIVE", "note": "SLA cron + inquiry flow"},
        {"name": "Credits & Payments (SSLCommerz: cards/bKash/Nagad/Rocket)", "status": "LIVE", "note": "Callbacks, IPN, ledger, fulfilment, expiry cron"},
        {"name": "Legal MCP Server (3 tools: acts, sections, case-law)", "status": "LIVE", "note": "FastMCP; retry ladder survives SSE disconnects"},
        {"name": "Live Metrics Page (metric_events)", "status": "LIVE", "note": "Benchmark runs stream to live DB events"},
        {"name": "Enterprise Workspaces & Playbooks", "status": "LIVE", "note": "Shared audits, quotas, approvals"},
        {"name": "Deterministic Hallucination Guards", "status": "LIVE", "note": "Foreign-jurisdiction router + non-existent-section absence guard"},
        {"name": "OCR Ingest (scanned PDFs)", "status": "BETA", "note": "12/13 gold cases; one known table-PDF defect"},
        {"name": "India / Pakistan / UAE jurisdictions", "status": "PLANNED", "note": "Q1-Q3 2027 — data ingestion only"},
    ],
    "architecture_mermaid": """flowchart LR
    UI[Web Client] -->|HTTPS JWT| API[FastAPI Backend]
    API --> AUTH[Auth / RBAC / token_version]
    API --> CHAT[Chat Router - LangGraph qa_graph]
    API --> AUDIT[Contract Router - Audit Graph]
    API --> PAY[Payments - SSLCommerz + Ledger]
    API --> LAW[Lawyer Marketplace - HITL SLA]
    API --> DOCS[/docs Module - this page/]
    CHAT --> RET[H Hybrid 2-path Retriever]
    RET --> Q[(Qdrant Cloud - e5-small)]
    RET --> PG[(Supabase PostgreSQL - 3547 acts)]
    CHAT --> LLM[LLM Ladder - Groq primary / GLM fallback]
    AUDIT --> LLM
    PAY --> PG
    LAW --> PG
    DOCS --> PG
    MCP[Legal MCP Server - FastMCP x3 tools] --> RET""",
    "dataflow_mermaid": """flowchart LR
    IN[Input: question or PDF] --> PRE[Language detect + route guard]
    PRE -->|chat| RET2[Hybrid retrieval: acts + case-law]
    PRE -->|audit| PARSE[PDF parse / OCR + clause split]
    RET2 --> GEN[LLM generation with citations]
    PARSE --> RULE[Rule verification layer + playbooks]
    RULE --> SEV[Severity ranking]
    GEN --> GUARD[Leak / injection / absence guards]
    SEV --> OUT[Output: cited answer or audit memo]
    OUT --> FB[metric_events feedback + live metrics]""",
    "tech_stack": [
        {"layer": "Backend", "items": "FastAPI (async), SQLAlchemy 2, Pydantic, LangGraph agent graphs"},
        {"layer": "AI", "items": "Groq LLM (primary) → GLM fallback ladder, hybrid 2-path retriever, deterministic guards; no reranker/BM25/graph-RAG needed for current scores"},
        {"layer": "Data", "items": "Supabase PostgreSQL, Qdrant Cloud (e5-small inference), 3,547-act alias index"},
        {"layer": "Payments", "items": "SSLCommerz (cards, bKash, Nagad, Rocket, NetBanking), credit ledger + expiry cron"},
        {"layer": "Infra", "items": "Render (auto-deploy on push), health checks ping DB + vector store"},
        {"layer": "Integrations", "items": "MCP server (FastMCP, 3 tools), email OTP, cron scheduler"},
    ],
    "api_docs": [
        {"group": "Auth", "endpoints": "POST /api/auth/request-otp · /verify-otp · /login · /refresh · POST /api/auth/logout", "auth": "JWT + token_version revocation"},
        {"group": "Chat", "endpoints": "POST /api/chat (grounded Q&A, persona routing, case-law search)", "auth": "JWT; credits per message"},
        {"group": "Contract Audit", "endpoints": "POST /api/contract/upload · GET /api/contract/memos · download", "auth": "JWT + BOLA owner/enterprise check"},
        {"group": "Payments", "endpoints": "POST /api/payments/initiate · SSLCommerz callback + IPN · ledger", "auth": "JWT + signed gateway callbacks"},
        {"group": "Lawyer", "endpoints": "Inquiries, SLA queue, countersigning, profiles", "auth": "JWT, LAWYER role gates"},
        {"group": "Platform", "endpoints": "GET /api/platform/metrics · /architecture · /pitchdeck · /jurisdictions", "auth": "Public"},
        {"group": "Docs", "endpoints": "GET /api/docs/access · /content · /team · /live-stats · /export.md", "auth": "Public when visibility window open; admin bypass"},
        {"group": "System", "endpoints": "GET /health · GET / · GET /api/docs (Swagger UI)", "auth": "Public"},
    ],
    "data_layer": {
        "sources": ["35,000+ Bangladesh statutes ingested into the act store (3,547 acts with alias index)", "Gold benchmark sets (contract, RAG, hallucination, OCR) maintained as executable tests", "No user data is scraped — uploads are user-initiated only"],
        "storage": "Supabase PostgreSQL (users, chats, memos, ledger, metric_events, docs_config) + Qdrant Cloud for vectors",
        "privacy": "OTP codes masked in emails, JWT token_version revocation, PII-redaction handled in the browser client, traversal-proof temp file handling for uploads",
    },
    "ai_layer": {
        "models": "Groq-hosted LLM (primary) with GLM as an audited third fallback; local Ollama models evaluated (gemma3n:e4b selected) for on-prem option",
        "retrieval": "Hybrid 2-path retriever (act aliases → sections; doctrine/precedent → case law). Act Hit@K 93.9%, MRR 0.864 on the banked benchmark.",
        "personalization": "Persona routing (GENERAL vs LAWYER) changes depth/style, never retrieval scope — retrieval is always the same verified corpus.",
        "explainability": "Every answer carries act + section citations; contract findings reference clause text; guards return deterministic refusals instead of silent guesses.",
    },
    "performance": {
        "load_expectation": "Single-region launch (Bangladesh): audit ~30s end-to-end, chat latency dominated by LLM + one Qdrant round-trip; connection pool + async DB layer sized for hundreds of concurrent sessions.",
        "optimization": "Async FastAPI everywhere, background cron for SLA/expiry, incremental audit caching, lazy-loaded /docs page with cached live stats, LLM retry ladder instead of blocking failures.",
    },
    "security": {
        "auth": "JWT with token_version revocation (logout bumps version), OTP-verified signup, RBAC (GENERAL/LAWYER/ENTERPRISE/ADMIN), production fails fast on weak SECRET_KEY.",
        "rbac": "Role-gated routers; enterprise members require admin approval; downloads use explicit owner-or-enterprise checks (BOLA-hardened).",
        "data_protection": "Prompt-injection hardening + leak guards on audit output, traversal-proof temp files, OTP masking, admin-only docs/publish controls.",
    },
}

# ──────────────────────────────────────────────────────────
# ROADMAP + CHANGELOG
# ──────────────────────────────────────────────────────────

DEFAULT_ROADMAP: Dict[str, Any] = {
    "short_term": [
        "Close the two remaining benchmark rotation fails (OCR table-PDF, RAG edge cases)",
        "Ship the 180-sec product video + submission polish",
        "Docs page admin panel refinements (drag/drop section ordering)",
    ],
    "mid_term": [
        "Corporate playbook self-service for enterprises",
        "MCP server public licensing program",
        "Dispute-preparation assistant (notice drafting, timeline building)",
    ],
    "long_term": [
        "India launch (Q1 2027) — 100,000 statute ingestion, Razorpay rail",
        "Pakistan (Q2 2027) and UAE (Q3 2027) following the same zero-code expansion path",
        "Lawyer network expansion into diaspora-heavy markets (UK, US, Gulf)",
    ],
}

DEFAULT_CHANGELOG: List[Dict[str, Any]] = [
    {"date": "2026-11-16", "version": "v1.3", "changes": [
        "Live /docs module: models, API router, access scheduler, this page",
        "Deterministic cited_cases backfill from referenced live precedents",
        "MCP tool-call retry ladder — survives Render SSE stream disconnects",
        "Case-law search now fires on doctrine/precedent lawyer queries",
    ]},
    {"date": "2026-11-15", "version": "v1.2", "changes": [
        "Solve-batch: foreign-jurisdiction router guard, non-existent-section absence guard",
        "Injection hardening + leak guards; JWT token_version revocation + logout",
        "OTP masking; BOLA download check; traversal-proof temp files",
        "Gold fixes: RAG-BN-011, RAG-EN-014 — Bengali accuracy +20pts",
        "Banked benchmarks: Contract 91.7%, RAG 46/55, Hallucination 88.9%, OCR 12/13",
    ]},
    {"date": "2026-11-14", "version": "v1.1", "changes": [
        "Rule verification layer: adaptive threshold, Qdrant cloud e5-small inference",
        "Benchmark harness runs agent functions directly (zero production side-effects)",
    ]},
]

DEFAULT_CUSTOM_SECTIONS: List[Dict[str, Any]] = []


def merged_content(cfg) -> Dict[str, Any]:
    """
    Merge the singleton DocsConfig row with defaults.
    Admin-stored JSON blobs win; missing/null fields fall back to defaults so
    the /docs page is never empty ("trust the data as real").
    """
    pitch = dict(DEFAULT_PITCH_DECK)
    stored_pitch = cfg.pitch_deck or {}
    for key, value in stored_pitch.items():
        if value is not None:
            pitch[key] = value

    if not pitch.get("company") or "ContractMind" in str(pitch.get("company")):
        pitch["company"] = "Advotech Copilot"

    tech = dict(DEFAULT_TECH_DOCS)
    stored_tech = cfg.tech_docs or {}
    for key, value in stored_tech.items():
        if value is not None:
            tech[key] = value

    roadmap = dict(DEFAULT_ROADMAP)
    stored_roadmap = cfg.roadmap or {}
    for key, value in stored_roadmap.items():
        if value is not None:
            roadmap[key] = value

    changelog = cfg.changelog if cfg.changelog else DEFAULT_CHANGELOG
    custom = cfg.custom_sections if cfg.custom_sections else DEFAULT_CUSTOM_SECTIONS

    return {
        "pitch_deck": pitch,
        "tech_docs": tech,
        "roadmap": roadmap,
        "changelog": changelog,
        "custom_sections": custom,
        "architecture_systems": get_architecture_systems(),
    }


# ──────────────────────────────────────────────────────────
# MARKDOWN EXPORT
# ──────────────────────────────────────────────────────────

def _md_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _md_block(title: str, data: Any, depth: int = 0) -> str:
    """Recursively render dicts/lists as Markdown (used by the .md export)."""
    lines: List[str] = []
    if isinstance(data, dict):
        if title:
            lines.append(f"{'#' * (depth + 2)} {title}\n")
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(_md_block(_key_to_title(key), value, depth + 1))
            elif value is not None:
                lines.append(f"- **{_key_to_title(key)}:** {_md_scalar(value)}")
        lines.append("")
    elif isinstance(data, list):
        if title:
            lines.append(f"{'#' * (depth + 2)} {title}\n")
        for item in data:
            if isinstance(item, dict):
                for key, value in item.items():
                    if isinstance(value, (dict, list)):
                        lines.append(_md_block(_key_to_title(key), value, depth + 1))
                    elif value is not None:
                        lines.append(f"- **{_key_to_title(key)}:** {_md_scalar(value)}")
                lines.append("")
            else:
                lines.append(f"- {_md_scalar(item)}")
        lines.append("")
    elif data is not None:
        if title:
            lines.append(f"{'#' * (depth + 2)} {title}\n")
        lines.append(f"{_md_scalar(data)}\n")
    return "\n".join(lines)


def _key_to_title(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def build_markdown(content: Dict[str, Any], team: List[Dict[str, Any]], stats: Dict[str, Any], schedule: Dict[str, Any]) -> str:
    """Build the full Markdown export of the /docs page."""
    pitch = content.get("pitch_deck", {})
    tech = content.get("tech_docs", {})
    out: List[str] = []
    out.append(f"# {pitch.get('company', 'Advotech Copilot')} — Live Documentation & Pitch Deck\n")
    out.append(f"> {pitch.get('tagline', '')}\n")
    out.append(f"> {pitch.get('one_liner', '')}\n")

    out.append(_md_block("Pitch Deck", pitch))
    out.append(_md_block("Technology & Architecture", tech))
    out.append(_md_block("Roadmap", content.get("roadmap", {})))
    out.append(_md_block("Changelog", content.get("changelog", [])))
    out.append(_md_block("Custom Sections", content.get("custom_sections", [])))

    out.append("## Team\n")
    for m in team:
        email = f" — {m['email']}" if m.get("email") else ""
        out.append(f"- **{m['full_name']}** ({m['role']}){email}")
    out.append("")

    out.append("## Live System Stats\n")
    for key, value in stats.items():
        out.append(f"- **{_key_to_title(key)}:** {_md_scalar(value)}")
    out.append("")

    out.append("## Visibility Schedule\n")
    out.append(f"- Visibility: {schedule.get('visibility')}")
    out.append(f"- Start: {schedule.get('schedule_start') or '—'}")
    out.append(f"- End: {schedule.get('schedule_end') or '—'}")
    out.append("")
    out.append(f"_Exported {schedule.get('exported_at', '')}_")
    return "\n".join(out)


# Optional: keep import surface explicit
__all__ = [
    "DEFAULT_PITCH_DECK",
    "DEFAULT_TECH_DOCS",
    "DEFAULT_ROADMAP",
    "DEFAULT_CHANGELOG",
    "DEFAULT_CUSTOM_SECTIONS",
    "merged_content",
    "build_markdown",
]
