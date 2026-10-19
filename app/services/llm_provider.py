import logging
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from app.config import settings

logger = logging.getLogger(__name__)

_llm_instance = None


class CustomFallbackLLM:
    """প্রাইমারি মডেল ফেইল করলে বা 503 পেলে স্বয়ংক্রিয়ভাবে Groq ফলব্যাক ব্যবহার করবে।"""
    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback

    async def ainvoke(self, *args, **kwargs):
        try:
            return await self.primary.ainvoke(*args, **kwargs)
        except Exception as e:
            logger.warning(f"[LLM Fallback] Primary LLM failed: {e}. Switching immediately to Groq...")
            if self.fallback:
                return await self.fallback.ainvoke(*args, **kwargs)
            raise e

    def invoke(self, *args, **kwargs):
        try:
            return self.primary.invoke(*args, **kwargs)
        except Exception as e:
            logger.warning(f"[LLM Fallback] Primary LLM failed: {e}. Switching immediately to Groq...")
            if self.fallback:
                return self.fallback.invoke(*args, **kwargs)
            raise e


def get_llm():
    """Returns a cached LLM instance with optimized timeouts."""
    global _llm_instance
    if _llm_instance is not None:
        return _llm_instance

    gemini_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    groq_key = getattr(settings, "GROQ_API_KEY", None) or os.getenv("GROQ_API_KEY")

    primary_llm = None
    fallback_llm = None

    # Groq Fallback (llama-3.3-70b-versatile is ultra-fast and reliable for legal reasoning)
    if groq_key:
        try:
            fallback_llm = ChatGroq(
                model="llama-3.3-70b-versatile",
                api_key=groq_key,
                temperature=0.1,
                max_retries=2,
                timeout=90.0
            )
            logger.info("Groq fallback LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Groq: {exc}")

    # Primary: Gemini 1.5 Flash (stable model name)
    if gemini_key:
        try:
            primary_llm = ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                google_api_key=gemini_key,
                temperature=0.1,
                max_retries=1,          
                timeout=90.0            
            )
            logger.info("Gemini primary LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Gemini: {exc}")

    if primary_llm and fallback_llm:
        _llm_instance = CustomFallbackLLM(primary=primary_llm, fallback=fallback_llm)
    elif primary_llm:
        _llm_instance = primary_llm
    elif fallback_llm:
        _llm_instance = fallback_llm
    else:
        raise ValueError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured!")

    return _llm_instance