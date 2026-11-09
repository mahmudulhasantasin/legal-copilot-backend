# app/services/email_service.py
"""
Email delivery for ContractMind.
"""

import os
import sys
import time
import base64
import asyncio
import logging
import smtplib
import threading
import json  # ✅ নতুন যুক্ত করা হয়েছে
from email.message import EmailMessage

from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "gmail_api").lower()   # gmail_api | smtp

# ── Gmail API ──
GMAIL_SCOPES           = ["https://www.googleapis.com/auth/gmail.send"]
GMAIL_CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
GMAIL_TOKEN_FILE       = os.getenv("GMAIL_TOKEN_FILE", "token.json")
GMAIL_SENDER           = os.getenv("GMAIL_SENDER", os.getenv("SMTP_USER", ""))
GMAIL_HEADLESS         = os.getenv("GMAIL_HEADLESS", "0") == "1"

# ── SMTP (optional backend) ──
SMTP_HOST     = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")


# ──────────────────────────────────────────────────────────
# GMAIL API BACKEND
# ──────────────────────────────────────────────────────────
_gmail_lock: threading.Lock = threading.Lock()
_gmail_service = None


def _load_gmail_service():
    """Build (and cache) the Gmail API client."""
    global _gmail_service
    if _gmail_service is not None:
        return _gmail_service

    with _gmail_lock:
        if _gmail_service is not None:
            return _gmail_service

        creds = None
        
        # ✅ FIX: চেক করা হচ্ছে টোকেনটি ফাইল নাকি সরাসরি JSON স্ট্রিং (Render-এর জন্য)
        if GMAIL_TOKEN_FILE.strip().startswith("{"):
            try:
                info = json.loads(GMAIL_TOKEN_FILE)
                creds = Credentials.from_authorized_user_info(info, GMAIL_SCOPES)
                logger.info("[GMAIL AUTH] Loaded token from Environment Variable (JSON).")
            except Exception as e:
                logger.error(f"[GMAIL AUTH] Failed to parse token JSON from env: {e}")
        elif os.path.exists(GMAIL_TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, GMAIL_SCOPES)
            logger.info("[GMAIL AUTH] Loaded token from local file.")

        # Refresh an expired token, or run the interactive flow once.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    logger.info("[GMAIL AUTH] Access token refreshed.")
                except Exception as e:
                    logger.error(f"[GMAIL AUTH] Refresh failed: {e}")
                    creds = None

            if not creds:
                if GMAIL_HEADLESS:
                    raise RuntimeError(
                        "No valid token and GMAIL_HEADLESS=1. "
                        "Generate token.json locally and paste its content into GMAIL_TOKEN_FILE."
                    )
                
                # লোকাল সেটআপের জন্য (যদি ব্রাউজার ওপেন করার দরকার হয়)
                if GMAIL_CREDENTIALS_FILE.strip().startswith("{"):
                    info = json.loads(GMAIL_CREDENTIALS_FILE)
                    flow = InstalledAppFlow.from_client_config(info, GMAIL_SCOPES)
                    creds = flow.run_local_server(port=0)
                elif os.path.exists(GMAIL_CREDENTIALS_FILE):
                    flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDENTIALS_FILE, GMAIL_SCOPES)
                    creds = flow.run_local_server(port=0)
                else:
                    raise FileNotFoundError("Missing credentials.json or env JSON.")

            # লোকাল পিসিতে নতুন টোকেন সেভ করা (Render-এ ফাইল রাইট করা যাবে না, তাই চেক করা হচ্ছে)
            if not os.getenv("RENDER") and not os.getenv("RENDER_EXTERNAL_URL"):
                with open("token.json", "w") as f:
                    f.write(creds.to_json())

        _gmail_service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        logger.info("[GMAIL AUTH] Service ready.")
        return _gmail_service


def _send_via_gmail_api_sync(message: EmailMessage, max_attempts: int = 3) -> bool:
    """Send one message via Gmail API. Blocking — run via asyncio.to_thread."""
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    payload = {"raw": raw}

    for attempt in range(1, max_attempts + 1):
        try:
            service = _load_gmail_service()
            sent = (
                service.users()
                .messages()
                .send(userId="me", body=payload)
                .execute()
            )
            logger.info(f"[GMAIL API] Sent id={sent.get('id')} (attempt {attempt})")
            return True

        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (429, 500, 502, 503) and attempt < max_attempts:
                delay = 2 ** (attempt - 1)
                logger.warning(f"[GMAIL API] {status} — retrying in {delay}s")
                time.sleep(delay)
                continue
            logger.error(f"[GMAIL API] HttpError {status}: {e}")
            return False

        except Exception as e:
            logger.error(f"[GMAIL API] Failed: {e}")
            return False

    return False


