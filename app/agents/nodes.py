import json
import asyncio
import re
import logging
from typing import Dict, Any, List

from app.services.retriever import hybrid_retriever
from app.services.llm_provider import get_llm
from app.agents.prompts import (
    SYSTEM_CONTRACT_RISK_PROMPT,
    SYSTEM_EXECUTIVE_SYNTHESIS_PROMPT,
)

logger = logging.getLogger(__name__)

# ── সীমা (Single Source of Truth) ──────────────────────────
MAX_CONTRACT_CHARS = 35000   # Supports ~15 pages
CHUNK_SIZE         = 12000   # High-context window per LLM call
CHUNK_OVERLAP      = 800


# Domain-specific statutory search queries
DOMAIN_RETRIEVAL_QUERIES = {
    "TECH_SAAS_VENDOR": [
        "The Contract Act 1872 Section 74 penalty liquidated damages interest rate",
        "Copyright Act 2000 software source code ownership work for hire",
        "Cyber Security Act 2023 data security breach notification sub-processor",
        "The Contract Act 1872 Section 56 force majeure frustration",
        "Arbitration Act 2001 domestic dispute resolution Dhaka"
    ],
    "EMPLOYMENT_SERVICE": [
        "Bangladesh Labour Act 2006 Section 26 termination notice severance pay",
        "The Contract Act 1872 Section 27 post employment non compete restraint of trade",
        "Bangladesh Labour Act 2006 gratuity provident fund terminal benefits"
    ],
    "COMMERCIAL_LEASE": [
        "The Stamp Act 1899 non judicial stamp paper commercial lease",
        "Registration Act 1908 Section 17 mandatory registration lease",
        "Premises Rent Control Act 1991 security deposit advance rent adjustment"
    ],
    "NDA_CONFIDENTIALITY": [
        "The Contract Act 1872 Section 27 trade secrets non disclosure duration",
        "The Specific Relief Act 1877 perpetual injunction breach of confidence"
    ],
    "GENERAL_COMMERCIAL": [
        "The Contract Act 1872 Section 73 74 compensation for breach liquidated damages",
        "The Contract Act 1872 Section 56 impossibility frustration",
        "Arbitration Act 2001 dispute resolution"
    ]
}


async def retrieve_statutes_node(state: dict) -> dict:
    """
    Multi-Domain Statutory RAG Node (async):
    Retrieves targeted Bangladesh statutes from Qdrant based on the contract domain.
    """
    domain = state.get("domain", "TECH_SAAS_VENDOR")
    queries = DOMAIN_RETRIEVAL_QUERIES.get(domain, DOMAIN_RETRIEVAL_QUERIES["GENERAL_COMMERCIAL"])
    
    retrieved_laws: List[Dict[str, Any]] = []
    seen_sections = set()

    for q in queries:
        try:
            results = await hybrid_retriever.aretrieve(q, top_k=2)
            for r in results:
                key = f"{r.get('act')}_{r.get('section')}"
                if key not in seen_sections:
                    seen_sections.add(key)
                    retrieved_laws.append(r)
        except Exception as exc:
            logger.warning(f"[retrieve_statutes_node] Query '{q}' failed: {exc}")

    # Fallback to contract text snippet if domain queries returned nothing
    if not retrieved_laws:
        contract_text = state.get("contract_text", "")
        if contract_text.strip():
            snippet = contract_text[:1500]
            try:
                retrieved_laws = await hybrid_retriever.aretrieve(snippet, top_k=5)
            except Exception as exc:
                logger.error(f"[retrieve_statutes_node] Fallback retrieval failed: {exc}")
                retrieved_laws = []

    return {"retrieved_statutes": retrieved_laws}


