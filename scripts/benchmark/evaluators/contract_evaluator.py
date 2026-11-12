"""Contract Audit & Enterprise Playbook evaluator against contract_cases.json."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from app.agents.contract_graph import analyze_contract_risk_async
from app.services.clause_segmenter import (
    classify_contract_domain,
    detect_missing_mandatory_provisions,
    extract_clauses_from_text,
)
from app.services.legal_normalizer import BN_TO_EN

logger = logging.getLogger(__name__)


def _norm_clause_num(val: Any) -> str:
    s = str(val or "").translate(BN_TO_EN).strip()
    m = re.search(r"\d+", s)
    return m.group(0) if m else s.lower()


def _match_violation(expected: Dict[str, Any], detected_risks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    exp_num = _norm_clause_num(expected.get("clause_number"))
    for r in detected_risks:
        c_num = _norm_clause_num(r.get("source_clause_number") or r.get("clause_number"))
        if exp_num and c_num == exp_num:
            return r
    return None


def _has_summary_contradiction(memo_summary: str, detected_risks: List[Dict[str, Any]]) -> bool:
    high_or_crit = [
        r for r in detected_risks
        if str(r.get("severity", "")).upper() in ("HIGH", "CRITICAL")
    ]
    if not high_or_crit:
        return False
    lower = (memo_summary or "").lower()
    contradictory_phrases = (
        "no violations",
        "no risk",
        "fully compliant",
        "zero violations",
        "no major statutory violations",
    )
    return any(p in lower for p in contradictory_phrases)


def _evaluate_deterministic_checks(case: Dict[str, Any]) -> Dict[str, Any]:
    text = case["contract_text"]
    cls_res = classify_contract_domain(text)
    clauses_ast = extract_clauses_from_text(text)
    missing = detect_missing_mandatory_provisions(clauses_ast, cls_res["domain"], text)

    expected_domain = case.get("domain", "")
    domain_match = cls_res["domain"] == expected_domain

    exp_missing = set(case.get("expected_missing_provisions", []))
    actual_missing = {m.get("provision_id") or m.get("title", "") for m in missing}
    missing_recall = (
        len(exp_missing.intersection(actual_missing)) / len(exp_missing)
        if exp_missing
        else 1.0
    )
    return {
        "domain_match": domain_match,
        "predicted_domain": cls_res["domain"],
        "parsed_clause_count": len(clauses_ast),
        "missing_provisions_recall": round(missing_recall, 4),
    }


async def evaluate_contract_case(case: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """Evaluates a contract audit case (or runs offline AST/domain validation in dry_run)."""
    case_id = case["id"]
    t0 = time.perf_counter()
    try:
        det = _evaluate_deterministic_checks(case)
        if dry_run:
            latency_ms = int((time.perf_counter() - t0) * 1000)
            passed = det["domain_match"] and det["parsed_clause_count"] >= 1
            return {
                "id": case_id,
                "suite": "contract",
                "mode": "dry_run",
                "contract_title": case.get("contract_title"),
                "domain": case.get("domain"),
                "audited_for": case.get("audited_for"),
                "status": "PASSED" if passed else "FAILED",
                "passed": passed,
                "latency_ms": latency_ms,
                **det,
            }

        res = await analyze_contract_risk_async(
            contract_text=case["contract_text"],
            playbook_rules=case.get("playbook_rules", []),
            audited_for=case.get("audited_for", "CLIENT"),
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)

        actual_overall = str(res.get("overall_risk_score", "")).upper()
        expected_overall = str(case.get("expected_overall_risk", "")).upper()
        overall_risk_match = actual_overall == expected_overall

        detected_risks = res.get("detected_risks") or []
        exp_violations = case.get("expected_violations", [])

        tp = 0
        sev_hits = 0
        stat_hits = 0
        kw_hits = 0

        for ev in exp_violations:
            matched = _match_violation(ev, detected_risks)
            if matched:
                tp += 1
                if str(matched.get("severity", "")).upper() == str(ev.get("expected_severity", "")).upper():
                    sev_hits += 1
                if str(matched.get("statutory_status", "")).upper() == str(ev.get("expected_statutory_status", "")).upper():
                    stat_hits += 1
                blob = " ".join([
                    str(matched.get("violated_law", "")),
                    str(matched.get("statute_verified_label", "")),
                    str(matched.get("explanation", "")),
                ]).translate(BN_TO_EN).lower()
                exp_kws = [str(k).translate(BN_TO_EN).lower() for k in ev.get("expected_law_keywords", [])]
                if not exp_kws or any(k in blob for k in exp_kws):
                    kw_hits += 1

        if exp_violations:
            recall = tp / len(exp_violations)
            flagged_noncompliant = [
                r for r in detected_risks
                if str(r.get("severity", "")).upper() in ("HIGH", "MODERATE", "CRITICAL")
                and not r.get("is_compliant", False)
            ]
            precision = min(1.0, tp / len(flagged_noncompliant)) if flagged_noncompliant else 0.0
            f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        else:
            # Compliant baseline contract: passing means no HIGH risk false positives
            high_fp = [
                r for r in detected_risks
                if str(r.get("severity", "")).upper() in ("HIGH", "CRITICAL")
                and not r.get("is_compliant", False)
            ]
            recall = 1.0 if not high_fp else 0.0
            precision = recall
            f1 = recall

        has_playbook = bool(case.get("playbook_rules"))
        if has_playbook:
            playbook_risks = [
                r for r in detected_risks
                if r.get("statute_verified_label") == "Company Playbook Rule"
                or "rule-" in str(r.get("violated_law", "")).lower()
                or "playbook" in str(r.get("violated_law", "")).lower()
            ]
            playbook_detected = len(playbook_risks) > 0
            playbook_label_ok = any(
                r.get("is_statute_verified") is True
                and r.get("statute_verified_label") == "Company Playbook Rule"
                for r in playbook_risks
            )
        else:
            playbook_detected = True
            playbook_label_ok = True

        contradiction = _has_summary_contradiction(res.get("memo_summary", ""), detected_risks)

        passed = (
            det["domain_match"]
            and overall_risk_match
            and (recall >= 0.5)
            and playbook_label_ok
            and not contradiction
        )

        missed_clauses = [
            str(ev.get("clause_number"))
            for ev in exp_violations
            if not _match_violation(ev, detected_risks)
        ]
        risks_summary = [
            {
                "clause": r.get("source_clause_number") or r.get("clause_number"),
                "title": r.get("clause_title", ""),
                "severity": r.get("severity", ""),
                "statutory_status": r.get("statutory_status", ""),
                "violated_law": r.get("violated_law", ""),
                "verified_label": r.get("statute_verified_label", ""),
                "explanation_preview": str(r.get("explanation", ""))[:240],
            }
            for r in detected_risks[:20]
        ]

        return {
            "id": case_id,
            "suite": "contract",
            "contract_title": case.get("contract_title"),
            "domain": case.get("domain"),
            "audited_for": case.get("audited_for"),
            "has_playbook": has_playbook,
            "status": "PASSED" if passed else "FAILED",
            "passed": passed,
            "latency_ms": latency_ms,
            **det,
            "overall_risk_match": overall_risk_match,
            "expected_overall_risk": expected_overall,
            "actual_overall_risk": actual_overall,
            "expected_violation_clauses": [str(ev.get("clause_number")) for ev in exp_violations],
            "missed_clauses": missed_clauses,
            "clause_recall": round(recall, 4),
            "clause_precision": round(precision, 4),
            "clause_f1": round(f1, 4),
            "severity_accuracy": round(sev_hits / tp, 4) if tp else (1.0 if not exp_violations else 0.0),
            "statutory_status_accuracy": round(stat_hits / tp, 4) if tp else (1.0 if not exp_violations else 0.0),
            "law_keyword_accuracy": round(kw_hits / tp, 4) if tp else (1.0 if not exp_violations else 0.0),
            "playbook_detected": playbook_detected,
            "playbook_label_ok": playbook_label_ok,
            "playbook_hits": len(playbook_risks) if has_playbook else 0,
            "summary_contradiction": contradiction,
            "detected_risk_count": len(detected_risks),
            "detected_risks_summary": risks_summary,
            "memo_summary_preview": str(res.get("memo_summary", ""))[:700],
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        logger.exception(f"[contract_evaluator] Case {case_id} crashed: {exc}")
        return {
            "id": case_id,
            "suite": "contract",
            "status": "ERROR",
            "passed": False,
            "latency_ms": latency_ms,
            "error": str(exc),
        }


async def evaluate_contract_suite(
    cases: List[Dict[str, Any]],
    dry_run: bool = False,
    on_result=None,
    resume: Optional[Dict[str, Dict[str, Any]]] = None,
    max_concurrency: int = 3,
) -> Dict[str, Any]:
    """Evaluates contract cases with bounded concurrency; streams each result via on_result."""
    import asyncio

    resume = resume or {}
    sem = asyncio.Semaphore(max_concurrency)

    async def _one(case: Dict[str, Any]) -> Dict[str, Any]:
        if case["id"] in resume:
            return resume[case["id"]]
        async with sem:
            result = await evaluate_contract_case(case, dry_run=dry_run)
        if on_result:
            try:
                on_result(result)
            except Exception:
                logger.debug("on_result callback failed", exc_info=True)
        return result

    results = await asyncio.gather(*(_one(case) for case in cases))

    ok = [r for r in results if r.get("status") != "ERROR"]
    pb = [r for r in ok if r.get("has_playbook")]
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "errors": sum(1 for r in results if r.get("status") == "ERROR"),
        "domain_accuracy": _avg([1.0 if r.get("domain_match") else 0.0 for r in ok]),
    }
    if not dry_run:
        summary.update({
            "overall_risk_accuracy": _avg([1.0 if r.get("overall_risk_match") else 0.0 for r in ok]),
            "clause_f1": _avg([r.get("clause_f1", 0.0) for r in ok]),
            "statutory_status_accuracy": _avg([r.get("statutory_status_accuracy", 0.0) for r in ok]),
            "contradiction_rate": _avg([1.0 if r.get("summary_contradiction") else 0.0 for r in ok]),
            "playbook_detection_rate": _avg([1.0 if r.get("playbook_label_ok") else 0.0 for r in pb]),
        })
    return {"suite": "contract", "summary": summary, "cases": results}


def _avg(vals: List[float]) -> float:
    return round(sum(vals) / len(vals), 4) if vals else 0.0
