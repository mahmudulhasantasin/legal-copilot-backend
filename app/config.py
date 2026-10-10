import os
from dotenv import load_dotenv

load_dotenv()

# Database & Storage Configurations
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./legal_copilot.db")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./chroma_db")
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# JWT Security Configurations
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key-for-jwt-token-signing")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1 day

# AI / Google Vision API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")