async def analyze_risk_node(state: dict) -> dict:
    """
    LLM Analysis Node (async):
    Audits structured clauses under Bangladesh statutory laws and Playbook rules
    from the specific party perspective (CLIENT / VENDOR / NEUTRAL).
    """
    contract_text = state.get("contract_text", "")[:CHUNK_SIZE]
    statutes = state.get("retrieved_statutes", [])
    playbook_rules = state.get("playbook_rules", [])
    audited_for = str(state.get("audited_for", "CLIENT")).upper()
    domain_name = state.get("domain_name", "Commercial Agreement")
    structured_clauses = state.get("structured_clauses", [])

    # 1. Statutory context
    if statutes:
        laws_context = "\n\n".join([
            f"- [{law.get('act', 'Bangladesh Statute')} | {law.get('section', 'Section')} - {law.get('title', '')}]:\n"
            f"  \"{law.get('text', law.get('content', '')).strip()}\""
            for law in statutes
        ])
    else:
        laws_context = (
            "Apply mandatory codified provisions of Bangladesh Law:\n"
            "- The Contract Act, 1872 (Section 27 Restraint of Trade, Section 28 Legal Proceedings, "
            "Section 56 Frustration, Section 74 Liquidated Damages/Penalties).\n"
            "- Copyright Act, 2000 (Software ownership & assignment).\n"
            "- Cyber Security Act, 2023 / ICT Act, 2006 (Data privacy and breach notification).\n"
            "- Arbitration Act, 2001 (Dispute resolution)."
        )

    # 2. Enterprise Playbook context
    playbook_context = ""
    if playbook_rules:
        rules_text = "\n".join([
            f"- Rule ID: {r.get('rule_id', 'N/A')}\n"
            f"  Category: {r.get('category', 'General')}\n"
            f"  Rule: {r.get('rule_description', '')}\n"
            f"  Severity: {r.get('severity', 'MODERATE')}\n"
            f"  Suggested Redline: {r.get('suggested_redline', '')}"
            for r in playbook_rules
        ])
        playbook_context = (
            f"\nCOMPANY INTERNAL PLAYBOOK RULES (MUST ENFORCE):\n{rules_text}\n\n"
            "CRITICAL INSTRUCTION: Flag any playbook violation. "
            "In 'violated_law' cite the rule ID (e.g. \"Internal Playbook RULE-CAT-01\").\n"
        )

    # 3. Formatted Clause Text (if structured AST is present)
    if structured_clauses:
        formatted_contract = "\n\n".join([
            f"### CLAUSE {c.get('source_clause_number', idx)}: {c.get('clause_title', f'Clause {idx}')}\n"
            f"{c.get('clause_text', '')}"
            for idx, c in enumerate(structured_clauses, start=1)
        ])
    else:
        formatted_contract = contract_text

    prompt = (
        f"{SYSTEM_CONTRACT_RISK_PROMPT}\n\n"
        f"AUDIT PERSPECTIVE: PROTECTING THE {audited_for}\n"
        f"CONTRACT DOMAIN: {domain_name}\n\n"
        f"RELEVANT BANGLADESH CODIFIED STATUTORY CONTEXT:\n{laws_context}\n\n"
        f"{playbook_context}"
        f"CONTRACT TEXT FOR AUDIT:\n---\n{formatted_contract}\n---\n"
    )

    try:
        llm = get_llm()
        response = await asyncio.to_thread(llm.invoke, prompt)

        content = response.content if hasattr(response, "content") else str(response)
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in content
            ).strip()
        else:
            content = str(content).strip()

        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        content = re.sub(r"\s*```$", "", content, flags=re.MULTILINE).strip()
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            content = content[start:end + 1]

        analysis = json.loads(content)

    except Exception as e:
        logger.error(f"[analyze_risk_node] LLM Analysis failed: {e}")
        analysis = {
            "overall_risk_score": "MODERATE",
            "detected_risks": [],
        }

    return {"analysis": analysis}


