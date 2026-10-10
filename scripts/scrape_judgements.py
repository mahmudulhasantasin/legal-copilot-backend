import os
import time
import requests
from bs4 import BeautifulSoup
import urllib3

# SSL warning disable kora (supremecourt.gov.bd certificate bypass er jonno)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATA_DIR = os.path.join(os.getcwd(), "data", "judgements")
os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# Supreme Court official target links
SC_TARGETS = [
    {
        "name": "sc_main_portal",
        "url": "https://supremecourt.gov.bd/web/",
        "desc": "Supreme Court General Portal & Guidelines"
    },
    {
        "name": "sc_appellate_division",
        "url": "https://supremecourt.gov.bd/web/?page=appellate_division.php",
        "desc": "Appellate Division Notices & Practice Directions"
    },
    {
        "name": "sc_high_court_division",
        "url": "https://supremecourt.gov.bd/web/?page=high_court_division.php",
        "desc": "High Court Division Judicial Information"
    }
]

def clean_text(soup_element):
    """HTML theke clean text extract kora"""
    if not soup_element:
        return ""
    # Script ebong style tag remove kora
    for element in soup_element(["script", "style", "nav", "footer"]):
        element.decompose()
    return soup_element.get_text(separator="\n", strip=True)

def scrape_supreme_court():
    print("[*] Starting Supreme Court data extraction...")
    
    for item in SC_TARGETS:
        name = item["name"]
        url = item["url"]
        desc = item["desc"]
        
        print(f"\n[*] Fetching: {desc} ({url})")
        try:
            res = requests.get(url, headers=HEADERS, timeout=25, verify=False)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                
                # Main body ba content container khuje ber kora
                main_box = soup.find("div", {"id": "content"}) or soup.find("div", {"class": "main-content"}) or soup.find("body")
                extracted_content = clean_text(main_box)
                
                if extracted_content:
                    file_path = os.path.join(DATA_DIR, f"{name}.txt")
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(f"COURT: Supreme Court of Bangladesh\n")
                        f.write(f"SECTION: {desc}\n")
                        f.write(f"SOURCE_URL: {url}\n\n")
                        f.write(extracted_content)
                    print(f"[SUCCESS] Saved {len(extracted_content)} characters -> {file_path}")
                else:
                    print(f"[-] No text extracted for {name}")
            else:
                print(f"[-] HTTP Status {res.status_code} for {url}")
        except Exception as e:
            print(f"[-] Failed to scrape {name}: {e}")
        
        time.sleep(0.5)

    # Core Landmark Precedents Ground-Truth Bundle (ChromaDB Vector Matching er jonno)
    precedent_file = os.path.join(DATA_DIR, "landmark_precedents.txt")
    landmark_cases = """
    SUPREME COURT OF BANGLADESH - LANDMARK JUDGEMENTS & PRECEDENTS

    Case Reference: Kudrat-E-Elahi Panjhar v. Bangladesh (44 DLR AD 319)
    Principle: Independence of Judiciary and supremacy of the Constitution. Local Government bodies and statutory powers under Article 59 and 60.

    Case Reference: Bangladesh v. Anowar Hossain Khan (8th Amendment Case, 1989 BLD Spl 1)
    Principle: Basic Structure Doctrine in Bangladesh constitutional law. The Parliament cannot alter the basic structure of the Constitution.

    Case Reference: Secretary, Ministry of Finance v. Masdar Hossain (52 DLR AD 82)
    Principle: Separation of the Judiciary from the Executive branch under Article 22 of the Constitution of Bangladesh.

    Case Reference: Dr. Mohiuddin Farooque v. Bangladesh (FAP 20 Case, 49 DLR AD 1)
    Principle: Locus Standi and Public Interest Litigation (PIL) under Article 102. Any person or organization aggrieved by public wrong can approach the High Court.

    Contractual Jurisprudence Precedent:
    - In breach of commercial contract, damages under Section 73 of the Contract Act 1872 must be naturally arising or in contemplation of both parties at the time of contract execution.
    - An injunction cannot be granted to prevent the breach of a contract the performance of which would not be specifically enforced under Section 56 of the Specific Relief Act 1877.
    """
    with open(precedent_file, "w", encoding="utf-8") as f:
        f.write(landmark_cases.strip())
    print(f"[SUCCESS] Saved core landmark precedents -> {precedent_file}")

if __name__ == "__main__":
    scrape_supreme_court()