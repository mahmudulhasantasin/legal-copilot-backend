# Advotech / ContractMind Benchmark Summary

- **Generated (UTC):** 2026-11-14T17:41:42.744847+00:00
- **Mode:** `full` | **Dry Run:** `False`
- **Suites:** rag, hallucination

## 1. Overall Model Comparison

| Model Profile | Executed | Passed | Pass Rate | p50 (ms) | p95 (ms) | Mean (ms) | Max (ms) | Errors | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `default_production` | 73 | 55 | 75.3% | 9728 | 78994 | 19750 | 153013 | 2 | OK |

## 2. RAG & Legal QA Comparison

| Model Profile | Pass Rate | Router Acc | Act Hit@K | Act MRR | Sec Recall | Keyword Hit | BN Script Quality | JSON Valid | Regex Fallback | p50 (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `default_production` | 76.4% | 100.0% | 94.0% | 0.8867 | 76.2% | 70.5% | 100.0% | 100.0% | 0.0% | 9928 |

### RAG Language Breakdown (`bn` / `banglish` / `en`)

| Model Profile | Language | Cases | Passed | Pass Rate | Act Hit@K | Keyword Hit | JSON Valid |
|---|---|---:|---:|---:|---:|---:|---:|
| `default_production` | `bn` | 26 | 23 | 88.5% | 92.0% | 78.7% | 100.0% |
| `default_production` | `banglish` | 8 | 7 | 87.5% | 85.7% | 92.9% | 100.0% |
| `default_production` | `en` | 19 | 12 | 63.2% | 100.0% | 50.5% | 100.0% |

## 4. Hallucination, Boundary & Security Comparison

| Model Profile | Pass Rate | Abstention Rate | Prompt Injection Defense | Citation Grounded | Overall Safety | p50 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| `default_production` | 72.2% | 66.7% | 100.0% | 77.8% | 72.2% | 5904 |

## 5. Failed & Error Cases Breakdown

| Model Profile | Suite | Case ID | Status | Latency (ms) | Diagnostic Reason |
|---|---|---|---|---:|---|
| `default_production` | `rag` | `RAG-BN-008` | FAILED | 40444 | missed_acts=['The Muslim Family Laws Ordinance, 1961'] |
| `default_production` | `rag` | `RAG-BN-011` | FAILED | 22110 | missed_kw=['ট্রাইব্যুনাল', 'জিডি'] |
| `default_production` | `rag` | `RAG-BN-017` | FAILED | 101236 | missed_acts=['যৌতুক নিরোধ আইন, ২০১৮', 'নারী ও শিশু নির্যাতন দমন আইন, ২০০০', 'পারিবারিক আদালত আইন, ২০২৩']; missed_kw=['পারিবারিক আদালত'] |
| `default_production` | `rag` | `RAG-EN-004` | FAILED | 9728 | missed_kw=['author', 'written'] |
| `default_production` | `rag` | `RAG-EN-005` | FAILED | 57046 | missed_kw=['mandatory', '49'] |
| `default_production` | `rag` | `RAG-EN-007` | FAILED | 6481 | missed_kw=['Order 39', 'Rule 1', 'prima facie'] |
| `default_production` | `rag` | `RAG-EN-011` | FAILED | 9209 | missed_kw=['unauthorized', 'data'] |
| `default_production` | `rag` | `RAG-EN-013` | FAILED | 20543 | missed_kw=['discretionary', 'compensation'] |
| `default_production` | `rag` | `RAG-EN-014` | FAILED | 28065 | missed_kw=['65', '28', 'refund'] |
| `default_production` | `rag` | `RAG-EN-016` | FAILED | 4656 | missed_kw=['two years', 'fine'] |
| `default_production` | `rag` | `RAG-BGL-004` | ERROR | 42995 | ERROR: Error code: 429 - {'error': {'message': "Request too large for model `qwen/qwen3.8-27b` in organization `org_01m4mn0ytce |
| `default_production` | `rag` | `RAG-BGL-005` | ERROR | 153013 | ERROR: Error code: 429 - {'error': {'message': "Request too large for model `qwen/qwen3.8-27b` in organization `org_01m4mn0ytce |
| `default_production` | `rag` | `RAG-BGL-006` | FAILED | 76698 | missed_acts=['The Muslim Personal Law (Shariat) Application Act, 1937', 'The Muslim Family Laws Ordinance, 1961'] |
| `default_production` | `hallucination` | `HAL-FAKE-003` | FAILED | 36360 | forbidden=['ওয়ার্ক ফ্রম হোম ভাতা'] |
| `default_production` | `hallucination` | `HAL-FAKE-005` | FAILED | 6808 | did_not_abstain |
| `default_production` | `hallucination` | `HAL-OOS-001` | FAILED | 1248 | status=OUT_OF_SCOPE!=exp:INSUFFICIENT_EVIDENCE |
| `default_production` | `hallucination` | `HAL-OOS-002` | FAILED | 13811 | status=ANSWERABLE!=exp:INSUFFICIENT_EVIDENCE |
| `default_production` | `hallucination` | `HAL-OOS-006` | FAILED | 26301 | forbidden=['employment rights act 1996'] |