# ──────────────────────────────────────────────────────────
# SMTP BACKEND (optional)
# ──────────────────────────────────────────────────────────
def _send_via_smtp_sync(message: EmailMessage) -> bool:
    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(message)
        return True
    except Exception as e:
        logger.error(f"[SMTP ERROR] Failed: {e}")
        return False


# ──────────────────────────────────────────────────────────
# UNIFIED SENDER
# ──────────────────────────────────────────────────────────
def _demo_fallback(recipient_email: str, subject: str, body: str) -> None:
    logger.warning("=" * 60)
    logger.warning("🚨 EMAIL FAILED — DEMO FALLBACK ACTIVATED:")
    logger.warning(f"TO: {recipient_email}")
    logger.warning(f"SUBJECT: {subject}")
    logger.warning(f"BODY:\n{body}")
    logger.warning("=" * 60)

    try:
        from datetime import datetime
        with open("demo_emails.log", "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now()}] TO: {recipient_email}\n")
            f.write(f"SUBJECT: {subject}\n{body}\n{'-' * 40}\n")
    except Exception:
        pass


async def _send_email(recipient_email: str, subject: str, body: str) -> bool:
    sender = GMAIL_SENDER or SMTP_USER
    if not sender:
        logger.error("[EMAIL ERROR] No sender configured (set GMAIL_SENDER or SMTP_USER).")
        _demo_fallback(recipient_email, subject, body)
        return False

    message = EmailMessage()
    message["From"] = f"ContractMind <{sender}>"
    message["To"] = recipient_email
    message["Subject"] = subject
    message.set_content(body)

    if EMAIL_BACKEND == "smtp":
        if not SMTP_USER or not SMTP_PASSWORD:
            logger.error("[EMAIL ERROR] SMTP credentials missing.")
            _demo_fallback(recipient_email, subject, body)
            return False
        success = await asyncio.to_thread(_send_via_smtp_sync, message)
    else:
        success = await asyncio.to_thread(_send_via_gmail_api_sync, message)

    if success:
        logger.info(f"[EMAIL] Sent to {recipient_email} via {EMAIL_BACKEND}")
    else:
        _demo_fallback(recipient_email, subject, body)

    return success


