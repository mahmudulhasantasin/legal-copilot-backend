import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional
import httpx
from langchain_core.callbacks.manager import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from app.config import settings

logger = logging.getLogger(__name__)


def _env_or_setting(name: str, default: Any = "") -> Any:
    env_val = os.getenv(name)
    if env_val is not None:
        return env_val
    return getattr(settings, name, default)


class OpenAICompatibleChatModel(BaseChatModel):
    """Lightweight BaseChatModel adapter for OpenAI-compatible endpoints (e.g., Colab/Ollama)."""

    base_url: str
    model: str = "gemma4:e4b"
    api_key: str = "colab-local"
    temperature: float = 0.1
    max_tokens: int = 3000
    timeout: float = 120.0

    @property
    def _llm_type(self) -> str:
        return "openai_compatible_chat"

    @property
    def _identifying_params(self) -> dict:
        return {"base_url": self.base_url, "model": self.model}

    def _chat_completions_url(self) -> str:
        cleaned = self.base_url.strip().rstrip("/")
        if cleaned.endswith("/chat/completions"):
            return cleaned
        if cleaned.endswith("/v1"):
            return f"{cleaned}/chat/completions"
        return f"{cleaned}/v1/chat/completions"

    @staticmethod
    def _message_role(message: BaseMessage) -> str:
        role_map = {
            "system": "system",
            "human": "user",
            "ai": "assistant",
            "tool": "tool",
        }
        msg_type = getattr(message, "type", "human")
        if msg_type in role_map:
            return role_map[msg_type]
        return getattr(message, "role", "user")

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return "".join(parts)
        return str(content or "")

    def _build_payload(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        formatted_messages = [
            {"role": self._message_role(m), "content": self._content_to_text(m.content)}
            for m in messages
        ]
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "stream": False,
        }
        if stop:
            payload["stop"] = stop
        return payload

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
        }
        token = (self.api_key or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _parse_response(resp: httpx.Response) -> ChatResult:
        if resp.status_code != 200:
            raise RuntimeError(
                f"Colab/OpenAI-compatible endpoint returned HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Colab/OpenAI-compatible response missing choices: {data}")
        msg = choices[0].get("message") or {}
        text = msg.get("content")
        if text is None:
            text = ""
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=str(text)))])

    def _request_timeout(self) -> httpx.Timeout:
        """Short connect timeout so a dead tunnel fails in seconds, not after the full read timeout."""
        connect = min(10.0, self.timeout)
        return httpx.Timeout(connect=connect, read=self.timeout, write=self.timeout, pool=connect)

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        url = self._chat_completions_url()
        payload = self._build_payload(messages, stop=stop, **kwargs)
        headers = self._build_headers()
        last_exc: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                with httpx.Client(timeout=self._request_timeout()) as client:
                    resp = client.post(url, headers=headers, json=payload)
                return self._parse_response(resp)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exc = exc
                if attempt == 2:
                    break
                logger.warning(f"[Colab LLM] connect failed ({exc}); retrying in 2s...")
                time.sleep(2.0)
        raise last_exc

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        url = self._chat_completions_url()
        payload = self._build_payload(messages, stop=stop, **kwargs)
        headers = self._build_headers()
        last_exc: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                return self._parse_response(resp)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exc = exc
                if attempt == 2:
                    break
                logger.warning(f"[Colab LLM] connect failed ({exc}); retrying in 2s...")
                await asyncio.sleep(2.0)
        raise last_exc


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


def _build_default_llm() -> Optional[BaseChatModel]:
    """Builds the standard production LLM (Gemini primary with Groq fallback)."""
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
                # Free tier comfortably supports 8K output (250K TPM / 500 RPD);
                # 3000 truncated full-contract audit JSON (17+ clause risks).
                max_output_tokens=8000,
                max_retries=2,
                timeout=90.0,
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
                # qwen3 is a reasoning model — without this its thinking tokens
                # eat the 950-token output budget and the audit JSON truncates
                # (verified: reasoning_effort="none" → finish_reason=stop, ~205 tokens).
                reasoning_effort="none",
                max_retries=1,
                timeout=45.0,
            )
            logger.info("Groq fallback LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Groq: {exc}")
    else:
        logger.warning("GROQ_API_KEY is not configured in environment or settings.")

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
    return None


def get_llm() -> BaseChatModel:
    """Returns configured LLM: optional Colab endpoint when COLAB_LLM_BASE_URL is set, else Gemini + Groq."""
    colab_base_url = str(_env_or_setting("COLAB_LLM_BASE_URL", "") or "").strip()
    if colab_base_url:
        colab_model = str(_env_or_setting("COLAB_LLM_MODEL", "gemma4:e4b") or "gemma4:e4b").strip()
        colab_api_key = str(_env_or_setting("COLAB_LLM_API_KEY", "colab-local") or "").strip()
        colab_timeout = float(_env_or_setting("COLAB_LLM_TIMEOUT", 120.0) or 120.0)
        colab_max_tokens = int(_env_or_setting("COLAB_LLM_MAX_TOKENS", 8000) or 8000)
        fallback_raw = _env_or_setting("COLAB_LLM_FALLBACK", False)
        use_fallback = (
            fallback_raw
            if isinstance(fallback_raw, bool)
            else str(fallback_raw).strip().lower() in ("1", "true", "yes", "on")
        )

        colab_llm = OpenAICompatibleChatModel(
            base_url=colab_base_url,
            model=colab_model,
            api_key=colab_api_key,
            temperature=0.1,
            max_tokens=colab_max_tokens,
            timeout=colab_timeout,
        )
        logger.info(f"Colab OpenAI-compatible LLM initialized ({colab_model} @ {colab_base_url}).")

        if use_fallback:
            default_llm = _build_default_llm()
            if default_llm is not None:
                return CustomFallbackLLM(
                    primary=colab_llm,
                    fallback=default_llm,
                    primary_name=f"Colab({colab_model})",
                    fallback_name="Default(Gemini/Groq)",
                )
        return colab_llm

    default_llm = _build_default_llm()
    if default_llm is not None:
        return default_llm
    raise ValueError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured!")
