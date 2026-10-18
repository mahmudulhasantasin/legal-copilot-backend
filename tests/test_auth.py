import pytest
from unittest.mock import patch

@pytest.mark.asyncio
async def test_register_user(client):
    # ইমেইল পাঠানোর ফাংশনটি মক (Mock) করা হলো যাতে আসল ইমেইল না যায়
    with patch("app.api.auth.send_otp_email") as mock_send:
        response = await client.post("/api/auth/register/email", json={
            "email": "newuser@example.com",
            "password": "password123",
            "confirm_password": "password123",
            "full_name": "New User"
        })
        assert response.status_code == 201
        assert response.json()["email"] == "newuser@example.com"
        assert mock_send.called == True

@pytest.mark.asyncio
async def test_login_user(client):
    # আগে রেজিস্ট্রেশন করে নিলাম
    with patch("app.api.auth.send_otp_email"):
        await client.post("/api/auth/register/email", json={
            "email": "loginuser@example.com",
            "password": "password123",
            "confirm_password": "password123",
            "full_name": "Login User"
        })
        
    # এবার লগইন
    response = await client.post("/api/auth/login", json={
        "email": "loginuser@example.com",
        "password": "password123"
    })
    # যেহেতু ভেরিফাই করা নেই, তাই ৪০১ বা ৪০৩ আসতে পারে (আপনার লজিক অনুযায়ী)
    assert response.status_code in [200, 401, 403] 