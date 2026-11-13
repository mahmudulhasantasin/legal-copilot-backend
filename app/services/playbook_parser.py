import json
import re
import logging
from typing import List, Dict, Any
from langchain_core.prompts import ChatPromptTemplate

# ✅ FIX 1: Remove direct ChatGoogleGenerativeAI and settings import
# from langchain_google_genai import ChatGoogleGenerativeAI
# from app.config import settings

# ✅ FIX 2: Import centralized LLM provider
from app.services.llm_provider import get_llm
from app.services.ingestion import doc_processor, extract_text_from_file

logger = logging.getLogger(__name__)

# Hard caps so a pathological playbook can never explode cost or the P1 audit budget
_MAX_PLAYBOOK_RULES = 40
_MAX_UNCOVERED_AREAS = 4
_MAX_SOURCE_TEXT_CHARS = 25000

_EXTRACT_SYSTEM = """You are a Legal Policy Engineer. Analyze the internal corporate policy/playbook document provided.
Extract all explicit contract rules, operational policies, liability caps, governing laws, and payment guidelines.

Output MUST strictly be a valid JSON array of objects conforming to this schema:
[
  {{
    "rule_id": "RULE-CAT-01",
    "category": "Payment | Liability | Termination | Governing Law | Confidentiality | Intellectual Property | Employment | Data Protection | General",
    "rule_description": "Clear statement of what is allowed or prohibited",
    "severity": "LOW | MODERATE | HIGH | CRITICAL",
    "suggested_redline": "Standard replacement clause if violated"
  }}
]

Rules:
1. Return ONLY the valid JSON array without commentary or explanation.
2. Ensure every rule contains actionable guidance for legal audits.
3. FULL COVERAGE: extract at least one rule from EVERY distinct policy section of the document — never skip a
   section (e.g. intellectual property, data protection, employment, approval authority) just because other
   sections are already covered.
4. If no clear rules are identified, return an empty array []."""

_COVERAGE_SYSTEM = """You are a Legal Policy QA Auditor. You are given a company policy/playbook document and the
list of rules ALREADY extracted from it. Your job: identify IMPORTANT policy areas present in the document that
have NO corresponding extracted rule.

An area counts as uncovered ONLY if the document contains explicit, actionable policy requirements for it that
none of the extracted rules capture. Use semantic judgment — an extracted rule may label its category differently
(e.g. an intellectual-property rule categorized as "General") — judge by what the rule actually says, not by the
category string. Never invent requirements that are not in the document. Minor/administrative statements are not
"important policy areas".

Return strictly a valid JSON array of objects:
[
  {{
    "category": "<short area name, e.g. 'Intellectual Property'>",
    "source_excerpt": "<verbatim excerpt from the document (max 1200 chars) containing the uncovered requirements>"
  }}
]
Return [] if every important policy area is already covered. No commentary, JSON array only."""

_RECOVERY_SYSTEM = """You are a Legal Policy Engineer. These excerpts come from corporate playbook sections whose
rules were missed in a first extraction pass. Extract all explicit, actionable contract rules from them.

Output MUST strictly be a valid JSON array of objects conforming to this schema:
[
  {{
    "rule_id": "",
    "category": "Payment | Liability | Termination | Governing Law | Confidentiality | Intellectual Property | Employment | Data Protection | General",
    "rule_description": "Clear statement of what is allowed or prohibited",
    "severity": "LOW | MODERATE | HIGH | CRITICAL",
    "suggested_redline": "Standard replacement clause if violated"
  }}
]

Rules:
1. Return ONLY the valid JSON array without commentary or explanation.
2. Leave "rule_id" empty — IDs are assigned programmatically after merge.
3. If an excerpt contains no actionable requirements, skip it."""


def _clean_llm_json(content: str) -> str:
    """Strips Markdown fences and extracts raw JSON string."""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


def _response_to_text(response: Any) -> str:
    """
    Extracts plain text from an LLM response. Newer langchain-google-genai
    versions return `content` as a list of typed blocks
    ([{'type': 'text', 'text': ...}]) — stringifying that directly produces
    an unparseable repr instead of JSON, which silently yielded 0 rules.
    """
    content = getattr(response, "content", None)
    if content is None:
        content = str(response)
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content).strip()


def _parse_json_array(text: str) -> List[Any]:
    """Parses an LLM reply into a JSON list; returns [] on any failure."""
    try:
        parsed = json.loads(_clean_llm_json(text))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        logger.warning("[playbook_parser] Failed to parse LLM output as JSON: %s", text[:200])
        return []


