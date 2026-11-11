"""
Production-grade async SSLCommerz v4 client.

Supports both Sandbox and Live modes via settings:
  - SSLCOMMERZ_IS_SANDBOX=True  -> https://sandbox.sslcommerz.com
  - SSLCOMMERZ_IS_SANDBOX=False -> https://securepay.sslcommerz.com
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _sanitize_ssl_text(value: Optional[str], default: str, max_len: int) -> str:
    """Sanitize customer/product text to ASCII characters accepted by SSLCommerz v4."""
    cleaned = re.sub(r"[^A-Za-z0-9 .,\-_()/]", " ", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 2:
        return default[:max_len]
    return cleaned[:max_len]


def _sanitize_ssl_email(value: Optional[str]) -> str:
    """Return a valid <=50 char email for SSLCommerz without chopping long domains."""
    email = (value or "").strip()
    if email and len(email) <= 50 and _EMAIL_RE.fullmatch(email):
        return email
    return "billing@contractmind.ai"


def _sanitize_ssl_phone(value: Optional[str]) -> str:
    """Normalize customer phone into digits suitable for SSLCommerz v4."""
    digits = re.sub(r"[^\d]", "", value or "")
    if digits.startswith("00880") and len(digits) == 15:
        digits = "0" + digits[5:]
    elif digits.startswith("880") and len(digits) == 13:
        digits = "0" + digits[3:]
    if len(digits) < 6 or len(digits) > 20:
        return "01700000000"
    return digits[:20]


class SSLCommerzConfigError(Exception):
    """Raised when SSLCommerz store credentials are missing or invalid."""


class SSLCommerzGatewayError(Exception):
    """Raised when SSLCommerz API returns an error or cannot be reached."""


_SAFE_RAW_FIELDS = (
    "status",
    "tran_id",
    "val_id",
    "amount",
    "store_amount",
    "currency",
    "currency_type",
    "currency_amount",
    "bank_tran_id",
    "card_type",
    "card_no",
    "card_issuer",
    "card_brand",
    "card_sub_brand",
    "card_issuer_country",
    "tran_date",
    "risk_level",
    "risk_title",
    "validated_on",
    "gw_version",
)


class SSLCommerzService:
    """Async client for SSLCommerz v4 Session, Validation, Query, and IPN signature checks."""

    def __init__(self) -> None:
        self.timeout = httpx.Timeout(20.0, connect=10.0)

    @property
    def store_id(self) -> str:
        return (settings.SSLCOMMERZ_STORE_ID or "").strip()

    @property
    def store_passwd(self) -> str:
        return (settings.SSLCOMMERZ_STORE_PASSWD or "").strip()

    @property
    def is_sandbox(self) -> bool:
        return bool(settings.SSLCOMMERZ_IS_SANDBOX)

    @property
    def base_url(self) -> str:
        return (
            "https://sandbox.sslcommerz.com"
            if self.is_sandbox
            else "https://securepay.sslcommerz.com"
        )

    def is_configured(self) -> bool:
        return bool(self.store_id and self.store_passwd)

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise SSLCommerzConfigError(
                "SSLCommerz credentials are not configured. "
                "Set SSLCOMMERZ_STORE_ID and SSLCOMMERZ_STORE_PASSWD in backend .env."
            )

    def verify_ipn_signature(self, post_data: Dict[str, Any]) -> bool:
        """
        Verify SSLCommerz IPN / callback MD5 signature using verify_sign & verify_key.
        Algorithm matches SSLCommerz official v4 specification.
        """
        if not self.store_passwd:
            return False

        verify_sign = str(post_data.get("verify_sign") or "").strip()
        verify_key = str(post_data.get("verify_key") or "").strip()
        if not verify_sign or not verify_key:
            return False

        keys = [k.strip() for k in verify_key.split(",") if k.strip()]
        if not keys:
            return False

        store_passwd_md5 = hashlib.md5(self.store_passwd.encode("utf-8")).hexdigest()
        data_for_hash: Dict[str, str] = {"store_passwd": store_passwd_md5}

        for key in keys:
            val = post_data.get(key, "")
            data_for_hash[key] = "" if val is None else str(val)

        sorted_pairs = sorted(data_for_hash.items(), key=lambda item: item[0])
        hash_string = "&".join(f"{k}={v}" for k, v in sorted_pairs)
        computed_sign = hashlib.md5(hash_string.encode("utf-8")).hexdigest()

        return hmac.compare_digest(computed_sign.lower(), verify_sign.lower())

    async def create_session(
        self,
        *,
        tran_id: str,
        amount_bdt: Decimal,
        success_url: str,
        fail_url: str,
        cancel_url: str,
        ipn_url: str,
        customer_name: str,
        customer_email: str,
        customer_phone: str,
        product_name: str,
        product_category: str = "Legal AI Credits",
        value_a: str = "",
        value_b: str = "",
        value_c: str = "",
    ) -> Dict[str, Any]:
        """Create an SSLCommerz v4 checkout session and return GatewayPageURL & sessionkey."""
        self._require_configured()

        payload = {
            "store_id": self.store_id,
            "store_passwd": self.store_passwd,
            "total_amount": f"{Decimal(amount_bdt):.2f}",
            "currency": "BDT",
            "tran_id": tran_id,
            "success_url": success_url,
            "fail_url": fail_url,
            "cancel_url": cancel_url,
            "ipn_url": ipn_url,
            "emi_option": 0,
            "cus_name": _sanitize_ssl_text(customer_name, "Customer", 50),
            "cus_email": _sanitize_ssl_email(customer_email),
            "cus_phone": _sanitize_ssl_phone(customer_phone),
            "cus_add1": "Dhaka",
            "cus_city": "Dhaka",
            "cus_country": "Bangladesh",
            "shipping_method": "NO",
            "num_of_item": 1,
            "product_name": _sanitize_ssl_text(product_name, "Credit Package", 250),
            "product_category": _sanitize_ssl_text(product_category, "Legal AI Credits", 100),
            "product_profile": "non-physical-goods",
            "value_a": value_a[:250],
            "value_b": value_b[:250],
            "value_c": value_c[:250],
        }

        url = f"{self.base_url}/gwprocess/v4/api.php"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, data=payload)
        except httpx.HTTPError as exc:
            logger.error(f"[SSLCommerz] Session request network failure: {exc}")
            raise SSLCommerzGatewayError("Unable to reach SSLCommerz payment gateway.") from exc

        if resp.status_code != 200:
            logger.error(f"[SSLCommerz] Session HTTP {resp.status_code}: {resp.text[:300]}")
            raise SSLCommerzGatewayError(f"SSLCommerz gateway returned HTTP {resp.status_code}.")

        try:
            data = resp.json()
        except ValueError as exc:
            logger.error(f"[SSLCommerz] Invalid JSON from session API: {resp.text[:300]}")
            raise SSLCommerzGatewayError("Invalid response from SSLCommerz gateway.") from exc

        if str(data.get("status", "")).upper() != "SUCCESS" or not data.get("GatewayPageURL"):
            reason = data.get("failedreason") or data.get("status") or "Unknown gateway error"
            logger.warning(f"[SSLCommerz] Session creation failed for {tran_id}: {reason}")
            raise SSLCommerzGatewayError(f"SSLCommerz session rejected: {reason}")

        return {
            "gateway_url": str(data["GatewayPageURL"]),
            "session_key": str(data.get("sessionkey") or ""),
            "status": str(data.get("status") or "SUCCESS"),
        }

    async def validate_order(self, val_id: str) -> Dict[str, Any]:
        """Validate a transaction with SSLCommerz Order Validation API using val_id."""
        self._require_configured()
        if not val_id or not val_id.strip():
            raise SSLCommerzGatewayError("Missing val_id for SSLCommerz order validation.")

        url = f"{self.base_url}/validator/api/validationserverAPI.php"
        params = {
            "val_id": val_id.strip(),
            "store_id": self.store_id,
            "store_passwd": self.store_passwd,
            "format": "json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.error(f"[SSLCommerz] Validation API network failure: {exc}")
            raise SSLCommerzGatewayError("Unable to reach SSLCommerz validation server.") from exc

        if resp.status_code != 200:
            raise SSLCommerzGatewayError(f"SSLCommerz validation returned HTTP {resp.status_code}.")

        try:
            return resp.json()
        except ValueError as exc:
            raise SSLCommerzGatewayError("Invalid JSON from SSLCommerz validation API.") from exc

    async def query_by_tran_id(self, tran_id: str) -> Optional[Dict[str, Any]]:
        """
        Query SSLCommerz Transaction Query API by merchant tran_id.
        Returns the most recent valid/matched transaction element, or None if not found.
        """
        self._require_configured()
        url = f"{self.base_url}/validator/api/merchantTransIDvalidationAPI.php"
        params = {
            "tran_id": tran_id.strip(),
            "store_id": self.store_id,
            "store_passwd": self.store_passwd,
            "format": "json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.error(f"[SSLCommerz] Transaction query failed: {exc}")
            raise SSLCommerzGatewayError("Unable to reach SSLCommerz transaction query API.") from exc

        if resp.status_code != 200:
            raise SSLCommerzGatewayError(f"SSLCommerz query returned HTTP {resp.status_code}.")

        try:
            data = resp.json()
        except ValueError as exc:
            raise SSLCommerzGatewayError("Invalid JSON from SSLCommerz query API.") from exc

        elements = data.get("element")
        if isinstance(elements, list) and elements:
            # Prefer VALID or VALIDATED record if multiple attempts exist
            for item in elements:
                if str(item.get("status", "")).upper() in ("VALID", "VALIDATED"):
                    return item
            return elements[0]
        if isinstance(data, dict) and data.get("tran_id"):
            return data
        return None

    @staticmethod
    def sanitize_gateway_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Strip any sensitive/unexpected keys and persist only safe gateway telemetry."""
        return {
            key: payload[key]
            for key in _SAFE_RAW_FIELDS
            if key in payload and payload[key] not in (None, "")
        }

    @staticmethod
    def parse_decimal(value: Any) -> Optional[Decimal]:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def extract_telemetry(validated: Dict[str, Any]) -> Dict[str, Any]:
        store_amount = SSLCommerzService.parse_decimal(validated.get("store_amount"))
        return {
            "val_id": str(validated.get("val_id") or "")[:120] or None,
            "bank_tran_id": str(validated.get("bank_tran_id") or "")[:120] or None,
            "card_type": str(validated.get("card_type") or "")[:80] or None,
            "card_issuer": str(validated.get("card_issuer") or "")[:120] or None,
            "card_brand": str(validated.get("card_brand") or "")[:60] or None,
            "store_amount_bdt": store_amount,
            "gateway_status": str(validated.get("status") or "")[:40] or None,
            "gateway_validated_at": datetime.now(timezone.utc),
            "gateway_raw_response": SSLCommerzService.sanitize_gateway_payload(validated),
        }


sslcommerz_service = SSLCommerzService()
