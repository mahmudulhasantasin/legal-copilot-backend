# ContractMind — Frontend API Reference

> **Base URL:** `http://localhost:8000` (dev) · Swagger UI: `/api/docs` · ReDoc: `/api/redoc`
>
> All protected endpoints require: `Authorization: Bearer <access_token>`
>
> All error responses follow the FastAPI standard `{ "detail": "Error message here" }` shape.

---

## Table of Contents

1. [Authentication & Onboarding](#1-authentication--onboarding)
2. [Contract Audit & HITL](#2-contract-audit--hitl)
3. [Legal Chat (RAG)](#3-legal-chat-rag)
4. [Payments (bKash Manual Flow)](#4-payments-bkash-manual-flow)
5. [Lawyer HITL Panel](#5-lawyer-hitl-panel)
6. [Enterprise Admin Panel](#6-enterprise-admin-panel)
7. [Settings & Profile](#7-settings--profile)
8. [System Admin Panel](#8-system-admin-panel)
9. [Credit & Pricing Reference](#9-credit--pricing-reference)

---

## 1. Authentication & Onboarding

### 1.1 Register with Email

| Field         | Value                                |
|---------------|--------------------------------------|
| **Method**    | `POST`                               |
| **Path**      | `/api/auth/register/email`           |
| **Auth**      | None                                 |

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "secret123",
  "confirm_password": "secret123",
  "full_name": "Rahim Uddin",
  "phone": "01711000000",
  "date_of_birth": "1990-06-15T00:00:00Z",
  "gender": "Male"
}
```

**Response 201:**
```json
{
  "message": "OTP sent to your email.",
  "email": "user@example.com"
}
```

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `400` | `"Email already registered. Please log in."` | Show toast, redirect to login |
| `400` | `"Email or Phone number already registered."` | Show toast on field |
| `400` | `"Passwords do not match."` | Inline validation error |
| `500` | `"Internal server error during registration."` | Generic error toast |

---

### 1.2 Verify OTP

| Field         | Value                                |
|---------------|--------------------------------------|
| **Method**    | `POST`                               |
| **Path**      | `/api/auth/verify-otp`               |
| **Auth**      | None                                 |

**Request Body:**
```json
{
  "email": "user@example.com",
  "otp_code": "482951"
}
```

**Response 200 — Returns the JWT token immediately:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "role": "GENERAL",
  "credit_balance": 50,
  "is_verified": true
}
```

> **Note:** On first-time OTP verification, the backend automatically credits **50 signup bonus credits** to the user's wallet.

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `400` | `"Invalid OTP code."` | Show error under OTP input |
| `400` | `"OTP expired. Request a new one."` | Show "Resend OTP" button |
| `404` | `"No account found for this email."` | Navigate to register |

---

### 1.3 Resend OTP

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/auth/resend-otp` |
| **Auth** | None |

**Request Body:** `{ "email": "user@example.com" }`

**Response 200:** `{ "message": "New OTP sent." }`

---

### 1.4 Login with Email

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/auth/login` |
| **Auth** | None |

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "secret123"
}
```

**Response 200:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "role": "GENERAL",
  "credit_balance": 215,
  "is_verified": true
}
```

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `401` | `"Incorrect email or password."` | Show form-level error |
| `403` | `"Account disabled."` | Show "Contact support" message |

---

### 1.5 Register with Google

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/auth/register/google` |
| **Auth** | None |

**Request Body:**
```json
{
  "token": "<Google ID Token from Google Sign-In>",
  "password": "secret123",
  "confirm_password": "secret123"
}
```

**Response 201:** Same as Login response (JWT token). No OTP step needed — Google accounts are auto-verified and receive **50 bonus credits immediately**.

---

### 1.6 Login with Google

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/auth/login/google` |
| **Auth** | None |

**Request Body:** `{ "token": "<Google ID Token>" }`

**Response 200:** Same JWT token format.

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `401` | `"Invalid or expired Google credential."` | Force re-authenticate with Google |
| `404` | `"No account. Please register first."` | Redirect to `/api/auth/register/google` |

---

### 1.7 Register as Enterprise Member

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/auth/register/enterprise-member` |
| **Auth** | None |

**Request Body:**
```json
{
  "email": "member@company.com",
  "password": "secret123",
  "confirm_password": "secret123",
  "full_name": "Karim Hossain",
  "phone": "01800000000",
  "referral_code": "LEGALCO-AB12"
}
```

**Response 201:**
```json
{
  "message": "OTP sent. After verifying, your company admin must approve your account.",
  "email": "member@company.com",
  "company": "LegalCo Bangladesh"
}
```

> **Important for UI:** After OTP verification, the member has `member_approval_status: "PENDING"`. They cannot use the platform until the Enterprise Admin approves them. Show a "Waiting for admin approval" holding screen.

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `400` | `"Invalid or inactive company referral code."` | Error on referral code field |
| `400` | `"Company member limit reached (N). Contact your admin."` | Show limit-reached banner |

---

### 1.8 Onboard as Lawyer

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/auth/onboard/lawyer` |
| **Auth** | Bearer Token (Role: `GENERAL`) |

**Request Body:**
```json
{
  "bar_council_id": "BC/DHK/2019/001234",
  "experience_years": 7,
  "specializations": ["Contract Law", "Employment Law", "Corporate Law"]
}
```

**Response 200:**
```json
{
  "status": "success",
  "message": "Application submitted. Awaiting admin verification."
}
```

---

### 1.9 Onboard as Enterprise Admin

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/auth/onboard/enterprise` |
| **Auth** | Bearer Token (Role: `GENERAL`) |

**Request Body:**
```json
{
  "company_name": "LegalCo Bangladesh Ltd.",
  "company_type": "Private Limited",
  "registration_no": "C-12345/2020",
  "tin_no": "123456789",
  "member_limit": 20,
  "billing_email": "billing@legalco.com",
  "billing_phone": "01900000000"
}
```

**Response 200:**
```json
{
  "status": "success",
  "message": "Enterprise registered. Awaiting system admin approval.",
  "referral_code": "LEGALCO-AB12",
  "enterprise_id": 3
}
```

> **Show the `referral_code` prominently** — employees need it to join. Suggest copy-to-clipboard.

---

### 1.10 Get Current User (Me)

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/auth/me` |
| **Auth** | Bearer Token (Any role) |

**Response 200:**
```json
{
  "id": 42,
  "email": "user@example.com",
  "full_name": "Rahim Uddin",
  "phone": "01711000000",
  "role": "GENERAL",
  "subscription_tier": "FREE",
  "credit_balance": 215,
  "is_active": true,
  "is_verified": true,
  "enterprise_id": null
}
```

---

## 2. Contract Audit & HITL

### 2.1 Upload & Audit Contract

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/contracts/upload` |
| **Auth** | Bearer Token (Any role) |
| **Content-Type** | `multipart/form-data` |
| **Credit Cost** | **25 credits** (General) / **50 credits** (Enterprise) |

**Multipart Form Fields:**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file` | `File` | ✅ Yes | `.pdf`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg` |
| `title` | `string` | ❌ No | Defaults to filename if omitted |

**Response 200:**
```json
{
  "memo_id": 17,
  "contract_id": 9,
  "title": "Employment Agreement - Rahim.pdf",
  "overall_risk": "HIGH",
  "risk_score": 72,
  "executive_summary": "This employment contract contains several high-risk clauses related to non-compete provisions and unilateral termination rights that may conflict with Bangladesh Labour Act 2006...",
  "counts": {
    "high_risk": 3,
    "review_needed": 4,
    "compliant": 5
  },
  "clauses": [
    {
      "clause_number": "§ 1",
      "clause_title": "Non-Compete Clause",
      "risk_level": "High Risk",
      "original_text": "Employee agrees not to work for any competitor for 5 years after termination...",
      "statute_reference": "Bangladesh Labour Act 2006, Section 27",
      "legal_explanation": "A 5-year non-compete restriction is likely unenforceable under Bangladesh law. Courts generally uphold restrictions up to 1 year with geographic limitations.",
      "proposed_safe_redline": "Employee agrees not to work for a direct competitor within Dhaka Division for a period of 12 months following termination.",
      "is_compliant": false
    },
    {
      "clause_number": "§ 2",
      "clause_title": "Governing Law",
      "risk_level": "Compliant",
      "original_text": "This agreement is governed by the laws of Bangladesh.",
      "statute_reference": "N/A",
      "legal_explanation": "Standard governing law clause. Compliant.",
      "proposed_safe_redline": null,
      "is_compliant": true
    }
  ],
  "hitl_status": "NOT_REQUESTED",
  "lawyer_name": null,
  "lawyer_notes": null,
  "lawyer_signature": null,
  "completed_at": null
}
```

**Key fields for UI rendering:**

| Field | UI Purpose |
|-------|-----------|
| `overall_risk` | Large risk badge: `LOW` (green) · `MODERATE` (yellow) · `HIGH` (red) · `CRITICAL` (dark red) |
| `risk_score` | Circular progress bar (0–100) |
| `counts` | Summary cards: "3 High Risk", "4 Review Needed", "5 Compliant" |
| `clauses[].risk_level` | Row color: `"High Risk"` → red · `"Review Needed"` → yellow · `"Compliant"` → green |
| `clauses[].proposed_safe_redline` | "Suggested Fix" expandable block (hide if `null`) |
| `hitl_status` | Status badge for lawyer verification panel |

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `400` | `"Unsupported file format. Allowed: .pdf, .docx..."` | File type validation error |
| `400` | `"The uploaded document contains no readable text."` | Warn user (scanned image PDF, needs OCR) |
| `402` | `"Insufficient credits. Need 25, have 10."` | Show "Top Up Credits" CTA button |
| `500` | `"Storage service is not configured."` | Generic server error toast |

---

### 2.2 Request Lawyer Verification (HITL)

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/contracts/{contract_id}/request-verification` |
| **Auth** | Bearer Token (Any role) |
| **Credit Cost** | **1500 credits** |

No request body required.

**Response 200:**
```json
{
  "status": "success",
  "memo_id": 17,
  "message": "Verification requested. A lawyer has been notified.",
  "credits_deducted": 1500
}
```

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `400` | `"Verification already requested. Status: QUEUED_FOR_LAWYER"` | Show current HITL status badge |
| `402` | `"Insufficient credits. Need 1500, have 200."` | Show top-up modal |
| `503` | `"No lawyers are currently available. Your credits have been refunded."` | Show refund notification toast |

---

### 2.3 Get Contract History

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/contracts/history` |
| **Auth** | Bearer Token (Any role) |
| **Query Params** | `?limit=50&offset=0` |

**Response 200:**
```json
[
  {
    "contract_id": 9,
    "memo_id": 17,
    "title": "Employment Agreement - Rahim.pdf",
    "overall_risk": "HIGH",
    "risk_score": 72,
    "hitl_status": "COMPLETED",
    "created_at": "2026-10-15T14:30:00Z"
  },
  {
    "contract_id": 8,
    "memo_id": 16,
    "title": "Rental Agreement.docx",
    "overall_risk": "MODERATE",
    "risk_score": 41,
    "hitl_status": "NOT_REQUESTED",
    "created_at": "2026-10-10T09:15:00Z"
  }
]
```

> **Enterprise users** see contracts from all members of their company (in addition to their own).

---

### 2.4 Get Contract Download URL

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/contracts/{contract_id}/download` |
| **Auth** | Bearer Token |

**Response 200:**
```json
{
  "download_url": "https://your-supabase.co/storage/v1/object/sign/contracts/u_42_abc123.pdf?token=...",
  "is_lawyer_verified": true
}
```

> If `is_lawyer_verified` is `true`, show a **"Verified by Lawyer ✓"** badge next to the download button. The URL expires in 1 hour — do not cache it.

---

### 2.5 HITLStatus State Machine Reference

Use this table to render status badges correctly across all contract views:

| `hitl_status` | Badge Label | Color | User-Facing Meaning |
|---------------|-------------|-------|---------------------|
| `NOT_REQUESTED` | Not Verified | Gray | AI-only analysis |
| `QUEUED_FOR_LAWYER` | Finding Lawyer… | Blue | Searching for available lawyer |
| `OFFERED` | Offer Sent | Indigo | Waiting for lawyer to accept (3h window) |
| `ASSIGNED` | In Progress | Orange | Lawyer accepted, working on it |
| `IN_REVIEW` | In Review | Orange | Active review underway |
| `COMPLETED` | Verified ✓ | Green | Lawyer signed off |
| `REJECTED_BY_LAWYER` | Re-queuing… | Yellow | Previous lawyer declined, reassigning |
| `EXPIRED` | Expired | Red | 72-hour SLA breached |
| `REFUNDED` | Refunded | Teal | Credits returned automatically |
| `DISPUTED` | Disputed | Red | Under dispute resolution |

---

## 3. Legal Chat (RAG)

### 3.1 Create Chat Session

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/chat/sessions` |
| **Auth** | Bearer Token (Any role) |

**Request Body:**
```json
{
  "mode": "GENERAL",
  "title": "Labour Law Question",
  "contract_id": null
}
```

**Chat Mode Options:**

| Mode | Allowed Roles | Purpose |
|------|--------------|---------|
| `GENERAL` | All roles | General Bangladesh legal questions |
| `LAWYER_RESEARCH` | `LAWYER` only | Deep legal research mode |
| `USER_CONTRACT` | All roles | Chat scoped to a specific uploaded contract |

> For `USER_CONTRACT` mode, `contract_id` (from a previous upload) is **required**.

**Response 200:**
```json
{
  "session_id": 55,
  "mode": "GENERAL",
  "message_limit": 5,
  "title": "Labour Law Question"
}
```

> Store `session_id` in local state — it's needed for every subsequent query.

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `400` | `"contract_id is required for USER_CONTRACT mode."` | Validate before calling |
| `403` | `"Only lawyers can use LAWYER_RESEARCH mode."` | Hide option for non-lawyers |
| `403` | `"You have reached the maximum of 3 active sessions."` | Show "Close a session first" prompt |

---

### 3.2 Send Chat Query

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/chat/query` |
| **Auth** | Bearer Token |
| **Credit Cost** | **1 credit** per query |

**Request Body:**
```json
{
  "session_id": 55,
  "query": "What are the legal notice requirements before terminating an employee under Bangladesh Labour Act 2006?"
}
```

**Response 200:**
```json
{
  "session_id": 55,
  "user_query": "What are the legal notice requirements before terminating an employee under Bangladesh Labour Act 2006?",
  "response": {
    "detailed_answer": "Under the Bangladesh Labour Act 2006, the notice period for terminating a permanent worker depends on their payment cycle. For a monthly-paid worker, the employer must provide 120 days written notice...",
    "cited_sections": [
      {
        "act": "Bangladesh Labour Act, 2006",
        "section_number": "Section 26",
        "title": "Termination of Permanent Worker",
        "provision_summary": "A permanent worker may be dismissed by giving 120 days notice in writing if employed on a monthly basis...",
        "relevance": "Primary statutory basis for employment termination notice periods."
      },
      {
        "act": "Bangladesh Labour Act, 2006",
        "section_number": "Section 27",
        "title": "Payment in Lieu of Notice",
        "provision_summary": "In lieu of notice period, equivalent wages may be paid...",
        "relevance": "Employer's option to pay instead of serving notice."
      }
    ],
    "suggested_next_steps": [
      "Review the referenced statutory provisions with an advocate.",
      "Gather any formal contracts, notices, or evidence related to this issue."
    ]
  },
  "credits_remaining": 214,
  "credits_source": "wallet",
  "language_detected": "en"
}
```

**Key fields for UI rendering:**

| Field | UI Purpose |
|-------|-----------|
| `response.detailed_answer` | Main answer text (render as Markdown) |
| `response.cited_sections` | Collapsible "Legal Sources" panel — render each as a statute card |
| `response.suggested_next_steps` | Checklist/bullet list at the bottom of the AI response |
| `credits_remaining` | Update the global credit counter in the header/navbar |
| `credits_source` | Show pill: `"wallet"` · `"monthly_pool"` · `"enterprise_pool"` |
| `language_detected` | `"en"`, `"bn"`, or `"banglish"` — for analytics or UI indicator |

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `402` | `"Insufficient credits. Need 1, have 0."` | Disable send, show top-up CTA |
| `403` | `"Session limit reached (5 messages). Please start a new session."` | Disable input, show "New Session" button |
| `404` | `"Session not found or closed."` | Redirect to session list |
| `500` | `"An error occurred while generating the response."` | Show retry button |

---

### 3.3 List Chat Sessions

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/chat/sessions` |
| **Auth** | Bearer Token |

**Response 200:**
```json
[
  {
    "session_id": 55,
    "title": "Labour Law Question",
    "mode": "GENERAL",
    "message_count": 4,
    "message_limit": 5,
    "updated_at": "2026-10-18T13:00:00Z"
  }
]
```

> Use `message_count / message_limit` to render a progress bar showing session usage.

---

### 3.4 Get Session History (for Re-hydrating Chat UI)

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/chat/sessions/{session_id}` |
| **Auth** | Bearer Token |

**Response 200:**
```json
{
  "session_id": 55,
  "mode": "GENERAL",
  "message_count": 4,
  "message_limit": 5,
  "messages": [
    {
      "role": "user",
      "content": "What are the notice requirements...",
      "citations": null,
      "created_at": "2026-10-18T13:00:00Z"
    },
    {
      "role": "assistant",
      "content": "Under the Bangladesh Labour Act 2006...",
      "citations": [
        {
          "act": "Bangladesh Labour Act, 2006",
          "section_number": "Section 26",
          "title": "Termination of Permanent Worker",
          "provision_summary": "...",
          "relevance": "Primary statutory basis..."
        }
      ],
      "created_at": "2026-10-18T13:00:05Z"
    }
  ]
}
```

> `citations` is only present on `"role": "assistant"` messages.

---

### 3.5 Close Chat Session

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/chat/sessions/{session_id}/close` |
| **Auth** | Bearer Token |

No body. **Response 200:** `{ "status": "success" }`

---

### 3.6 Chat Tier Limits Reference

| Tier | Max Active Sessions | Messages Per Session |
|------|--------------------|--------------------|
| `FREE` (General) | 3 | 5 |
| `PRO` (General) | 20 | 20 |
| `ENTERPRISE_MEMBER` / `ENTERPRISE_ADMIN` | 40 | 40 |
| `LAWYER` | 20 | 20 |

> **Lawyer Contract Review sessions** are separate: up to 3 sessions per contract, 10 messages each.

---

## 4. Payments (bKash Manual Flow)

### 4.1 List Available Credit Packages

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/payments/packages` |
| **Auth** | Bearer Token |

**Response 200:**
```json
[
  {
    "id": 1,
    "name": "Starter Pack",
    "description": "Perfect for trying the platform.",
    "price_bdt": 500.0,
    "total_credits": 440,
    "badge_text": null,
    "display_order": 1
  },
  {
    "id": 2,
    "name": "Standard Pack",
    "description": "Best for regular legal questions.",
    "price_bdt": 1000.0,
    "total_credits": 950,
    "badge_text": "Most Popular",
    "display_order": 2
  },
  {
    "id": 3,
    "name": "Pro Pack",
    "description": "For power users and small teams.",
    "price_bdt": 2500.0,
    "total_credits": 2600,
    "badge_text": "Best Value",
    "display_order": 3
  }
]
```

> Use `badge_text` to render a ribbon/chip overlay on the package card. Render in `display_order` order.

---

### 4.2 Initiate Payment

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/payments/initiate` |
| **Auth** | Bearer Token |

**Request Body:**
```json
{
  "package_id": 2
}
```

**Response 201:**
```json
{
  "payment_id": 88,
  "reference_code": "PAY-X7K2QA",
  "amount_bdt": 1000.0,
  "total_credits": 950,
  "expires_at": "2026-10-19T14:30:00Z",
  "bkash": {
    "bkash_number": "01XXXXXXXXX",
    "bkash_type": "Personal",
    "instructions_en": "1. Open bKash app.\n2. Choose 'Send Money'.\n3. Send to 01XXXXXXXXX.\n4. Enter the exact amount shown.\n5. Add the reference code in the 'Reference' field.\n6. Copy the TrxID from the bKash confirmation SMS.",
    "instructions_bn": "১. বিকাশ অ্যাপ খুলুন।..."
  },
  "next_step": "Send bKash, then POST /api/payments/{payment_id}/submit-trx with TrxID."
}
```

> Show bKash instructions in a modal with a **countdown timer** to `expires_at` (24 hours). Highlight `reference_code` for copy-paste.

---

### 4.3 Submit bKash Transaction ID

| Field | Value |
|-------|-------|
| **Method** | `POST` |
| **Path** | `/api/payments/{payment_id}/submit-trx` |
| **Auth** | Bearer Token |

**Request Body:**
```json
{
  "trx_id": "AB1234XYZ789",
  "sender_number": "01711000000"
}
```

**Response 200:**
```json
{
  "status": "success",
  "payment_id": 88,
  "message": "TrxID submitted. Admin will verify within 24 hours.",
  "current_status": "PENDING_VERIFICATION"
}
```

---

### 4.4 Cancel Payment

| Method | Path |
|--------|------|
| `POST` | `/api/payments/{payment_id}/cancel` |

No body. **Response 200:** `{ "status": "success" }`

---

### 4.5 List My Payments

| Field | Value |
|-------|-------|
| **Method** | `GET` |
| **Path** | `/api/payments/my` |
| **Auth** | Bearer Token |
| **Query Params** | `?status=PENDING_VERIFICATION&limit=50&offset=0` |

**Status filter values:** `PENDING`, `PENDING_VERIFICATION`, `APPROVED`, `REJECTED`, `EXPIRED`

**Response 200 (array):**
```json
[
  {
    "id": 88,
    "reference_code": "PAY-X7K2QA",
    "amount_bdt": 1000.0,
    "credits_to_grant": 800,
    "bonus_credits": 150,
    "total_credits": 950,
    "purpose": "CREDIT_TOPUP",
    "status": "APPROVED",
    "method": "BKASH",
    "trx_id": "AB1234XYZ789",
    "sender_number": "01711000000",
    "admin_note": "Verified.",
    "created_at": "2026-10-15T09:00:00Z",
    "trx_submitted_at": "2026-10-15T09:10:00Z",
    "reviewed_at": "2026-10-15T11:00:00Z",
    "expires_at": "2026-10-16T09:00:00Z"
  }
]
```

---

### 4.6 Get Single Payment

| Method | Path |
|--------|------|
| `GET` | `/api/payments/{payment_id}` |

Returns the same `PaymentView` object as above.

---

### 4.7 Get Payment Summary

| Method | Path |
|--------|------|
| `GET` | `/api/payments/summary/me` |

**Response 200:**
```json
{
  "pending_submission": 0,
  "awaiting_admin": 1,
  "approved": 3,
  "rejected": 0,
  "total_topup_bdt": 2500.0
}
```

---

## 5. Lawyer HITL Panel

> All endpoints require `Bearer Token` with `role: LAWYER` and an **APPROVED** `LawyerProfile`.

### 5.1 Get Pending Offers (Queue)

| Method | Path |
|--------|------|
| `GET` | `/api/lawyer/queue` |

**Response 200:**
```json
[
  {
    "memo_id": 17,
    "contract_id": 9,
    "contract_title": "Employment Agreement - Rahim.pdf",
    "overall_risk": "HIGH",
    "risk_score": 72,
    "payout_bdt": 1000.0,
    "offer_expires_at": "2026-10-18T17:00:00Z",
    "time_left_seconds": 7842
  }
]
```

> Show a live countdown timer using `time_left_seconds`. Offer expires in 3 hours from being offered.

---

### 5.2 Accept Offer

| Method | Path |
|--------|------|
| `POST` | `/api/lawyer/queue/{memo_id}/accept` |

No body. **Response 200:**
```json
{
  "status": "success",
  "memo_id": 17,
  "submit_deadline": "2026-10-18T17:00:00Z"
}
```

> After accepting, show the `submit_deadline` prominently — lawyer has **3 hours** to complete the review.

---

### 5.3 Decline Offer

| Method | Path |
|--------|------|
| `POST` | `/api/lawyer/queue/{memo_id}/decline` |

**Request Body:**
```json
{ "reason": "Outside my specialization area." }
```

**Response 200:** `{ "status": "success", "message": "Offer declined. Task has been re-queued." }`

---

### 5.4 Get Active Tasks

| Method | Path |
|--------|------|
| `GET` | `/api/lawyer/active` |

**Response 200:**
```json
[
  {
    "memo_id": 17,
    "contract_id": 9,
    "contract_title": "Employment Agreement - Rahim.pdf",
    "hitl_status": "ASSIGNED",
    "payout_bdt": 1000.0,
    "submit_deadline": "2026-10-18T17:00:00Z",
    "hours_left": 2.5
  }
]
```

---

### 5.5 Submit Review (Complete Task)

| Method | Path |
|--------|------|
| `POST` | `/api/lawyer/active/{memo_id}/submit` |

**Request Body:**
```json
{
  "updated_clauses": [
    {
      "clause_number": "§ 1",
      "clause_title": "Non-Compete Clause",
      "risk_level": "Review Needed",
      "original_text": "...",
      "statute_reference": "Bangladesh Labour Act 2006, Section 27",
      "legal_explanation": "Lawyer's updated assessment after review...",
      "proposed_safe_redline": "Lawyer's recommended revision text...",
      "is_compliant": false
    }
  ],
  "lawyer_notes": "I have reviewed this employment contract. The non-compete clause should be revised to 12 months maximum within Dhaka Division only...",
  "signature_url": "signatures/lawyer_42_sign_abc.png",
  "signed_pdf_url": "signed_contracts/memo_17_final.pdf"
}
```

> `signature_url` and `signed_pdf_url` are Supabase storage paths, not public URLs.

**Response 200:**
```json
{
  "status": "success",
  "memo_id": 17,
  "earned_bdt": 1000.0,
  "new_wallet_balance": 2500.0
}
```

---

### 5.6 Start Contract Review Chat Session

| Method | Path |
|--------|------|
| `POST` | `/api/lawyer/active/{memo_id}/chat-session` |

No body. **Response 200:**
```json
{
  "status": "success",
  "session_id": 60,
  "scoped_contract_id": 9,
  "message": "Chat session started. Limit: 10 messages."
}
```

> Use the returned `session_id` with `POST /api/chat/query` (mode: `CONTRACT_REVIEW`) for AI-assisted contract review. **Free for lawyers.**

---

### 5.7 Earnings Dashboard

| Method | Path |
|--------|------|
| `GET` | `/api/lawyer/earnings` |

**Response 200:**
```json
{
  "wallet_balance_bdt": 2500.0,
  "total_earned_bdt": 7000.0,
  "total_withdrawn_bdt": 4500.0,
  "pending_withdrawals_bdt": 0.0,
  "completed_reviews": 7,
  "rating": 4.8,
  "active_tasks": 1
}
```

---

### 5.8 Request Withdrawal

| Method | Path |
|--------|------|
| `POST` | `/api/lawyer/withdrawals/request` |

**Request Body:**
```json
{
  "amount_bdt": 2000.00,
  "bkash_number": "01711000000",
  "account_holder_name": "Barrister Karim Ahmed"
}
```

**Response 200:**
```json
{
  "status": "success",
  "withdrawal_id": 5,
  "amount_bdt": 2000.0,
  "message": "Withdrawal requested. Admin will process within 2 business days."
}
```

**Error Responses:**

| Code | `detail` | UI Action |
|------|----------|-----------|
| `400` | `"Minimum withdrawal is ৳500."` | Show minimum amount warning |
| `400` | `"Insufficient balance. Available: ৳500"` | Show available balance |
| `400` | `"You already have a pending withdrawal request."` | Disable form, show existing pending request |

---

### 5.9 List My Withdrawals

| Method | Path |
|--------|------|
| `GET` | `/api/lawyer/withdrawals` |

**Response 200 (array):**
```json
[
  {
    "id": 5,
    "amount_bdt": 2000.0,
    "status": "PAID",
    "bkash_number": "01711000000",
    "requested_at": "2026-10-10T09:00:00Z",
    "processed_at": "2026-10-12T11:00:00Z",
    "admin_note": "Transferred via bKash.",
    "payout_trx_id": "BKH9876543210"
  }
]
```

---

## 6. Enterprise Admin Panel

> All endpoints require `Bearer Token` with `role: ENTERPRISE_ADMIN`.

### 6.1 Company Dashboard

| Method | Path |
|--------|------|
| `GET` | `/api/company/dashboard` |

**Response 200:**
```json
{
  "enterprise": {
    "id": 3,
    "name": "LegalCo Bangladesh Ltd.",
    "referral_code": "LEGALCO-AB12",
    "credit_balance": 8500,
    "member_limit": 20
  },
  "members": {
    "total": 15,
    "active": 13,
    "pending_approval": 2,
    "slots_remaining": 5
  },
  "credits": {
    "pool_balance": 8500,
    "used_this_month": 320
  }
}
```

---

### 6.2 List All Members

| Method | Path | Query Params |
|--------|------|-------------|
| `GET` | `/api/company/members` | `?status=APPROVED&department=Legal&search=karim&limit=50` |

**Status values:** `PENDING`, `APPROVED`, `REJECTED`, `SUSPENDED`

---

### 6.3 Get Pending Members

| Method | Path |
|--------|------|
| `GET` | `/api/company/members/pending` |

Returns members awaiting approval (for the "Pending Requests" tab).

---

### 6.4 Get Member Detail

| Method | Path |
|--------|------|
| `GET` | `/api/company/members/{user_id}` |

Returns full member profile with quota info and recent 30-day activity log.

---

### 6.5 Approve Member

| Method | Path |
|--------|------|
| `POST` | `/api/company/members/{user_id}/approve` |

**Request Body:**
```json
{
  "company_role": "MEMBER",
  "department": "Legal Operations",
  "initial_quota": 200
}
```

**Company roles:** `ADMIN`, `MANAGER`, `MEMBER`

---

### 6.6 Reject / Suspend / Reactivate / Remove Member

| Method | Path | Body |
|--------|------|------|
| `POST` | `/api/company/members/{user_id}/reject` | `{ "reason": "..." }` |
| `POST` | `/api/company/members/{user_id}/suspend` | `{ "reason": "..." }` |
| `POST` | `/api/company/members/{user_id}/reactivate` | None |
| `POST` | `/api/company/members/{user_id}/remove` | None |

---

### 6.7 Update Member Info

| Method | Path |
|--------|------|
| `PATCH` | `/api/company/members/{user_id}` |

**Request Body:**
```json
{
  "company_role": "MANAGER",
  "department": "Compliance"
}
```

---

### 6.8 Adjust Member Credit Quota

| Method | Path |
|--------|------|
| `POST` | `/api/company/members/{user_id}/quota` |

**Request Body:**
```json
{
  "delta": 100,
  "reason": "Monthly allowance increase for Q4 workload."
}
```

> `delta` is **signed**: `+100` grants more, `-50` reduces. Credits stay in the enterprise pool — quota is just a per-member soft cap.

**Response 200:**
```json
{
  "status": "success",
  "user_id": 22,
  "quota_total": 300,
  "quota_used": 145,
  "quota_remaining": 155
}
```

---

### 6.9 Get Quota Summary

| Method | Path |
|--------|------|
| `GET` | `/api/company/quota/summary` |

**Response 200:**
```json
{
  "pool_balance": 8500,
  "total_quota_allocated": 3000,
  "total_quota_used": 1200,
  "total_quota_remaining": 1800,
  "unallocated_pool": 6700
}
```

---

### 6.10 Upload Company Playbook

| Method | Path | Content-Type |
|--------|------|-------------|
| `POST` | `/api/company/playbook/upload` | `multipart/form-data` |

**Form Fields:**

| Field | Type | Required |
|-------|------|----------|
| `title` | `string` | ✅ Yes |
| `file` | `File` | ✅ Yes (PDF or DOCX) |

**Response 200:**
```json
{
  "status": "success",
  "playbook_id": 2,
  "title": "LegalCo Standard Contract Policy 2026",
  "rules_extracted_count": 12,
  "rules": [
    {
      "rule_id": 1,
      "rule_text": "All contracts must include a Force Majeure clause.",
      "severity": "HIGH"
    }
  ]
}
```

---

### 6.11 List / Activate / Delete Playbooks

| Method | Path |
|--------|------|
| `GET` | `/api/company/playbooks` |
| `POST` | `/api/company/playbooks/{playbook_id}/activate` |
| `DELETE` | `/api/company/playbooks/{playbook_id}` |

---

### 6.12 Usage Analytics

| Method | Path | Query Params |
|--------|------|-------------|
| `GET` | `/api/company/analytics/usage` | `?days=30` |

Returns daily credit spend aggregated by day for the last N days.

---

## 7. Settings & Profile

### 7.1 Get Full Settings

| Method | Path | Auth |
|--------|------|------|
| `GET` | `/api/settings/me` | Bearer Token |

**Response 200 (for a Lawyer account):**
```json
{
  "id": 42,
  "email": "lawyer@example.com",
  "full_name": "Barrister Karim Ahmed",
  "phone": "01711000000",
  "role": "LAWYER",
  "subscription_tier": "FREE",
  "credit_balance": 0,
  "is_verified": true,
  "last_login_at": "2026-10-18T13:00:00Z",
  "enterprise": null,
  "lawyer": {
    "bar_council_id": "BC/DHK/2019/001234",
    "specializations": ["Contract Law", "Employment Law"],
    "availability": "AVAILABLE",
    "wallet_balance_bdt": 2500.0,
    "completed_reviews": 7,
    "rating": 4.8,
    "monthly_free_chat_used": 23,
    "monthly_free_chat_remaining": 77
  }
}
```

> The `enterprise` object is populated for enterprise users; the `lawyer` object for lawyers. Render conditionally.

---

### 7.2 Update Profile

| Method | Path |
|--------|------|
| `PATCH` | `/api/settings/profile` |

**Request Body:**
```json
{
  "full_name": "Rahim Uddin Ahmed",
  "phone": "01722000000"
}
```

**Response 200:** `{ "status": "success", "message": "Profile updated." }`

---

### 7.3 Change Password

| Method | Path |
|--------|------|
| `POST` | `/api/settings/change-password` |

**Request Body:**
```json
{
  "current_password": "old_password",
  "new_password": "new_strong_password"
}
```

**Error:** `400` `"Current password is incorrect."`

---

### 7.4 Update Lawyer Preferences

| Method | Path |
|--------|------|
| `PATCH` | `/api/settings/lawyer` |

**Request Body:**
```json
{
  "specializations": ["Contract Law", "Corporate Law", "Banking Law"],
  "availability": "AVAILABLE",
  "bio": "Senior advocate with 10 years of experience in corporate law."
}
```

> Availability values: `AVAILABLE`, `OFFLINE`. `BUSY` is managed automatically by the system.

---

## 8. System Admin Panel

> All endpoints require `Bearer Token` with `role: ADMIN`.

### Endpoint Summary

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/admin/dashboard` | Platform overview stats |
| `GET` | `/api/admin/lawyers/pending` | Lawyers awaiting verification |
| `POST` | `/api/admin/lawyers/{id}/approve` | Approve lawyer (upgrades role to `LAWYER`) |
| `POST` | `/api/admin/lawyers/{id}/reject` | Reject with `{ "reason": "..." }` |
| `POST` | `/api/admin/lawyers/{id}/suspend` | Suspend lawyer with reason |
| `GET` | `/api/admin/enterprises/pending` | Enterprises awaiting approval |
| `POST` | `/api/admin/enterprises/{id}/approve` | Activate enterprise, upgrade user to `ENTERPRISE_ADMIN` |
| `POST` | `/api/admin/enterprises/{id}/reject` | Reject enterprise |
| `GET` | `/api/admin/payments/pending` | bKash payments with TrxIDs awaiting verification |
| `POST` | `/api/admin/payments/{id}/approve` | Grant credits to user or enterprise |
| `POST` | `/api/admin/payments/{id}/reject` | Reject with `{ "reason": "..." }` |
| `GET` | `/api/admin/withdrawals/pending` | Lawyer withdrawals awaiting processing |
| `POST` | `/api/admin/withdrawals/{id}/approve` | Approve, deduct from lawyer wallet |
| `POST` | `/api/admin/withdrawals/{id}/mark-paid` | Record bKash TrxID `{ "payout_trx_id": "..." }` |
| `POST` | `/api/admin/withdrawals/{id}/reject` | Reject, refund wallet if already approved |
| `GET` | `/api/admin/users` | List all users with `?role=&is_active=&search=` |
| `POST` | `/api/admin/users/{id}/suspend` | Suspend user `{ "reason": "..." }` |
| `POST` | `/api/admin/users/{id}/adjust-credits` | `{ "amount": 100, "reason": "..." }` |
| `POST` | `/api/admin/enterprises/{id}/adjust-credits` | Same, for enterprise pool |

**Dashboard Response:**
```json
{
  "users": {
    "total": 1250,
    "general": 1100,
    "lawyers": 85,
    "enterprise_admins": 30,
    "enterprise_members": 35
  },
  "pending": {
    "lawyers": 5,
    "enterprises": 2,
    "payments": 12,
    "withdrawals": 3
  }
}
```

---

## 9. Credit & Pricing Reference

### Service Costs (in Credits)

| Service | Credit Cost | Applicable Roles |
|---------|-------------|-----------------|
| Legal Chat Query | **1** | `GENERAL`, `ENTERPRISE_*` |
| Contract Audit (Personal) | **25** | `GENERAL` |
| Contract Audit (Enterprise) | **50** | `ENTERPRISE_ADMIN`, `ENTERPRISE_MEMBER` |
| Lawyer Verification (HITL) | **1500** | Any user |
| PDF Export | **5** | `GENERAL`, `ENTERPRISE_*` |

### Credit Sources by Role

| Role | Credit Source | Free Pool |
|------|--------------|-----------|
| `GENERAL` | Personal `wallet` | — |
| `ENTERPRISE_MEMBER` | `enterprise_pool` (quota capped) | — |
| `ENTERPRISE_ADMIN` | `enterprise_pool` | — |
| `LAWYER` | `monthly_pool` → then `wallet` | 100 free chat credits/month |
| `ADMIN` | Free (no deduction) | — |

### Credit Packages (Seeded Defaults)

| Name | Price | Total Credits | Badge |
|------|-------|---------------|-------|
| Starter Pack | ৳500 | 440 | — |
| Standard Pack | ৳1,000 | 950 | Most Popular |
| Pro Pack | ৳2,500 | 2,600 | Best Value |
| Enterprise Pack | ৳10,000 | 11,500 | — |

> **Security note:** BDT ↔ credit conversion rates are intentionally opaque. Never expose `internal_bdt_value` in the frontend.
