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

# Pure boilerplate headings that never carry audit-relevant obligations
_BOILERPLATE_TITLES = {"signatures", "signature page", "witness", "witnesses", "execution"}


def _normalize_for_match(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for verbatim quote matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\u0980-\u09ff]+", " ", (s or "").lower())).strip()


def _ground_flagged_quotes(
    risks: List[Dict[str, Any]],
    clauses_ast: List[Dict[str, Any]],
    full_text: str,
) -> List[Dict[str, Any]]:
    """
    Anti-hallucination grounding: verifies each risk's `flagged_text` quote actually
    exists (verbatim, whitespace/punctuation-insensitive) in the source contract.
    Unverifiable quotes are replaced with the real clause text so the report never
    presents invented contract language.
    """
    clause_map: Dict[str, str] = {}
    for c in clauses_ast:
        num = str(c.get("source_clause_number") or "").strip()
        if num and c.get("clause_text"):
            clause_map[num] = c["clause_text"]

    full_norm = _normalize_for_match(full_text)

    for risk in risks:
        quote = str(risk.get("flagged_text") or "").strip()
        cnum = str(risk.get("source_clause_number") or risk.get("clause_number") or "").strip()
        quote_norm = _normalize_for_match(quote)

        grounded = False
        if len(quote_norm) >= 20:
            candidates = [full_norm]
            target_norm = _normalize_for_match(clause_map.get(cnum) or "")
            if target_norm:
                candidates.insert(0, target_norm)
            for cand in candidates:
                if quote_norm in cand:
                    grounded = True
                    break
                # Tolerate minor LLM edits at the tail: verify the informative prefix
                q_tokens = quote_norm.split()
                if len(q_tokens) > 12 and " ".join(q_tokens[:12]) in cand:
                    grounded = True
                    break

        if grounded:
            risk["is_quote_grounded"] = True
        else:
            risk["is_quote_grounded"] = False
            real_text = (clause_map.get(cnum) or "").strip()
            if real_text:
                risk["flagged_text"] = real_text[:400]
                note = (
                    "[Note: the originally flagged quote could not be verified verbatim in the "
                    "contract text; the actual clause text is shown instead.]"
                )
                explanation = str(risk.get("explanation") or "").rstrip()
                if note not in explanation:
                    risk["explanation"] = f"{explanation} {note}".strip()

    return risks


def _fill_clause_coverage(
    risks: List[Dict[str, Any]],
    clauses_ast: List[Dict[str, Any]],
    degraded: bool = False,
) -> List[Dict[str, Any]]:
    """
    Coverage fill: guarantees every parsed clause appears in the audit matrix.
    Parsed clauses with no LLM-detected risk are appended as COMPLIANT rows
    (pure signature/witness boilerplate is skipped).

    When `degraded` is set (LLM analysis failed entirely), unanalyzed clauses are
    marked NOT_ANALYZED / REVIEW_REQUIRED instead of COMPLIANT so a provider
    outage can never masquerade as a clean audit.
    """
    def _first_int(num_str: str) -> Optional[int]:
        m = re.search(r"\d+", num_str)
        return int(m.group()) if m else None

    covered = set()
    for r in risks:
        raw = str(r.get("source_clause_number") or r.get("clause_number") or "").strip()
        n = _first_int(raw)
        if n is not None:
            covered.add(n)

    for c in clauses_ast:
        cnum_raw = str(c.get("source_clause_number") or "").strip()
        n = _first_int(cnum_raw)
        title = str(c.get("clause_title") or "").strip()
        if n is not None and n in covered:
            continue
        if title.lower().rstrip(":.- ") in _BOILERPLATE_TITLES:
            continue
        body = (c.get("clause_text") or "").strip()
        if degraded:
            risks.append({
                "source_clause_number": cnum_raw or (str(n) if n is not None else ""),
                "clause_title": title or f"Clause {n}",
                "severity": "NOT_ANALYZED",
                "statutory_status": "REVIEW_REQUIRED",
                "flagged_text": body[:300],
                "violated_law": "",
                "explanation": "The automated AI analysis engine was temporarily unavailable, so this provision could not be audited. Manual legal review is required before relying on this report.",
                "recommendation": "",
                "is_compliant": False,
                "is_statute_verified": False,
                "statute_verified_label": "Not Verified",
                "is_quote_grounded": False,
            })
        else:
            risks.append({
                "source_clause_number": cnum_raw or (str(n) if n is not None else ""),
                "clause_title": title or f"Clause {n}",
                "severity": "COMPLIANT",
                "statutory_status": "FULLY_COMPLIANT",
                "flagged_text": body[:300],
                "violated_law": "Commercial Standard / General Contract Principle",
                "explanation": "No statutory violation or significant commercial risk was detected in this provision during the automated audit.",
                "recommendation": "",
                "is_compliant": True,
                "is_statute_verified": False,
                "statute_verified_label": "Commercial Standard",
                "is_quote_grounded": False,
            })

    return risks


_BN_TO_EN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# 4-digit years (act enactment years) must never be treated as section numbers
_YEAR_TOKEN_RE = re.compile(r"\b(?:1[7-9]\d{2}|20[0-4]\d)\b")
_SECTION_TOKEN_RE = re.compile(r"\b(\d{1,3}[A-Za-z]?)\b")

