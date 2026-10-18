# ContractMind — Project Architecture Map

> A simplified architecture reference for the Frontend team.
> This map shows how the React/Next.js frontend relates to the backend systems,
> and what each user role's dashboard should primarily focus on.

---

## System Architecture Diagram

```mermaid
flowchart TD
    subgraph CLIENT["🌐 Client Layer"]
        FE["React / Next.js Frontend\n(Browser)"]
    end

    subgraph API_GATEWAY["⚡ FastAPI Backend  (ContractMind API)"]
        direction TB
        AUTH["auth.py\n/api/auth/*"]
        CONTRACT["contract.py\n/api/contracts/*"]
        CHAT["chat.py\n/api/chat/*"]
        PAYMENTS["payments.py\n/api/payments/*"]
        LAWYER["lawyer.py\n/api/lawyer/*"]
        COMPANY["company.py\n/api/company/*"]
        SETTINGS["settings.py\n/api/settings/*"]
        ADMIN["admin.py\n/api/admin/*"]
        CRON["services/cron.py\nBackground Scheduler"]
    end

    subgraph AI_LAYER["🤖 AI Layer  (LangGraph + LangChain)"]
        CG["Contract Risk Agent\nagents/contract_graph.py"]
        QA["Legal QA Agent\nagents/qa_graph.py"]
        LLM["LLM Provider\n(OpenAI / Gemini)"]
    end

    subgraph STORAGE["🗄️ Storage Layer"]
        DB[("PostgreSQL\n(SQLAlchemy ORM)")]
        VS[("Qdrant\nVector Store\n(Law + Precedents)")]
        FS["Supabase Storage\n(PDF / DOC Files)"]
    end

    subgraph EMAIL["📧 Communication"]
        ES["Email Service\n(OTP, Notifications)"]
    end

    %% Frontend → Backend
    FE -->|"HTTPS REST (JSON)"| AUTH
    FE -->|"multipart/form-data"| CONTRACT
    FE -->|"JSON"| CHAT
    FE -->|"JSON"| PAYMENTS
    FE -->|"JSON"| LAWYER
    FE -->|"JSON"| COMPANY
    FE -->|"JSON"| SETTINGS
    FE -->|"JSON (Admin only)"| ADMIN

    %% Backend → AI
    CONTRACT -->|"contract text + playbook rules"| CG
    CHAT -->|"query + history + context"| QA
    CG --> LLM
    QA --> LLM

    %% AI → Vector Store
    QA -->|"semantic search"| VS
    VS -->|"relevant statutes"| QA

    %% Backend → Storage
    CONTRACT -->|"upload PDF/DOC"| FS
    AUTH --> DB
    CONTRACT --> DB
    CHAT --> DB
    PAYMENTS --> DB
    LAWYER --> DB
    COMPANY --> DB
    ADMIN --> DB
    CRON -->|"HITL SLA checks\nPayment expiry"| DB

    %% Backend → Email
    AUTH -->|"OTP emails"| ES
    LAWYER -->|"assignment & completion emails"| ES

    style CLIENT fill:#dbeafe,stroke:#3b82f6
    style API_GATEWAY fill:#f0fdf4,stroke:#22c55e
    style AI_LAYER fill:#fef3c7,stroke:#f59e0b
    style STORAGE fill:#f5f3ff,stroke:#8b5cf6
    style EMAIL fill:#fce7f3,stroke:#ec4899
```

---

## Request Lifecycle: Contract Audit

```mermaid
sequenceDiagram
    participant FE as React Frontend
    participant API as FastAPI Backend
    participant DB as PostgreSQL
    participant AI as LangGraph Agent
    participant FS as Supabase Storage
    participant LLM as LLM (OpenAI)

    FE->>API: POST /api/contracts/upload (multipart/form-data)
    Note over API: 1. Validate file type & size
    API->>DB: Deduct 25 credits (row-locked)
    API->>FS: Upload file bytes → get storage_path
    Note over API: 2. Extract text from PDF/DOCX
    API->>AI: analyze_contract_risk_async(text, playbook_rules)
    AI->>LLM: Prompt with contract text
    LLM-->>AI: Clause risk JSON
    AI-->>API: detected_risks[] + memo_summary
    Note over API: 3. Format clauses, calculate score
    API->>DB: INSERT Contract + ContractMemo
    API-->>FE: ContractMemoResponse (memo_id, clauses, risk_score...)
    Note over FE: Render audit results (10–30s wait)
```