# ──────────────────────────────────────────────────────────
# EMAIL TRIGGERS
# ──────────────────────────────────────────────────────────
async def send_otp_email(recipient_email: str, otp_code: str) -> bool:
    subject = "Your ContractMind Verification Code"
    body = (
        f"Hello,\n\n"
        f"Your OTP code is: {otp_code}\n\n"
        f"This code expires in 5 minutes.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

# ✅ নতুন: পাসওয়ার্ড রিসেট ইমেইল
async def send_password_reset_email(recipient_email: str, otp_code: str) -> bool:
    subject = "Reset Your ContractMind Password"
    body = (
        f"Hello,\n\n"
        f"We received a request to reset your password.\n"
        f"Your verification code is: {otp_code}\n\n"
        f"This code expires in 5 minutes. If you did not request this, please ignore this email.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_lawyer_offer_email(recipient_email: str, contract_title: str, payout_bdt: float, hours: int = 3) -> bool:
    subject = "New Contract Review Offer"
    body = (
        f"Hello,\n\n"
        f"You have been offered a contract review for: '{contract_title}'.\n"
        f"Payout: BDT {payout_bdt}\n\n"
        f"You have {hours} hours to accept this offer in your dashboard.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_client_lawyer_assigned_email(recipient_email: str, lawyer_name: str, contract_title: str) -> bool:
    subject = "Lawyer Assigned to Your Contract"
    body = (
        f"Hello,\n\n"
        f"Advocate {lawyer_name} has accepted your request and is reviewing "
        f"'{contract_title}'.\n"
        f"You will be notified as soon as the review is complete.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_client_verification_complete_email(recipient_email: str, lawyer_name: str, contract_title: str) -> bool:
    subject = "Contract Verification Complete"
    body = (
        f"Hello,\n\n"
        f"Advocate {lawyer_name} has successfully verified '{contract_title}'.\n"
        f"Log in to your dashboard to view the lawyer's notes and digital signature.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_lawyer_approved_email(recipient_email: str, full_name: str) -> bool:
    subject = "Your Lawyer Account is Verified!"
    body = (
        f"Dear {full_name},\n\n"
        f"Congratulations! Your application to become a verified lawyer on ContractMind has been approved by the Admin.\n"
        f"You can now log in to your account and access the Lawyer Workspace to start reviewing contracts.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_lawyer_rejected_email(recipient_email: str, full_name: str, reason: str) -> bool:
    subject = "Update on Your Lawyer Application - ContractMind"
    body = (
        f"Dear {full_name},\n\n"
        f"Thank you for your application to join ContractMind as a verified lawyer.\n\n"
        f"After reviewing your credentials, we regret to inform you that your application could not be approved at this time.\n\n"
        f"Reason for rejection:\n{reason}\n\n"
        f"If you believe this decision was made in error or have updated credentials to provide, please contact our support team.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_enterprise_approved_email(recipient_email: str, admin_name: str, company_name: str, referral_code: str) -> bool:
    subject = f"Your Enterprise Account is Verified: {company_name}"
    body = (
        f"Dear {admin_name},\n\n"
        f"Congratulations! Your enterprise registration for '{company_name}' has been verified and approved by the System Admin.\n\n"
        f"Your organization is now active. Team members can register and join your organization using your referral code:\n\n"
        f"  Team Referral Code: {referral_code}\n\n"
        f"You can now access your Enterprise Admin Dashboard to approve team members, assign credit quotas, and configure company playbooks.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_enterprise_rejected_email(recipient_email: str, admin_name: str, company_name: str, reason: str) -> bool:
    subject = f"Update on Your Enterprise Application: {company_name}"
    body = (
        f"Dear {admin_name},\n\n"
        f"Thank you for submitting an enterprise application for '{company_name}'.\n\n"
        f"After reviewing the company details and verification documents, we are unable to approve your enterprise account at this time.\n\n"
        f"Reason for rejection:\n{reason}\n\n"
        f"If you have questions or require further assistance, please contact our support team.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_member_approved_email(recipient_email: str, full_name: str, company_name: str, role_title: str) -> bool:
    subject = f"Membership Approved: Welcome to {company_name} on ContractMind"
    body = (
        f"Dear {full_name},\n\n"
        f"Great news! Your request to join '{company_name}' has been approved by your Company Admin.\n\n"
        f"Assigned Role: {role_title}\n\n"
        f"You can now log in to ContractMind and access your organization's legal copilot, shared templates, and assigned credit quota.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_member_rejected_email(recipient_email: str, full_name: str, company_name: str, reason: str) -> bool:
    subject = f"Membership Request Update: {company_name}"
    body = (
        f"Dear {full_name},\n\n"
        f"Your request to join '{company_name}' as a team member has been declined by the Company Admin.\n\n"
        f"Reason:\n{reason}\n\n"
        f"If you believe this was done in error, please contact your organization administrator.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)

async def send_sla_refund_email(recipient_email: str, contract_title: str, credits: int) -> bool:
    subject = "Refund Processed - Verification Timeout"
    body = (
        f"Hello,\n\n"
        f"We were unable to assign a lawyer to '{contract_title}' within the "
        f"3-day window.\n"
        f"Your {credits} credits have been fully refunded to your wallet.\n\n"
        f"- ContractMind Team"
    )
    return await _send_email(recipient_email, subject, body)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if "--auth" in sys.argv:
        _load_gmail_service()
        print(f"Auth complete. Wrote {GMAIL_TOKEN_FILE}")

    elif "--test" in sys.argv:
        to_addr = sys.argv[2] if len(sys.argv) > 2 else (GMAIL_SENDER or SMTP_USER)
        if not to_addr:
            print("Provide a recipient: --test you@example.com")
            sys.exit(1)
        ok = asyncio.run(send_otp_email(to_addr, "123456"))
        print(f"\nSend result: {ok}")
        if not ok:
            print("Check demo_emails.log and the terminal log above for the fallback.")

    else:
        print(
            "Usage:\n"
            "  python -m app.services.email_service --auth\n"
            "  python -m app.services.email_service --test recipient@example.com"
        )
