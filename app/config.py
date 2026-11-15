import os
from typing import Optional  # ✅ Optional ইমপোর্ট করা হলো
from dotenv import load_dotenv
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ✅ override=True যোগ করা হলো যাতে .env ফাইলের নতুন ভ্যালু আগেরগুলোকে ওভাররাইড করতে পারে
load_dotenv(override=True)


class Settings(BaseSettings):
    # Relational Database (Supabase PostgreSQL)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Qdrant Vector DB Settings
    QDRANT_URL: str = os.getenv("QDRANT_URL", "")
    QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "")

    # ✅ Hugging Face API Key
    HF_TOKEN: str = os.getenv("HF_TOKEN", "")

    # ✅ নতুন ছোট মডেল এবং কালেকশনের নাম
    QDRANT_COLLECTION_NAME: str = os.getenv("QDRANT_COLLECTION_NAME", "bd_laws_e5_small")
    EMBED_MODEL: str = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-small")

    # Security & JWT Configuration
    # ✅ SEC-AUTH-02: "production" environments must set a real SECRET_KEY — the weak
    # fallback below is only tolerated in development (a warning is logged there).
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "temporary-fallback-secret-key-change-in-env")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    # ✅ SEC-AUTH-01: OTP brute-force protection
    OTP_MAX_ATTEMPTS: int = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))
    OTP_LOCKOUT_MINUTES: int = int(os.getenv("OTP_LOCKOUT_MINUTES", "15"))

    # ✅ SEC-DOS-01: upload size caps (MB)
    MAX_CONTRACT_UPLOAD_MB: int = int(os.getenv("MAX_CONTRACT_UPLOAD_MB", "20"))
    MAX_PROOF_UPLOAD_MB: int = int(os.getenv("MAX_PROOF_UPLOAD_MB", "10"))
    MAX_AVATAR_UPLOAD_MB: int = int(os.getenv("MAX_AVATAR_UPLOAD_MB", "5"))

    # AI Model Keys
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    # ✅ Groq free tier enforces an OTPM (output tokens/min) cap of 1000 — max_tokens must stay below it
    GROQ_MAX_TOKENS: int = int(os.getenv("GROQ_MAX_TOKENS", "950"))
    ZAI_API_KEY: str = os.getenv("ZAI_API_KEY", "")
    ZAI_BASE_URL: str = os.getenv("ZAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
    ZAI_MODEL: str = os.getenv("ZAI_MODEL", "glm-4.5-flash")
    ZAI_DISABLE_THINKING: bool = os.getenv("ZAI_DISABLE_THINKING", "true").strip().lower() in (
        "1", "true", "yes", "on"
    )
    PRIMARY_LLM: str = os.getenv("PRIMARY_LLM", "gemini")
    GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")

    # ✅ Tavily API Key (নতুন যুক্ত করা হলো)
    TAVILY_API_KEY: Optional[str] = os.getenv("TAVILY_API_KEY", None)

    # ✅ Supabase Storage Configuration (অডিটে পাওয়া ৫০০ এরর ফিক্স করার জন্য)
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

    # ✅ Legacy bKash reference config (retained for backward compatibility)
    BKASH_MERCHANT_NUMBER: str = os.getenv("BKASH_MERCHANT_NUMBER", "01700000000")

    # ✅ SSLCommerz Payment Gateway Config (Sandbox / Live)
    SSLCOMMERZ_STORE_ID: str = os.getenv("SSLCOMMERZ_STORE_ID", "")
    SSLCOMMERZ_STORE_PASSWD: str = os.getenv("SSLCOMMERZ_STORE_PASSWD", "")
    SSLCOMMERZ_IS_SANDBOX: bool = os.getenv("SSLCOMMERZ_IS_SANDBOX", "true").strip().lower() in (
        "1", "true", "yes", "on"
    )
    BACKEND_PUBLIC_URL: str = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:7860").rstrip("/")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")

    MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "https://bd-legal-mcp-server.onrender.com/mcp")

    # ✅ Pydantic V2 এর নতুন কনফিগারেশন
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _check_secret_key_for_production(self) -> "Settings":
        """SEC-AUTH-02: refuse to boot production with the insecure fallback secret."""
        is_production = (self.ENVIRONMENT or "").strip().lower() in ("production", "prod")
        if is_production and (
            not self.SECRET_KEY or self.SECRET_KEY == "temporary-fallback-secret-key-change-in-env"
        ):
            raise RuntimeError(
                "SECRET_KEY is required when ENVIRONMENT=production. "
                "Set a strong random value (e.g. `python -c \"import secrets; print(secrets.token_urlsafe(64))\"`)."
            )
        if self.SECRET_KEY == "temporary-fallback-secret-key-change-in-env":
            import logging

            logging.getLogger(__name__).warning(
                "SECRET_KEY falls back to the insecure development default — never use in production."
            )
        return self


settings = Settings()

# Direct exports for backward compatibility
DATABASE_URL = settings.DATABASE_URL
QDRANT_URL = settings.QDRANT_URL
QDRANT_API_KEY = settings.QDRANT_API_KEY
QDRANT_COLLECTION_NAME = settings.QDRANT_COLLECTION_NAME
EMBED_MODEL = settings.EMBED_MODEL
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
GEMINI_API_KEY = settings.GEMINI_API_KEY
GROQ_API_KEY = settings.GROQ_API_KEY
ZAI_API_KEY = settings.ZAI_API_KEY
ZAI_BASE_URL = settings.ZAI_BASE_URL
ZAI_MODEL = settings.ZAI_MODEL
ZAI_DISABLE_THINKING = settings.ZAI_DISABLE_THINKING
PRIMARY_LLM = settings.PRIMARY_LLM
GOOGLE_APPLICATION_CREDENTIALS = settings.GOOGLE_APPLICATION_CREDENTIALS
SUPABASE_URL = settings.SUPABASE_URL
SUPABASE_SERVICE_KEY = settings.SUPABASE_SERVICE_KEY
BKASH_MERCHANT_NUMBER = settings.BKASH_MERCHANT_NUMBER
SSLCOMMERZ_STORE_ID = settings.SSLCOMMERZ_STORE_ID
SSLCOMMERZ_STORE_PASSWD = settings.SSLCOMMERZ_STORE_PASSWD
SSLCOMMERZ_IS_SANDBOX = settings.SSLCOMMERZ_IS_SANDBOX
BACKEND_PUBLIC_URL = settings.BACKEND_PUBLIC_URL
FRONTEND_URL = settings.FRONTEND_URL

# ✅ Tavily Direct Export (প্রয়োজন হলে)
TAVILY_API_KEY = settings.TAVILY_API_KEY
MCP_SERVER_URL = settings.MCP_SERVER_URL
