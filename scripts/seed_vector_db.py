import sys
import re
import ast
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import json
import time
import uuid
from qdrant_client.models import PointStruct
from app.services.vector_store import vector_manager
from langchain_core.messages import SystemMessage, HumanMessage
from app.services.llm_provider import get_llm

DATA_FILE = ROOT_DIR / "data" / "laws" / "all_bangladesh_laws.json"
CHECKPOINT_FILE = ROOT_DIR / "data" / "seed_checkpoint_fastembed.txt"
FAILED_LOG_FILE = ROOT_DIR / "data" / "failed_sections.jsonl"  # <--- NEW FILE FOR ERRORS

BATCH_SIZE = 50 


def get_last_processed_index() -> int:
    if CHECKPOINT_FILE.exists():
        try:
            return int(CHECKPOINT_FILE.read_text().strip())
        except Exception:
            return 0
    return 0


def save_checkpoint(index: int):
    CHECKPOINT_FILE.write_text(str(index))


# ==========================================
# ADVANCED FUNCTION: Generate Layman Keywords (Ultimate Fix)
# ==========================================
def generate_layman_keywords(act_name: str, section_title: str, section_text: str) -> list:
    TAGGING_PROMPT = """You are an expert legal data engineer classifying Bangladesh Law.
    Read the provided section and generate exactly 10 to 15 layman keywords, search phrases, and common problem descriptions.
    Ensure the list includes everyday Bengali terms, formal synonyms, and Banglish search behaviors.
    Return ONLY a flat JSON list of strings. Example: ["চাকরি নাই", "fired", "বরখাস্ত"]"""

    try:
        llm = get_llm()
        content = f"Act: {act_name}\nTitle: {section_title}\nText: {section_text}"
        response = llm.invoke([
            SystemMessage(content=TAGGING_PROMPT),
            HumanMessage(content=content)
        ])
        
        raw_data = response.content
        if isinstance(raw_data, list):
            if len(raw_data) > 0 and isinstance(raw_data[0], dict) and "text" in raw_data[0]:
                raw_data = raw_data[0]["text"]
            else:
                raw_data = str(raw_data[0])
                
        raw_text = str(raw_data).strip()
        
        match = re.search(r'\[.*?\]', raw_text, re.DOTALL)
        clean_str = match.group(0) if match else raw_text

        try:
            keywords = json.loads(clean_str)
        except Exception:
            try:
                keywords = ast.literal_eval(clean_str)
            except Exception:
                keywords = []
                
        return keywords if isinstance(keywords, list) else []
        
    except Exception as e:
        print(f"      [!] Error generating keywords: {e}")
        return []
# ==========================================


def seed_database():
    if not DATA_FILE.exists():
        print(f"[!] Data file not found: {DATA_FILE}")
        return

    print("[*] Loading master laws dataset...")
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)

    total_records = len(records)
    start_idx = get_last_processed_index()
    print(f"[*] Total records: {total_records} | Resuming from: {start_idx}")

    for i in range(start_idx, total_records, BATCH_SIZE):
        batch_slice = records[i : min(i + BATCH_SIZE, total_records)]
        chunks = []
        item_ids = []
        metas = []

        for item in batch_slice:
            act_title = item.get("act", "")
            sec_name = item.get("section", "")
            title_text = item.get("title", "")
            body_text = item.get("text", "")
            
            print(f"  -> Tagging: {act_title} - {sec_name}")
            layman_terms = generate_layman_keywords(act_title, title_text, body_text)
            
            # ==========================================
            # NEW LOGIC: ফেইল হলে আলাদা ফাইলে লগ করা
            # ==========================================
            if not layman_terms:
                with open(FAILED_LOG_FILE, "a", encoding="utf-8") as err_file:
                    err_file.write(json.dumps(item, ensure_ascii=False) + "\n")
                print(f"      [!] Failed. Saved to {FAILED_LOG_FILE.name} for later.")
            # ==========================================

            time.sleep(5)  # API লিমিট বাঁচাতে ৫ সেকেন্ড (প্রয়োজনে এটি ১৫ করতে পারেন)
            
            chunk = f"{act_title} {sec_name}: {title_text}\n{body_text}\nLayman Keywords: {', '.join(layman_terms)}"
            
            chunks.append(chunk)
            item_ids.append(item["id"])
            metas.append({
                "act": act_title,
                "section": sec_name,
                "title": title_text,
                "jurisdiction": "Bangladesh",
                "layman_keywords": layman_terms 
            })

        vectors = vector_manager.embed_batch(chunks)

        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, item_id)),
                vector=vec,
                payload={
                    "custom_id": item_id,
                    "text": chunk,
                    "metadata": meta
                }
            )
            for item_id, vec, chunk, meta in zip(item_ids, vectors, chunks, metas)
        ]

        upserted = False
        for attempt in range(5):
            try:
                vector_manager.client.upsert(
                    collection_name=vector_manager.collection_name,
                    points=points
                )
                upserted = True
                break
            except Exception as e:
                print(f"\n[!] Network timeout at {i}. Retrying in 3s (Attempt {attempt + 1}/5)...")
                time.sleep(3)

        if not upserted:
            print(f"\n[X] Failed to upload batch at index {i}. Stopping safely.")
            break

        current_pos = min(i + BATCH_SIZE, total_records)
        save_checkpoint(current_pos)
        print(f" -> Synced {current_pos}/{total_records} ({((current_pos)/total_records)*100:.1f}%)")

    print("\n[SUCCESS] Sync process finished!")


if __name__ == "__main__":
    seed_database()