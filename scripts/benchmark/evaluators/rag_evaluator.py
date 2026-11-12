"""RAG & Legal Chat QA evaluator against scripts/benchmark/datasets/rag_cases.json."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.qa_graph import qa_graph
from app.services.legal_normalizer import BN_TO_EN, normalize_act, resolve_act_robust

logger = logging.getLogger(__name__)

_BN_CHAR_RE = re.compile(r"[\u0980-\u09FF]")
_EN_ALPHA_RE = re.compile(r"[A-Za-z]")


def _to_lc_messages(history: List[Dict[str, str]]) -> list:
    msgs = []
    for item in history or []:
        role = str(item.get("role", "user")).lower()
        content = str(item.get("content", ""))
        if role in ("assistant", "ai"):
            msgs.append(AIMessage(content=content))
        else:
            msgs.append(HumanMessage(content=content))
    return msgs


def _canonical_act(name: str) -> str:
    if not name:
        return ""
    resolved = resolve_act_robust(name)
    return normalize_act(resolved or name)


def _norm_section(sec: Any) -> str:
    s = str(sec or "").translate(BN_TO_EN).strip()
    m = re.search(r"\d+[A-Za-z]?", s)
    return m.group(0).upper() if m else s.upper()


def _bengali_script_ratio(text: str) -> float:
    bn = len(_BN_CHAR_RE.findall(text or ""))
    en = len(_EN_ALPHA_RE.findall(text or ""))
    total = bn + en
    return (bn / total) if total > 0 else 0.0


def _compute_act_metrics(expected_acts: List[str], candidate_acts: List[str]) -> Dict[str, float]:
    if not expected_acts:
        return {"act_hit_at_k": 1.0, "act_mrr": 1.0}
    expected_norm = {_canonical_act(a) for a in expected_acts if a}
    cand_norm = [_canonical_act(a) for a in candidate_acts if a]

    hits = 0
    first_rank: Optional[int] = None
    for idx, cand in enumerate(cand_norm, start=1):
        if any(exp in cand or cand in exp for exp in expected_norm if exp and cand):
            hits += 1
            if first_rank is None:
                first_rank = idx
    return {
        "act_hit_at_k": 1.0 if hits > 0 else 0.0,
        "act_mrr": (1.0 / first_rank) if first_rank else 0.0,
    }


def _compute_section_recall(expected_sections: List[str], docs: List[Dict[str, Any]], cited: List[Dict[str, Any]], answer: str) -> float:
    if not expected_sections:
        return 1.0
    found = set()
    for d in docs or []:
        if isinstance(d, dict) and d.get("section"):
            found.add(_norm_section(d.get("section")))
    for c in cited or []:
        if isinstance(c, dict) and c.get("section"):
            found.add(_norm_section(c.get("section")))
    norm_ans = (answer or "").translate(BN_TO_EN).upper()

    matched = 0
    for sec in expected_sections:
        norm_s = _norm_section(sec)
        if norm_s in found or re.search(rf"\b{re.escape(norm_s)}\b", norm_ans):
            matched += 1
    return matched / len(expected_sections)


def _compute_keyword_rate(keywords: List[str], text: str) -> float:
    if not keywords:
        return 1.0
    norm_text = (text or "").translate(BN_TO_EN).lower()
    hits = 0
    for kw in keywords:
        norm_kw = str(kw).translate(BN_TO_EN).lower()
        if norm_kw in norm_text:
            hits += 1
    return hits / len(keywords)


def _check_json_schema(structured: Optional[Dict[str, Any]], query_type: str) -> Dict[str, bool]:
    if query_type != "ANSWERABLE":
        return {"json_valid": True, "regex_fallback_used": False}
    if not isinstance(structured, dict) or not structured.get("detailed_answer"):
        return {"json_valid": False, "regex_fallback_used": False}
    required_keys = {"detailed_answer", "cited_sections", "cited_cases", "suggested_next_steps"}
    has_keys = required_keys.issubset(set(structured.keys()))
    # Heuristic matching qa_graph.py regex fallback when all list fields are empty
    regex_fallback = (
        has_keys
        and structured.get("cited_sections") == []
        and structured.get("cited_cases") == []
        and structured.get("suggested_next_steps") == []
        and structured.get("suggested_follow_ups") == []
    )
    return {"json_valid": has_keys and not regex_fallback, "regex_fallback_used": regex_fallback}


async def evaluate_rag_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Runs a single RAG gold test case through qa_graph and computes quality metrics."""
    case_id = case["id"]
    t0 = time.perf_counter()
    try:
        state_in = {
            "messages": _to_lc_messages(case.get("history", [])),
            "user_id": 0,
            "user_role": case.get("user_role", "GENERAL"),
            "query": case["query"],
            "retrieved_docs": [],
            "citation_verified": False,
            "contract_id": None,
            "contract_context": None,
            "language": case.get("language", "bn"),
        }
        res = await qa_graph.ainvoke(state_in)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        actual_status = str(res.get("query_type") or ("ANSWERABLE" if res.get("retrieved_docs") else "UNKNOWN")).upper()
        expected_status = str(case.get("expected_status", "ANSWERABLE")).upper()
        router_match = actual_status == expected_status

        docs = res.get("retrieved_docs") or []
        structured = res.get("structured_response") or {}
        final_answer = str(res.get("final_answer") or "")
        cited_sections = structured.get("cited_sections") or []

        candidate_acts = [d.get("act", "") for d in docs if isinstance(d, dict) and d.get("source_type") != "precedent"]
        candidate_acts += [c.get("act", "") for c in cited_sections if isinstance(c, dict)]

        act_metrics = _compute_act_metrics(case.get("expected_acts", []), candidate_acts)
        sec_recall = _compute_section_recall(case.get("expected_sections", []), docs, cited_sections, final_answer)
        kw_rate = _compute_keyword_rate(case.get("must_include_keywords", []), final_answer)

        lang = str(case.get("language", "en")).lower()
        bn_ratio = _bengali_script_ratio(final_answer)
        bengali_script_ok = (bn_ratio >= 0.25) if lang in ("bn", "banglish") and final_answer else True

        schema_info = _check_json_schema(structured, actual_status)
        needs_case_law_match = (
            bool(res.get("needs_case_law", False)) == bool(case.get("expected_needs_case_law", False))
            if expected_status == "ANSWERABLE"
            else True
        )

        passed = (
            router_match
            and bengali_script_ok
            and (
                expected_status != "ANSWERABLE"
                or (act_metrics["act_hit_at_k"] > 0 and kw_rate >= 0.5)
            )
        )

        norm_text = final_answer.translate(BN_TO_EN).lower()
        missed_keywords = [
            str(kw) for kw in case.get("must_include_keywords", [])
            if str(kw).translate(BN_TO_EN).lower() not in norm_text
        ]

        return {
            "id": case_id,
            "suite": "rag",
            "category": case.get("category"),
            "language": lang,
            "context_length": case.get("context_length"),
            "status": "PASSED" if passed else "FAILED",
            "passed": passed,
            "latency_ms": latency_ms,
            "query": case["query"],
            "router_match": router_match,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "needs_case_law_match": needs_case_law_match,
            "act_hit_at_k": act_metrics["act_hit_at_k"],
            "act_mrr": round(act_metrics["act_mrr"], 4),
            "expected_acts": case.get("expected_acts", []),
            "retrieved_acts": list(dict.fromkeys(a for a in candidate_acts if a))[:8],
            "section_recall": round(sec_recall, 4),
            "expected_sections": case.get("expected_sections", []),
            "cited_sections": cited_sections[:6],
            "keyword_hit_rate": round(kw_rate, 4),
            "missed_keywords": missed_keywords,
            "bengali_script_ok": bengali_script_ok,
            "bengali_script_ratio": round(bn_ratio, 4),
            "json_valid": schema_info["json_valid"],
            "regex_fallback_used": schema_info["regex_fallback_used"],
            "retrieved_doc_count": len(docs),
            "answer_preview": final_answer[:1200],
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.exception(f"[rag_evaluator] Case {case_id} crashed: {exc}")
        return {
            "id": case_id,
            "suite": "rag",
            "status": "ERROR",
            "passed": False,
            "latency_ms": latency_ms,
            "error": str(exc),
        }


async def evaluate_rag_suite(
    cases: List[Dict[str, Any]],
    on_result=None,
    resume: Optional[Dict[str, Dict[str, Any]]] = None,
    max_concurrency: int = 3,
) -> Dict[str, Any]:
    """Evaluates RAG cases with bounded concurrency; streams each result via on_result."""
    import asyncio

    resume = resume or {}
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(case: Dict[str, Any]) -> Dict[str, Any]:
        if case["id"] in resume:
            return resume[case["id"]]
        async with sem:
            result = await evaluate_rag_case(case)
        if on_result:
            try:
                on_result(result)
            except Exception:
                logger.debug("on_result callback failed", exc_info=True)
        return result

    results = await asyncio.gather(*(_one(case) for case in cases))

    answerable = [r for r in results if r.get("expected_status") == "ANSWERABLE" and r.get("status") != "ERROR"]
    bn_cases = [r for r in results if r.get("language") in ("bn", "banglish") and r.get("status") != "ERROR"]

    by_language: Dict[str, Any] = {}
    for lang_key in ("bn", "banglish", "en"):
        lang_rows = [r for r in results if r.get("language") == lang_key and r.get("status") != "ERROR"]
        if lang_rows:
            lang_ans = [r for r in lang_rows if r.get("expected_status") == "ANSWERABLE"]
            by_language[lang_key] = {
                "total": len(lang_rows),
                "passed": sum(1 for r in lang_rows if r.get("passed")),
                "pass_rate": round(sum(1 for r in lang_rows if r.get("passed")) / len(lang_rows), 4),
                "act_hit_at_k": _avg([r.get("act_hit_at_k", 0.0) for r in lang_ans]),
                "keyword_hit_rate": _avg([r.get("keyword_hit_rate", 0.0) for r in lang_ans]),
                "json_valid_rate": _avg([1.0 if r.get("json_valid") else 0.0 for r in lang_ans]),
            }

    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "errors": sum(1 for r in results if r.get("status") == "ERROR"),
        "router_accuracy": _avg([1.0 if r.get("router_match") else 0.0 for r in results if r.get("status") != "ERROR"]),
        "act_hit_at_k": _avg([r.get("act_hit_at_k", 0.0) for r in answerable]),
        "act_mrr": _avg([r.get("act_mrr", 0.0) for r in answerable]),
        "section_recall": _avg([r.get("section_recall", 0.0) for r in answerable]),
        "keyword_hit_rate": _avg([r.get("keyword_hit_rate", 0.0) for r in answerable]),
        "bengali_script_quality_rate": _avg([1.0 if r.get("bengali_script_ok") else 0.0 for r in bn_cases]),
        "json_valid_rate": _avg([1.0 if r.get("json_valid") else 0.0 for r in answerable]),
        "regex_fallback_rate": _avg([1.0 if r.get("regex_fallback_used") else 0.0 for r in answerable]),
        "by_language": by_language,
    }
    return {"suite": "rag", "summary": summary, "cases": results}


def _avg(vals: List[float]) -> float:
    return round(sum(vals) / len(vals), 4) if vals else 0.0
