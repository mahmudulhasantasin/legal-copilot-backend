import os
import json
import re
import asyncio
import logging
from typing import List, Dict, Any, Optional
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

# ── Post-extraction verification (Layer 1) ──────────────────────────────────
# Extraction quality guard: LLM paraphrase can silently drop or invent critical
# numbers ("advance must not exceed 30%" → "advance should be reasonable") or
# semantically drift from the source. Two complementary checks flag suspicious
# rules for admin review instead of trusting the extraction blindly:
#   1a. Deterministic critical-term check — every number/amount/duration cited in
#       a rule MUST appear verbatim (normalized) in the source playbook text.
#   1b. Local embedding similarity — rule vs source chunk cosine (fastembed e5,
#       no API call) catches paraphrase drift that string matching cannot.
# Rules are enriched with needs_review / review_reasons / source_similarity so
# the frontend can run an approve-edit-delete pass before relying on them.

_WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15",
    "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
    "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
    "seventy": "70", "eighty": "80", "ninety": "90", "hundred": "100",
}
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def _normalize_for_term_match(text: str) -> str:
    """Lowercase, word-numbers → digits, strip commas/parens, collapse spaces so
    'thirty (30) calendar days' and '30 days' normalize identically."""
    s = (text or "").lower().translate(_BN_DIGITS)
    s = re.sub(r"\b(" + "|".join(_WORD_NUMBERS) + r")\b", lambda m: _WORD_NUMBERS[m.group(1)], s)
    s = re.sub(r"[,\(\)\[\]]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_CRITICAL_TERM_RES = [
    re.compile(r"\d[\d.]*(?:\.\d+)?\s*%"),                                        # 30%, 12.5 %
    re.compile(r"(?:bdt|tk|taka|৳)\s*\d[\d.]*(?:\.\d+)?"),                        # BDT 300,000 / Tk 50000
    re.compile(r"\d[\d.]*(?:\.\d+)?\s*(?:lakh|lakhs|crore|crores|কোটি|লক্ষ)"),    # 5 lakh
    re.compile(r"\d[\d.]*(?:\.\d+)?\s*(?:calendar\s+|working\s+|business\s+)?(?:days?|months?|weeks?|years?)"),  # 30 days, 12 months
]


def _critical_term_violations(rule: Dict[str, Any], normalized_source: str) -> List[str]:
    """Critical terms cited in a rule that do NOT appear in the source playbook."""
    haystack = f"{rule.get('rule_description', '')} {rule.get('suggested_redline', '')}"
    norm_hay = _normalize_for_term_match(haystack)
    tokens = set()
    for regex in _CRITICAL_TERM_RES:
        for match in regex.findall(norm_hay):
            tokens.add(re.sub(r"\s+", " ", match).strip())
    return sorted(t for t in tokens if t not in normalized_source)


# Layer 1b runs on the EXISTING Qdrant Cloud connection via server-side inference
# (the same intfloat/multilingual-e5-small the laws collection uses — proven
# supported). Rationale (Render free tier): a local ONNX embedder (fastembed) risks
# OOM on 512MB instances and re-downloads its model on every restart (ephemeral
# disk). Instead a tiny temp collection is created per upload, chunks are upserted
# and rules queried with cloud-side embedding, and the collection is ALWAYS deleted.
# Set PLAYBOOK_SIMILARITY_CHECK=0 to disable (layer 1a still runs).
_SIMILARITY_E5_MODEL = "intfloat/multilingual-e5-small"
_E5_VECTOR_SIZE = 384

# Adaptive flagging: a rule is an outlier when its similarity drops this much
# below its own playbook's median score. Floor guards tiny rule sets.
_REVIEW_OUTLIER_MARGIN = 0.04
_ABSOLUTE_SIMILARITY_FLOOR = 0.80
_CHUNK_MAX_CHARS = 500   # max window for unbroken (no-newline) paragraphs


def _similarity_check_enabled() -> bool:
    return os.environ.get("PLAYBOOK_SIMILARITY_CHECK", "1").strip().lower() not in ("0", "false", "no")


def _chunk_text(text: str, max_chars: int = _CHUNK_MAX_CHARS) -> List[str]:
    """Dual-granularity chunks: every source line (rules citing one policy line
    match sharply) PLUS ~max_chars merged line groups (rules summarizing several
    adjacent policies match the combined context). Long unbroken lines split at
    max_chars."""
    lines: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        while len(line) > max_chars:
            lines.append(line[:max_chars])
            line = line[max_chars:]
        lines.append(line)

    merged: List[str] = []
    buf = ""
    for line in lines:
        buf = f"{buf} {line}".strip() if buf else line
        if len(buf) >= max_chars:
            merged.append(buf)
            buf = ""
    if buf:
        merged.append(buf)

    seen = set()
    chunks: List[str] = []
    for chunk in lines + merged:
        if chunk not in seen:
            seen.add(chunk)
            chunks.append(chunk)
    return chunks


