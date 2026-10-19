import json
import logging
import re
from typing import List, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

BN_TO_EN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
MAX_INFERRED = 6

class AnalyzedQuery(BaseModel):
    detected_acts: List[str] = Field(default_factory=list)
    explicit_sections: List[str] = Field(default_factory=list)
    inferred_sections: List[str] = Field(default_factory=list)
    optimized_query: str = Field(..., description="Dense legal keywords for vector search")

_SEC_LIST = re.compile(r"(?:ধারাসমূহ|ধারা|সেকশন|sections?|sec\.?)[\s\-:]*((?:[0-9]+[A-Za-zক-চ]?(?:\s*(?:,|ও|এবং|and|&)\s*)?)+)", re.IGNORECASE)
_SEC_POSTFIX = re.compile(r"([0-9]+[A-Za-zক-চ]?)\s*(?:নং\s*)?ধারা")
_NUM = re.compile(r"[0-9]+[A-Za-zক-চ]?")

def _looks_like_year(token: str) -> bool:
    return token.isdigit() and len(token) == 4 and 1700 <= int(token) <= 2100

def _extract_explicit_sections(query: str) -> List[str]:
    normalized = query.translate(BN_TO_EN_DIGITS)
    found: List[str] = []
    for m in _SEC_LIST.finditer(normalized): found.extend(_NUM.findall(m.group(1)))
    for m in _SEC_POSTFIX.finditer(normalized): found.append(m.group(1))
    return list(dict.fromkeys(t for t in found if not _looks_like_year(t)))

def _content_to_text(content) -> str:
    if isinstance(content, str): return content
    if isinstance(content, list): return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)

def _parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start: raise ValueError("no JSON object in LLM output")
    return json.loads(text[start:end + 1])


# ✅ NEW PROMPT: আরও কঠোর এবং কী-ওয়ার্ড ফোকাসড
ROUTER_PROMPT = """You are an expert Legal Query Reformulator for Bangladesh Law.
Your core task is to bridge the "Semantic Gap" between a user's layman terms and formal legal terminology.

CRITICAL RULES:
1. If the user mentions "contract", "চুক্তি", or "vhong" (breach), you MUST detect "The Contract Act, 1872" as the act.
2. If the user mentions "labour", "শ্রম", "chakri", or "employee", detect "Bangladesh Labour Act, 2006".
3. NEVER hallucinate sections if you are not 100% sure. Leave `inferred_sections` empty if unsure.
4. EXPAND the query with formal synonyms (e.g., breach -> termination, damages, compensation, rescission).

Analyze the user's query (and chat history if provided) and respond ONLY with a strict JSON object:
{
  "detected_acts": ["Official name of Act"],
  "inferred_sections": [],
  "optimized_query": "Expanded semantic search string in both Bengali and English."
}"""


# ✅ SMART FALLBACK: LLM ফেইল করলে কী-ওয়ার্ড দেখে আইন অনুমান করবে
def _fallback(user_query: str, explicit: List[str]) -> AnalyzedQuery:
    detected_acts = []
    q_lower = user_query.lower()
    
    if "contract" in q_lower or "চুক্তি" in q_lower or "vhong" in q_lower:
        detected_acts.append("The Contract Act, 1872")
    if "labour" in q_lower or "শ্রম" in q_lower or "chakri" in q_lower:
        detected_acts.append("Bangladesh Labour Act, 2006")
    if "penal" in q_lower or "crime" in q_lower or "শাস্তি" in q_lower:
        detected_acts.append("The Penal Code, 1860")

    return AnalyzedQuery(
        detected_acts=detected_acts, 
        explicit_sections=explicit, 
        inferred_sections=[], 
        optimized_query=user_query[:250]
    )


def analyze_query_dynamically(user_query: str, history: list = None, llm_instance=None) -> AnalyzedQuery:
    explicit = _extract_explicit_sections(user_query)

    if llm_instance is None:
        try:
            from app.services.llm_provider import get_llm
            llm_instance = get_llm()
        except Exception:
            return _fallback(user_query, explicit)

    # Format Chat History for LLM
    history_context = ""
    if history:
        history_text = "\n".join([f"{msg.type}: {msg.content}" for msg in history[-4:]])
        history_context = f"Recent Chat History:\n{history_text}\n\n"

    final_prompt = f"{history_context}Current User Query:\n{user_query}"

    try:
        response = llm_instance.invoke([
            SystemMessage(content=ROUTER_PROMPT),
            HumanMessage(content=final_prompt),
        ])
        raw = response.content if hasattr(response, "content") else response
        data = _parse_json_object(_content_to_text(raw))

        acts_raw = data.get("detected_acts", [])
        if not isinstance(acts_raw, list): acts_raw = [acts_raw]
        acts = [a.strip() for a in acts_raw if isinstance(a, str) and a.strip().lower() not in ("", "null", "none")]
        acts = list(dict.fromkeys(acts))

        inferred = []
        for s in (data.get("inferred_sections") or []):
            s = str(s).translate(BN_TO_EN_DIGITS).strip()
            if s and s not in inferred and s not in explicit: inferred.append(s)

        result = AnalyzedQuery(
            detected_acts=acts,
            explicit_sections=explicit,
            inferred_sections=inferred[:MAX_INFERRED],
            optimized_query=data.get("optimized_query") or user_query[:250],
        )
        logger.info(f"[analyzer] acts={result.detected_acts} optimized_query='{result.optimized_query}'")
        return result
    except Exception as exc:
        logger.exception(f"[analyze_query_dynamically] LLM analysis failed (likely 429). Using fallback.")
        return _fallback(user_query, explicit)