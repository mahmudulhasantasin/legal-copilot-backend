# ContractMind API — Sovereign Legal AI & Contract Defense Backend

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-blue)](https://www.langchain.com/langgraph)
[![Qdrant](https://img.shields.io/badge/Qdrant-Cloud-DC382D?logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-4169E1?logo=postgresql&logoColor=white)](https://supabase.com/)
[![SSLCommerz](https://img.shields.io/badge/Payments-SSLCommerz_v4-0070BA)](https://sslcommerz.com/)
[![FastMCP](https://img.shields.io/badge/MCP-FastMCP_2.0-8A2BE2)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/License-Proprietary-red)](#)

> **The Sovereign AI Legal Intelligence & Contract Defense Backend for Bangladesh Law.**  
> Built for the **International AI Builders Congress 2026** under **Domain 10: JusticeSphere AI — Legal, Compliance & Trust Technologies**.

---

## 📌 Table of Contents
- [Executive Overview](#-executive-overview)
- [System Architecture](#-system-architecture)
- [Core Engines & Capabilities](#-core-engines--capabilities)
  - [1. Dual-Path Statutory RAG Engine](#1-dual-path-statutory-rag-engine)
  - [2. Autonomous ContractMind Risk Auditor](#2-autonomous-contractmind-risk-auditor)
  - [3. Human-in-the-Loop (HITL) Advocate Marketplace](#3-human-in-the-loop-hitl-advocate-marketplace)
  - [4. FinTech & Double-Entry Credit Ledger](#4-fintech--double-entry-credit-ledger)
  - [5. Standalone FastMCP 2.0 Server](#5-standalone-fastmcp-20-server)
- [Security & Production Hardening](#-security--production-hardening)
- [API Specification & Endpoints](#-api-specification--endpoints)
- [Local Setup & Installation](#-local-setup--installation)
- [Docker & Cloud Deployment](#-docker--cloud-deployment)
- [FastMCP Integration (Cursor & Claude Desktop)](#-fastmcp-integration-cursor--claude-desktop)
- [Testing & Quality Assurance](#-testing--quality-assurance)
- [Live Deployments & Project Links](#-live-deployments--project-links)

---

## 🏛️ Executive Overview

In South Asia, access to legal counsel is crippled by severe structural asymmetry:
1. **Contract Inequity**: Over 78% of freelancers, MSMEs, and expatriate workers sign vendor contracts containing unenforceable covenants (e.g. § 27 Contract Act 1872 non-competes), uncapped indemnities, and non-compliant termination terms under the Bangladesh Labour Act 2006.
2. **LLM Hallucination Hazards**: Generic LLMs hallucinate non-existent statutory sections in 43% of South Asian law queries and cite repealed colonial provisions.
3. **Chamber Fragmentation**: Judicial backlogs (4M+ pending cases) force manual legislative gazette searches, while clients lack transparent pricing and SLA-backed advocate reviews.

**ContractMind API** solves this through a sovereign 5-tier architecture combining deterministic statutory guardrails, multi-agent LangGraph reasoning, Qdrant hybrid retrieval, client-side zero-trust PII scrubbing, and verified advocate escrow dispatch.

---

## 📐 System Architecture

```mermaid
graph TD
    Client["Client Layer\n(Next.js 16 Web / Cursor / Claude Desktop)"] -->|HTTPS / REST / FastMCP| Gateway["FastAPI API Gateway\n(Uvicorn + Security Middleware)"]

    Gateway --> Auth["Authentication & RBAC\n(JWT tv-versioning, BCrypt DOS guard)"]
    Gateway --> QA["Chat Copilot Graph\n(LangGraph State Machine)"]
    Gateway --> Audit["ContractMind Auditor\n(AST Clause Segmenter & Redlining)"]
    Gateway --> HITL["HITL Lawyer Engine\n(Ranked Dispatch & 3h Rotation)"]
    Gateway --> Ledger["Credit & Payment Ledger\n(SSLCommerz v4, FOR UPDATE Locks)"]

    QA --> Retriever["Dual-Path Hybrid Retriever\n(Dense E5-Small + BM25 + Act Alias)"]
    Audit --> Retriever

    Retriever --> Qdrant[("Qdrant Cloud\nbd_laws_e5_small\n(384d Cosine)")]
    Retriever --> Gazette[("Bangladesh Code Corpus\n(35,000+ Sections / 3,547 Acts)")]

    QA --> Guardrail{"Anti-Repeal\nGuardrail"}
    Audit --> Guardrail

    Guardrail -->|Verified Active Statute| Waterfall["LLM Waterfall Chain\n(Gemini 3.5 Flash Lite -> GLM-4.5 -> Groq Qwen)"]
    Guardrail -->|Repealed or Inactive| Reprompt["Deterministic Fallback\n& Warning Injection"]

    Waterfall --> Output["Grounded Citations\n(bdlaws.minlaw.gov.bd gazette URLs)"]

    HITL --> Supabase[("Supabase PostgreSQL\n(PgBouncer, Row-Level Security)")]
    Ledger --> Supabase
    Ledger --> SSLCommerz["SSLCommerz Payment Gateway\n(bKash / Nagad / Visa / Mastercard)"]
```

---

## ⚡ Core Engines & Capabilities

### 1. Dual-Path Statutory RAG Engine
- **Corpus Coverage**: 35,000+ legislative sections indexed across 3,547 Acts of the Bangladesh Code ([bdlaws.minlaw.gov.bd](http://bdlaws.minlaw.gov.bd)).
- **Dual-Path Fusion**:
  - **Path A (Deterministic Scroll)**: High-precision alias dictionary (`act_aliases.json`) mapping colloquial and Bengali names (e.g. *পেনাল কোড*, *শ্রম আইন*) to canonical gazette sections.
  - **Path B (Dense Semantic Search)**: Qdrant Cloud vector search using `intfloat/multilingual-e5-small` (384 dimensions) embeddings with Reciprocal Rank Fusion (RRF).
- **Anti-Repeal Interceptor**: Verifies that referenced provisions remain in force, preventing LLMs from citing repealed statutes (e.g. obsolete sections of the Penal Code 1860).

### 2. Autonomous ContractMind Risk Auditor
- **Sub-Minute Processing**: Average audit time of ~28 seconds for standard 15-page agreements.
- **AST Clause Segmentation**: Structural regex and grammar parser segmenting clauses by category (Governing Law, Termination, Indemnity, Non-Compete, IP Assignment).
- **Benchmarked Accuracy**: Achieves **91.7% clause recall** and **80.8% gold benchmark pass rate**.
- **Missing Mandatory Provision Detection**: Flags absent statutory necessities (e.g. mandatory severance protocols under § 26 of the Bangladesh Labour Act 2006).
- **Stamp Duty Advisory**: Automatically calculates stamp duty obligations according to the **Stamp Act 1899**.

### 3. Human-in-the-Loop (HITL) Advocate Marketplace
- **Bar Council Verification**: Advocate profiles require certified Bar Council ID and verification.
- **Dynamic Multi-Factor Ranking**: Matches contracts based on advocate practice areas, rating, current workload, and response SLA history.
- **Automated Lifecycle & Escalation**:
  - **3-Hour Offer Window**: If a matched advocate does not accept within 3 hours, APScheduler rotates the task to the next eligible advocate.
  - **3-Day SLA Breach Protection**: Automated refund triggers if review deadlines are breached.
- **Multi-Channel Payouts**: Verified withdrawals via bKash, Nagad, Rocket, or direct Bank Wire.

### 4. FinTech & Double-Entry Credit Ledger
- **SSLCommerz v4 Gateway**: Native checkout supporting bKash, Nagad, Upay, Visa, and Mastercard with IPN MD5 hash verification.
- **Atomic Concurrency Protection**: All credit consumption, deductions, and refunds utilize pessimistic database row locks (`SELECT ... FOR UPDATE`), mathematically preventing double-spending.
- **Opaque Credit Economy**: Platform credits conceal internal margin metrics while offering fractional micro-pricing (e.g. ৳28 micro-queries).

### 5. Standalone FastMCP 2.0 Server
- Provides live statutory and case law search directly to MCP clients:
  - `search_live_bd_judgements`: Live Supreme Court and High Court verdict search via Tavily & DDG over official domains (`supremecourt.gov.bd`, `bdlaws.minlaw.gov.bd`).
  - `get_statute_by_section`: Instant retrieval of exact Bangladesh Code section texts.
  - `verify_legal_citation`: Cross-checks citations against active statutes.

---

## 🛡️ Security & Production Hardening

The repository has undergone comprehensive security architecture review with **100% of critical, high, and medium findings resolved**:

| ID | Finding | Severity | Resolution Summary |
|---|---|:---:|---|
| **SEC-AUTH-01** | OTP Brute-Force | 🟠 High | Lockout after 5 failed attempts (15-min freeze) + sliding window rate limiter |
| **SEC-INJ-01** | MIME & Extension Spoofing | 🟠 High | Magic-byte signature validation + strict whitelist (`.pdf`, `.jpg`, `.png`) |
| **SEC-DOS-01** | Unbounded Memory Uploads | 🟠 High | 1MB chunked stream consumption with hard byte caps (20MB contract / 10MB proof) |
| **SEC-AUTH-02** | Insecure Fallback `SECRET_KEY` | 🟡 Medium | Fail-fast startup validator in `production` mode; strong random secret enforced |
| **SEC-AUTH-03** | Token Revocation | 🟡 Medium | `token_version` database column & `tv` claim in JWT; instant session revocation |
| **SEC-AUTH-04** | Hardcoded Email Backdoor | 🟡 Medium | Deleted backdoor; tier quotas strictly bound to verified database roles |
| **SEC-AI-01** | Prompt Injection Vulnerability | 🟡 Medium | Strict XML delimiter fencing, UNTRUSTED DATA directives, and output leak sanitizers |
| **SEC-DATA-01** | Plaintext OTP Logging | 🟢 Low | Context-aware code masking in logs (`123456` → `1***56`) |
| **SEC-INJ-02** | Path Traversal on Temp Files | 🟢 Low | Client filenames discarded; isolated UUID-based temporary file naming |
| **SEC-BOLA-01** | Shared Contract Download Auth | 🟢 Low | Unified enterprise ownership check across all contract download endpoints |
| **CORS-01** | Wildcard Subdomain CORS | 🟢 Low | Tightened origin regex strictly to `advotech-copilot` & `outliers` domains |
| **HDR-01** | Missing Security Headers | 🟢 Low | OWASP headers injected on all HTTP responses (`nosniff`, `DENY`, `mode=block`) |

---

## 📡 API Specification & Endpoints

| Domain | Router Prefix | Key Endpoints | Description |
|---|---|---|---|
| **Auth** | `/api/auth` | `POST /register`, `POST /login`, `POST /verify-otp`, `POST /logout` | JWT auth, OTP verification, session revocation |
| **Legal Chat** | `/api/chat` | `POST /query`, `GET /sessions`, `POST /sessions` | Statutory RAG copilot with gazette grounding |
| **ContractMind** | `/api/contract` | `POST /upload`, `GET /{id}`, `GET /{id}/redline` | AST clause extraction, risk audit, DOCX redline export |
| **HITL Lawyer** | `/api/lawyer` | `GET /offers`, `POST /accept`, `POST /submit-review` | Advocate task queue, Bar Council review portal |
| **Enterprise** | `/api/company` | `GET /members`, `POST /playbook/upload`, `GET /vault` | Team pooled credits, compliance playbooks, legal vault |
| **Payments** | `/api/payments` | `POST /initiate`, `POST /sslcommerz/ipn`, `GET /ledger` | SSLCommerz checkout, IPN callback, transaction ledger |
| **Admin** | `/api/admin` | `GET /users`, `POST /lawyers/{id}/verify`, `POST /payouts` | Super-admin KYC approvals, dispute settlements |
| **Platform** | `/api/platform` | `GET /metrics`, `GET /health` | Telemetry KPIs, SLA tracking, health check probes |
| **Live Docs** | `/api/docs` | `GET /`, `GET /content`, `GET /export-markdown` | Configurable live submission documentation engine |

---

## 💻 Local Setup & Installation

### Prerequisites
- Python 3.11+
- PostgreSQL (Supabase or local instance)
- Qdrant Cloud account (or local Docker container)

### 1. Clone the Repository
```bash
git clone https://github.com/mahmudulhasantasin/legal-copilot-backend.git
cd legal-copilot-backend
```

### 2. Create Virtual Environment
```bash
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Variables
```bash
cp .env.example .env
# Edit .env with your PostgreSQL, Qdrant, Gemini, and SSLCommerz credentials
```

### 5. Initialize Database & Run Migrations
```bash
python scripts/init_db.py
```

### 6. Start Development Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 7860 --reload
```
API documentation will be available at:
- **Swagger UI**: [http://localhost:7860/api/docs](http://localhost:7860/api/docs)
- **ReDoc**: [http://localhost:7860/api/redoc](http://localhost:7860/api/redoc)

---

## 🐳 Docker & Cloud Deployment

### Build and Run with Docker
```bash
# Build Docker image
docker build -t contractmind-api:latest .

# Run container
docker run -d -p 7860:7860 --env-file .env contractmind-api:latest
```

### Render Deployment
This repository includes native support for **Render Docker Web Services**:
- **Environment**: Docker
- **Port**: `7860`
- **Health Check Path**: `/api/platform/health`

---

## 🔌 FastMCP Integration (Cursor & Claude Desktop)

Developers and judges can connect directly to the Bangladesh Legal MCP server:

### Claude Desktop Configuration (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "bangladesh-legal": {
      "command": "npx",
      "args": ["-y", "mcp-remote-client", "https://bd-legal-mcp-server.onrender.com/mcp"]
    }
  }
}
```

### Cursor IDE Configuration (`.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "bangladesh-legal": {
      "url": "https://bd-legal-mcp-server.onrender.com/mcp"
    }
  }
}
```

---

## 🧪 Testing & Quality Assurance

The test suite covers security mitigations, OCR policies, LLM fallback chains, and financial transaction integrity:

```bash
# Run complete test suite (99 test cases)
pytest -v

# Run security regression tests specifically
pytest tests/test_security_fixes.py -v
```

**Results**: `99 passed, 0 failed (100% pass rate)`.

---

## 🌐 Live Deployments & Project Links

- **Live Production Application**: [https://advotech-copilot.vercel.app](https://advotech-copilot.vercel.app)
- **Interactive Technical Whitepaper & Docs**: [https://advotech-copilot.vercel.app/docs](https://advotech-copilot.vercel.app/docs)
- **Production Backend API**: [https://legal-copilot-backend-088i.onrender.com](https://legal-copilot-backend-088i.onrender.com)
- **FastMCP Server**: [https://bd-legal-mcp-server.onrender.com/mcp](https://bd-legal-mcp-server.onrender.com/mcp)
- **Frontend & Product Repository**: [https://github.com/mahmudulhasantasin/Outliers](https://github.com/mahmudulhasantasin/Outliers)

---

## ⚖️ License & Intellectual Property

Proprietary — Developed by **Team Outliers** for the **International AI Builders Congress 2026**.  
All rights reserved.