_ACT_HINTS = [
    ("contract", "The Contract Act, 1872"),
    ("labour", "Bangladesh Labour Act, 2006"),
    ("copyright", "Copyright Act, 2000"),
    ("cyber", "Cyber Security Act, 2023"),
    ("ict", "ICT Act, 2006"),
    ("arbitration", "Arbitration Act, 2001"),
    ("transfer of property", "The Transfer of Property Act, 1882"),
    ("registration", "The Registration Act, 1908"),
    ("partnership", "The Partnership Act, 1932"),
    ("sale of goods", "The Sale of Goods Act, 1930"),
    ("specific relief", "The Specific Relief Act, 1877"),
    ("stamp", "The Stamp Act, 1899"),
    ("negotiable instruments", "The Negotiable Instruments Act, 1881"),
]


def _extract_cited_sections(law_str: str) -> List[str]:
    """
    Extracts genuine section numbers from a `violated_law` string.
    Act years (1872, 2006, ...) are stripped first so they are never
    mistaken for sections; Bengali digits are normalized.
    """
    normalized = _YEAR_TOKEN_RE.sub(" ", law_str.translate(_BN_TO_EN_DIGITS))
    sections: List[str] = []
    for token in _SECTION_TOKEN_RE.findall(normalized):
        if token not in sections:
            sections.append(token)
        if len(sections) >= 4:
            break
    return sections


def _resolve_act_hint(law_str: str) -> str:
    law_lower = law_str.lower()
    for keyword, canonical in _ACT_HINTS:
        if keyword in law_lower:
            return canonical
    return law_str.strip() or "Bangladesh Statute"


async def _verify_detected_statutes(
    detected_risks: List[Dict[str, Any]],
    retrieved_docs: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Verifies cited statutes against retrieved bdlaws documents and the MCP server.
    Attaches `is_statute_verified` and canonical formatting to each risk item.

    Sections are extracted with year-stripping (the old logic sent the act year
    e.g. "1872" as the section number, invalidating every real citation), and
    (act, section) pairs are deduplicated so a 17-risk audit makes only a handful
    of MCP round trips instead of one per risk.
    """
    # Collect unique (act, section) verification tasks first
    unique_pairs: List[tuple] = []
    risk_pairs: List[List[tuple]] = []

    for risk in detected_risks:
        law_str = str(risk.get("violated_law", ""))
        law_lower = law_str.lower()

        if "playbook" in law_lower or re.search(r"\brule-[a-z0-9\-]+\b", law_lower):
            risk["is_statute_verified"] = True
            risk["statute_verified_label"] = "Company Playbook Rule"
            risk_pairs.append([])
            continue

        if not law_str.strip() or "commercial standard" in law_lower or "general contract" in law_lower:
            risk["is_statute_verified"] = False
            risk["statute_verified_label"] = "Commercial Standard"
            risk_pairs.append([])
            continue

        act_hint = _resolve_act_hint(law_str)
        sections = _extract_cited_sections(law_str)
        pairs = [(act_hint, sec) for sec in sections]
        for p in pairs:
            if p not in unique_pairs:
                unique_pairs.append(p)
        risk_pairs.append(pairs)

    # Verify each unique pair once, concurrently (bounded), with a timeout so a
    # cold Render instance cannot stall the whole audit.
    verification_results: Dict[tuple, bool] = {}
    sem = asyncio.Semaphore(4)

    async def _verify_pair(act: str, section: str) -> None:
        key = (act, section)
        if key in verification_results:
            return
        async with sem:
            try:
                verif_res = await asyncio.wait_for(
                    verifier.verify_structured_response(
                        {
                            "cited_sections": [{"act": act, "section": section}],
                            "cited_cases": [],
                        },
                        retrieved_docs,
                    ),
                    timeout=25.0,
                )
                is_valid = verif_res.get("all_valid", False) or any(
                    s.get("valid") for s in verif_res.get("verified_sections", [])
                )
            except Exception as exc:
                logger.warning(f"[contract_graph] MCP verify failed for {act} §{section}: {exc}")
                is_valid = False
        verification_results[key] = is_valid

    if unique_pairs:
        await asyncio.gather(*[_verify_pair(act, sec) for act, sec in unique_pairs])

    # Apply results: a risk counts as verified when ANY of its cited sections is real
    for risk, pairs in zip(detected_risks, risk_pairs):
        if not pairs:
            continue  # label already set above
        is_valid = any(verification_results.get(p, False) for p in pairs)
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
    llm_degraded = False

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
        if analysis.get("llm_failed"):
            llm_degraded = True
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
            if b_res.get("llm_failed"):
                llm_degraded = True
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

    # Step 5b: Anti-hallucination grounding of flagged quotes
    verified_risks = _ground_flagged_quotes(verified_risks, clauses_ast, text)

    # Step 5c: Coverage fill — one audit row per parsed clause
    # In degraded mode (LLM outage) unanalyzed clauses are marked NOT_ANALYZED, never COMPLIANT
    verified_risks = _fill_clause_coverage(verified_risks, clauses_ast, degraded=llm_degraded)

    if llm_degraded:
        logger.error("[contract_graph] LLM audit degraded — unanalyzed clauses marked NOT_ANALYZED.")
        overall_best_score = "MODERATE" if overall_best_score == "LOW" else overall_best_score

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

    if llm_degraded:
        memo_summary = (
            "⚠️ INCOMPLETE AUDIT: The AI analysis engine was temporarily unavailable while auditing this "
            "contract, so the provisions marked \"NOT_ANALYZED\" below were not reviewed and no risk "
            "conclusions can be drawn for them. Please re-run the audit or seek manual legal review "
            "before relying on this memo.\n\n" + str(memo_summary)
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
        "llm_degraded": llm_degraded,
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
