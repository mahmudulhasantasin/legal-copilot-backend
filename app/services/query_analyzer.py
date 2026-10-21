import json
import logging
import re
from typing import List
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


_SEC_LIST = re.compile(
    r"(?:ধারাসমূহ|ধারা|সেকশন|sections?|sec\.?)[\s\-:]*((?:[0-9]+[A-Za-zক-চ]?(?:\s*(?:,|ও|এবং|and|&)\s*)?)+)",
    re.IGNORECASE,
)
_SEC_POSTFIX = re.compile(r"([0-9]+[A-Za-zক-চ]?)\s*(?:নং\s*)?ধারা")
_NUM = re.compile(r"[0-9]+[A-Za-zক-চ]?")


def _looks_like_year(token: str) -> bool:
    return token.isdigit() and len(token) == 4 and 1700 <= int(token) <= 2100


def _extract_explicit_sections(query: str) -> List[str]:
    normalized = query.translate(BN_TO_EN_DIGITS)
    found: List[str] = []
    for m in _SEC_LIST.finditer(normalized):
        found.extend(_NUM.findall(m.group(1)))
    for m in _SEC_POSTFIX.finditer(normalized):
        found.append(m.group(1))
    return list(dict.fromkeys(t for t in found if not _looks_like_year(t)))


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n?```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in LLM output")
    return json.loads(text[start:end + 1])


# ✅ নতুন ইউনিভার্সাল প্রম্পট: LLM-কে হুবহু নাম বলতে বাধ্য করা হয়নি
ROUTER_PROMPT = """You are an expert Legal Query Reformulator for Bangladesh Law.
Your core task is to analyze the user's query and extract the Act names and Section numbers.

CRITICAL RULES FOR ACT EXTRACTION:
1. Do NOT guess the full official title (like "The Penal Code, 1860"). Just extract the core English or Bengali keywords the user used.
   - If user asks about Constitution/equality/fundamental rights -> return ["Constitution"]
   - If user asks about Penal Code/murder/theft/crime -> return ["Penal Code"]
   - If user asks about Labour Act/job/termination -> return ["Labour Act"]
   - If user asks about Contract Act/agreement/breach -> return ["Contract Act"]
   - If user asks about NI Act/cheque dishonor -> return ["Negotiable Instruments Act"]
   - If user asks about CPC/civil suit -> return ["Code of Civil Procedure"]
   - If user asks about CrPC/bail/FIR -> return ["Code of Criminal Procedure"]
2. Only include `detected_acts` if the query clearly relates to a specific Law/Act. If unsure or if multiple unknown laws might apply, leave it empty `[]`.
3. If the user specifies a section or article number (e.g., "২৭ নং অনুচ্ছেদ", "Section 138"), add it to `explicit_sections`.
4. Only add `inferred_sections` when you are highly confident (e.g., "চেক ডিজঅনার" -> infer "138", "সমতার অধিকার" -> infer "27", "28").
5. In `optimized_query`, write a rich semantic search string containing both English and Bengali legal terms to maximize vector search recall. Include the exact words the user used.

Respond ONLY with a strict JSON object:
{
  "detected_acts": [],
  "explicit_sections": [],
  "inferred_sections": [],
  "optimized_query": "Expanded semantic search string in both Bengali and English."
}"""


def _fallback(user_query: str, explicit: List[str]) -> AnalyzedQuery:
    detected_acts = []
    inferred_secs = []
    q_lower = user_query.lower()
    
    # ✅ Fallback-এও নতুন কী-ওয়ার্ড যোগ করা হলো
    if any(k in q_lower for k in ("constitution", "সংবিধান", "মৌলিক অধিকার", "fundamental rights")):
        detected_acts.append("Constitution")
    if any(k in q_lower for k in ("contract", "চুক্তি", "vhong", "ভঙ্গ", "agreement")):
        detected_acts.append("Contract Act")
    if any(k in q_lower for k in ("labour", "labor", "শ্রম", "chakri", "চাকরি", "বেতন", "শ্রমিক", "ছাঁটাই")):
        detected_acts.append("Labour Act")
    if any(k in q_lower for k in ("penal", "crime", "শাস্তি", "খুন", "চুরি", "প্রতারণা", "হুমকি", "মারামারি")):
        detected_acts.append("Penal Code")
    if any(k in q_lower for k in ("bail", "arrest", "জামিন", "গ্রেপ্তার", "মামলা", "থানা", "জিডি", "এজাহার")):
        detected_acts.append("Code of Criminal Procedure")
    if any(k in q_lower for k in ("cheque", "চেক", "ডিজঅনার", "dishonor", "dishonour")):
        detected_acts.append("Negotiable Instruments Act")
        inferred_secs.append("138")

    return AnalyzedQuery(
        detected_acts=detected_acts, 
        explicit_sections=explicit, 
        inferred_sections=inferred_secs, 
        optimized_query=user_query[:300]
    )


def analyze_query_dynamically(user_query: str, history: list = None, llm_instance=None) -> AnalyzedQuery:
    explicit = _extract_explicit_sections(user_query)

    if llm_instance is None:
        try:
            from app.services.llm_provider import get_llm
            llm_instance = get_llm()
        except Exception:
            return _fallback(user_query, explicit)

    history_context = ""
    if history:
        history_text = "\n".join([f"{getattr(msg, 'type', 'message')}: {getattr(msg, 'content', '')}" for msg in history[-4:]])
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
        if not isinstance(acts_raw, list):
            acts_raw = [acts_raw]
        acts = [a.strip() for a in acts_raw if isinstance(a, str) and a.strip().lower() not in ("", "null", "none")]
        acts = list(dict.fromkeys(acts))

        inferred = []
        for s in (data.get("inferred_sections") or []):
            s = str(s).translate(BN_TO_EN_DIGITS).strip()
            if s and s not in inferred and s not in explicit:
                inferred.append(s)

        result = AnalyzedQuery(
            detected_acts=acts,
            explicit_sections=explicit,
            inferred_sections=inferred[:MAX_INFERRED],
            optimized_query=data.get("optimized_query") or user_query[:300],
        )
        logger.info(
            f"[analyzer] acts={result.detected_acts} explicit={result.explicit_sections} "
            f"inferred={result.inferred_sections} optimized_query='{result.optimized_query}'"
        )
        return result
    except Exception:
        logger.exception("[analyze_query_dynamically] LLM analysis failed. Using fallback.")
        return _fallback(user_query, explicit)