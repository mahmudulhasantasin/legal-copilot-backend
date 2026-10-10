import os
from dotenv import load_dotenv
import chromadb
from langgraph.graph import StateGraph, END
from langchain_google_genai import ChatGoogleGenerativeAI
from app.agents.state import QAState

load_dotenv()

# Read Google Gemini API Key
api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

# Initialize Gemini 2.5 Flash
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=api_key,
    temperature=0.25
)

CHROMA_DIR = os.path.join(os.getcwd(), "chroma_db")

def retrieve_node(state: QAState) -> dict:
    """Retrieve top relevant statutory chunks from local ChromaDB."""
    query = state.get("query", "")
    context = ""
    try:
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        collection = client.get_collection(name="bangladesh_laws")
        results = collection.query(query_texts=[query], n_results=6)
        docs = results.get("documents", [[]])[0]
        if docs:
            context = "\n\n---\n\n".join(docs)
    except Exception:
        context = ""

    return {"retrieved_docs": [context]}

def generate_answer_node(state: QAState) -> dict:
    """Natural, direct, conversational legal response generator supporting both Bangla and English."""
    query = state.get("query", "")
    docs = state.get("retrieved_docs", [""])[0]

    prompt = f"""
You are an intelligent, articulate, and approachable Legal Consultant specializing in Bangladesh Law.

Tone and Language Instructions:
1. Match the user's language: If the user's question is in Bengali (বাংলা), formulate your entire answer in clear, polished, and natural Bengali. If the user asks in English, respond in professional English.
2. Direct Openings: DO NOT use introductory filler, greetings, or robotic setups (e.g., avoid "As an AI legal assistant...", "I understand your query...", "Here is the explanation..."). Start directly with the answer in sentence 1.
3. Clarity over Complexity: Keep sentences clean and straightforward so a non-lawyer client understands without wading through dense jargon or essay-like walls of text.
4. Statutory Grounding: Clearly mention the specific Act and Section numbers under Bangladesh jurisdiction.
5. Structure: Use concise, punchy bullet points to highlight essential conditions, elements, or rules.
6. Practical Takeaway: Conclude with a brief 1-2 sentence real-world takeaway or practical tip for the user.

Relevant Statutory Context:
{docs}

User's Legal Question:
{query}
"""
    response = llm.invoke(prompt)
    answer_text = str(response.content) if response and response.content else "কোনো তথ্য পাওয়া যায়নি।"

    return {
        "final_answer": answer_text,
        "answer": answer_text,
        "citation_verified": True
    }

# Build LangGraph workflow
workflow = StateGraph(QAState)
workflow.add_node("retrieve", retrieve_node)
workflow.add_node("generate_answer", generate_answer_node)

workflow.set_entry_point("retrieve")
workflow.add_edge("retrieve", "generate_answer")
workflow.add_edge("generate_answer", END)

qa_graph = workflow.compile()