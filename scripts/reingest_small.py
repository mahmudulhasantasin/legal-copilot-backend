import json
import time
import uuid
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tqdm import tqdm
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from app.config import settings
from app.services.legal_normalizer import normalize_act, normalize_section, resolve_act
from fastembed.common.model_description import ModelSource, PoolingType

TextEmbedding.add_custom_model(
    model="intfloat/multilingual-e5-small",
    pooling=PoolingType.MEAN,
    normalization=True,
    sources=ModelSource(hf="intfloat/multilingual-e5-small"),
    dim=384,
    model_file="onnx/model.onnx",
)

# ──────────────────────────────────────────────────────────
# কনফিগারেশন
# ──────────────────────────────────────────────────────────
COLLECTION_NAME = "bd_laws_e5_small"
MODEL_NAME = "intfloat/multilingual-e5-small" 
JSON_FILE = "data/laws/all_bangladesh_laws.json" 
BATCH_SIZE = 500
RESET_COLLECTION = False

def main():
    print("="*60)
    print("🚀 Starting Data Re-Ingestion Process")
    print(f"🤖 Model: {MODEL_NAME}")
    print(f"📦 Batch Size: {BATCH_SIZE} documents")
    print("="*60)

    start_time = time.time()

    print("🔌 Connecting to Qdrant Cloud...")
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY, timeout=300.0)
    
    if RESET_COLLECTION and client.collection_exists(COLLECTION_NAME):
        print(f"🗑️ Deleting existing collection: {COLLECTION_NAME}...")
        client.delete_collection(COLLECTION_NAME)
        
    if not client.collection_exists(COLLECTION_NAME):
        print(f"📁 Creating new collection: {COLLECTION_NAME} (Dimension: 384)...")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=384, distance=Distance.COSINE),
        )
    else:
        print(f"✅ Collection {COLLECTION_NAME} already exists.")

    print(f"⏳ Loading FastEmbed model (This might take a minute)...")
    embed_model = TextEmbedding(model_name=MODEL_NAME)
    
    print(f"📖 Reading data from {JSON_FILE}...")
    with open(JSON_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    total_docs = len(data)
    print(f"✅ Found {total_docs} total documents.")

    current_count = client.count(collection_name=COLLECTION_NAME).count
    if current_count > 0:
        print(f"🔄 Resuming! Found {current_count} documents already uploaded.")
    else:
        print("🆕 Starting from the very beginning.")

    print("\n")

    for i in tqdm(range(current_count, total_docs, BATCH_SIZE), desc="Uploading to Qdrant", unit="batch"):
        batch = data[i : i + BATCH_SIZE]
        
        texts = []
        payloads = []
        for item in batch:
            text_content = item.get("text", "")
            
            # ✅ FIX 1: E5 মডেলের জন্য 'passage: ' প্রিফিক্স যুক্ত করা হলো
            texts.append(f"passage: {text_content}")
            
            raw_act = item.get("act", "")
            raw_sec = item.get("section", "")
            
            canonical_act = resolve_act(raw_act)
            
            meta_data = {
                "act": raw_act,
                "section": raw_sec,
                "title": item.get("title", ""),
                "act_no": item.get("act_no", ""),
                "published_date": item.get("published_date", ""),
                "jurisdiction": item.get("jurisdiction", ""),
                "doc_id": item.get("id", ""),
                "act_norm": canonical_act if canonical_act else normalize_act(raw_act),
                "section_norm": normalize_section(raw_sec)
            }
            
            payloads.append({
                "text": text_content, # পেলোডে অরিজিনাল টেক্সট রাখা হলো
                "content": text_content,
                "metadata": meta_data,
                "act": raw_act,
                "section": raw_sec,
                "title": item.get("title", "")
            })
            
        embeddings = list(embed_model.embed(texts))
        
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, str(i + j))),
                vector=embeddings[j].tolist(),
                payload=payloads[j]
            )
            for j in range(len(batch))
        ]
        
        try:
            client.upsert(
                collection_name=COLLECTION_NAME,
                points=points
            )
        except Exception as e:
            print(f"\n❌ Error uploading batch starting at index {i}: {e}")
            print("⚠️ Don't worry. Just run the script again, it will resume from here.")
            break

    end_time = time.time()
    total_time = end_time - start_time
    minutes = int(total_time // 60)
    seconds = int(total_time % 60)

    final_count = client.count(collection_name=COLLECTION_NAME).count
    print("\n" + "="*60)
    if final_count >= total_docs:
        print("🎉 Re-Ingestion Completed Successfully!")
    else:
        print(f"⚠️ Process stopped early. {final_count}/{total_docs} uploaded.")
        print("👉 Just run 'python scripts/reingest_small.py' again to resume.")
    
    print(f"📊 Total Documents in Database: {final_count}")
    print(f"⏱️ Time Taken in this run: {minutes} minutes and {seconds} seconds.")
    print("="*60)

if __name__ == "__main__":
    main()