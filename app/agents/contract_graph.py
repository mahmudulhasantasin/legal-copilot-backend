import asyncio
import logging
import re
from typing import TypedDict, List, Dict, Any, Optional

from langgraph.graph import StateGraph, END
from app.agents.nodes import (
    retrieve_statutes_node,
    analyze_risk_node,
    synthesize_summary_node,
    MAX_CONTRACT_CHARS,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
)
from app.services.clause_segmenter import (
    extract_clauses_from_text,
    classify_contract_domain,
    detect_missing_mandatory_provisions,
    generate_stamp_duty_advisory,
)
from app.services.verifier import verifier

logger = logging.getLogger(__name__)

CONTRACT_TEXT_LIMIT = MAX_CONTRACT_CHARS


# ── LangGraph State Definition ─────────────────────────────
class ContractState(TypedDict):
    contract_text: str
    audited_for: str
    domain: str
    domain_name: str
    structured_clauses: List[Dict[str, Any]]
    playbook_rules: List[Dict[str, Any]]
    retrieved_statutes: List[Dict[str, Any]]
    analysis: Dict[str, Any]


_RISK_PRIORITY = {"LOW": 1, "MEDIUM": 2, "MODERATE": 2, "HIGH": 3, "CRITICAL": 3}


async def _verify_detected_statutes(detected_risks: List[Dict[str, Any]], retrieved_docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Verifies cited statutes against retrieved bdlaws documents and the MCP server.
    Attaches `is_statute_verified` and canonical formatting to each risk item.
    """
    for risk in detected_risks:
        law_str = str(risk.get("violated_law", ""))
        if not law_str or "commercial standard" in law_str.lower() or "general contract" in law_str.lower():
            risk["is_statute_verified"] = False
            risk["statute_verified_label"] = "Commercial Standard"
            continue

        # Extract possible section numbers and act names
        sec_matches = [s for s in law_str.split() if any(c.isdigit() for c in s)]
        act_hint = "The Contract Act, 1872" if "contract" in law_str.lower() else (
            "Bangladesh Labour Act, 2006" if "labour" in law_str.lower() else (
                "Copyright Act, 2000" if "copyright" in law_str.lower() else (
                    "Cyber Security Act, 2023" if "cyber" in law_str.lower() else (
                        "Arbitration Act, 2001" if "arbitration" in law_str.lower() else law_str
                    )
                )
            )
        )

        test_payload = {
            "cited_sections": [{"act": act_hint, "section": sec_matches[0] if sec_matches else "1"}],
            "cited_cases": []
        }

        try:
            verif_res = await verifier.verify_structured_response(test_payload, retrieved_docs)
            is_valid = verif_res.get("all_valid", False) or any(
                s.get("valid") for s in verif_res.get("verified_sections", [])
            )
        except Exception:
            is_valid = False

        risk["is_statute_verified"] = is_valid
        risk["statute_verified_label"] = "Verified against bdlaws" if is_valid else "Statutory Reference"

    return detected_risks


# ── Main Async Entry Point ───────────────────────────────────

async def analyze_contract_risk_async(
    contract_text: str,
    playbook_rules: Optional[List[Dict[str, Any]]] = None,
    audited_for: str = "CLIENT"
) -> Dict[str, Any]:
    """
    Production-grade contract audit pipeline:
      1. Structural AST clause extraction (retains source clause # and title)
      2. Contract domain classification & missing mandatory clause detection
      3. Multi-domain targeted statutory RAG retrieval from Qdrant
      4. Perspective-aware LLM risk reasoning (CLIENT vs VENDOR vs NEUTRAL)
      5. Real-time statutory citation verification (bdlaws / MCP)
      6. Map-reduce executive summary synthesis
      7. Bangladesh Stamp Duty advisory generation
    """
    rules = playbook_rules or []
    text = contract_text[:MAX_CONTRACT_CHARS]
    audited_for_clean = audited_for.upper() if audited_for else "CLIENT"

    # Step 1: Structural AST & Domain Classification
    classification = classify_contract_domain(text)
    domain = classification["domain"]
    domain_name = classification["domain_name"]

    clauses_ast = extract_clauses_from_text(text)
    logger.info(f"[contract_graph] Parsed {len(clauses_ast)} structured clause(s). Domain: {domain_name}")

    # Step 2: Missing Mandatory Clause Gap Analysis
    missing_provisions = detect_missing_mandatory_provisions(clauses_ast, domain, text)
    stamp_duty_advisory = generate_stamp_duty_advisory(domain, text)

    # Step 3: Targeted Multi-Domain Statutory Retrieval
    try:
        statute_state = await retrieve_statutes_node({
            "contract_text": text,
            "domain": domain,
            "playbook_rules": rules
        })
        retrieved_statutes = statute_state.get("retrieved_statutes", [])
    except Exception as exc:
        logger.error(f"[contract_graph] Statute retrieval failed: {exc}")
        retrieved_statutes = []

    # Step 4: Perspective-Aware LLM Audit
    # Batch clauses if text exceeds CHUNK_SIZE, else audit all clauses together
    all_detected_risks: List[Dict[str, Any]] = []
    overall_best_score = "LOW"

    if len(text) <= CHUNK_SIZE or len(clauses_ast) <= 15:
        # Single high-context cohesive audit
        node_res = await analyze_risk_node({
            "contract_text": text,
            "structured_clauses": clauses_ast,
            "retrieved_statutes": retrieved_statutes,
            "playbook_rules": rules,
            "audited_for": audited_for_clean,
            "domain_name": domain_name,
        })
        analysis = node_res.get("analysis", {})
        all_detected_risks = analysis.get("detected_risks", [])
        overall_best_score = str(analysis.get("overall_risk_score", "MODERATE")).upper()
    else:
        # Clause-batched audit to respect token limits while preserving clause boundaries
        batch_size = max(1, len(clauses_ast) // 2)
        clause_batches = [clauses_ast[i:i + batch_size] for i in range(0, len(clauses_ast), batch_size)]

        async def _audit_batch(batch: List[Dict[str, Any]]) -> Dict[str, Any]:
            batch_text = "\n\n".join([f"Clause {c.get('source_clause_number')}: {c.get('clause_text')}" for c in batch])
            res = await analyze_risk_node({
                "contract_text": batch_text,
                "structured_clauses": batch,
                "retrieved_statutes": retrieved_statutes,
                "playbook_rules": rules,
                "audited_for": audited_for_clean,
                "domain_name": domain_name,
            })
            return res.get("analysis", {})

        batch_results = await asyncio.gather(*[_audit_batch(b) for b in clause_batches])
        for b_res in batch_results:
            all_detected_risks.extend(b_res.get("detected_risks", []))
            b_score = str(b_res.get("overall_risk_score", "LOW")).upper()
            if _RISK_PRIORITY.get(b_score, 0) > _RISK_PRIORITY.get(overall_best_score, 0):
                overall_best_score = b_score

    # Dedup detected risks by source clause number and title
    seen_clauses = set()
    unique_risks: List[Dict[str, Any]] = []
    for r in all_detected_risks:
        c_num = str(r.get("source_clause_number") or r.get("clause_number") or "").strip()
        c_title = str(r.get("clause_title") or "").strip().lower()
        dedup_key = f"{c_num}_{c_title}"
        if dedup_key and dedup_key not in seen_clauses:
            seen_clauses.add(dedup_key)
            unique_risks.append(r)
        elif not dedup_key:
            unique_risks.append(r)

    # Step 5: Real-time Statutory Citation Verification
    verified_risks = await _verify_detected_statutes(unique_risks, retrieved_statutes)

    # Sort verified risks by natural clause number so the report follows contract sequence
    def _clause_sort_key(r: Dict[str, Any]) -> int:
        raw = str(r.get("source_clause_number") or r.get("clause_number") or "").strip()
        m = re.search(r'\d+', raw)
        return int(m.group()) if m else 999

    verified_risks.sort(key=_clause_sort_key)

    # Step 6: Map-Reduce Executive Summary Synthesis
    memo_summary = await synthesize_summary_node(
        detected_risks=verified_risks,
        missing_provisions=missing_provisions,
        audited_for=audited_for_clean,
        domain_name=domain_name,
        contract_title=classification.get("party_a", "Contract")
    )

    return {
        "overall_risk_score": overall_best_score,
        "memo_summary": memo_summary,
        "detected_risks": verified_risks,
        "missing_provisions": missing_provisions,
        "domain": domain,
        "domain_name": domain_name,
        "party_a": classification["party_a"],
        "party_b": classification["party_b"],
        "stamp_duty_advisory": stamp_duty_advisory,
        "audited_for": audited_for_clean,
        "total_parsed_clauses": len(clauses_ast),
    }


def analyze_contract_risk(
    contract_text: str,
    playbook_rules: Optional[List[Dict[str, Any]]] = None,
    audited_for: str = "CLIENT"
) -> Dict[str, Any]:
    """Synchronous fallback (scripts / testing use only)."""
    return asyncio.run(
        analyze_contract_risk_async(contract_text, playbook_rules, audited_for)
    )
