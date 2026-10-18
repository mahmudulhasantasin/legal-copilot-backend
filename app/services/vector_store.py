import os
from typing import Any, Dict, List
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from app.config import settings


class QdrantVectorManager:
    """Manages Qdrant vector operations and FastEmbed embeddings."""

    def __init__(self):
        self.qdrant_url = getattr(settings, "QDRANT_URL", None)
        self.qdrant_api_key = getattr(settings, "QDRANT_API_KEY", None)
        self.collection_name = getattr(settings, "QDRANT_COLLECTION_NAME", "law_documents")

        # ✅ Local vs Cloud Logic
        if self.qdrant_url and self.qdrant_api_key:
            print("Connecting to Qdrant Cloud...")
            self.client = QdrantClient(url=self.qdrant_url, api_key=self.qdrant_api_key, timeout=60.0)
        else:
            print("💾Using Local Qdrant Database (./local_qdrant_data)...")
            # Creates a folder on your PC to store vectors locally
            self.client = QdrantClient(path="./local_qdrant_data")

        # ✅ Multilingual Model (Supports Bengali + English)
        # This model outputs 1024 dimensions
        model_name = getattr(settings, "EMBED_MODEL", "intfloat/multilingual-e5-large")
        self.embedding_model = TextEmbedding(model_name=model_name)
        self.dimension = 1024 

        # Verify collection exists
        try:
            collections = [c.name for c in self.client.get_collections().collections]
            if self.collection_name not in collections:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.dimension, distance=Distance.COSINE
                    ),
                )
                print(f"Created new collection '{self.collection_name}' with dimension {self.dimension}")
        except Exception as err:
            print(f"[WARN] Failed to verify/create Qdrant collection: {err}")

    def embed_single(self, text: str) -> List[float]:
        embeddings = list(self.embedding_model.embed([text]))
        return embeddings[0].tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        embeddings = list(self.embedding_model.embed(texts))
        return [e.tolist() for e in embeddings]

    def similarity_search(self, query: str, limit: int = 5, **kwargs) -> List[Dict[str, Any]]:
        search_limit = kwargs.get("top_k", limit)
        query_vector = self.embed_single(query)

        if hasattr(self.client, "query_points"):
            query_response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=search_limit,
                with_payload=True,
            )
            search_result = query_response.points
        else:
            search_result = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=search_limit,
                with_payload=True,
            )

        results = []
        for hit in search_result:
            payload = getattr(hit, "payload", {}) or {}
            score = getattr(hit, "score", 0.0)
            text_content = payload.get("text") or payload.get("content") or ""

            # Note: Extracting metadata for the retriever
            metadata = payload.get("metadata", {})
            
            results.append({
                "id": hit.id,
                "score": score,
                "text": text_content,
                "content": text_content,
                "act": metadata.get("act", ""),
                "section": metadata.get("section", ""),
                "title": metadata.get("title", ""),
                "metadata": metadata,
                "payload": payload,
            })
        return results


# Global singleton instance
vector_manager = QdrantVectorManager()