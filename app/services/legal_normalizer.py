import json
import re
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALIAS_FILE_PATH = os.path.join(BASE_DIR, "../../act_aliases.json")

ACT_ALIASES = {}
try:
    with open(ALIAS_FILE_PATH, 'r', encoding='utf-8') as f:
        ACT_ALIASES = json.load(f)
except FileNotFoundError:
    print(f"[Warning] {ALIAS_FILE_PATH} not found!")


BN_TO_EN = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def resolve_act(act_name: str):
    """
    User/LLM act name → canonical DB name.
    Handles Bengali spelling variations (ণ↔ন, ূ↔ু, etc.)
    """
    if not act_name:
        return None
    
    cleaned = act_name.strip().lower()
    
    # Direct match
    if cleaned in ACT_ALIASES:
        return ACT_ALIASES[cleaned]
    
    # Without year
    no_year = re.sub(r'[,|৷]\s*(18|19|20|১৮|১৯|২০)\d{2}', '', cleaned).strip()
    if no_year in ACT_ALIASES:
        return ACT_ALIASES[no_year]
    
    # Bengali spelling normalization
    def norm_bn(s):
        return (s.replace("ণ", "ন").replace("ূ", "ু").replace("ী", "ি")
                 .replace("শ", "স").replace("ষ", "স").replace("ড়", "ড"))
    
    norm_input = norm_bn(cleaned)
    
    for key, val in ACT_ALIASES.items():
        if norm_bn(key) == norm_input:
            return val
        if no_year and norm_bn(key) == norm_bn(no_year):
            return val
    
    return None


def normalize_act(act_name: str) -> str:
    """Act name → consistent lowercase format for Qdrant filter."""
    if not act_name:
        return ""
    text = str(act_name).strip().lower()
    text = text.translate(BN_TO_EN)                       # Bengali → English digit
    text = re.sub(r'\[\*+\]', ' ', text)                  # [***] remove
    text = re.sub(r'[^\w\u0980-\u09FF\s]', ' ', text)     # punctuation → space
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def normalize_section(section_text: str) -> str:
    """
    'Section 27' → '27'
    'ধারা ২৬ক' → '26ক'
    'Section 302(a)' → '302(a)'
    """
    if not section_text:
        return ""
    
    text = str(section_text).translate(BN_TO_EN)          # Bengali digit → English
    
    # Bengali words explicit remove
    text = text.replace("ধারা", " ").replace("আইন", " ").replace("নং", " ")
    
    # Year বাদ
    text = re.sub(r'\b(18|19|20)\d{2}\b', '', text)
    
    # English keywords বাদ
    text = re.sub(r'(?i)\b(section|of|act|the|sec|number|no)\b', '', text)
    
    # Keep only digit, letter, bracket
    matches = re.findall(r'[\da-zA-Z\u0980-\u09FF\(\)]+', text)
    # "".join(matches) এর বদলে:
    return "-".join(matches) # "Section 2 and 3" হলে "2-3" হবে