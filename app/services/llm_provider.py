import logging
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from app.config import settings

logger = logging.getLogger(__name__)


class CustomFallbackLLM:
    """একটি কাস্টম ফলব্যাক ক্লাস, যা প্রাইমারি মডেল ফেইল করলে স্বয়ংক্রিয়ভাবে ফলব্যাক মডেল ব্যবহার করবে।"""
    def __init__(self, primary, fallback):
        self.primary = primary
        self.fallback = fallback

    async def ainvoke(self, *args, **kwargs):
        try:
            return await self.primary.ainvoke(*args, **kwargs)
        except Exception as e:
            logger.warning(f"[LLM Fallback] Primary LLM (Gemini) failed: {e}. Switching to Groq...")
            if self.fallback:
                return await self.fallback.ainvoke(*args, **kwargs)
            raise e

    def invoke(self, *args, **kwargs):
        try:
            return self.primary.invoke(*args, **kwargs)
        except Exception as e:
            logger.warning(f"[LLM Fallback] Primary LLM failed: {e}. Switching to Groq...")
            if self.fallback:
                return self.fallback.invoke(*args, **kwargs)
            raise e


def get_llm():
    """Returns an LLM instance with Gemini as primary and Groq as guaranteed fallback."""
    gemini_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    groq_key = getattr(settings, "GROQ_API_KEY", None) or os.getenv("GROQ_API_KEY")

    primary_llm = None
    fallback_llm = None

    # Fallback: Groq
    if groq_key:
        try:
            fallback_llm = ChatGroq(
                model="openai/gpt-oss-20b",
                api_key=groq_key,
                temperature=0.2,
                max_retries=1,
                timeout=60.0
            )
            logger.info("Groq fallback LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Groq: {exc}")
    else:
        logger.warning("GROQ_API_KEY is not configured in environment or settings.")

    # Primary: Gemini Flash
    if gemini_key:
        try:
            primary_llm = ChatGoogleGenerativeAI(
                model="gemini-flash-latest",
                google_api_key=gemini_key,
                temperature=0.2,
                max_retries=0,          
                timeout=60.0            
            )
            logger.info("Gemini primary LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Gemini: {exc}")

    if primary_llm and fallback_llm:
        return CustomFallbackLLM(primary=primary_llm, fallback=fallback_llm)
    elif primary_llm:
        return primary_llm
    elif fallback_llm:
        return fallback_llm
    else:
        raise ValueError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured!")