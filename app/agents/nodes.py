import json
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from app.services.retriever import hybrid_retriever

api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=api_key,
    temperature=0.0,
    request_timeout=35
)

def retrieve_statutes_node(state: dict) -> dict:
    """চুক্তির টেক্সট অনুযায়ী সংশ্লিষ্ট আইন রিট্রিভ করে state-এ যোগ করে।"""
    contract_text = state.get("contract_text", "")
    retrieved_laws = hybrid_retriever.retrieve(contract_text[:4000], top_k=3)
    return {"retrieved_statutes": retrieved_laws}

def analyze_risk_node(state: dict) -> dict:
    """রিট্রিভ করা আইনের আলোকে জেমিনি দিয়ে রিস্ক অ্যানালাইসিস করে।"""
    contract_text = state.get("contract_text", "")[:8000]
    statutes = state.get("retrieved_statutes", [])

    laws_context = "\n\n".join([
        f"- {law['act']}, {law['section']} ({law['title']}):\n  \"{law['text']}\""
        for law in statutes
    ])

    prompt = f"""
You are a legal risk analyst specializing strictly in Bangladesh statutory jurisdiction.
Carefully evaluate the contract terms using the following statutory provisions:

RELEVANT BANGLADESH STATUTES:
{laws_context}

Output strictly valid JSON matching this format:
{{
  "overall_risk_score": "HIGH",
  "memo_summary": "Summary of primary contractual risks based on the retrieved statutory provisions.",
  "detected_risks": [
    {{
      "clause_title": "Clause Title/Number",
      "severity": "HIGH",
      "flagged_text": "Exact risky sentence from contract",
      "violated_law": "Act and Section reference",
      "explanation": "Legal rationale under Bangladesh law",
      "recommendation": "Redline / revision suggestion"
    }}
  ]
}}

Contract:
{contract_text}
"""
    try:
        response = llm.invoke(prompt)
        content = response.content.strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        analysis = json.loads(content.strip())
    except Exception as e:
        analysis = {
            "overall_risk_score": "MODERATE",
            "memo_summary": f"Fallback parse: {str(e)}",
            "detected_risks": []
        }

    return {"analysis": analysis}