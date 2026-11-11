import os
from typing import Optional  # ✅ Optional ইমপোর্ট করা হলো
from dotenv import load_dotenv
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
    SECRET_KEY: str = os.getenv("SECRET_KEY", "temporary-fallback-secret-key-change-in-env")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    # AI Model Keys
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
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