def _similarity_scores_sync(rule_texts: List[str], chunk_texts: List[str]) -> List[float]:
    """
    Temp-collection similarity search on Qdrant Cloud (blocking client calls —
    caller wraps in asyncio.to_thread). Returns one best-match score per rule.
    """
    import uuid as _uuid
    from qdrant_client import models as _models
    from app.services.vector_store import vector_manager

    client = vector_manager.client
    coll = f"pb_verify_{_uuid.uuid4().hex[:10]}"
    try:
        client.create_collection(
            collection_name=coll,
            vectors_config=_models.VectorParams(size=_E5_VECTOR_SIZE, distance=_models.Distance.COSINE),
        )
        client.upsert(
            collection_name=coll,
            points=[
                _models.PointStruct(
                    id=idx,
                    # Symmetric "query: " prefixes on BOTH sides — e5's passage/query
                    # asymmetric convention compresses score separation badly here
                    vector=_models.Document(text=f"query: {chunk}", model=_SIMILARITY_E5_MODEL),
                    payload={"chunk": chunk[:200]},
                )
                for idx, chunk in enumerate(chunk_texts)
            ],
            wait=True,
        )
        scores: List[float] = []
        for rule_text in rule_texts:
            res = client.query_points(
                collection_name=coll,
                query=_models.Document(text=f"query: {rule_text}", model=_SIMILARITY_E5_MODEL),
                limit=1,
                with_payload=False,
            )
            scores.append(float(res.points[0].score) if res.points else 0.0)
        return scores
    finally:
        try:
            client.delete_collection(coll)
        except Exception:
            pass


async def _verify_rules_against_source(rules: List[Dict[str, Any]], raw_text: str) -> List[Dict[str, Any]]:
    """
    Enriches each rule with review metadata (guarded — on any internal failure the
    rules are returned unflagged so upload never breaks):
      - source_similarity: best similarity vs source chunks (omitted if layer 1b is
        disabled or unavailable)
      - needs_review + review_reasons: critical-term violations and/or low similarity

    Similarity threshold is ADAPTIVE: real LLM rule descriptions legitimately score
    across a range (0.87–0.93 observed) that shifts per playbook, so a rule is
    flagged when it falls well below its own playbook's median (outlier detection),
    with an absolute floor for small/catastrophic cases. Calibrated on:
      synthetic set  — faithful 0.94–0.98, semantic drift 0.89, bogus 0.80
      real playbook  — 9 genuine rules 0.894–0.931 (median 0.919), planted bogus 0.853
    """
    if not rules:
        return rules

    normalized_source = _normalize_for_term_match(raw_text[:_MAX_SOURCE_TEXT_CHARS])

    # Layer 1b: similarity via Qdrant cloud inference (optional, guarded).
    # Description only — the suggested_redline is LLM-drafted template text that
    # is not expected to appear in the source and only dilutes the score.
    similarity_scores: Optional[List[float]] = None
    if _similarity_check_enabled():
        rule_texts = [str(r.get("rule_description", "")).strip() for r in rules]
        chunks = _chunk_text(raw_text[:_MAX_SOURCE_TEXT_CHARS])
        if any(rule_texts) and chunks:
            try:
                similarity_scores = await asyncio.to_thread(_similarity_scores_sync, rule_texts, chunks)
            except Exception as exc:
                logger.warning("[playbook_parser] Similarity check unavailable (skipping layer 1b): %s", exc)

    if similarity_scores is not None:
        sims = similarity_scores
        if len(sims) >= 4:
            sorted_sims = sorted(sims)
            mid = len(sorted_sims) // 2
            median = sorted_sims[mid] if len(sorted_sims) % 2 else (sorted_sims[mid - 1] + sorted_sims[mid]) / 2
            effective_threshold = max(_ABSOLUTE_SIMILARITY_FLOOR, round(median - _REVIEW_OUTLIER_MARGIN, 3))
        else:
            effective_threshold = _ABSOLUTE_SIMILARITY_FLOOR
        logger.info("[playbook_parser] Adaptive similarity threshold: %.3f", effective_threshold)

    for idx, rule in enumerate(rules):
        reasons: List[str] = []
        violations = _critical_term_violations(rule, normalized_source)
        for tok in violations:
            reasons.append(f"Critical term '{tok}' cited in the rule was not found in the source playbook text.")

        if similarity_scores is not None:
            similarity = round(similarity_scores[idx], 3)
            rule["source_similarity"] = similarity
            if similarity < effective_threshold:
                reasons.append(
                    f"Low textual similarity to the source playbook ({similarity:.2f} < "
                    f"{effective_threshold:.2f}). The rule may be paraphrased, invented, or misplaced."
                )

        rule["needs_review"] = bool(reasons)
        rule["review_reasons"] = reasons

    flagged = sum(1 for r in rules if r.get("needs_review"))
    logger.info("[playbook_parser] Verification: %d/%d rule(s) flagged for admin review.", flagged, len(rules))
    return rules

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

    rules = _assign_rule_ids(rules, set())[:_MAX_PLAYBOOK_RULES]

    # Layer 1: deterministic + embedding verification against the source text
    try:
        rules = await _verify_rules_against_source(rules, raw_text)
    except Exception as exc:
        logger.warning("[playbook_parser] Rule verification failed (returning unflagged rules): %s", exc)

    return rules