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


# NEW PROMPT: HyDE (Hypothetical Document Embeddings) and Legal Translation
ROUTER_PROMPT = """You are an expert Legal Query Reformulator for Bangladesh Law.
Your core task is to bridge the "Semantic Gap" between a user's layman terms and formal legal terminology.

CRITICAL INSTRUCTION: Do NOT just directly translate. EXPAND the core legal concept into formal synonyms to maximize vector search accuracy.
For example: If a user says "বিনা নোটিশে বের করে দেওয়া" (fired without notice), do not just write "বরখাস্ত". Expand it to encompass all possibilities: "মালিক কর্তৃক শ্রমিকের চাকুরীর অবসান, ছাঁটাই, ডিসচার্জ, বরখাস্ত ব্যতীত চাকরিচ্যুতি, Termination simpliciter, Retrenchment".

Analyze the user's query (and chat history if provided) and respond ONLY with a strict JSON object:
{
  "detected_acts": ["Official name of Act 1", "Official name of Act 2"],
  "inferred_sections": ["list", "of", "likely", "sections", "if", "you", "know", "them"],
  "optimized_query": "Expanded semantic search string containing formal legal concepts, legal mechanisms, and synonyms in both Bengali and English."
}"""


def _fallback(user_query: str, explicit: List[str]) -> AnalyzedQuery:
    return AnalyzedQuery(detected_acts=[], explicit_sections=explicit, inferred_sections=[], optimized_query=user_query[:250])

# NEW: Added 'history' parameter
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
        # Get the last 4 messages for recent context
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
        logger.exception(f"[analyze_query_dynamically] LLM analysis failed: {exc}")
        return _fallback(user_query, explicit)