import pytest
from unittest.mock import patch, AsyncMock
from app.models.chat import ChatSession, ChatMessage
from app.models.user import User

@pytest.mark.asyncio
async def test_chat_session_and_query_flow(authenticated_client, mock_general_user, db_session):
    """
    Tests creating a chat session, sending a query (mocking the AI),
    and verifying credit deduction and message storage.
    """
    # ১. নতুন চ্যাট সেশন তৈরি
    session_response = await authenticated_client.post(
        "/api/chat/sessions", 
        json={"mode": "GENERAL", "title": "Test Legal Chat"}
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["session_id"]

    # ২. এআই (LangGraph) কে মক করা হলো
    with patch("app.api.chat.qa_graph.ainvoke", new_callable=AsyncMock) as mock_qa_graph:
        # এআই-এর ফেক রেসপন্স
        mock_qa_graph.return_value = {
            "final_answer": "According to Section 73 of the Contract Act 1872, you can claim compensation.",
            "retrieved_docs": [
                {"act": "Contract Act 1872", "section": "73", "text": "Compensation for loss..."}
            ]
        }

        # ৩. চ্যাটে প্রশ্ন পাঠানো
        query_response = await authenticated_client.post(
            "/api/chat/query",
            json={"session_id": session_id, "query": "What is the penalty for breach of contract?"}
        )
        
        # ৪. রেসপন্স চেক করা
        assert query_response.status_code == 200
        data = query_response.json()
        
        assert "According to Section 73" in data["response"]["detailed_answer"]
        assert data["credits_source"] == "wallet"
        
        # ৫. ডাটাবেসে ক্রেডিট কাটা হয়েছে কি না চেক করা (মক ইউজারের ১০০ ছিল, ১ কাটার পর ৯৯ হওয়ার কথা)
        await db_session.refresh(mock_general_user)
        assert mock_general_user.credit_balance == 99
        
        # ৬. ডাটাবেসে মেসেজ সেভ হয়েছে কি না চেক করা
        session = await db_session.get(ChatSession, session_id)
        assert session.message_count == 2 # ১টি ইউজার, ১টি এআই
        
        assert mock_qa_graph.called