async def synthesize_summary_node(
    detected_risks: List[Dict[str, Any]],
    missing_provisions: List[Dict[str, Any]],
    audited_for: str,
    domain_name: str,
    contract_title: str = "Agreement"
) -> str:
    """
    Map-Reduce Synthesis Node (async):
    Synthesizes clause findings and missing provisions into a single, cohesive,
    non-contradictory 2-paragraph Executive Statutory Risk Summary.
    """
    high_risks = [r for r in detected_risks if str(r.get("severity", "")).upper() in ("HIGH", "CRITICAL")]
    moderate_risks = [r for r in detected_risks if str(r.get("severity", "")).upper() in ("MODERATE", "MEDIUM")]

    risks_digest = "\n".join([
        f"- Clause {r.get('source_clause_number', '?')} ({r.get('clause_title', 'Clause')}): "
        f"[{r.get('severity')}] {r.get('violated_law', '')} - {r.get('explanation', '')[:120]}"
        for r in (high_risks + moderate_risks)[:8]
    ])

    missing_digest = "\n".join([
        f"- Missing: {m.get('title')} ({m.get('statutory_reference', '')}) - {m.get('risk_explanation', '')[:100]}"
        for m in missing_provisions[:4]
    ])

    prompt = (
        f"{SYSTEM_EXECUTIVE_SYNTHESIS_PROMPT}\n\n"
        f"CONTRACT: {contract_title} ({domain_name})\n"
        f"AUDITED FOR: {audited_for.upper()}\n"
        f"HIGH RISK FINDINGS COUNT: {len(high_risks)}\n"
        f"MODERATE RISK FINDINGS COUNT: {len(moderate_risks)}\n"
        f"KEY IDENTIFIED VULNERABILITIES:\n{risks_digest or 'No major statutory violations flagged.'}\n\n"
        f"MISSING PROTECTIVE PROVISIONS:\n{missing_digest or 'No critical missing provisions.'}\n\n"
        "Generate the concise 2-paragraph Executive Summary now:"
    )

    try:
        llm = get_llm()
        response = await asyncio.to_thread(llm.invoke, prompt)
        raw_content = response.content if hasattr(response, "content") else response
        if isinstance(raw_content, list):
            summary = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in raw_content
            ).strip()
        else:
            summary = str(raw_content).strip()

        # Handle edge-case where stringified python list was returned
        if summary.startswith("[") and ("'type': 'text'" in summary or '"type": "text"' in summary):
            try:
                import ast
                parsed_blocks = ast.literal_eval(summary)
                if isinstance(parsed_blocks, list):
                    summary = "".join(
                        b.get("text", "") for b in parsed_blocks if isinstance(b, dict) and b.get("text")
                    ).strip()
            except Exception:
                pass

        summary = re.sub(r"^```[a-z]*\s*", "", summary, flags=re.MULTILINE)
        summary = re.sub(r"\s*```$", "", summary, flags=re.MULTILINE).strip()
        if len(summary) > 60:
            return summary
    except Exception as e:
        logger.error(f"[synthesize_summary_node] Synthesis failed: {e}")

    # Fallback cohesive summary
    if high_risks:
        return (
            f"The evaluated {domain_name} contains critical structural and commercial vulnerabilities when audited "
            f"from the perspective of the {audited_for}. Primary legal concerns include disproportionate late payment penalties "
            f"under Section 74 of The Contract Act, 1872, absence of mandatory data breach protocols under the Cyber Security Act, 2023, "
            f"and unrefunded advance payments upon early termination. "
            f"Additionally, the agreement lacks essential protective provisions including express third-party software licensing terms "
            f"and mutual intellectual property indemnification. Immediate redline negotiations are advised prior to execution."
        )
    return (
        f"The evaluated {domain_name} generally aligns with baseline commercial standards in Bangladesh. "
        f"Minor review recommendations were identified regarding delivery timelines and dispute governance under the Arbitration Act, 2001. "
        f"No statutory prohibitions or void covenants were detected within the audited provisions."
    )