---

## Request Lifecycle: Legal Chat Query

```mermaid
sequenceDiagram
    participant FE as React Frontend
    participant API as FastAPI Backend
    participant DB as PostgreSQL
    participant VS as Qdrant Vector Store
    participant LLM as LLM (OpenAI)

    Note over FE: Step 1: Create session (once)
    FE->>API: POST /api/chat/sessions { mode, title }
    API->>DB: INSERT ChatSession
    API-->>FE: { session_id, message_limit }

    Note over FE: Step 2: Send query
    FE->>API: POST /api/chat/query { session_id, query }
    API->>DB: Deduct 1 credit (or use monthly_pool)
    API->>DB: Load last 6 messages for context
    API->>VS: Semantic search for relevant statutes
    VS-->>API: Retrieved law documents
    API->>LLM: Prompt with query + history + retrieved docs
    LLM-->>API: Structured JSON (detailed_answer + cited_sections)
    Note over API: Validate citations against retrieved docs
    API->>DB: Save user & assistant messages
    API-->>FE: ChatResponse (response, credits_remaining, language_detected)

    Note over FE: Render: answer + statute cards + next steps
```

---

## HITL Lawyer Verification Lifecycle

```mermaid
flowchart LR
    A["NOT_REQUESTED\nUser sees 'Request Verification' button"] -->|"User clicks (1500 credits deducted)"| B
    B["QUEUED_FOR_LAWYER\nSystem running ranking algorithm"] -->|"Cron sends offer to top-ranked lawyer"| C
    C["OFFERED\nLawyer sees offer in queue (3h window)"] -->|"Lawyer accepts"| D
    C -->|"Lawyer declines or 3h passes"| B
    D["ASSIGNED\nLawyer has 3h submit deadline"] -->|"Lawyer submits review"| F
    D -->|"Deadline passed → re-queue"| B
    F["COMPLETED\nClient gets email + download link"] --> END1["🔚 Terminal"]
    B -->|"72h global SLA breached → auto-refund"| G
    G["REFUNDED\nCredits returned to user"] --> END2["🔚 Terminal"]
    B -->|"No lawyers available"| G
    style A fill:#e5e7eb
    style B fill:#dbeafe
    style C fill:#e0e7ff
    style D fill:#fed7aa
    style F fill:#bbf7d0
    style G fill:#99f6e4
    style END1 fill:#bbf7d0
    style END2 fill:#99f6e4
```

---

## Data Flow: Payment (bKash Manual)

```mermaid
sequenceDiagram
    participant User as User (Frontend)
    participant API as FastAPI Backend
    participant DB as PostgreSQL
    participant Admin as System Admin

    User->>API: GET /api/payments/packages
    API-->>User: Available credit packages

    User->>API: POST /api/payments/initiate { package_id }
    API->>DB: INSERT Payment (status=PENDING)
    API-->>User: { payment_id, reference_code, bkash_number, instructions }

    Note over User: User sends bKash manually to merchant number
    Note over User: User gets bKash TrxID from SMS

    User->>API: POST /api/payments/{id}/submit-trx { trx_id, sender_number }
    API->>DB: UPDATE Payment (status=PENDING_VERIFICATION)
    API-->>User: "TrxID submitted. Admin will verify within 24 hours."

    Note over Admin: Admin logs into admin panel
    Admin->>API: POST /api/admin/payments/{id}/approve
    API->>DB: UPDATE User.credit_balance += credits
    API->>DB: UPDATE Payment (status=APPROVED)
    API-->>Admin: { credits_granted: 950 }
    Note over User: User's credit balance updates on next page load
```

---

## 4 User Roles — Dashboard Focus

