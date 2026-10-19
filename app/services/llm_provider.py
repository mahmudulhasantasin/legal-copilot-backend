import logging
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from app.config import settings

logger = logging.getLogger(__name__)

def get_llm():
    """Returns an LLM instance with Gemini as primary and Groq as guaranteed fallback."""
    gemini_key = (
        getattr(settings, "GEMINI_API_KEY", None)
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    groq_key = getattr(settings, "GROQ_API_KEY", None) or os.getenv("GROQ_API_KEY")

    primary_llm = None
    fallback_llm = None

    # Fallback: Groq (লিমিট বেশি হওয়ায় llama-3.1-8b-instant ব্যবহার করা হলো)
    if groq_key:
        try:
            fallback_llm = ChatGroq(
                model="llama-3.1-8b-instant", 
                api_key=groq_key,
                temperature=0.2,
                max_retries=1,
                timeout=60.0
            )
            logger.info("Groq fallback LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Groq: {exc}")
    else:
        logger.warning("GROQ_API_KEY is not configured in environment or settings.")

    # Primary: Gemini Flash
    if gemini_key:
        try:
            primary_llm = ChatGoogleGenerativeAI(
                model="gemini-flash-latest",
                google_api_key=gemini_key,
                max_retries=0,          
                timeout=60.0            
            )
            logger.info("Gemini primary LLM initialized successfully.")
        except Exception as exc:
            logger.warning(f"Failed to initialize Gemini: {exc}")

    if primary_llm and fallback_llm:
        # যেকোনো Exception (যেমন 429) হলেই যেন Groq-এ ফলব্যাক করে
        return primary_llm.with_fallbacks([fallback_llm], exceptions_to_handle=(Exception,))
    elif primary_llm:
        return primary_llm
    elif fallback_llm:
        return fallback_llm
    else:
        raise ValueError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured!")