import logging
import os
from typing import Any, List, Optional
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from app.config import settings

logger = logging.getLogger(__name__)


class CustomFallbackLLM(BaseChatModel):
    """
    Primary LLM wrapper with automatic fallback (429, 404, timeout, 503) to the secondary provider.
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


def _chain_llms(providers: List[tuple[str, Any]]) -> Optional[BaseChatModel]:
    """Chains multiple LLM providers together using CustomFallbackLLM."""
    if not providers:
        return None
    if len(providers) == 1:
        return providers[0][1]

    curr_name, curr_llm = providers[-1]
    for name, llm in reversed(providers[:-1]):
        curr_llm = CustomFallbackLLM(
            primary=llm,
            fallback=curr_llm,
            primary_name=name,
            fallback_name=curr_name,
        )
        curr_name = f"{name}->{curr_name}"
    return curr_llm


def _resolve_key(name: str, fallback_env: Optional[str] = None) -> str:
    val = getattr(settings, name, None)
    if val is not None:
        return str(val).strip()
    if fallback_env:
        fb = os.getenv(fallback_env)
        if fb is not None:
            return str(fb).strip()
    return (os.getenv(name) or "").strip()


def get_llm(prefer_chat_model: bool = False) -> BaseChatModel:
    """
    Returns the production LLM with multi-tier automatic fallback.
    Supported providers:
      1. Gemini: Google Gemini API (gemini-3.5-flash-lite)
      2. ZAI: Zhipu AI / GLM OpenAI-compatible API (e.g. glm-4-flash)
      3. Groq: Groq Cloud API (qwen/qwen3.8-27b)
    """
    gemini_key = _resolve_key("GEMINI_API_KEY", fallback_env="GOOGLE_API_KEY")
    groq_key = _resolve_key("GROQ_API_KEY")
    zai_key = _resolve_key("ZAI_API_KEY")
    zai_base_url = _resolve_key("ZAI_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4/"
    zai_model = _resolve_key("ZAI_MODEL") or "glm-4.5-flash"
    disable_thinking_raw = getattr(settings, "ZAI_DISABLE_THINKING", None)
    if disable_thinking_raw is None:
        disable_thinking_raw = os.getenv("ZAI_DISABLE_THINKING", "true")
    disable_thinking = (
        disable_thinking_raw
        if isinstance(disable_thinking_raw, bool)
        else str(disable_thinking_raw).strip().lower() in ("1", "true", "yes", "on")
    )
    primary_pref = _resolve_key("PRIMARY_LLM").lower()

    gemini_llm = None
    zai_llm = None
    groq_llm = None

    # 1. Gemini
    if gemini_key:
        try:
            gemini_llm = ChatGoogleGenerativeAI(
                model="gemini-3.5-flash-lite",
                google_api_key=gemini_key,
                temperature=0.1,
                max_output_tokens=8000,
                max_retries=2,
                timeout=90.0,
            )
            logger.info("Gemini primary LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Gemini: {exc}")
    else:
        logger.warning("GEMINI_API_KEY is not configured in environment or settings.")

    # 2. ZAI (Zhipu AI / GLM OpenAI-compatible)
    if zai_key:
        try:
            zai_kwargs = {
                "model": zai_model,
                "api_key": zai_key,
                "base_url": zai_base_url,
                "temperature": 0.1,
                "max_tokens": 4096,
                "timeout": 60.0,
            }
            if disable_thinking:
                # Disabling thinking stops internal reasoning tokens, eliminating 11s-57s latency spikes
                zai_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

            zai_llm = ChatOpenAI(**zai_kwargs)
            logger.info(f"ZAI ({zai_model}) LLM initialized successfully (thinking_disabled={disable_thinking}).")
        except Exception as exc:
            logger.warning(f"Failed to initialize ZAI: {exc}")
    else:
        logger.warning("ZAI_API_KEY is not configured in environment or settings.")

    # 3. Groq
    if groq_key:
        try:
            groq_max_tokens = int(getattr(settings, "GROQ_MAX_TOKENS", 0) or 0) or int(
                os.getenv("GROQ_MAX_TOKENS", "950")
            )
            groq_llm = ChatGroq(
                model="qwen/qwen3.8-27b",
                api_key=groq_key,
                temperature=0.1,
                # Groq free tier enforces an OUTPUT tokens-per-minute (OTPM) cap of 1000:
                # requesting max_tokens=2000 made EVERY fallback request fail with
                # 429 "Request too large ... OTPM: Limit 1000, Requested 2000" (observed
                # live in the 2026-11-15 benchmark runs). Keep 950 so Groq stays a usable
                # small-request fallback; large outputs are served by Z.ai (4096) first.
                max_tokens=groq_max_tokens,
                reasoning_effort="none",
                max_retries=1,
                timeout=45.0,
            )
            logger.info("Groq fallback LLM initialized successfully (max_tokens=%s).", groq_max_tokens)
        except Exception as exc:
            logger.warning(f"Failed to initialize Groq: {exc}")
    else:
        logger.warning("GROQ_API_KEY is not configured in environment or settings.")

    # Determine provider priority order
    available = {}
    if gemini_llm:
        available["gemini"] = ("Gemini", gemini_llm)
    if zai_llm:
        available["zai"] = ("ZAI", zai_llm)
    if groq_llm:
        available["groq"] = ("Groq", groq_llm)

    if not available:
        raise ValueError("Neither GEMINI_API_KEY, ZAI_API_KEY, nor GROQ_API_KEY is configured!")

    providers_list = []
    # Primary model selection: Gemini is default primary unless explicitly overridden by PRIMARY_LLM
    if primary_pref == "zai" and "zai" in available:
        providers_list.append(available.pop("zai"))
    elif primary_pref == "groq" and "groq" in available:
        providers_list.append(available.pop("groq"))
    elif "gemini" in available:
        providers_list.append(available.pop("gemini"))

    # Append remaining available fallback providers in order: Gemini -> ZAI -> Groq
    for key in ("gemini", "zai", "groq"):
        if key in available:
            providers_list.append(available.pop(key))

    return _chain_llms(providers_list)


def get_chat_llm() -> BaseChatModel:
    """Returns the LLM configured for legal chat (Gemini primary -> ZAI fallback -> Groq fallback)."""
    return get_llm()