### Role 1: GENERAL User

**Who they are:** Individual users (freelancers, business owners, individuals) seeking legal help.

**JWT `role` value:** `"GENERAL"`

**Primary Dashboard:** `/dashboard`

| Section | Purpose |
|---------|---------|
| **Contract Audit** | Upload PDF/DOCX → view clause-by-clause risk analysis |
| **Legal Chat** | Ask Bangladesh law questions (RAG-powered) |
| **Request Verification** | Upgrade any audit to lawyer-verified status |
| **Credit Balance** | Current balance, top-up via bKash |
| **Contract History** | Past audits with HITL status badges |

**Key UX Concerns:**
- Always show credit balance prominently in header.
- Show "Request Lawyer Verification" CTA prominently after every AI audit.
- Warn when approaching session message limit.
- After `POST /api/auth/register/email`, force through OTP screen before showing dashboard.

---

### Role 2: LAWYER

**Who they are:** Licensed Bangladesh Bar Council attorneys who review contracts.

**JWT `role` value:** `"LAWYER"`

**Primary Dashboard:** `/lawyer/dashboard`

**Prerequisites:** Must have an approved `LawyerProfile` (admin-verified).

| Section | Purpose |
|---------|---------|
| **Offer Queue** | Live offers with countdown timers (Accept / Decline) |
| **Active Tasks** | Contracts accepted — with 3-hour submit countdown |
| **Contract Review** | View AI analysis → modify clauses → submit with notes & signature |
| **AI Research Chat** | Use `LAWYER_RESEARCH` mode to research statutes for the contract |
| **Earnings Dashboard** | Wallet balance (BDT), completed reviews, rating |
| **Withdrawal** | Request bKash payout (minimum ৳500) |

**Key UX Concerns:**
- The offer queue must feel **urgent** — show countdown timers for each offer.
- After accepting, show a clear "Submit by [deadline]" header on the review screen.
- Lawyers get **100 free chat credits/month** — show `monthly_free_chat_remaining` prominently.
- Never show BDT ↔ credit conversion rates.

---

### Role 3: ENTERPRISE_ADMIN

**Who they are:** Company administrators managing their organization's legal platform usage.

**JWT `role` value:** `"ENTERPRISE_ADMIN"`

**Primary Dashboard:** `/enterprise/dashboard`

| Section | Purpose |
|---------|---------|
| **Company Dashboard** | Member count, credit pool balance, pending approvals |
| **Member Management** | Approve/reject/suspend members, set departments |
| **Credit Quota** | Allocate per-member credit quotas from the pool |
| **Playbook** | Upload company legal policy documents (auto-parsed to rules) |
| **Analytics** | Daily credit spend chart across the organization |
| **Billing** | Top up company credit pool via bKash |
| **Referral Code** | Display the code employees use to join |

**Key UX Concerns:**
- Show `members.pending_approval` count as a notification badge.
- The referral code should be easily copyable from the dashboard.
- Quotas are **soft caps** — they don't move credits, just limit per-member spend.
- Enterprise members use the **enterprise credit pool**, not personal wallets.

---

### Role 4: ENTERPRISE_MEMBER

**Who they are:** Employees of an approved enterprise, using the platform on behalf of their company.

**JWT `role` value:** `"ENTERPRISE_MEMBER"`

**Primary Dashboard:** `/dashboard` (same route as `GENERAL`, but context-aware)

**Post-Login Gate:** If `member_approval_status !== "APPROVED"`, show an approval-pending screen — do **not** allow access.

| Section | Purpose |
|---------|---------|
| **Contract Audit** | Upload and audit contracts (cost: 50 credits from enterprise pool) |
| **Legal Chat** | Ask questions (cost: 1 credit from enterprise pool) |
| **My Quota** | Show `quota_used / quota_total` usage bar |
| **Contract History** | Their own contracts + enterprise-wide contracts |

**Key UX Concerns:**
- Replace personal credit balance with "Company Pool Balance" and personal quota bar.
- If `quota_remaining === 0`, show a message: "Your credit quota is exhausted. Contact your company admin."
- Show which enterprise they belong to (from `enterprise.name` in `/api/settings/me`).

