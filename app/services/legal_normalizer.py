import json
import re
import os
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALIAS_FILE_PATH = os.path.join(BASE_DIR, "../../act_aliases.json")

ACT_ALIASES = {}
try:
    with open(ALIAS_FILE_PATH, 'r', encoding='utf-8') as f:
        ACT_ALIASES = json.load(f)
except FileNotFoundError:
    print(f"[Warning] {ALIAS_FILE_PATH} not found!")


BN_TO_EN = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# বাংলা সাব-সেকশন লেটার থেকে ইংরেজি Uppercase লেটারে ম্যাপিং (যেমন: ২৬ক -> 26A)
BN_TO_EN_SUB = {
    "ক": "A", "খ": "B", "গ": "C", "ঘ": "D", "ঙ": "E",
    "চ": "F", "ছ": "G", "জ": "H", "ঝ": "I", "ঞ": "J",
    "ট": "K", "ঠ": "L", "ড": "M", "ঢ": "N", "ণ": "O",
    "ত": "P", "থ": "Q", "দ": "R", "ধ": "S", "ন": "T",
    "প": "U", "ফ": "V", "ব": "W", "ভ": "X", "ম": "Y",
    "য": "Z"
}


def resolve_act(act_name: str) -> Optional[str]:
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
    
    # Without year (e.g., "the contract act")
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


def resolve_act_robust(act_name: str) -> Optional[str]:
    """
    Robust resolver that handles LLM formatting issues:
      - Missing "the " prefix
      - Missing comma before year (e.g., "Act 1908" vs "Act, 1908")
      - Extra whitespace
    """
    if not act_name:
        return None

    raw = act_name.strip()
    candidates = set()

    def _add(s: str):
        s = s.strip()
        if s:
            candidates.add(s)
            candidates.add(s.lower())
            if not s.lower().startswith("the "):
                candidates.add("the " + s.lower())
            if s.lower().startswith("the "):
                candidates.add(s[4:].strip().lower())

    _add(raw)

    # Add comma-less year variant:  "act 1908"  →  "act, 1908"
    comma_fixed = re.sub(r'\s+((?:18|19|20|১৮|১৯|২০)\d{2})$', r', \1', raw)
    if comma_fixed != raw:
        _add(comma_fixed)

    # Add year-stripped variant
    no_year = re.sub(r'[,|৷]\s*(?:18|19|20|১৮|১৯|২০)\d{2}', '', raw).strip()
    if no_year != raw:
        _add(no_year)

    # Try each candidate through the base resolver
    for c in candidates:
        resolved = resolve_act(c)
        if resolved:
            return resolved

    return None


def normalize_act(act_name: str) -> str:
    """Act name → consistent lowercase format for Qdrant filter (if needed)."""
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
    'ধারা ২৬ক' → '26ক' (Bengali letter remains, handled by get_section_variants)
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
    
    # "Section 2 and 3" হলে "2-3" হবে
    return "-".join(matches) 


def get_section_variants(section_text: str) -> List[str]:
    """
    Qdrant MatchAny ফিল্টারের জন্য সেকশন নম্বরের সম্ভাব্য সব ভ্যারিয়েন্ট তৈরি করে।
    Example: 'ধারা ২৬ক' -> ['26A', '26a', '26ক']
    Example: 'Section 5A' -> ['5A', '5a']
    """
    base = normalize_section(section_text)
    if not base:
        return []
    
    # ১. বাংলা লেটার থাকলে ইংরেজি Uppercase-এ কনভার্ট করা
    en_base = "".join(BN_TO_EN_SUB.get(ch, ch) for ch in base)
    
    # ২. যেহেতু ডেটাবেসে সাধারণত Uppercase থাকে, তাই Upper ও Lower দুটোই পাঠানো হচ্ছে
    variants = {en_base.upper(), en_base.lower()}
    
    # ৩. যদি ইনপুটে বাংলা লেটার থাকে, তবে সেটাও ভ্যারিয়েন্ট হিসেবে রাখা (Backward compatibility)
    if any(ch in BN_TO_EN_SUB for ch in base):
        variants.add(base)
        
    return list(variants)