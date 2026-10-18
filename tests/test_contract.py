import pytest
from unittest.mock import patch
from app.models.memo import ContractMemo, Contract

@pytest.mark.asyncio
async def test_upload_contract_and_audit(authenticated_client, db_session):
    """
    Tests the contract upload endpoint by mocking Supabase storage, 
    text extraction, and the AI LangGraph analysis.
    """
    # ১. এক্সটার্নাল সার্ভিসগুলোকে মক (Mock) করা হলো
    with patch("app.api.contract._upload_to_supabase") as mock_upload, \
         patch("app.api.contract.extract_text_from_file") as mock_extract, \
         patch("app.api.contract.analyze_contract_risk_async") as mock_ai:
        
        # মক রিটার্ন ভ্যালু সেট করা
        mock_upload.return_value = "u_1_dummy_path.pdf"
        mock_extract.return_value = "This is a dummy employment contract text."
        
        # এআই-এর ফেক রেসপন্স
        mock_ai.return_value = {
            "overall_risk_score": "HIGH",
            "memo_summary": "The contract has high risks related to termination.",
            "detected_risks": [
                {
                    "clause_title": "Termination",
                    "severity": "HIGH",
                    "flagged_text": "Employee can be terminated without notice.",
                    "violated_law": "Labour Act 2006, Section 26",
                    "explanation": "Violates mandatory notice period.",
                    "recommendation": "Provide 30 days notice."
                }
            ]
        }

        # ২. ফাইল আপলোড রিকোয়েস্ট পাঠানো
        file_content = b"dummy pdf content"
        files = {"file": ("test_contract.pdf", file_content, "application/pdf")}
        
        response = await authenticated_client.post(
            "/api/contracts/upload",
            files=files,
            data={"title": "Test Employment Contract"}
        )
        
        # ৩. রেসপন্স ভ্যালিডেশন
        assert response.status_code == 200
        data = response.json()
        
        assert data["title"] == "Test Employment Contract"
        assert data["overall_risk"] == "HIGH"
        assert data["risk_score"] > 0
        assert len(data["clauses"]) == 1
        assert data["clauses"][0]["clause_title"] == "Termination"
        
        # ৪. ডাটাবেসে সেভ হয়েছে কিনা তা চেক করা
        contract = await db_session.get(Contract, data["contract_id"])
        assert contract is not None
        assert contract.title == "Test Employment Contract"
        
        memo = await db_session.get(ContractMemo, data["memo_id"])
        assert memo is not None
        assert memo.overall_risk.value == "HIGH"

        # ৫. মকগুলো আসলেই কল হয়েছে কিনা নিশ্চিত হওয়া
        assert mock_upload.called
        assert mock_extract.called
        assert mock_ai.called