import logging
import os
from typing import Any, List, Optional
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from app.config import settings

logger = logging.getLogger(__name__)


class CustomFallbackLLM(BaseChatModel):
    """
    একটি কাস্টম ফলব্যাক ক্লাস, যা প্রাইমারি মডেল (Groq) ফেইল করলে (429, 404, Timeout) 
    স্বয়ংক্রিয়ভাবে ফলব্যাক মডেল (Gemini) ব্যবহার করবে।
    """
    primary: Any
    fallback: Any

    @property
    def _llm_type(self) -> str:
        return "custom_fallback_llm"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            return self.primary._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as e:
            logger.warning(f"[LLM Fallback] Primary LLM (Groq) failed in sync call: {e}. Switching to Gemini...")
            return self.fallback._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            return await self.primary._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as e:
            logger.warning(f"[LLM Fallback] Primary LLM (Groq) failed in async call: {e}. Switching to Gemini...")
            return await self.fallback._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @property
    def _identifying_params(self) -> dict:
        return {"primary": "Groq", "fallback": "Gemini"}


def get_llm() -> BaseChatModel:
    """Returns an LLM instance with Groq as primary and Gemini as guaranteed fallback."""
    gemini_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    groq_key = getattr(settings, "GROQ_API_KEY", None) or os.getenv("GROQ_API_KEY")

    primary_llm = None
    fallback_llm = None

    # ১. Primary: Groq (সবচেয়ে দ্রুত)
    if groq_key:
        try:
            primary_llm = ChatGroq(
                model="qwen/qwen3.8-27b",  # ✅ নতুন রিকমেন্ডেড মডেল
                api_key=groq_key,
                temperature=0.1,
                max_tokens=2300, # ✅ খুব গুরুত্বপূর্ণ!
                max_retries=3,
                timeout=60.0
            )   
            logger.info("Groq primary LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Groq: {exc}")
    else:
        logger.warning("GROQ_API_KEY is not configured in environment or settings.")

    # ২. Fallback: Gemini Flash 
    if gemini_key:
        try:
            fallback_llm = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash-lite",
                google_api_key=gemini_key,
                temperature=0.1,              # ✅ কমিয়ে 0.1 করা হলো
                max_output_tokens=3000,        # ✅ সর্বোচ্চ 2500 টোকেন 
                max_retries=3,          
                timeout=60.0                 # ✅ ৬০ সেকেন্ডের বদলে ৫ সেকেন্ড
            )
            logger.info("Gemini fallback LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Gemini: {exc}")

    # ৩. রিটার্ন লজিক
    if primary_llm and fallback_llm:
        return CustomFallbackLLM(primary=primary_llm, fallback=fallback_llm)
    elif primary_llm:
        return primary_llm
    elif fallback_llm:
        return fallback_llm
    else:
        raise ValueError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured!")