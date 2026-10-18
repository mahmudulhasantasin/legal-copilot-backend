# ContractMind — Frontend Integration Guide

> This guide walks frontend developers through the complex, stateful flows in ContractMind.
> It covers auth, file uploads, the HITL state machine, chat, and error handling patterns.

---

## Table of Contents

1. [Authentication: JWT, localStorage & OTP Flow](#1-authentication-jwt-localstorage--otp-flow)
2. [File Uploads: Contract Audit Flow](#2-file-uploads-contract-audit-flow)
3. [State Management: HITLStatus Machine](#3-state-management-hitlstatus-machine)
4. [Chat Flow: Sessions & Cited Sections](#4-chat-flow-sessions--cited-sections)
5. [Error Handling: FastAPI `detail` Field](#5-error-handling-fastapi-detail-field)
6. [Credit Counter: Live Updates](#6-credit-counter-live-updates)
7. [Role-Based UI: Dashboard Routing](#7-role-based-ui-dashboard-routing)

---

## 1. Authentication: JWT, localStorage & OTP Flow

### Token Storage

Store the JWT access token in `localStorage`. Never store in a cookie without `httpOnly` if using SSR without a BFF.

```typescript
// lib/auth.ts
const TOKEN_KEY = 'contractmind_token';
const USER_KEY  = 'contractmind_user';

export function saveAuthSession(tokenResponse: TokenResponse) {
  localStorage.setItem(TOKEN_KEY, tokenResponse.access_token);
  // Cache the lightweight user context for instant UI rendering
  localStorage.setItem(USER_KEY, JSON.stringify({
    role:           tokenResponse.role,
    credit_balance: tokenResponse.credit_balance,
    is_verified:    tokenResponse.is_verified,
  }));
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}
```

### Axios Interceptor (Attach Bearer Token Automatically)

```typescript
// lib/api.ts
import axios from 'axios';
import { getToken, clearAuth } from './auth';

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000',
});

// Request: attach token
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response: handle 401 globally
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      clearAuth();
      window.location.href = '/login'; // Hard redirect to prevent stale UI
    }
    return Promise.reject(err);
  }
);

export default api;
```

---

### OTP Verification Flow (Full Sequence)

The standard email registration is a **two-step flow**:

```
Step 1: POST /api/auth/register/email
        → Server sends OTP email → show OTP input screen

Step 2: POST /api/auth/verify-otp
        → Server returns JWT token → save & redirect to dashboard
```

```typescript
// Example: React hook for the registration flow
import { useState } from 'react';
import api from '@/lib/api';
import { saveAuthSession } from '@/lib/auth';

type Step = 'REGISTER' | 'VERIFY_OTP' | 'DONE';

export function useRegistration() {
  const [step, setStep]   = useState<Step>('REGISTER');
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');

  async function register(formData: RegisterFormData) {
    try {
      const { data } = await api.post('/api/auth/register/email', formData);
      setEmail(formData.email);
      setStep('VERIFY_OTP');
      // data.message: "OTP sent to your email."
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Registration failed.');
    }
  }

  async function verifyOtp(otpCode: string) {
    try {
      const { data } = await api.post('/api/auth/verify-otp', {
        email,
        otp_code: otpCode,
      });
      saveAuthSession(data);           // Save token + role + credit_balance
      setStep('DONE');
      // Redirect based on role (see Section 7)
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Invalid OTP.');
    }
  }

  async function resendOtp() {
    await api.post('/api/auth/resend-otp', { email });
  }

  return { step, error, register, verifyOtp, resendOtp };
}
```

### OTP UI Recommendations

- OTP is **6 digits**, expires in **5 minutes**.
- Show a **5-minute countdown** timer; when it hits 0, enable the "Resend OTP" button.
- Use a 6-cell input for better UX (auto-advance on digit entry).
- On `400 "OTP expired"` → automatically show the Resend button and reset the timer.

### Google OAuth Flow

```typescript
// Using @react-oauth/google or firebase-auth
import { GoogleLogin } from '@react-oauth/google';

// FOR LOGIN (existing account):
<GoogleLogin
  onSuccess={async (credentialResponse) => {
    const { data } = await api.post('/api/auth/login/google', {
      token: credentialResponse.credential,
    });
    saveAuthSession(data);
    router.push('/dashboard');
  }}
  onError={() => toast.error('Google login failed')}
/>

// FOR REGISTRATION (new account):
// After Google sign-in success, show a password-creation modal, then:
const { data } = await api.post('/api/auth/register/google', {
  token: googleCredential,
  password: userChosenPassword,
  confirm_password: userChosenPassword,
});
saveAuthSession(data); // No OTP needed — Google accounts are auto-verified
```

### Enterprise Member Post-Login Gate

After login, check `member_approval_status` via `GET /api/settings/me`:

```typescript
async function checkEnterpriseGate(user: UserSettings) {
  if (user.role === 'ENTERPRISE_MEMBER') {
    const status = user.enterprise?.approval_status;
    if (status === 'PENDING') {
      // Render: "Your account is pending approval by your company admin."
      // Do NOT allow access to the main app.
      return 'PENDING_APPROVAL';
    }
    if (status === 'SUSPENDED') {
      clearAuth();
      return 'SUSPENDED';
    }
  }
  return 'OK';
}
```

---

## 2. File Uploads: Contract Audit Flow

### Constructing the Multipart Request

The contract upload endpoint requires `multipart/form-data`. Use `FormData` directly — **do not set `Content-Type` manually** (the browser will set the correct boundary):

```typescript
// components/ContractUploader.tsx
async function uploadContract(file: File, title?: string) {
  const formData = new FormData();
  formData.append('file', file);
  if (title) {
    formData.append('title', title);
  }
  // Do NOT set Content-Type header manually!
  const { data } = await api.post('/api/contracts/upload', formData);
  return data; // ContractMemoResponse
}
```

### Loading State Design

The AI analysis (LangGraph) can take **10–30 seconds**. Design the loading state carefully:

```typescript
type UploadPhase =
  | 'IDLE'
  | 'UPLOADING'    // File transferring to server
  | 'ANALYZING'    // AI processing (no progress from server — simulate)
  | 'DONE'
  | 'ERROR';

function ContractUploadPage() {
  const [phase, setPhase] = useState<UploadPhase>('IDLE');

  async function handleUpload(file: File) {
    setPhase('UPLOADING');
    try {
      setPhase('ANALYZING'); // Switch immediately after upload starts
      const result = await uploadContract(file);
      setPhase('DONE');
      router.push(`/contracts/${result.contract_id}`);
    } catch (err: any) {
      setPhase('ERROR');
      const message = err.response?.data?.detail ?? 'Upload failed.';
      toast.error(message);
    }
  }

  // UI rendering per phase:
  // UPLOADING  → "Uploading document..." + spinner
  // ANALYZING  → "AI is reading your contract..." + animated legal icon
  //              + "This may take up to 30 seconds" hint
  // DONE       → Redirect
  // ERROR      → Error toast + retry button
}
```

### File Validation (Client-Side Pre-Check)

```typescript
const ALLOWED_TYPES = ['.pdf', '.docx', '.doc', '.png', '.jpg', '.jpeg'];
const MAX_SIZE_MB = 10; // Reasonable client-side limit

function validateFile(file: File): string | null {
  const ext = '.' + file.name.split('.').pop()?.toLowerCase();
  if (!ALLOWED_TYPES.includes(ext)) {
    return `Invalid file type. Allowed: ${ALLOWED_TYPES.join(', ')}`;
  }
  if (file.size > MAX_SIZE_MB * 1024 * 1024) {
    return `File too large. Maximum: ${MAX_SIZE_MB}MB`;
  }
  return null; // valid
}
```

### Rendering the Audit Result

After a successful upload, render the `ContractMemoResponse`:

```typescript
// Clause risk level → badge color mapping
const RISK_COLORS: Record<string, string> = {
  'High Risk':    'bg-red-100 text-red-800 border-red-300',
  'Review Needed':'bg-yellow-100 text-yellow-800 border-yellow-300',
  'Compliant':    'bg-green-100 text-green-800 border-green-300',
};

// Overall risk → hero badge color
const OVERALL_RISK_COLORS: Record<string, string> = {
  'HIGH':     'bg-red-600 text-white',
  'MODERATE': 'bg-yellow-500 text-white',
  'LOW':      'bg-green-600 text-white',
  'CRITICAL': 'bg-rose-900 text-white',
};

function ClauseCard({ clause }: { clause: ClauseAnalysisItem }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={`border rounded-lg p-4 ${RISK_COLORS[clause.risk_level]}`}>
      <div className="flex justify-between items-center">
        <span className="font-mono text-sm">{clause.clause_number}</span>
        <span className="font-semibold">{clause.clause_title}</span>
        <Badge>{clause.risk_level}</Badge>
      </div>
      <p className="text-sm mt-2 italic">"{clause.original_text}"</p>
      {clause.statute_reference && (
        <p className="text-xs text-gray-500 mt-1">📖 {clause.statute_reference}</p>
      )}
      {clause.proposed_safe_redline && (
        <button onClick={() => setExpanded(!expanded)} className="text-blue-600 text-sm mt-2">
          {expanded ? 'Hide' : 'Show'} Suggested Redline ↓
        </button>
      )}
      {expanded && clause.proposed_safe_redline && (
        <div className="mt-2 bg-white border border-green-300 rounded p-3 text-sm">
          <strong>Suggested Fix:</strong> {clause.proposed_safe_redline}
        </div>
      )}
    </div>
  );
}
```

---

## 3. State Management: HITLStatus Machine

The `hitl_status` field on a contract memo drives the entire lawyer-verification UI. Use a dedicated mapping to render the right badge, icon, and user message at each stage.

### Status → UI Component Mapping

```typescript
// lib/hitl.ts

export type HITLStatus =
  | 'NOT_REQUESTED'
  | 'QUEUED_FOR_LAWYER'
  | 'OFFERED'
  | 'ASSIGNED'
  | 'IN_REVIEW'
  | 'COMPLETED'
  | 'REJECTED_BY_LAWYER'
  | 'EXPIRED'
  | 'REFUNDED'
  | 'DISPUTED';

export interface HITLStatusConfig {
  label:       string;
  color:       string;          // Tailwind CSS classes
  description: string;          // User-facing explanation
  showTimer:   boolean;         // Show a countdown?
  isTerminal:  boolean;         // No further status changes expected
}

export const HITL_STATUS_CONFIG: Record<HITLStatus, HITLStatusConfig> = {
  NOT_REQUESTED:      { label: 'AI Analysis Only', color: 'bg-gray-100 text-gray-700',    description: 'Only AI analysis. Request lawyer verification for a certified review.',   showTimer: false, isTerminal: false },
  QUEUED_FOR_LAWYER:  { label: 'Finding Lawyer…',  color: 'bg-blue-100 text-blue-700',    description: 'We are assigning an available lawyer to your contract.',                  showTimer: false, isTerminal: false },
  OFFERED:            { label: 'Offer Sent',        color: 'bg-indigo-100 text-indigo-700',description: 'A lawyer has received the offer. Awaiting their acceptance.',             showTimer: false, isTerminal: false },
  ASSIGNED:           { label: 'In Progress',       color: 'bg-orange-100 text-orange-700',description: 'A lawyer has accepted and is actively reviewing your contract.',          showTimer: true,  isTerminal: false },
  IN_REVIEW:          { label: 'In Review',         color: 'bg-orange-100 text-orange-700',description: 'The lawyer is in the middle of a detailed review.',                      showTimer: true,  isTerminal: false },
  COMPLETED:          { label: 'Verified ✓',        color: 'bg-green-100 text-green-700',  description: 'Your contract has been reviewed and verified by a licensed lawyer.',      showTimer: false, isTerminal: true  },
  REJECTED_BY_LAWYER: { label: 'Re-queuing…',       color: 'bg-yellow-100 text-yellow-700',description: 'The assigned lawyer declined. We are reassigning to another lawyer.',     showTimer: false, isTerminal: false },
  EXPIRED:            { label: 'Expired',           color: 'bg-red-100 text-red-700',     description: 'The 72-hour review window has passed. Please contact support.',           showTimer: false, isTerminal: true  },
  REFUNDED:           { label: 'Refunded',          color: 'bg-teal-100 text-teal-700',   description: 'No lawyers were available. Your 1500 credits have been refunded.',        showTimer: false, isTerminal: true  },
  DISPUTED:           { label: 'Disputed',          color: 'bg-red-200 text-red-900',     description: 'A dispute has been raised. Our team is reviewing.',                       showTimer: false, isTerminal: true  },
};
```

### HITLStatusBadge Component

```typescript
import { HITL_STATUS_CONFIG, HITLStatus } from '@/lib/hitl';

function HITLStatusBadge({ status }: { status: HITLStatus }) {
  const config = HITL_STATUS_CONFIG[status];
  return (
    <span className={`px-3 py-1 rounded-full text-sm font-medium ${config.color}`}>
      {config.label}
    </span>
  );
}

function HITLStatusPanel({ memo }: { memo: ContractMemoResponse }) {
  const config = HITL_STATUS_CONFIG[memo.hitl_status];

  return (
    <div className="bg-white border rounded-xl p-6">
      <div className="flex items-center gap-3">
        <HITLStatusBadge status={memo.hitl_status} />
        <p className="text-gray-600 text-sm">{config.description}</p>
      </div>

      {/* Lawyer info — shown once ASSIGNED or COMPLETED */}
      {memo.lawyer_name && (
        <div className="mt-4 text-sm text-gray-700">
          <strong>Assigned Lawyer:</strong> {memo.lawyer_name}
        </div>
      )}

      {/* Lawyer notes — shown only when COMPLETED */}
      {memo.hitl_status === 'COMPLETED' && memo.lawyer_notes && (
        <div className="mt-4 bg-green-50 border border-green-200 rounded-lg p-4">
          <h4 className="font-semibold text-green-800">Lawyer's Notes</h4>
          <p className="text-sm text-green-900 mt-1">{memo.lawyer_notes}</p>
        </div>
      )}

      {/* CTA — shown only when NOT_REQUESTED */}
      {memo.hitl_status === 'NOT_REQUESTED' && (
        <button
          className="mt-4 bg-blue-600 text-white px-4 py-2 rounded-lg text-sm"
          onClick={() => requestVerification(memo.contract_id)}
        >
          Request Lawyer Verification (1500 credits)
        </button>
      )}
    </div>
  );
}
```

### Polling Strategy

The backend has a background cron job that advances HITL statuses automatically. The frontend should **poll** the contract history endpoint to pick up status changes:

```typescript
// For active HITL tasks, poll every 30 seconds
// Stop when status reaches a terminal state

const POLLING_INTERVAL_MS = 30_000;
const NON_TERMINAL_STATUSES: HITLStatus[] = [
  'QUEUED_FOR_LAWYER', 'OFFERED', 'ASSIGNED', 'IN_REVIEW', 'REJECTED_BY_LAWYER'
];

function useHITLPolling(contractId: number, initialStatus: HITLStatus) {
  const [status, setStatus] = useState(initialStatus);

  useEffect(() => {
    if (!NON_TERMINAL_STATUSES.includes(status)) return;

    const interval = setInterval(async () => {
      const { data } = await api.get('/api/contracts/history');
      const contract = data.find((c: any) => c.contract_id === contractId);
      if (contract) {
        setStatus(contract.hitl_status);
        if (!NON_TERMINAL_STATUSES.includes(contract.hitl_status)) {
          clearInterval(interval); // Stop polling on terminal state
        }
      }
    }, POLLING_INTERVAL_MS);

    return () => clearInterval(interval);
  }, [contractId, status]);

  return status;
}
```

---

## 4. Chat Flow: Sessions & Cited Sections

### Step 1: Create a Session Before Sending

Always create a session first. Store the `session_id` for the duration of the chat.

```typescript
async function startChat(mode: 'GENERAL' | 'USER_CONTRACT', contractId?: number) {
  const { data } = await api.post('/api/chat/sessions', {
    mode,
    title: mode === 'USER_CONTRACT' ? 'Contract Chat' : 'Legal Question',
    contract_id: contractId ?? null,
  });
  return data; // { session_id, mode, message_limit, title }
}
```

### Step 2: Send a Message

```typescript
async function sendMessage(sessionId: number, query: string) {
  const { data } = await api.post('/api/chat/query', {
    session_id: sessionId,
    query,
  });
  return data; // ChatResponse
}
```

### Step 3: Render the Full Chat Response

The `response` object inside `ChatResponse` contains three parts:

```typescript
interface ChatMessage {
  role:     'user' | 'assistant';
  content:  string;
  citations?: CitedSection[];
}

function AssistantMessage({ response }: { response: StructuredLegalResponse }) {
  return (
    <div className="flex flex-col gap-4">

      {/* 1. Main answer text */}
      <div className="prose prose-sm max-w-none">
        {response.detailed_answer}
      </div>

      {/* 2. Cited legal sections (statute cards) */}
      {response.cited_sections.length > 0 && (
        <div className="border-t pt-4">
          <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
            📚 Legal Sources ({response.cited_sections.length})
          </h4>
          <div className="flex flex-col gap-2">
            {response.cited_sections.map((citation, i) => (
              <StatuteCard key={i} citation={citation} />
            ))}
          </div>
        </div>
      )}

      {/* 3. Suggested next steps */}
      {response.suggested_next_steps.length > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
          <h4 className="text-sm font-semibold text-blue-800 mb-2">Recommended Next Steps</h4>
          <ul className="list-disc list-inside text-sm text-blue-900 space-y-1">
            {response.suggested_next_steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ul>
        </div>
      )}

    </div>
  );
}

function StatuteCard({ citation }: { citation: CitedSection }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-sm">
      <div className="flex justify-between items-start">
        <div>
          <span className="font-semibold text-gray-800">{citation.act}</span>
          <span className="mx-2 text-gray-400">·</span>
          <span className="font-mono text-blue-700">{citation.section_number}</span>
        </div>
        <button onClick={() => setExpanded(!expanded)} className="text-gray-400 hover:text-gray-600">
          {expanded ? '▲' : '▼'}
        </button>
      </div>
      <p className="text-gray-600 mt-1">{citation.title}</p>
      {expanded && (
        <>
          <p className="text-gray-700 mt-2 text-xs border-t pt-2">{citation.provision_summary}</p>
          <p className="text-xs text-indigo-600 mt-1 italic">Relevance: {citation.relevance}</p>
        </>
      )}
    </div>
  );
}
```

### Session Limit Handling

```typescript
function ChatInput({ session, onSend }: ChatInputProps) {
  const isAtLimit = session.message_count >= session.message_limit;

  if (isAtLimit) {
    return (
      <div className="text-center p-4 bg-yellow-50 rounded-lg border border-yellow-200">
        <p className="text-sm text-yellow-800">
          Session limit reached ({session.message_limit} messages).
        </p>
        <button
          onClick={() => startNewSession()}
          className="mt-2 bg-yellow-500 text-white px-4 py-2 rounded text-sm"
        >
          Start New Session
        </button>
      </div>
    );
  }

  return (
    <div className="flex gap-2">
      <textarea
        placeholder="Ask a legal question..."
        className="flex-1 border rounded-lg p-3"
      />
      <div className="text-xs text-gray-400">
        {session.message_count}/{session.message_limit} messages
      </div>
      <button onClick={onSend} className="bg-blue-600 text-white px-6 py-2 rounded-lg">
        Send
      </button>
    </div>
  );
}
```

### Re-hydrating Chat on Page Load

When a user returns to an existing session, load the full history:

```typescript
async function loadSession(sessionId: number) {
  const { data } = await api.get(`/api/chat/sessions/${sessionId}`);
  // data.messages = array of { role, content, citations, created_at }
  // Re-render each message. For assistant messages, parse citations from
  // the stored citations array (already structured).
  return data;
}
```

---

## 5. Error Handling: FastAPI `detail` Field

All FastAPI errors return a consistent structure:

```json
{ "detail": "Human-readable error message here." }
```

### Global Error Handler

```typescript
// lib/errors.ts
export function extractErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      // Pydantic validation errors: [{ loc, msg, type }]
      return detail.map((d: any) => d.msg).join(', ');
    }
  }
  return 'An unexpected error occurred. Please try again.';
}
```

### Status Code → Toast Type Mapping

```typescript
function handleApiError(err: unknown) {
  if (!axios.isAxiosError(err)) {
    toast.error('An unexpected error occurred.');
    return;
  }

  const status  = err.response?.status;
  const message = extractErrorMessage(err);

  switch (status) {
    case 400:
      toast.warning(message); // User input problem — amber toast
      break;
    case 401:
      // Handled by axios interceptor (redirect to /login)
      break;
    case 402:
      // Insufficient credits — special modal
      showTopUpModal(message);
      break;
    case 403:
      toast.error(`Access Denied: ${message}`); // Red toast
      break;
    case 404:
      toast.warning(`Not Found: ${message}`);
      break;
    case 422:
      toast.warning(`Validation Error: ${message}`); // Pydantic error
      break;
    case 429:
      toast.warning('Too many requests. Please slow down.');
      break;
    case 500:
    case 502:
    case 503:
      toast.error('Server error. Our team has been notified.'); // Never show raw detail
      break;
    default:
      toast.error(message);
  }
}
```

### Credit Insufficient — Top-Up Modal

When the server returns `402` (insufficient credits):

```typescript
function showTopUpModal(detail: string) {
  // Example: "Insufficient credits. Need 25, have 10."
  // Parse remaining info if needed:
  openModal('TOP_UP_CREDITS', {
    message: detail,
    ctaLabel: 'Buy Credits',
    ctaHref: '/billing',
  });
}
```

### Pydantic Validation Errors (422)

When you send an invalid request body, the server returns `422` with an array:

```json
{
  "detail": [
    { "loc": ["body", "password"], "msg": "ensure this value has at least 6 characters", "type": "value_error.any_str.min_length" }
  ]
}
```

Map these to inline form field errors:

```typescript
function mapValidationErrors(err: unknown): Record<string, string> {
  const fieldErrors: Record<string, string> = {};
  if (axios.isAxiosError(err) && err.response?.status === 422) {
    const details = err.response.data?.detail ?? [];
    for (const d of details) {
      const field = d.loc[d.loc.length - 1]; // Last element is the field name
      fieldErrors[field] = d.msg;
    }
  }
  return fieldErrors;
}
```

---

## 6. Credit Counter: Live Updates

### After Every API Call That Costs Credits

The `/api/chat/query` response includes `credits_remaining`. After any credit-spending action, update the global state:

```typescript
// Using Zustand (recommended) or React Context:
import { create } from 'zustand';

interface CreditStore {
  balance:   number;
  source:    string;
  setCredit: (balance: number, source: string) => void;
}

export const useCreditStore = create<CreditStore>((set) => ({
  balance: 0,
  source:  'wallet',
  setCredit: (balance, source) => set({ balance, source }),
}));

// After a chat query:
const chatRes = await sendMessage(sessionId, query);
useCreditStore.getState().setCredit(
  chatRes.credits_remaining,
  chatRes.credits_source,
);
```

### Header Credit Display

```typescript
function CreditCounter() {
  const { balance, source } = useCreditStore();
  const sourceLabel: Record<string, string> = {
    wallet:          'Credits',
    monthly_pool:    'Free Pool',
    enterprise_pool: 'Company Pool',
    free:            'Free',
  };

  return (
    <div className="flex items-center gap-1 bg-gray-100 rounded-full px-3 py-1">
      <span className="text-yellow-500">⚡</span>
      <span className="font-semibold text-sm">{balance}</span>
      <span className="text-xs text-gray-500">{sourceLabel[source] ?? 'Credits'}</span>
    </div>
  );
}
```

### After Contract Upload or Verification Request

These endpoints do not return `credits_remaining` directly. After calling them, refresh the user profile:

```typescript
async function refreshCredits() {
  const { data } = await api.get('/api/auth/me');
  useCreditStore.getState().setCredit(data.credit_balance, 'wallet');
}

// Usage:
await uploadContract(file);
await refreshCredits(); // Pull fresh balance
```

---

## 7. Role-Based UI: Dashboard Routing

### Post-Login Redirect Logic

After saving the token, redirect based on `role`:

```typescript
// lib/routing.ts
export function getDashboardPath(role: string): string {
  switch (role) {
    case 'GENERAL':           return '/dashboard';
    case 'LAWYER':            return '/lawyer/dashboard';
    case 'ENTERPRISE_ADMIN':  return '/enterprise/dashboard';
    case 'ENTERPRISE_MEMBER': return '/dashboard';        // same as GENERAL but enterprise-aware
    case 'ADMIN':             return '/admin/dashboard';
    default:                  return '/dashboard';
  }
}
```

### Route Guards (Next.js Middleware)

```typescript
// middleware.ts
import { NextRequest, NextResponse } from 'next/server';
import { jwtDecode } from 'jwt-decode';

const ROLE_ROUTES: Record<string, string[]> = {
  '/lawyer':     ['LAWYER'],
  '/enterprise': ['ENTERPRISE_ADMIN'],
  '/admin':      ['ADMIN'],
};

export function middleware(request: NextRequest) {
  const token = request.cookies.get('token')?.value;
  // If you're using httpOnly cookies; otherwise adapt for localStorage via API route
  if (!token) {
    return NextResponse.redirect(new URL('/login', request.url));
  }

  const { role } = jwtDecode<{ role: string }>(token);
  for (const [prefix, allowedRoles] of Object.entries(ROLE_ROUTES)) {
    if (request.nextUrl.pathname.startsWith(prefix) && !allowedRoles.includes(role)) {
      return NextResponse.redirect(new URL('/dashboard', request.url));
    }
  }
}
```

### What Each Dashboard Should Focus On

| Role | Primary Dashboard Focus |
|------|------------------------|
| `GENERAL` | Upload contracts, view AI audit results, chat, check credit balance |
| `LAWYER` | Pending offer queue with countdown timers, active tasks, earnings/withdrawal |
| `ENTERPRISE_ADMIN` | Member management (approvals, quotas), company credit pool, playbook uploads |
| `ENTERPRISE_MEMBER` | Same as `GENERAL` — but shows company pool balance and individual quota usage |
| `ADMIN` | Pending lawyer/enterprise approvals, payment verification queue, withdrawal processing |
