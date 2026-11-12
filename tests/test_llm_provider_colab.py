import json
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate

import app.services.llm_provider as lp


def test_default_llm_path_unchanged_when_colab_url_unset(monkeypatch):
    monkeypatch.delenv("COLAB_LLM_BASE_URL", raising=False)
    monkeypatch.setattr(lp.settings, "COLAB_LLM_BASE_URL", "")

    sentinel = object()
    monkeypatch.setattr(lp, "_build_default_llm", lambda: sentinel)

    assert lp.get_llm() is sentinel


def test_colab_url_normalization():
    assert (
        lp.OpenAICompatibleChatModel(base_url="https://demo.trycloudflare.com")._chat_completions_url()
        == "https://demo.trycloudflare.com/v1/chat/completions"
    )
    assert (
        lp.OpenAICompatibleChatModel(base_url="https://demo.trycloudflare.com/v1/")._chat_completions_url()
        == "https://demo.trycloudflare.com/v1/chat/completions"
    )
    assert (
        lp.OpenAICompatibleChatModel(
            base_url="https://demo.trycloudflare.com/v1/chat/completions"
        )._chat_completions_url()
        == "https://demo.trycloudflare.com/v1/chat/completions"
    )


def _mock_transport(captured):
    def fake_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "ধারা ১৩৮ অনুযায়ী ৩০ দিনের মধ্যে নোটিশ দিতে হবে।",
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(fake_handler)


def test_colab_llm_sync_invoke(monkeypatch):
    monkeypatch.setenv("COLAB_LLM_BASE_URL", "https://demo.trycloudflare.com")
    monkeypatch.setenv("COLAB_LLM_MODEL", "gemma4:e4b")
    monkeypatch.setenv("COLAB_LLM_API_KEY", "secret-token")
    monkeypatch.setenv("COLAB_LLM_FALLBACK", "false")

    captured = {}
    transport = _mock_transport(captured)
    orig_client = httpx.Client
    monkeypatch.setattr(lp.httpx, "Client", lambda *a, **kw: orig_client(transport=transport, **kw))

    llm = lp.get_llm()
    assert isinstance(llm, lp.OpenAICompatibleChatModel)

    sync_res = llm.invoke("চেক ডিজঅনার")
    assert "ধারা ১৩৮" in sync_res.content
    assert captured["url"] == "https://demo.trycloudflare.com/v1/chat/completions"
    assert captured["auth"] == "Bearer secret-token"
    assert captured["body"]["model"] == "gemma4:e4b"


async def test_colab_llm_async_invoke_and_chain(monkeypatch):
    monkeypatch.setenv("COLAB_LLM_BASE_URL", "https://demo.trycloudflare.com")
    monkeypatch.setenv("COLAB_LLM_MODEL", "gemma4:e4b")
    monkeypatch.setenv("COLAB_LLM_API_KEY", "secret-token")
    monkeypatch.setenv("COLAB_LLM_FALLBACK", "false")

    captured = {}
    transport = _mock_transport(captured)
    orig_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        lp.httpx, "AsyncClient", lambda *a, **kw: orig_async_client(transport=transport, **kw)
    )

    llm = lp.get_llm()
    res = await llm.ainvoke([SystemMessage(content="Legal AI"), HumanMessage(content="Section 138?")])
    assert "ধারা ১৩৮" in res.content
    assert [m["role"] for m in captured["body"]["messages"]] == ["system", "user"]

    chain = ChatPromptTemplate.from_messages([("system", "Sys"), ("human", "{q}")]) | llm
    chain_res = await chain.ainvoke({"q": "Amar bail lagbe"})
    assert "ধারা ১৩৮" in chain_res.content


def test_colab_llm_with_optional_fallback(monkeypatch):
    monkeypatch.setenv("COLAB_LLM_BASE_URL", "https://down.trycloudflare.com")
    monkeypatch.setenv("COLAB_LLM_FALLBACK", "true")

    class StubDefaultLLM:
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            from langchain_core.outputs import ChatGeneration, ChatResult

            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="fallback-ok"))])

    monkeypatch.setattr(lp, "_build_default_llm", lambda: StubDefaultLLM())

    def fail_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="tunnel down")

    transport = httpx.MockTransport(fail_handler)
    orig_client = httpx.Client
    monkeypatch.setattr(lp.httpx, "Client", lambda *a, **kw: orig_client(transport=transport, **kw))

    llm = lp.get_llm()
    assert isinstance(llm, lp.CustomFallbackLLM)
    res = llm.invoke("test")
    assert res.content == "fallback-ok"
