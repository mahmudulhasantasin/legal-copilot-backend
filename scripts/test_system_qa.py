import os
import sys
import asyncio
import time

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)

from app.agents.qa_graph import qa_graph


QUERIES = [
    {
        "lang": "Bangla (বাংলা)",
        "query": "চুক্তি আইন ১৮৭২ অনুযায়ী ধারা ৭৪ এর মূল বিধান কী এবং আদালত কি অতিরিক্ত ক্ষতিপূরণ কমাতে পারে?",
    },
    {
        "lang": "English",
        "query": "Under the Bangladesh Labour Act 2006, what is the mandatory notice period for terminating a permanent worker without cause?",
    },
    {
        "lang": "Banglish (রোমান হরফে বাংলা)",
        "query": "amar check dishonour hoise, bank theke return slip peyesi. NI Act 138 onusare notice pathanor time koto din?",
    },
]


async def run_system_qa_test():
    print("=" * 80)
    print("RUNNING ADVOTECH COPILOT SYSTEM PIPELINE TEST (qa_graph)")
    print("Pipeline: Router -> Hybrid RAG (Qdrant 35,000+ BD Laws) -> LLM -> MCP Citations")
    print("=" * 80)

    for item in QUERIES:
        lang = item["lang"]
        q = item["query"]
        print(f"\n" + "-" * 80)
        print(f"QUERY [{lang}]: {q}")
        print("-" * 80)

        initial_state = {
            "query": q,
            "user_id": 1,
            "user_role": "GENERAL",
            "messages": [],
            "retrieved_docs": [],
            "final_answer": None,
            "citation_verified": False,
            "contract_id": None,
            "contract_context": None,
            "language": None,
            "query_type": None,
            "needs_case_law": False,
            "cited_cases": [],
            "structured_response": None,
        }

        start_t = time.perf_counter()
        try:
            output = await qa_graph.ainvoke(initial_state)
            elapsed = time.perf_counter() - start_t

            structured = output.get("structured_response") or {}
            final_text = output.get("final_answer") or structured.get("detailed_answer", "")
            cited_sections = structured.get("cited_sections", [])
            cited_cases = structured.get("cited_cases", [])
            next_steps = structured.get("suggested_next_steps", [])
            follow_ups = structured.get("suggested_follow_ups", [])
            docs_count = len(output.get("retrieved_docs", []))

            print(f">> Total System Pipeline Latency: {elapsed:.2f}s")
            print(f">> Classification Status: {output.get('query_type')}")
            print(f">> Qdrant Retrieved Statutes: {docs_count} section(s)")
            print(f"\n>> Final Structured Answer:\n{final_text}\n")

            if cited_sections:
                print(">> Cited Sections:")
                for cs in cited_sections:
                    print(f"   • {cs.get('act')} - Section {cs.get('section')}: {cs.get('relevance')}")

            if next_steps:
                print(">> Suggested Next Steps:")
                for ns in next_steps:
                    print(f"   ✓ {ns}")

            if follow_ups:
                print(">> Suggested Follow-ups:")
                for fu in follow_ups:
                    print(f"   ? {fu}")

        except Exception as exc:
            elapsed = time.perf_counter() - start_t
            print(f">> PIPELINE ERROR ({elapsed:.2f}s): {exc}")

    print("\n" + "=" * 80)
    print("END OF SYSTEM TEST")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_system_qa_test())
