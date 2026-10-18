import os
from dotenv import load_dotenv
# ✅ SettingsConfigDict ইমপোর্ট করা হলো
from pydantic_settings import BaseSettings, SettingsConfigDict

# ✅ override=True যোগ করা হলো যাতে .env ফাইলের নতুন ভ্যালু আগেরগুলোকে ওভাররাইড করতে পারে
load_dotenv(override=True)


class Settings(BaseSettings):
    # Relational Database (Supabase PostgreSQL)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Qdrant Vector DB Settings
    QDRANT_URL: str = os.getenv("QDRANT_URL", "")
    QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "")
    
    # ✅ নতুন ১০২৪ ডাইমেনশন কালেকশন এবং মডেলের নাম ডিফল্ট হিসেবে বসানো হলো
    QDRANT_COLLECTION_NAME: str = os.getenv("QDRANT_COLLECTION_NAME", "bd_laws_v2")
    EMBED_MODEL: str = os.getenv("EMBED_MODEL", "intfloat/multilingual-e5-large")

    # Security & JWT Configuration
    SECRET_KEY: str = os.getenv("SECRET_KEY", "temporary-fallback-secret-key-change-in-env")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

    # AI Model Keys
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")

    # ✅ Supabase Storage Configuration (অডিটে পাওয়া ৫০০ এরর ফিক্স করার জন্য)
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

    # ✅ bKash Payment Config (পেমেন্টের সময় এই নম্বরটি দেখানো হবে)
    BKASH_MERCHANT_NUMBER: str = os.getenv("BKASH_MERCHANT_NUMBER", "01700000000")

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