import os
import json
import re
from pathlib import Path
from datasets import load_dataset

DATA_DIR = Path("data/laws")
DATA_DIR.mkdir(parents=True, exist_ok=True)

print("[*] Processing local cached Bangladesh Laws dataset...")
ds = load_dataset("Ashik2380/bdlaws", split="train")

all_laws = []
by_act = {}
skipped_repealed = 0

for idx, row in enumerate(ds, 1):
    # বাতিল আইন স্কিপ করা
    if str(row.get("repealed", "")).strip().lower() == "true":
        skipped_repealed += 1
        continue

    act_title = str(row.get("act_name") or "Bangladesh Statute").strip()
    sec_title = str(row.get("section_name") or "").strip()
    sec_content = str(row.get("section_text") or "").strip()
    sec_id = str(row.get("section_id") or "").strip()

    # অপ্রাসঙ্গিক বা ফাঁকা এন্ট্রি ফিল্টার
    if len(sec_content) < 15 or "404 not" in sec_content.lower() or "oops!" in sec_content.lower():
        continue

    # সেকশন নম্বর আইডেন্টিফাই করা (যেমন Section 302)
    sec_match = re.search(r"(?:Section|Sec\.?|ধারা)?\s*(\d+[A-Za-z]?)", sec_title, re.IGNORECASE)
    sec_label = f"Section {sec_match.group(1)}" if sec_match else (f"Section {sec_id}" if sec_id else sec_title)

    # স্ট্যাবল এবং ইউনিক কাস্টম আইডি তৈরি
    act_slug = re.sub(r"[^a-zA-Z0-9]", "_", act_title.lower())[:35].strip("_")
    sec_slug = re.sub(r"[^a-zA-Z0-9]", "_", sec_label.lower()).strip("_")
    custom_id = f"{act_slug}_{sec_slug}_{idx}"

    record = {
        "id": custom_id,
        "act": act_title,
        "section": sec_label,
        "title": sec_title[:200] if sec_title else sec_label,
        "text": sec_content,
        "jurisdiction": "Bangladesh",
        "act_no": row.get("act_no", ""),
        "published_date": row.get("published_date", "")
    }

    all_laws.append(record)

    # আইনভিত্তিক গ্রুপিং
    if act_title not in by_act:
        by_act[act_title] = []
    by_act[act_title].append(record)

# ১. মাস্টার ফাইল সেভ (সম্পূর্ণ ৪৩ হাজার ধারা)
master_file = DATA_DIR / "all_bangladesh_laws.json"
with open(master_file, "w", encoding="utf-8") as f:
    json.dump(all_laws, f, ensure_ascii=False, indent=2)

print(f"\n[+] Repealed sections filtered out: {skipped_repealed}")
print(f"[+] Total active legal sections: {len(all_laws)}")
print(f"[+] Total distinct Acts: {len(by_act)}")
print(f"[SUCCESS] Saved master file: {master_file}")