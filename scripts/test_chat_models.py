import os
import sys
import time

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq

load_dotenv(override=True)

PROMPTS = [
    {
        "lang": "Bangla (বাংলা)",
        "query": "চুক্তি আইন ১৮৭২ অনুযায়ী ধারা ৭৪-এর মূল বিধান কী এবং আদালত কি অতিরিক্ত ক্ষতিপূরণ কমাতে পারে? সংক্ষেপে ২-৩ বাক্যে বলুন।",
    },
    {
        "lang": "English",
        "query": "Under the Bangladesh Labour Act 2006, what is the mandatory statutory notice period for terminating a permanent worker without cause? Answer in 2-3 concise sentences.",
    },
    {
        "lang": "Banglish (রোমান হরফে বাংলা)",
        "query": "amar cheque dishonour hoise, bank theke return memo peyesi. NI Act 138 onusare legal notice pathanor jonno koto din shomoy pabo? 2-3 sentence e bolun.",
    },
]


def get_models():
    models = {}

    # 1. Gemini
    gemini_key = getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            models["Google Gemini (gemini-3.5-flash-lite)"] = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash-lite",
                google_api_key=gemini_key,
                temperature=0.1,
                max_output_tokens=1000,
                timeout=45.0,
            )
        except Exception as e:
            print(f"[Init Error] Gemini: {e}")

    # 2. ZAI (GLM-4.5-Flash)
    zai_key = getattr(settings, "ZAI_API_KEY", "") or os.getenv("ZAI_API_KEY", "")
    zai_base = getattr(settings, "ZAI_BASE_URL", "") or os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    zai_model = getattr(settings, "ZAI_MODEL", "") or os.getenv("ZAI_MODEL", "glm-4.5-flash")
    if zai_key:
        try:
            models[f"Z.ai ({zai_model})"] = ChatOpenAI(
                model=zai_model,
                api_key=zai_key,
                base_url=zai_base,
                temperature=0.1,
                max_tokens=1000,
                timeout=45.0,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as e:
            print(f"[Init Error] ZAI: {e}")

    # 3. Groq (Qwen 3.8 27B)
    groq_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
    if groq_key:
        try:
            models["Groq (qwen/qwen3.8-27b)"] = ChatGroq(
                model="qwen/qwen3.8-27b",
                api_key=groq_key,
                temperature=0.1,
                max_tokens=1000,
                reasoning_effort="none",
                timeout=45.0,
            )
        except Exception as e:
            print(f"[Init Error] Groq: {e}")

    return models


def run_benchmark():
    models = get_models()
    print("=" * 80)
    print(f"BENCHMARK: Testing {len(models)} models across Bangla, English, and Banglish")
    print("=" * 80)

    system_instruction = (
        "You are Advotech Copilot, an expert Bangladesh legal assistant. "
        "Always answer accurately based on codified Bangladesh statutes. "
        "If queried in Bangla or Banglish, answer in clear formal Bengali script (বাংলা লিপি). "
        "If queried in English, answer in English."
    )

    results = []

    for model_name, model in models.items():
        print(f"\n[{model_name.upper()}]")
        print("-" * 80)

        for p in PROMPTS:
            lang = p["lang"]
            query = p["query"]
            print(f"\n>> Language: {lang}")
            print(f">> Question: {query}")

            start_t = time.perf_counter()
            try:
                resp = model.invoke([
                    SystemMessage(content=system_instruction),
                    HumanMessage(content=query),
                ])
                elapsed = time.perf_counter() - start_t
                raw_c = resp.content
                if isinstance(raw_c, list):
                    content = "".join([str(p.get("text", p) if isinstance(p, dict) else p) for p in raw_c]).strip()
                else:
                    content = str(raw_c or "").strip()

                print(f">> Latency: {elapsed:.2f}s")
                print(f">> Response:\n{content}\n")
                results.append({
                    "model": model_name,
                    "lang": lang,
                    "status": "SUCCESS",
                    "latency": elapsed,
                    "preview": content[:120].replace("\n", " "),
                })
            except Exception as exc:
                elapsed = time.perf_counter() - start_t
                print(f">> FAILED ({elapsed:.2f}s): {exc}\n")
                results.append({
                    "model": model_name,
                    "lang": lang,
                    "status": f"FAILED: {exc}",
                    "latency": elapsed,
                    "preview": "N/A",
                })

    print("\n" + "=" * 80)
    print("FINAL SUMMARY REPORT")
    print("=" * 80)
    print(f"{'Model':<35} | {'Language':<18} | {'Status':<10} | {'Latency':<8}")
    print("-" * 80)
    for r in results:
        stat = "PASS" if r["status"] == "SUCCESS" else "FAIL"
        print(f"{r['model']:<35} | {r['lang']:<18} | {stat:<10} | {r['latency']:.2f}s")
    print("=" * 80)


if __name__ == "__main__":
    run_benchmark()