---

## Backend Module Map

```
app/
├── main.py                  ← FastAPI app init, CORS config, router registration
├── config.py                ← Environment variables (SECRET_KEY, DB_URL, etc.)
│
├── api/                     ← HTTP route handlers (what the frontend calls)
│   ├── auth.py              ← Registration, login, OTP, onboarding
│   ├── contract.py          ← Upload, audit, HITL request, download
│   ├── chat.py              ← Sessions, queries (RAG)
│   ├── payments.py          ← bKash payment flow
│   ├── lawyer.py            ← Offer queue, task submission, earnings
│   ├── company.py           ← Enterprise member & playbook management
│   ├── settings.py          ← Profile, password, lawyer preferences
│   └── admin.py             ← System admin operations
│
├── agents/                  ← AI logic (do not call directly from frontend)
│   ├── contract_graph.py    ← LangGraph for contract risk analysis
│   ├── qa_graph.py          ← LangGraph for legal Q&A (RAG)
│   ├── nodes.py             ← Individual graph node functions
│   ├── prompts.py           ← LLM prompt templates
│   └── state.py             ← LangGraph state definitions
│
├── core/                    ← Cross-cutting concerns
│   ├── security.py          ← JWT, RBAC, credit deduction logic
│   ├── pricing.py           ← Credit costs per service (single source of truth)
│   ├── tier_limits.py       ← Chat session limits per role/tier
│   └── database.py          ← SQLAlchemy async session setup
│
├── models/                  ← SQLAlchemy ORM models
│   ├── user.py              ← User, CreditTransaction, WithdrawalRequest
│   ├── memo.py              ← Contract, ContractMemo, HITLStatus, RiskLevel
│   ├── chat.py              ← ChatSession, ChatMessage, ChatMode
│   ├── lawyer.py            ← LawyerProfile, LawyerStatus, LawyerAvailability
│   ├── playbook.py          ← Enterprise, CompanyPlaybook, CreditPackage
│   └── audit.py             ← Payment, AuditLog, PaymentStatus
│
└── services/                ← Business logic & integrations
    ├── ingestion.py         ← Extract text from PDF/DOCX/images
    ├── retriever.py         ← Qdrant vector search for statutes
    ├── llm_provider.py      ← LLM client factory
    ├── lawyer_assignment.py ← HITL ranking algorithm
    ├── email_service.py     ← OTP & notification emails
    ├── legal_normalizer.py  ← Normalize statute act/section names
    ├── playbook_parser.py   ← Parse policy docs into rules via LLM
    └── cron.py              ← Background jobs (HITL SLA, payment expiry)
```

---

## Environment Variables Frontend Needs

The frontend only needs the backend's base URL. All sensitive keys live on the server.

```env
# .env.local (Next.js)
NEXT_PUBLIC_API_URL=http://localhost:8000

# For Google OAuth on the client:
NEXT_PUBLIC_GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
```

> **Never** put `SECRET_KEY`, `DATABASE_URL`, `SUPABASE_SERVICE_KEY`, or any AI API keys in the frontend.

---

## Key Design Decisions for Frontend

| Decision | Rationale |
|----------|-----------|
| BDT values are never shown alongside credits | Intentional — prevents users from calculating conversion rates |
| OTP expires in 5 minutes | Frontend must show a countdown timer and a Resend button |
| JWT contains `role`, `tier`, `ent_id` | Frontend can decode it for route guards without an extra API call |
| HITL uses polling, not WebSockets | Backend has cron-based advancement — poll every 30s for active HITL |
| Enterprise contracts show all members' work | Enterprise members see team-wide contract history |
| Lawyer's `CONTRACT_REVIEW` chat is free | Deduction returns `{ source: "free" }` — do not show a credit deduction alert |
| `proposed_safe_redline` can be `null` | Only show the "Suggested Fix" section when this field is non-null |
| Download URLs expire in 1 hour | Always fetch fresh on the download click — do not cache the URL |
