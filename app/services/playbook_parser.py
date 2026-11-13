import json
import re
from typing import List, Dict, Any
from langchain_core.prompts import ChatPromptTemplate

# ✅ FIX 1: Remove direct ChatGoogleGenerativeAI and settings import
# from langchain_google_genai import ChatGoogleGenerativeAI
# from app.config import settings

# ✅ FIX 2: Import centralized LLM provider
from app.services.llm_provider import get_llm
from app.services.ingestion import doc_processor, extract_text_from_file


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


async def extract_playbook_text(filename: str, content: bytes) -> str:
    """
    Reuses ingestion.py logic to parse text from digital/scanned PDFs, DOCX, and text files.
    """
    # ✅ FIX 3: Added missing await (resolves AttributeError: 'coroutine' object has no attribute 'strip')
    raw_text = await extract_text_from_file(filename=filename, content=content)
    return raw_text.strip()


async def parse_playbook_to_rules(raw_text: str) -> List[Dict[str, Any]]:
    """
    Transforms raw policy text into structured compliance rules using centralized LLM.
    """
    if not raw_text:
        return []

    # ✅ FIX 4: Use get_llm() factory instead of hardcoding Gemini 
    # (Ensures Groq primary -> Gemini fallback works)
    llm = get_llm()

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are a Legal Policy Engineer. Analyze the internal corporate policy/playbook document provided.
Extract all explicit contract rules, operational policies, liability caps, governing laws, and payment guidelines.

Output MUST strictly be a valid JSON array of objects conforming to this schema:
[
  {{
    "rule_id": "RULE-CAT-01",
    "category": "Payment | Liability | Termination | Governing Law | Confidentiality | General",
    "rule_description": "Clear statement of what is allowed or prohibited",
    "severity": "LOW | MODERATE | HIGH | CRITICAL",
    "suggested_redline": "Standard replacement clause if violated"
  }}
]

Rules:
1. Return ONLY the valid JSON array without commentary or explanation.
2. Ensure every rule contains actionable guidance for legal audits.
3. If no clear rules are identified, return an empty array []."""
        ),
        ("human", "Company Policy Text:\n\n{text}")
    ])

    chain = prompt | llm

    # Limit input size to prevent context overflow while keeping core policies
    truncated_text = raw_text[:25000]
    response = await chain.ainvoke({"text": truncated_text})

    cleaned_json = _clean_llm_json(_response_to_text(response))
    try:
        rules = json.loads(cleaned_json)
        if isinstance(rules, list):
            return rules
        return []
    except json.JSONDecodeError:
        print(f"[ERROR] Failed to parse LLM output as JSON: {cleaned_json}")
        return []