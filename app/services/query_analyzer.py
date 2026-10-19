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


ROUTER_PROMPT = """You are an expert Legal Query Reformulator for Bangladesh Law.
Your core task is to bridge the "Semantic Gap" between a user's layman terms (Bengali, Banglish, or English) and formal legal terminology in Bangladesh statutes.

COMMON BANGLADESH ACT MAPPINGS:
- Contract, agreement, breach, indemnity, guarantee, agency, চুক্তি, চুক্তি ভঙ্গ, ক্ষতিপূরণ -> "The Contract Act, 1872"
- Job, worker, termination, layoff, wages, maternity, leave, শ্রম, চাকরি, ছাঁটাই, বেতন, শ্রমিক -> "Bangladesh Labour Act, 2006"
- Crime, murder, theft, extortion, fraud, cheating, hurt, defamation, চুরি, প্রতারণা, খুন, মারামারি, হুমকি, মানহানি -> "The Penal Code, 1860"
- Police, FIR, GD, arrest, bail, remand, investigation, trial, জামিন, গ্রেপ্তার, এজাহার, জিডি, রিমান্ড, ফৌজদারি কার্যবিধি -> "The Code of Criminal Procedure, 1898"
- Civil suit, injunction, decree, summon, দেওয়ানি মামলা, নিষেধাজ্ঞা, ডিক্রি -> "The Code of Civil Procedure, 1908"
- Cheque dishonor, bounced cheque, promissory note, চেক ডিজঅনার, ব্যাংক চেক প্রত্যাখ্যান -> "The Negotiable Instruments Act, 1881" (inferred_sections: ["138"])
- Land sale, mortgage, lease, gift, eviction, জমি হস্তান্তর, দলিল, ভাড়া, ইজারা, হেবা -> "The Transfer of Property Act, 1882"
- Specific performance, recovery of possession, declaration, দখল পুনরুদ্ধার, চুক্তি বলবৎ -> "The Specific Relief Act, 1877"
- Evidence, witness, burden of proof, confession, সাক্ষ্য, প্রমাণ, সাক্ষী -> "The Evidence Act, 1872"
- Limitation period, deadline to file suit/appeal, তামাদি, মামলা করার সময়সীমা -> "The Limitation Act, 1908"
- Company, director, shareholder, winding up, কোম্পানি, শেয়ারহোল্ডার, পরিচালক -> "The Companies Act, 1994"
- Arbitration, dispute resolution, সালিশ, মধ্যস্থতা -> "The Arbitration Act, 2001"

CRITICAL RULES:
1. Only include `detected_acts` if the query clearly relates to that specific Act. If unsure or if multiple unknown laws might apply, leave `detected_acts` empty `[]` so the retriever searches across all laws.
2. Only add `inferred_sections` when you are highly confident of the exact statutory section (e.g., ["73", "74"] for breach of contract damages, ["27"] for restraint of trade, ["138"] for cheque dishonor, ["420", "406"] for fraud/breach of trust).
3. In `optimized_query`, write a rich semantic search string containing both English legal terms and Bengali legal terms to maximize vector search recall.

Analyze the user's query (and chat history if provided) and respond ONLY with a strict JSON object:
{
  "detected_acts": [],
  "inferred_sections": [],
  "optimized_query": "Expanded semantic search string in both Bengali and English."
}"""


def _fallback(user_query: str, explicit: List[str]) -> AnalyzedQuery:
    detected_acts = []
    inferred_secs = []
    q_lower = user_query.lower()
    
    if any(k in q_lower for k in ("contract", "চুক্তি", "vhong", "ভঙ্গ", "agreement")):
        detected_acts.append("The Contract Act, 1872")
    if any(k in q_lower for k in ("labour", "labor", "শ্রম", "chakri", "চাকরি", "বেতন", "শ্রমিক", "ছাঁটাই")):
        detected_acts.append("Bangladesh Labour Act, 2006")
    if any(k in q_lower for k in ("penal", "crime", "শাস্তি", "খুন", "চুরি", "প্রতারণা", "হুমকি", "মারামারি")):
        detected_acts.append("The Penal Code, 1860")
    if any(k in q_lower for k in ("bail", "arrest", "জামিন", "গ্রেপ্তার", "মামলা", "থানা", "জিডি", "এজাহার")):
        detected_acts.append("The Code of Criminal Procedure, 1898")
    if any(k in q_lower for k in ("cheque", "চেক", "ডিজঅনার", "dishonor", "dishonour")):
        detected_acts.append("The Negotiable Instruments Act, 1881")
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