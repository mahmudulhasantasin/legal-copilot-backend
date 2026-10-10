import os
import chromadb
from chromadb.config import Settings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from typing import List, Dict, Any

CHROMA_DATA_PATH = os.path.join(os.getcwd(), "chroma_db")

class ChromaVectorManager:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=CHROMA_DATA_PATH)
        self.collection = self.client.get_or_create_collection(name="bangladesh_statutes")
        
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model="models/text-embedding-004",
            google_api_key=api_key
        )

    def add_statutes(self, documents: List[Dict[str, Any]]):
        """বাংলাদেশের আইনি ধারাগুলো ভেক্টর ডেটাবেজে এমবেড করে সংরক্ষণ করে।"""
        if not documents:
            return

        ids = [doc["id"] for doc in documents]
        texts = [f"{doc['act']} {doc['section']}: {doc['title']}\n{doc['text']}" for doc in documents]
        metadatas = [{"act": doc["act"], "section": doc["section"], "title": doc["title"]} for doc in documents]
        
        # জেমিনি দিয়ে এমবেডিং তৈরি
        vectors = self.embeddings.embed_documents(texts)

        self.collection.upsert(
            ids=ids,
            embeddings=vectors,
            documents=texts,
            metadatas=metadatas
        )

    def similarity_search(self, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
        """সেমান্টিক ভেক্টর সার্চ।"""
        count = self.collection.count()
        if count == 0:
            return []

        query_vector = self.embeddings.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=min(top_k, count)
        )

        matched_docs = []
        if results and results["documents"]:
            for i in range(len(results["documents"][0])):
                matched_docs.append({
                    "id": results["ids"][0][i],
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i] if "distances" in results and results["distances"] else 0.0
                })
        return matched_docs

vector_manager = ChromaVectorManager()