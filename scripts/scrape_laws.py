import os
import time
import requests
from bs4 import BeautifulSoup

DATA_DIR = os.path.join(os.getcwd(), "data", "laws")
os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# প্রধান ৫টি মৌলিক আইন
TARGET_ACTS = {
    "act-26": "The Contract Act, 1872",
    "act-11": "The Penal Code, 1860",
    "act-75": "The Code of Criminal Procedure, 1898",
    "act-32": "The Specific Relief Act, 1877",
    "act-952": "The Bangladesh Labour Act, 2006"
}

def scrape_act(act_id: str, title: str):
    print(f"\n[*] Processing: {title} ({act_id})")
    index_url = f"http://bdlaws.minlaw.gov.bd/{act_id}.html"
    
    try:
        res = requests.get(index_url, headers=HEADERS, timeout=20)
        if res.status_code != 200:
            print(f"[-] Failed to load index for {act_id} (HTTP {res.status_code})")
            return
            
        soup = BeautifulSoup(res.text, "html.parser")
        
        # সেকশনগুলোর অভ্যন্তরীণ লিঙ্ক খুঁজে বের করা
        section_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if f"{act_id}/section-" in href:
                full_link = href if href.startswith("http") else f"http://bdlaws.minlaw.gov.bd/{href.lstrip('/')}"
                if full_link not in section_links:
                    section_links.append(full_link)

        collected_sections = [f"STATUTE TITLE: {title}\nJURISDICTION: Bangladesh\n"]

        if section_links:
            print(f"[+] Found {len(section_links)} sections. Fetching top 25 core sections...")
            for link in section_links[:25]:
                try:
                    s_res = requests.get(link, headers=HEADERS, timeout=10)
                    if s_res.status_code == 200:
                        s_soup = BeautifulSoup(s_res.text, "html.parser")
                        box = s_soup.find("div", {"class": "act-content"}) or s_soup.find("body")
                        if box:
                            sec_text = box.get_text(separator="\n", strip=True)
                            collected_sections.append(sec_text)
                    time.sleep(0.15)  # সাইট যাতে ব্লক না করে
                except Exception:
                    continue
        else:
            # যদি সরাসরি এক পেজেই কন্টেন্ট থাকে
            box = soup.find("div", {"class": "act-content"}) or soup.find("body")
            if box:
                collected_sections.append(box.get_text(separator="\n", strip=True))

        out_file = os.path.join(DATA_DIR, f"{act_id}.txt")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("\n\n---\n\n".join(collected_sections))
        print(f"[SUCCESS] Saved {title} -> {out_file}")

    except Exception as e:
        print(f"[-] Error scraping {title}: {e}")

if __name__ == "__main__":
    for act_id, act_title in TARGET_ACTS.items():
        scrape_act(act_id, act_title)