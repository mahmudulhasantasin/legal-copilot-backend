import asyncio
import json
import os
import glob
import uuid
from qdrant_client import models
from qdrant_client.models import VectorParams, Distance
from app.services.vector_store import vector_manager
from app.services.legal_normalizer import normalize_act, normalize_section
from app.config import settings

# আপনার ফোল্ডার স্ট্রাকচার অনুযায়ী পাথ
LAW_DATA_DIR = "./data/laws" 

async def ingest_json_file(file_path: str):
    print(f"\n📄 Processing: {os.path.basename(file_path)}")
    
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = [data]

    points = []
    for i, item in enumerate(data):
        # আপনার JSON ফরম্যাট থেকে ডাটা নেওয়া হচ্ছে
        act_name = item.get("act", "Unknown Act")
        section = item.get("section", "")
        title = item.get("title", "")
        text = item.get("text", "")
        
        if not text:
            continue

        # এমবেডিং তৈরি করার জন্য টেক্সট (আইনের নাম + সেকশন + টাইটেল + আসল টেক্সট)
        combined_text = f"{act_name} | {section} | {title}\n\n{text}"

        # Qdrant-এর জন্য ইউনিক UUID তৈরি
        raw_id = str(item.get("id", f"doc_{i}"))
        doc_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, raw_id))
        
        payload = {
            "text": text,
            "metadata": {
                "act": act_name,
                "act_norm": normalize_act(act_name),
                "section": section,
                "section_norm": normalize_section(section),
                "title": title,
                "jurisdiction": item.get("jurisdiction", "Bangladesh"),
                "act_no": item.get("act_no", ""),
                "published_date": item.get("published_date", ""),
            }
        }
        
        points.append({
            "id": doc_uuid,
            "text": combined_text,
            "payload": payload
        })

    if not points:
        print("⚠️ No valid points found in this file.")
        return
        
    print(f"🚀 Generating embeddings for {len(points)} sections...")

    batch_size = 50
    for i in range(0, len(points), batch_size):
        batch = points[i:i + batch_size]
        texts_to_embed = [p["text"] for p in batch]
        
        # লোকাল মডেল দিয়ে এমবেডিং জেনারেট
        embeddings = vector_manager.embed_batch(texts_to_embed)
        
        # Qdrant পয়েন্ট তৈরি
        qdrant_points = [
            models.PointStruct(
                id=p["id"],
                vector=vector,
                payload=p["payload"]
            )
            for p, vector in zip(batch, embeddings)
        ]
        
        # Qdrant-এ আপলোড
        vector_manager.client.upsert(
            collection_name=settings.QDRANT_COLLECTION_NAME,
            points=qdrant_points,
            wait=True
        )
        
        progress = min(i + batch_size, len(points))
        print(f"✅ Processed {progress} / {len(points)} items...")

async def main():
    # ✅ ১. পুরনো কালেকশন ডিলিট করা (যাতে 384 vs 1024 ডাইমেনশন এরর না আসে)
    try:
        if vector_manager.client.collection_exists(vector_manager.collection_name):
            print(f"🗑️ Deleting old collection '{vector_manager.collection_name}'...")
            vector_manager.client.delete_collection(vector_manager.collection_name)
        
        print(f"🛠️ Creating new collection '{vector_manager.collection_name}' with dim 1024...")
        vector_manager.client.create_collection(
            collection_name=vector_manager.collection_name,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
        )
    except Exception as e:
        print(f"⚠️ Collection setup warning: {e}")

    # ✅ ২. টেস্ট ফাইলটি ইনজেস্ট করা
    test_file_path = os.path.join(LAW_DATA_DIR, "test_laws.json")
    
    if not os.path.exists(test_file_path):
        print(f"❌ '{test_file_path}' not found. Please check the path.")
        return

    await ingest_json_file(test_file_path)
        
    print("\n🎉 Test Data Ingestion Complete!")

if __name__ == "__main__":
    asyncio.run(main())