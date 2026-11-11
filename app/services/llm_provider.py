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
    Primary LLM wrapper with automatic fallback (429, 404, timeout) to the secondary provider.
    """
    primary: Any
    fallback: Any
    primary_name: str = "Primary"
    fallback_name: str = "Fallback"

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
            logger.warning(f"[LLM Fallback] Primary LLM ({self.primary_name}) failed in sync call: {e}. Switching to {self.fallback_name}...")
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
            logger.warning(f"[LLM Fallback] Primary LLM ({self.primary_name}) failed in async call: {e}. Switching to {self.fallback_name}...")
            return await self.fallback._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    @property
    def _identifying_params(self) -> dict:
        return {"primary": self.primary_name, "fallback": self.fallback_name}


def get_llm() -> BaseChatModel:
    """Returns an LLM instance with Gemini as primary and Groq as guaranteed fallback."""
    gemini_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    groq_key = getattr(settings, "GROQ_API_KEY", None) or os.getenv("GROQ_API_KEY")

    gemini_llm = None
    groq_llm = None

    # 1. Primary: Gemini Flash Lite (avoids Groq on_demand 1000 OTPM throttling on long JSON outputs)
    if gemini_key:
        try:
            gemini_llm = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash-lite",
                google_api_key=gemini_key,
                temperature=0.1,
                max_output_tokens=3000,
                max_retries=2,
                timeout=60.0,
            )
            logger.info("Gemini primary LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Gemini: {exc}")
    else:
        logger.warning("GEMINI_API_KEY is not configured in environment or settings.")

    # 2. Fallback: Groq (kept qwen/qwen3.8-27b; max_tokens capped under 1000 OTPM limit)
    if groq_key:
        try:
            groq_llm = ChatGroq(
                model="qwen/qwen3.8-27b",
                api_key=groq_key,
                temperature=0.1,
                max_tokens=950,
                max_retries=1,
                timeout=45.0,
            )
            logger.info("Groq fallback LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Groq: {exc}")
    else:
        logger.warning("GROQ_API_KEY is not configured in environment or settings.")

    # 3. Return logic
    if gemini_llm and groq_llm:
        return CustomFallbackLLM(
            primary=gemini_llm,
            fallback=groq_llm,
            primary_name="Gemini",
            fallback_name="Groq",
        )
    if gemini_llm:
        return gemini_llm
    if groq_llm:
        return groq_llm
    raise ValueError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured!")