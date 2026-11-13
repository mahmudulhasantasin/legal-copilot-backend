import pytest
from unittest.mock import MagicMock
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.messages import AIMessage

import app.services.llm_provider as lp
from app.services.llm_provider import CustomFallbackLLM, get_llm


def test_custom_fallback_llm_primary_success():
    primary = MagicMock()
    fallback = MagicMock()
    primary._generate.return_value = ChatResult(
        generations=[ChatGeneration(message=AIMessage(content="Primary response"))]
    )

    composite = CustomFallbackLLM(primary=primary, fallback=fallback)
    result = composite._generate([HumanMessage(content="Hello")])
    assert result.generations[0].message.content == "Primary response"
    primary._generate.assert_called_once()
    fallback._generate.assert_not_called()


def test_custom_fallback_llm_switches_to_fallback_on_primary_failure():
    primary = MagicMock()
    fallback = MagicMock()
    primary._generate.side_effect = Exception("Primary API Error 503")
    fallback._generate.return_value = ChatResult(
        generations=[ChatGeneration(message=AIMessage(content="Fallback response"))]
    )

    composite = CustomFallbackLLM(primary=primary, fallback=fallback)
    result = composite._generate([HumanMessage(content="Hello")])
    assert result.generations[0].message.content == "Fallback response"
    primary._generate.assert_called_once()
    fallback._generate.assert_called_once()


@pytest.mark.asyncio
async def test_custom_fallback_llm_async_switches_on_failure():
    primary = MagicMock()
    fallback = MagicMock()

    async def fail(*args, **kwargs):
        raise Exception("Primary async error 429")

    async def succ(*args, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Fallback async response"))])

    primary._agenerate.side_effect = fail
    fallback._agenerate.side_effect = succ

    composite = CustomFallbackLLM(primary=primary, fallback=fallback)
    result = await composite._agenerate([HumanMessage(content="Hello")])
    assert result.generations[0].message.content == "Fallback async response"


def test_get_llm_constructs_successfully(monkeypatch):
    monkeypatch.setattr(lp.settings, "GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setattr(lp.settings, "GROQ_API_KEY", "test-groq-key")

    llm = get_llm()
    assert isinstance(llm, CustomFallbackLLM)
    assert llm.primary_name == "Gemini"
    assert llm.fallback_name == "Groq"
