import pytest
import uuid  # <-- নতুন যুক্ত করা হয়েছে (ইউনিক প্যাকেজ নামের জন্য)
from app.models.playbook import CreditPackage, CreditPackageTier
from app.models.audit import Payment

@pytest.mark.asyncio
async def test_payment_flow(authenticated_client, db_session):
    """
    Tests the bKash manual payment flow: List Packages -> Initiate -> Submit TrxID.
    """
    # ✅ ইউনিক প্যাকেজের নাম তৈরি (যাতে বারবার রান করলেও এরর না আসে)
    pkg_name = f"Test Pack {uuid.uuid4().hex[:4]}"
    
    # ১. টেস্ট ডাটাবেসে একটি ডামি প্যাকেজ তৈরি করা
    pkg = CreditPackage(
        name=pkg_name,
        tier=CreditPackageTier.STARTER,
        price_bdt=500.00,
        base_credits=400,
        bonus_credits=50,
        applies_to="ALL",
        is_active=True
    )
    db_session.add(pkg)
    await db_session.commit()
    await db_session.refresh(pkg)

    # ২. প্যাকেজ লিস্ট চেক করা
    list_response = await authenticated_client.get("/api/payments/packages")
    assert list_response.status_code == 200
    assert len(list_response.json()) >= 1
    
    # আমাদের তৈরি করা প্যাকেজটি লিস্টে আছে কি না চেক করা
    package_names = [p["name"] for p in list_response.json()]
    assert pkg_name in package_names

    # ৩. পেমেন্ট ইনিশিয়েট করা
    init_response = await authenticated_client.post(
        "/api/payments/initiate",
        json={"package_id": pkg.id}
    )
    assert init_response.status_code == 201
    init_data = init_response.json()
    
    payment_id = init_data["payment_id"]
    assert init_data["amount_bdt"] == 500.00
    assert init_data["total_credits"] == 450

    # ৪. bKash TrxID সাবমিট করা
    trx_response = await authenticated_client.post(
        f"/api/payments/{payment_id}/submit-trx",
        json={"trx_id": "BKASH12345678", "sender_number": "01711223344"}
    )
    assert trx_response.status_code == 200
    assert trx_response.json()["current_status"] == "PENDING_VERIFICATION"

    # ৫. ডাটাবেসে পেমেন্ট স্ট্যাটাস আপডেট হয়েছে কি না চেক করা
    payment = await db_session.get(Payment, payment_id)
    assert payment is not None
    assert payment.status.value == "PENDING_VERIFICATION"
    assert payment.trx_id == "BKASH12345678"