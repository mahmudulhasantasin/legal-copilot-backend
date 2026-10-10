import os
import glob
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ChromaDB লোকাল স্টোরেজ পাথ
CHROMA_DIR = os.path.join(os.getcwd(), "chroma_db")
os.makedirs(CHROMA_DIR, exist_ok=True)

# সম্পূর্ণ লোকাল ও ফ্রি ChromaDB ক্লায়েন্ট (কোনো পেইড এম্বেডিং API কী লাগবে না)
chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
collection = chroma_client.get_or_create_collection(name="bangladesh_laws")

def seed_database():
    print("[*] Scanning 'data/' directory for statutes and court judgements...")
    
    # 750 অক্ষরের চাঙ্ক ও 120 অক্ষরের ওভারল্যাপ (আইনি ধারার জন্য স্ট্যান্ডার্ড সাইজ)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=750,
        chunk_overlap=120,
        separators=["\n\nSection", "\nSection", "\n\nCase", "\nCase", "\n\n", "\n", " "]
    )
    
    all_chunks = []
    all_metadatas = []
    all_ids = []
    counter = 0

    # data ফোল্ডারের সব .txt ফাইল (data/laws/ ও data/judgements/) রিড করা
    target_files = glob.glob("data/**/*.txt", recursive=True)
    if not target_files:
        print("[-] No .txt files found in 'data/' directory.")
        print("[-] Please run 'python scripts/scrape_laws.py' and 'python scripts/scrape_judgements.py' first.")
        return

    for file_path in target_files:
        file_name = os.path.basename(file_path)
        folder_name = os.path.basename(os.path.dirname(file_path))  # 'laws' অথবা 'judgements'
        
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()
                if not content:
                    continue

                chunks = splitter.split_text(content)
                print(f"[+] Loaded {len(chunks)} chunks from [{folder_name}] {file_name}")

                for chunk in chunks:
                    all_chunks.append(chunk)
                    all_metadatas.append({
                        "source_file": file_name,
                        "category": folder_name,
                        "jurisdiction": "Bangladesh",
                        "chunk_index": counter
                    })
                    all_ids.append(f"doc_chunk_{counter}")
                    counter += 1
        except Exception as e:
            print(f"[-] Error reading {file_path}: {e}")

    total_chunks = len(all_chunks)
    print(f"\n[*] Total prepared legal chunks: {total_chunks}")
    print("[*] Upserting embeddings into ChromaDB collection 'bangladesh_laws'...")

    # মেমোরি ক্র্যাশ ঠেকাতে ৫০টি করে ব্যাচে লোকাল ইনজেশন
    batch_size = 50
    for i in range(0, total_chunks, batch_size):
        end_idx = min(i + batch_size, total_chunks)
        collection.upsert(
            ids=all_ids[i:end_idx],
            documents=all_chunks[i:end_idx],
            metadatas=all_metadatas[i:end_idx]
        )
        print(f"    -> Embedded chunks {i+1} to {end_idx} of {total_chunks}")

    print(f"\n[SUCCESS] Successfully seeded {total_chunks} legal chunks into ChromaDB at '{CHROMA_DIR}'!")

if __name__ == "__main__":
    seed_database()