def _assign_rule_ids(rules: List[Dict[str, Any]], taken_ids: set) -> List[Dict[str, Any]]:
    """
    Ensures every rule has a unique rule_id: keeps valid existing IDs, and
    generates category-based IDs (RULE-INT-01) for missing/duplicated ones.
    """
    assigned: List[Dict[str, Any]] = []
    counters: Dict[str, int] = {}
    for rule in rules:
        rid = str(rule.get("rule_id") or "").strip()
        if not rid or rid in taken_ids or any(r.get("rule_id") == rid for r in assigned):
            abbr = re.sub(r"[^A-Za-z]", "", str(rule.get("category") or ""))[:3].upper() or "GEN"
            n = counters.get(abbr, 0) + 1
            candidate = f"RULE-{abbr}-{n:02d}"
            while candidate in taken_ids or any(r.get("rule_id") == candidate for r in assigned):
                n += 1
                counters[abbr] = n
                candidate = f"RULE-{abbr}-{n:02d}"
            counters[abbr] = n
            rid = candidate
            rule["rule_id"] = rid
        assigned.append(rule)
    return assigned


async def _extract_rules(llm: Any, text: str, system_prompt: str) -> List[Dict[str, Any]]:
    """One LLM pass: document text (or excerpts) -> list of rule dicts."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "Policy Text:\n\n{text}")
    ])
    response = await (prompt | llm).ainvoke({"text": text})
    candidates = [r for r in _parse_json_array(_response_to_text(response)) if isinstance(r, dict)]
    return [r for r in candidates if str(r.get("rule_description", "")).strip()]


async def _find_uncovered_areas(
    llm: Any, doc_text: str, rules: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Coverage audit: semantically compares extracted rules against the document and
    returns verbatim excerpts of policy areas that have no rule. LLM-judged (not
    string matching) because the extractor's category labels are unreliable.
    """
    rules_digest = "\n".join([
        f"- [{r.get('category', 'General')}] {str(r.get('rule_description', ''))[:120]}"
        for r in rules
    ]) or "(none extracted yet)"

    prompt = ChatPromptTemplate.from_messages([
        ("system", _COVERAGE_SYSTEM),
        ("human", "POLICY DOCUMENT:\n\n{doc}\n\nEXTRACTED RULES:\n\n{rules}")
    ])
    response = await (prompt | llm).ainvoke({"doc": doc_text, "rules": rules_digest})
    areas = [a for a in _parse_json_array(_response_to_text(response)) if isinstance(a, dict)]
    return [a for a in areas if str(a.get("source_excerpt", "")).strip()]


async def parse_playbook_to_rules(raw_text: str) -> List[Dict[str, Any]]:
    """
    Transforms raw policy text into structured compliance rules using centralized LLM.

    Production-grade 3-stage pipeline so ANY playbook gets complete coverage:
      1. Extract rules from the full document.
      2. Coverage audit — semantically find policy areas the extraction missed
         (LLM variance routinely drops a section, e.g. Intellectual Property).
      3. Recovery extraction on the missed excerpts, merged with unique rule IDs.

    Stages 2-3 are individually guarded: any failure degrades gracefully to
    stage-1 output (never worse than the old single-pass behavior).
    """
    if not raw_text:
        return []

    llm = get_llm()
    # Limit input size to prevent context overflow while keeping core policies
    truncated_text = raw_text[:_MAX_SOURCE_TEXT_CHARS]

    # Stage 1: full-document extraction
    try:
        rules = await _extract_rules(llm, truncated_text, _EXTRACT_SYSTEM)
    except Exception as exc:
        logger.error("[playbook_parser] Stage-1 extraction failed: %s", exc)
        rules = []

    # Stage 2 + 3: coverage audit and recovery (guarded)
    try:
        uncovered = await _find_uncovered_areas(llm, truncated_text, rules)
        if uncovered:
            logger.info(
                "[playbook_parser] Coverage audit found %d uncovered area(s): %s",
                len(uncovered), [a.get("category") for a in uncovered[:_MAX_UNCOVERED_AREAS]],
            )
            excerpts = "\n\n".join(
                f"### Policy area: {a.get('category', 'Unspecified')}\n{str(a.get('source_excerpt', ''))[:1200]}"
                for a in uncovered[:_MAX_UNCOVERED_AREAS]
            )
            recovered = await _extract_rules(llm, excerpts, _RECOVERY_SYSTEM)
            if recovered:
                taken_ids = {str(r.get("rule_id") or "").strip() for r in rules}
                rules.extend(_assign_rule_ids(recovered, taken_ids))
    except Exception as exc:
        logger.warning("[playbook_parser] Coverage recovery failed (keeping stage-1 rules): %s", exc)

    return _assign_rule_ids(rules, set())[:_MAX_PLAYBOOK_RULES]