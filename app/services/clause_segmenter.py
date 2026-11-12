"""
ContractMind - Sovereign Structural Clause Segmentation & AST Engine
Specialized for Bangladesh Commercial and Statutory Contracts (English & Bengali).

Features:
- Bilingual regex-based AST structural parser preserving original clause numbers and titles.
- Weighted, bilingual Contract Domain Classifier covering all major Bangladesh & commercial contract types.
- Bilingual Missing Mandatory Clause / Gap Detector per domain (no SaaS bias on general contracts).
- Bangladesh Stamp Duty (The Stamp Act, 1899) and Registration Act, 1908 Execution Advisory.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

_BN_TO_EN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def _normalize_digits(text: str) -> str:
    return (text or "").translate(_BN_TO_EN_DIGITS)


def _contains_term(text: str, term: str) -> bool:
    """Checks whole-word/phrase match for both English and Bengali without substring false hits (e.g. 'uat' in 'fluctuate')."""
    if not text or not term:
        return False
    pattern = r"(?<!\w)" + re.escape(term.lower()) + r"(?!\w)"
    return re.search(pattern, text) is not None


# ──────────────────────────────────────────────────────────
# DOMAIN CHECKLISTS: MANDATORY PROTECTIVE CLAUSES (EN + BN)
# ──────────────────────────────────────────────────────────

DOMAIN_MANDATORY_PROVISIONS: Dict[str, List[Dict[str, Any]]] = {
    "TECH_SAAS_VENDOR": [
        {
            "provision_id": "IP_ASSIGNMENT",
            "title": "Intellectual Property Assignment & Work Made for Hire",
            "keywords": [
                "intellectual property", "ownership", "work made for hire",
                "assignment of copyright", "source code", "মেধাস্বত্ব", "সোর্স কোড", "কপিরাইট",
            ],
            "statutory_ref": "Copyright Act, 2000 / কপিরাইট আইন, ২০২৩",
            "risk_if_missing": "Client may not legally own custom software source code, creating copyright clouds and licensing exposure.",
            "severity": "HIGH",
        },
        {
            "provision_id": "DATA_BREACH_NOTICE",
            "title": "Data Security & Breach Incident Notification (72h)",
            "keywords": [
                "data security", "data privacy", "incident notification",
                "breach notification", "security measures", "data deletion",
                "তথ্য সুরক্ষা", "ডেটা নিরাপত্তা", "ডেটা ব্রিচ",
            ],
            "negations": [
                "no specific incident notification", "no incident notification",
                "without breach notice", "ব্রিচ নোটিশ ছাড়া",
            ],
            "statutory_ref": "Cyber Security Act, 2023 / সাইবার নিরাপত্তা আইন, ২০২৩",
            "risk_if_missing": "Absence of mandatory security breach reporting timelines exposes client databases to regulatory liability.",
            "severity": "HIGH",
        },
        {
            "provision_id": "THIRD_PARTY_OSS_WARRANTY",
            "title": "Third-Party & Open Source Software (OSS) Licensing Warranty",
            "keywords": [
                "third-party software", "open source", "oss", "gpl",
                "third-party components", "proprietary methodologies", "ওপেন সোর্স",
            ],
            "negations": [
                "does not expressly establish ownership or licensing terms for third-party",
            ],
            "statutory_ref": "Copyright Act / Commercial Standard",
            "risk_if_missing": "Silence on OSS licenses may force disclosure of client proprietary business logic under viral copyleft licenses.",
            "severity": "MODERATE",
        },
        {
            "provision_id": "ADVANCE_PAYMENT_REFUND",
            "title": "Termination Refund for Unearned Advance Payments",
            "keywords": [
                "refund", "advance payment", "reimbursement", "unperformed services",
                "return of deposit", "অগ্রিম ফেরত", "টাকা ফেরত",
            ],
            "negations": [
                "no specific refund procedure", "no refund", "strictly non-refundable",
                "অফেরতযোগ্য", "ফেরত দেওয়া হবে না",
            ],
            "statutory_ref": "The Contract Act, 1872 (Sections 64, 65)",
            "risk_if_missing": "Service provider may retain unearned advance fees upon early termination or material breach.",
            "severity": "HIGH",
        },
        {
            "provision_id": "ACCEPTANCE_TESTING_CRITERIA",
            "title": "Objective Deliverable Acceptance & UAT Window",
            "keywords": [
                "acceptance testing", "uat", "testing period", "rejection notice",
                "acceptance criteria", "remedy period", "গ্রহণযোগ্যতা পরীক্ষা",
            ],
            "statutory_ref": "Commercial Standard / General Contract Principle",
            "risk_if_missing": "Deemed acceptance within an unreasonably brief period forces client to accept defective deliverables.",
            "severity": "MODERATE",
        },
        {
            "provision_id": "MUTUAL_IP_INDEMNIFICATION",
            "title": "Vendor Intellectual Property Infringement Indemnity",
            "keywords": [
                "indemnify", "indemnity", "hold harmless", "patent infringement",
                "copyright infringement", "ক্ষতিপূরণ দায়",
            ],
            "statutory_ref": "The Contract Act, 1872 (Section 124)",
            "risk_if_missing": "Client bears full liability if vendor-developed code infringes third-party patents or copyrights.",
            "severity": "HIGH",
        },
    ],
    "EMPLOYMENT_SERVICE": [
        {
            "provision_id": "STATUTORY_NOTICE_PERIOD",
            "title": "Statutory Termination Notice & Severance Pay",
            "keywords": [
                "notice period", "severance", "termination notice", "statutory pay",
                "compensation", "নোটিশ", "নোটিশ পিরিয়ড", "চাকরি অবসান", "ক্ষতিপূরণ",
            ],
            "negations": [
                "without prior notice", "without any severance", "zero statutory severance",
                "নোটিশ ছাড়া", "কোনো নোটিশ ব্যতীত",
            ],
            "statutory_ref": "Bangladesh Labour Act, 2006 / বাংলাদেশ শ্রম আইন, ২০০৬ (Sections 26, 27)",
            "risk_if_missing": "Termination without codified notice (120/60 days or wages in lieu) violates mandatory Bangladesh labour protections.",
            "severity": "HIGH",
        },
        {
            "provision_id": "PROVIDENT_FUND_GRATUITY",
            "title": "Provident Fund, Gratuity & Terminal Benefits",
            "keywords": [
                "gratuity", "provident fund", "pf", "terminal benefits", "separation pay",
                "গ্র্যাচুইটি", "প্রভিডেন্ট ফান্ড", "ভবিষ্যৎ তহবিল", "সার্ভিস বেনিফিট",
            ],
            "statutory_ref": "বাংলাদেশ শ্রম আইন, ২০০৬ (Sections 27, 28)",
            "risk_if_missing": "Failure to articulate statutory terminal benefits exposes enterprise to Labour Court litigation and penalties.",
            "severity": "MODERATE",
        },
        {
            "provision_id": "RESTRAINT_OF_TRADE_LIMITATION",
            "title": "Lawful Post-Employment Restraint Boundaries",
            "keywords": [
                "non-compete", "restraint of trade", "competing business", "post-employment",
                "restricts lawful future employment", "প্রতিযোগিতামূলক ব্যবসা", "অন্য চাকরিতে যোগদান",
            ],
            "statutory_ref": "The Contract Act, 1872 (Section 27)",
            "risk_if_missing": "Post-employment non-compete clauses are void ab initio in Bangladesh; covenant must be limited to trade secret confidentiality.",
            "severity": "HIGH",
        },
    ],
    "COMMERCIAL_LEASE": [
        {
            "provision_id": "SECURITY_DEPOSIT_RETURN",
            "title": "Security Deposit Adjustment & Return Timeline",
            "keywords": [
                "security deposit", "advance rent", "refund of deposit", "deduction from deposit",
                "সিকিউরিটি ডিপোজিট", "জামানত", "অগ্রিম ভাড়া", "অ্যাডভান্স",
            ],
            "negations": [
                "forfeit the entire security deposit", "non-refundable deposit",
                "জামানত ফেরত দেওয়া হবে না", "জামানত বাজেয়াপ্ত",
            ],
            "statutory_ref": "Premises Rent Control Act, 1991 / Transfer of Property Act, 1882",
            "risk_if_missing": "Landlord may withhold security deposit indefinitely without objective dilapidation claims.",
            "severity": "HIGH",
        },
        {
            "provision_id": "STAMP_DUTY_REGISTRATION",
            "title": "Mandatory Registration & Stamp Duty Formalities",
            "keywords": [
                "registration", "sub-registry", "stamp paper", "non-judicial stamp", "stamp duty",
                "রেজিস্ট্রেশন", "নিবন্ধন", "সাব-রেজিস্ট্রি", "নন-জুডিশিয়াল স্ট্যাম্প", "স্ট্যাম্প",
            ],
            "negations": [
                "shall not be registered", "plain a4 paper", "without registration",
                "রেজিস্ট্রেশন করা হবে না", "সাদা কাগজে",
            ],
            "statutory_ref": "The Registration Act, 1908 (Section 17) / The Stamp Act, 1899",
            "risk_if_missing": "Lease exceeding one year executed on plain paper without registration is inadmissible in evidence under Section 49.",
            "severity": "HIGH",
        },
    ],
    "NDA_CONFIDENTIALITY": [
        {
            "provision_id": "STANDARD_EXCLUSIONS",
            "title": "Standard Confidentiality Exclusions (Public Domain, Legal Compulsion)",
            "keywords": [
                "public domain", "prior knowledge", "court order", "required by law",
                "independently developed", "আদালতের নির্দেশ", "আইনগত বাধ্যবাধকতা", "উন্মুক্ত তথ্য",
            ],
            "negations": [
                "never disclose confidential information even if ordered by a competent court",
            ],
            "statutory_ref": "Commercial Standard / The Specific Relief Act, 1877",
            "risk_if_missing": "Absence of carve-outs creates strict liability if information is demanded by law enforcement or regulatory authorities.",
            "severity": "MODERATE",
        },
        {
            "provision_id": "REASONABLE_TERM_SUNSET",
            "title": "Defined Confidentiality Expiration Period (2-5 Years)",
            "keywords": [
                "survival", "sunset", "years after termination", "duration of confidentiality",
                "survive for", "মেয়াদ", "বছর পর্যন্ত বলবৎ",
            ],
            "statutory_ref": "The Contract Act, 1872 (Section 27)",
            "risk_if_missing": "Perpetual confidentiality on ordinary business info may be challenged as an unreasonable restraint of trade.",
            "severity": "MODERATE",
        },
    ],
    "PROPERTY_SALE_TRANSFER": [
        {
            "provision_id": "TITLE_ENCUMBRANCE_WARRANTY",
            "title": "Clear Title & No-Encumbrance Warranty (Chain of Title)",
            "keywords": [
                "title", "encumbrance", "mutation", "khatian", "cs", "rs", "bs",
                "মালিকানা", "নামজারি", "খতিয়ান", "দাগ", "দায়মুক্ত", "নিস্কণ্টক",
            ],
            "statutory_ref": "The Transfer of Property Act, 1882 (Section 55)",
            "risk_if_missing": "Buyer lacks express seller warranty of marketable title and freedom from prior mortgages or litigation.",
            "severity": "HIGH",
        },
        {
            "provision_id": "REGISTRATION_POSSESSION_TIMELINE",
            "title": "Deed Registration Deadline & Vacant Possession",
            "keywords": [
                "registration", "sub-registry", "possession", "handover",
                "রেজিস্ট্রেশন", "সাব-কবলা", "সাফ কবলা", "দখল হস্তান্তর", "দলিল সম্পাদন",
            ],
            "statutory_ref": "The Registration Act, 1908 (Section 17) / The Specific Relief Act, 1877",
            "risk_if_missing": "Unclear registration timeline or earnest money forfeiture rules invite specific-performance disputes.",
            "severity": "HIGH",
        },
    ],
    "PARTNERSHIP_JV": [
        {
            "provision_id": "PROFIT_LOSS_CAPITAL_RATIO",
            "title": "Capital Contribution & Profit/Loss Sharing Ratio",
            "keywords": [
                "profit", "loss", "capital contribution", "equity", "shareholding",
                "মূলধন", "লাভ", "ক্ষতি", "লভ্যাংশ", "অংশ",
            ],
            "statutory_ref": "The Partnership Act, 1932 (Section 13)",
            "risk_if_missing": "Ambiguous profit/loss and capital withdrawal rules trigger deadlock under the Partnership Act, 1932.",
            "severity": "HIGH",
        },
        {
            "provision_id": "EXIT_DISSOLUTION_ARBITRATION",
            "title": "Partner Exit, Valuation & Dispute Resolution Mechanism",
            "keywords": [
                "dissolution", "exit", "buyout", "arbitration", "dispute",
                "বিলুপ্তি", "অবসান", "সালিস", "বিরোধ নিষ্পত্তি",
            ],
            "statutory_ref": "The Partnership Act, 1932 / সালিস আইন, ২০০১",
            "risk_if_missing": "Absence of buyout and arbitration terms forces costly court-supervised dissolution.",
            "severity": "HIGH",
        },
    ],
    "LOAN_FINANCING": [
        {
            "provision_id": "REPAYMENT_INTEREST_DISCLOSURE",
            "title": "Principal, Interest Rate & Repayment Schedule Clarity",
            "keywords": [
                "principal", "interest", "installment", "repayment schedule",
                "আসল", "মুনাফা", "সুদ", "কিস্তি", "পরিশোধ",
            ],
            "statutory_ref": "The Contract Act, 1872 (Section 74) / Negotiable Instruments Act, 1881",
            "risk_if_missing": "Unclear default interest or compounding penalty terms risk judicial reduction under Section 74.",
            "severity": "HIGH",
        },
        {
            "provision_id": "DEFAULT_CURE_COLLATERAL",
            "title": "Event of Default Notice, Cure Period & Collateral Release",
            "keywords": [
                "default", "cure period", "collateral", "mortgage", "release",
                "খেলাপি", "নোটিশ", "জামানত", "বন্ধক", "দায়মুক্তি",
            ],
            "statutory_ref": "The Transfer of Property Act, 1882 / Artha Rin Adalat Ain, 2003",
            "risk_if_missing": "Immediate acceleration without cure period or collateral discharge clause creates severe borrower/lender exposure.",
            "severity": "MODERATE",
        },
    ],
    "CONSTRUCTION_EPC": [
        {
            "provision_id": "DEFECT_LIABILITY_RETENTION",
            "title": "Defect Liability Period & Quality Inspection Milestones",
            "keywords": [
                "defect liability", "retention", "completion certificate", "boq", "specifications",
                "ত্রুটি সংশোধন", "মান নিয়ন্ত্রণ", "নির্মাণ", "নকশা",
            ],
            "statutory_ref": "The Contract Act, 1872 (Sections 73, 74)",
            "risk_if_missing": "Lack of defect liability period leaves employer unprotected against structural defects post-handover.",
            "severity": "HIGH",
        },
        {
            "provision_id": "DELAY_LD_FORCE_MAJEURE",
            "title": "Milestone Extension, Force Majeure & Cap on Liquidated Damages",
            "keywords": [
                "liquidated damages", "force majeure", "extension of time", "delay",
                "দৈবদুর্বিপাক", "বিলম্ব", "সময় বৃদ্ধি",
            ],
            "statutory_ref": "The Contract Act, 1872 (Sections 56, 74)",
            "risk_if_missing": "Uncapped delay penalties or vague Force Majeure terms invite Section 74 disputes.",
            "severity": "MODERATE",
        },
    ],
    "PROCUREMENT_SUPPLY": [
        {
            "provision_id": "INSPECTION_REJECTION_REFUND",
            "title": "Goods Inspection Window, Rejection & Advance Refund",
            "keywords": [
                "inspect", "inspection", "rejection", "defective goods", "refund",
                "পরীক্ষা", "ত্রুটিপূর্ণ পণ্য", "ফেরত", "অগ্রিম",
            ],
            "statutory_ref": "The Sale of Goods Act, 1930 / The Contract Act, 1872 (Section 65)",
            "risk_if_missing": "Absence of objective inspection timelines allows arbitrary rejection or non-refund of advance payments.",
            "severity": "HIGH",
        },
        {
            "provision_id": "DELIVERY_DISPUTE_RESOLUTION",
            "title": "Delivery Terms, Force Majeure & Dispute Resolution",
            "keywords": [
                "arbitration", "dispute", "force majeure", "governing law",
                "সালিস", "বিরোধ নিষ্পত্তি", "দৈবদুর্বিপাক", "প্রযোজ্য আইন",
            ],
            "statutory_ref": "সালিস আইন, ২০০১ / The Contract Act, 1872",
            "risk_if_missing": "Missing dispute forum or Force Majeure boundaries prolongs commercial supply litigation.",
            "severity": "MODERATE",
        },
    ],
    "GENERAL_COMMERCIAL": [
        {
            "provision_id": "TERMINATION_RESTORATION",
            "title": "Termination Notice & Restoration of Unearned Advances",
            "keywords": [
                "termination", "notice", "refund", "cancel", "rescind",
                "চুক্তি বাতিল", "অবসান", "নোটিশ", "ফেরত",
            ],
            "negations": ["no refund", "non-refundable", "অফেরতযোগ্য"],
            "statutory_ref": "The Contract Act, 1872 (Sections 64, 65)",
            "risk_if_missing": "Unclear termination and advance-refund obligations risk unjust retention upon early exit.",
            "severity": "HIGH",
        },
        {
            "provision_id": "DISPUTE_RESOLUTION_FORUM",
            "title": "Governing Law & Lawful Dispute Resolution Forum",
            "keywords": [
                "governing law", "jurisdiction", "arbitration", "dispute", "court",
                "আদালত", "সালিস", "বিরোধ নিষ্পত্তি", "এখতিয়ার", "প্রযোজ্য আইন",
            ],
            "statutory_ref": "The Contract Act, 1872 (Section 28) / সালিস আইন, ২০০১",
            "risk_if_missing": "Absence of clear Bangladesh governing law and lawful arbitration/court forum creates jurisdictional ambiguity.",
            "severity": "MODERATE",
        },
    ],
}


# ──────────────────────────────────────────────────────────
# CLAUSE SPLITTER & AST PARSER (ENGLISH + BENGALI)
# ──────────────────────────────────────────────────────────

# Matches headers like:
#   "### CLAUSE 4: PAYMENT TERMS", "Clause 4. Payment", "Article IV - Indemnity",
#   "1. DEFINITIONS", "1) Definitions", "১। পক্ষসমূহ", "ধারা ৪: চুক্তি মূল্য", "অনুচ্ছেদ ২ - ..."
_EXPLICIT_HEADER_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?"
    r"(?:Clause|Section|Article|Schedule|Part|ধারা|উপধারা|অনুচ্ছেদ|দফা|শর্ত|শর্তাবলি)\s*"
    r"([0-9০-৯]{1,3}(?:\.[0-9০-৯]{1,2})?|[IVXLCDM]+)"
    r"\s*[\.\:\-\)\।]?\s*(.*)$",
    re.IGNORECASE,
)

# Matches bare numbered headers like "1. PARTIES", "12) GOVERNING LAW", "১। পরিচয়", "২. শর্তাবলি"
_NUMBERED_HEADER_RE = re.compile(
    r"^\s*([0-9০-৯]{1,3}|[IVXLCDM]+)\s*[\.\)\।]\s+(.+)$"
)


def _split_inline_title_and_body(rest: str) -> Tuple[str, str]:
    """Splits 'TITLE: body...' or 'Title. Long body...' into (title, full_rest)."""
    clean = rest.strip()
    if not clean:
        return "", ""
    # If short standalone heading line, keep whole line as title
    if len(clean) <= 85 and not re.search(r"[\.\।]\s+\S{20,}", clean):
        return clean.rstrip(":.-। "), ""
    # Try splitting inline title before first ':', '.', '।', or '—'
    m = re.match(r"^([^:\.\।\—]{2,70}?)\s*[:\.\।\—]\s+(.+)$", clean)
    if m:
        return m.group(1).strip(), clean
    words = clean.split()
    return " ".join(words[:6]).rstrip(":.,-। "), clean


def extract_clauses_from_text(raw_text: str) -> List[Dict[str, Any]]:
    """
    Splits contract text into distinct structured clauses preserving original
    numbering and headings. Supports English and Bengali legal drafting formats.
    """
    if not raw_text or not raw_text.strip():
        return []

    lines = raw_text.splitlines()
    clauses: List[Dict[str, Any]] = []

    current_number: Optional[str] = None
    current_title: Optional[str] = None
    current_lines: List[str] = []

    def _flush_current() -> None:
        if current_number is not None and current_lines:
            body = "\n".join(current_lines).strip()
            if body:
                clauses.append({
                    "source_clause_number": _normalize_digits(current_number),
                    "clause_title": current_title or f"Clause {_normalize_digits(current_number)}",
                    "clause_text": body,
                })

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue

        header_match = _EXPLICIT_HEADER_RE.match(stripped)
        num_match = None if header_match else _NUMBERED_HEADER_RE.match(stripped)

        # Avoid treating sub-clauses like "1.1 ..." inside an existing clause unless no explicit headers exist,
        # and avoid matching dates/years like "2026." as a clause number.
        is_new_clause = False
        num_token = ""
        rest_text = ""

        if header_match:
            num_token = header_match.group(1).strip()
            rest_text = header_match.group(2).strip()
            # Top-level clause if no dot, or if top-level hasn't started yet
            if "." not in _normalize_digits(num_token) or current_number is None:
                is_new_clause = True
        elif num_match:
            candidate_num = num_match.group(1).strip()
            norm_num = _normalize_digits(candidate_num)
            rest_candidate = num_match.group(2).strip()
            # Reject 4-digit years or single lowercase roman 'i.' sub-bullets inside an active clause
            is_reasonable_num = (
                (norm_num.isdigit() and 1 <= int(norm_num) <= 200)
                or (candidate_num.isupper() and len(candidate_num) <= 5)
            )
            if is_reasonable_num and len(rest_candidate) >= 2:
                is_new_clause = True
                num_token = candidate_num
                rest_text = rest_candidate

        if is_new_clause:
            _flush_current()
            title_part, _ = _split_inline_title_and_body(rest_text)
            current_number = num_token
            current_title = title_part or f"Clause {_normalize_digits(num_token)}"
            current_lines = [stripped]
        else:
            current_lines.append(line)

    _flush_current()

    # Fallback: If regex matched fewer than 2 clauses, try blank-line paragraph splitting
    if len(clauses) < 2:
        paragraphs = re.split(r"\n\s*\n+", raw_text.strip())
        fallback_clauses: List[Dict[str, Any]] = []
        for idx, p in enumerate(paragraphs, start=1):
            p_strip = p.strip()
            if not p_strip or len(p_strip) < 25:
                continue
            first_line = p_strip.splitlines()[0].strip()[:70]
            fallback_clauses.append({
                "source_clause_number": str(idx),
                "clause_title": first_line,
                "clause_text": p_strip,
            })
        if len(fallback_clauses) > len(clauses):
            return fallback_clauses

    return clauses


# ──────────────────────────────────────────────────────────
# WEIGHTED BILINGUAL CONTRACT DOMAIN CLASSIFIER
# ──────────────────────────────────────────────────────────

_DOMAIN_SIGNALS: Dict[str, Dict[str, Any]] = {
    "TECH_SAAS_VENDOR": {
        "name": "Technology / SaaS & Software Services Agreement",
        "title_keywords": [
            "saas", "software development", "software services", "cloud services", "api integration",
            "সফটওয়্যার", "ওয়েবসাইট", "অ্যাপ", "আইটি সেবা",
        ],
        "body_keywords": [
            "software development", "software", "saas", "source code", "platform data",
            "api integration", "cloud-based", "deliverable", "deliverables", "uat",
            "acceptance testing", "sub-processor", "sub-processors", "open source", "open-source", "gpl",
            "সোর্স কোড", "সফটওয়্যার", "ডেটা নিরাপত্তা", "সার্ভার",
        ],
    },
    "EMPLOYMENT_SERVICE": {
        "name": "Employment & Professional Services Agreement",
        "title_keywords": [
            "employment agreement", "employment offer", "appointment letter", "offer letter", "service bond",
            "নিয়োগপত্র", "চাকরির চুক্তি", "কর্মসংস্থান চুক্তি",
        ],
        "body_keywords": [
            "employment agreement", "appointment letter", "probation period", "gratuity",
            "provident fund", "salary per month", "gross monthly salary", "salary", "resignation", "resign",
            "labour act", "earned leave", "sick leave", "casual leave", "severance", "employee", "employer",
            "বেতন", "নিয়োগ", "কর্মচারী", "কর্মী", "চাকরি", "ছুটি", "গ্র্যাচুইটি", "প্রভিডেন্ট ফান্ড", "শ্রম আইন",
        ],
    },
    "COMMERCIAL_LEASE": {
        "name": "Commercial Real Estate & Premises Lease",
        "title_keywords": [
            "lease agreement", "tenancy agreement", "rental agreement", "rent agreement",
            "ভাড়া চুক্তি", "বাড়ি ভাড়া", "দোকান ভাড়া", "ফ্ল্যাট ভাড়া", "ইজারা চুক্তি",
        ],
        "body_keywords": [
            "lease agreement", "lease", "tenancy", "premises rent", "landlord", "tenant",
            "security deposit", "monthly rent", "rent", "vacant handover", "sub-registry",
            "ভাড়াটিয়া", "বাড়িওয়ালা", "মালিক", "মাসিক ভাড়া", "জামানত", "অগ্রিম ভাড়া", "ইজারা",
        ],
    },
    "NDA_CONFIDENTIALITY": {
        "name": "Non-Disclosure & Confidentiality Agreement",
        "title_keywords": [
            "non-disclosure", "nda", "confidentiality agreement",
            "গোপনীয়তা চুক্তি", "নন-ডিসক্লোজার",
        ],
        "body_keywords": [
            "non-disclosure", "confidentiality agreement", "receiving party",
            "disclosing party", "trade secret", "confidential information", "unaided memory",
            "গোপনীয় তথ্য", "প্রকাশকারী পক্ষ", "গ্রহণকারী পক্ষ", "বাণিজ্যিক গোপনীয়তা",
        ],
    },
    "PROPERTY_SALE_TRANSFER": {
        "name": "Property Sale, Bainanama & Land Transfer Deed",
        "title_keywords": [
            "sale deed", "bainanama", "agreement for sale", "conveyance",
            "বায়নাপত্র", "বায়নানামা", "সাফ কবলা", "বিক্রয় চুক্তি", "জমি ক্রয়", "হেবা",
        ],
        "body_keywords": [
            "earnest money", "sale consideration", "chain of title", "immovable property",
            "mouza", "khatian", "dag no", "বায়না", "মৌজা", "খতিয়ান", "দাগ", "কাঠা", "শতাংশ",
            "বিক্রয় মূল্য", "নামজারি", "সাব-কবলা",
        ],
    },
    "PARTNERSHIP_JV": {
        "name": "Partnership & Joint Venture Agreement",
        "title_keywords": [
            "partnership", "joint venture", "consortium agreement", "shareholders agreement",
            "অংশীদারি চুক্তি", "যৌথ ব্যবসা", "পার্টনারশিপ",
        ],
        "body_keywords": [
            "partnership act", "profit and loss", "capital contribution", "joint venture",
            "managing partner", "dissolution of partnership",
            "অংশীদার", "লভ্যাংশ", "মূলধন", "যৌথ মূলধন", "অংশীদারি আইন",
        ],
    },
    "LOAN_FINANCING": {
        "name": "Loan, Credit Facility & Mortgage Agreement",
        "title_keywords": [
            "loan agreement", "facility agreement", "mortgage deed", "promissory note",
            "ঋণ চুক্তি", "বন্ধকী চুক্তি", "কর্জ চুক্তি",
        ],
        "body_keywords": [
            "borrower", "lender", "principal amount", "interest rate", "collateral",
            "mortgage", "repayment schedule", "promissory",
            "ঋণগ্রহীতা", "ঋণদাতা", "আসল টাকা", "কিস্তি", "বন্ধক", "জামানত",
        ],
    },
    "CONSTRUCTION_EPC": {
        "name": "Construction, Civil Works & EPC Contract",
        "title_keywords": [
            "construction", "epc contract", "civil works", "building contract",
            "নির্মাণ চুক্তি", "ঠিকাদারি চুক্তি",
        ],
        "body_keywords": [
            "contractor", "defect liability", "bill of quantities", "boq", "site handover",
            "architect", "retention money", "ঠিকাদার", "নির্মাণ কাজ", "ভবন নির্মাণ", "নকশা",
        ],
    },
    "PROCUREMENT_SUPPLY": {
        "name": "Procurement & Commercial Supply Agreement",
        "title_keywords": [
            "procurement", "supply agreement", "purchase order", "distribution agreement",
            "সরবরাহ চুক্তি", "ক্রয় চুক্তি", "পরিবেশক চুক্তি",
        ],
        "body_keywords": [
            "procurement", "purchase order", "supply agreement", "bill of lading",
            "delivery of goods", "supplier", "buyer", "defective goods",
            "পণ্য সরবরাহ", "সরবরাহকারী", "ক্রেতা", "মালামাল", "ডেলিভারি",
        ],
    },
}


def classify_contract_domain(text: str) -> Dict[str, Any]:
    """
    Classifies any English or Bengali contract using weighted title + body scoring,
    preventing single stray keywords from misclassifying the entire contract.
    """
    lower = (text or "").lower()
    # Inspect the title/preamble (first 2 lines or 180 chars) for strong title signal
    first_lines = "\n".join(lower.splitlines()[:2])
    header_slice = first_lines[:200]

    best_domain = "GENERAL_COMMERCIAL"
    best_name = "General Commercial Contract"
    best_score = 0

    for domain_key, cfg in _DOMAIN_SIGNALS.items():
        score = 0
        for kw in cfg["title_keywords"]:
            if _contains_term(header_slice, kw):
                score += 5
            elif _contains_term(lower, kw):
                score += 1
        for kw in cfg["body_keywords"]:
            if _contains_term(lower, kw):
                score += 1
        if score > best_score:
            best_score = score
            best_domain = domain_key
            best_name = cfg["name"]

    # Require at least 2 signals (or a title match) so one generic word doesn't override GENERAL_COMMERCIAL
    if best_score < 2:
        best_domain = "GENERAL_COMMERCIAL"
        best_name = "General Commercial Contract"

    # Bilingual party extraction
    party_a = "Client / First Party"
    party_b = "Service Provider / Second Party"

    p_a_match = re.search(
        r"(?:^|[\n\r,;।])\s*(?:Party\s*A|First\s*Party|Client|Employer|Landlord|Disclosing\s*Party|Seller|Lender|"
        r"প্রথম\s*পক্ষ|১ম\s*পক্ষ|মালিক|নিয়োগকর্তা|বিক্রেতা|ঋণদাতা)"
        r"\s*[\:\—\-]\s*([^\n\r,;।]+)",
        text or "",
        re.IGNORECASE,
    )
    if p_a_match:
        party_a = p_a_match.group(1).strip()

    p_b_match = re.search(
        r"(?:^|[\n\r,;।])\s*(?:Party\s*B|Second\s*Party|Service\s*Provider|Vendor|Employee|Tenant|Receiving\s*Party|Buyer|Borrower|"
        r"দ্বিতীয়\s*পক্ষ|২য়\s*পক্ষ|ভাড়াটিয়া|কর্মচারী|(?<!বি)ক্রেতা|ঠিকাদার|ঋণগ্রহীতা)"
        r"\s*[\:\—\-]\s*([^\n\r,;।]+)",
        text or "",
        re.IGNORECASE,
    )
    if p_b_match:
        party_b = p_b_match.group(1).strip()

    return {
        "domain": best_domain,
        "domain_name": best_name,
        "party_a": party_a,
        "party_b": party_b,
    }


# ──────────────────────────────────────────────────────────
# MISSING PROVISIONS DETECTOR (BILINGUAL, NO SAAS BIAS)
# ──────────────────────────────────────────────────────────

def detect_missing_mandatory_provisions(
    clauses: List[Dict[str, Any]],
    domain: str,
    full_text: str,
) -> List[Dict[str, Any]]:
    """
    Compares parsed clauses against mandatory protective provisions for the detected
    Bangladesh contract domain (supporting both English and Bengali keywords).
    """
    mandatory_list = DOMAIN_MANDATORY_PROVISIONS.get(
        domain, DOMAIN_MANDATORY_PROVISIONS["GENERAL_COMMERCIAL"]
    )
    missing: List[Dict[str, Any]] = []

    lower_full = (full_text or "").lower()

    for item in mandatory_list:
        keywords = item["keywords"]
        found = any(_contains_term(lower_full, kw) for kw in keywords)

        negations = item.get("negations", [])
        is_specifically_negated = any(_contains_term(lower_full, neg) for neg in negations)

        if not found or is_specifically_negated:
            missing.append({
                "provision_id": item["provision_id"],
                "title": item["title"],
                "statutory_reference": item["statutory_ref"],
                "risk_explanation": item["risk_if_missing"],
                "severity": item["severity"],
            })

    return missing


# ──────────────────────────────────────────────────────────
# STAMP DUTY & FORMAL EXECUTION ADVISORY
# ──────────────────────────────────────────────────────────

def generate_stamp_duty_advisory(domain: str, contract_text: str) -> str:
    """
    Provides Bangladesh Stamp Act, 1899 and Registration Act, 1908 guidance
    across all contract domains, extracting BDT/Tk/টাকা amounts in EN or BN.
    """
    text = contract_text or ""
    val_match = re.search(
        r"(?:BDT|TK|Taka|Tk\.|৳)\s*[\d,০-৯]+|[\d,০-৯]+\s*(?:টাকা|লক্ষ|কোটি)",
        text,
        re.IGNORECASE,
    )
    contract_value = val_match.group(0).strip() if val_match else None
    val_str = f" for contracts valued at {contract_value}" if contract_value else ""

    if domain == "COMMERCIAL_LEASE":
        return (
            "Under Schedule 1, Article 35 of The Stamp Act, 1899, commercial leases in Bangladesh require "
            "ad valorem stamp duty calculated on average annual rent. Furthermore, leases exceeding one (1) year "
            "must be registered with the relevant Sub-Registry Office under Section 17 of The Registration Act, 1908; "
            "unregistered leases are inadmissible in evidence under Section 49."
        )
    if domain == "PROPERTY_SALE_TRANSFER":
        return (
            f"Immovable property transfer deeds or Bainanama agreements{val_str} require statutory stamp duty under "
            "Article 23 / Article 5 of Schedule 1 of The Stamp Act, 1899, and mandatory registration at the Sub-Registry "
            "Office under Section 17 of The Registration Act, 1908 and Section 54 of The Transfer of Property Act, 1882."
        )
    if domain == "PARTNERSHIP_JV":
        return (
            "Under Schedule 1, Article 46 of The Stamp Act, 1899, partnership deeds require specific slab stamp duty "
            "based on capital contribution, and optional/recommended firm registration with the RJSC under the Partnership Act, 1932."
        )
    if domain == "LOAN_FINANCING":
        return (
            f"Loan, promissory, or mortgage instruments{val_str} attract specific stamp duty under Schedule 1 "
            "(Articles 40/49/5) of The Stamp Act, 1899; mortgages over immovable property require registration under Section 17 of The Registration Act, 1908."
        )
    if domain == "EMPLOYMENT_SERVICE":
        return (
            "Employment contracts must comply with the mandatory written appointment letter and service record requirements "
            "under Section 5 of the Bangladesh Labour Act, 2006. Standard non-judicial stamp duty applies if executed "
            "as a formal bilateral covenant or indemnity bond."
        )
    if domain in ("TECH_SAAS_VENDOR", "PROCUREMENT_SUPPLY", "CONSTRUCTION_EPC", "GENERAL_COMMERCIAL"):
        return (
            f"Under Schedule 1, Article 5 of The Stamp Act, 1899, standard commercial agreements{val_str} "
            "must be executed on non-judicial stamp paper of appropriate value (typically BDT 300 non-judicial stamp). "
            "To ensure full evidentiary enforceability under Section 35 of The Stamp Act in Bangladesh courts, "
            "both parties must sign each page in the presence of at least two (2) attesting witnesses."
        )
    return (
        "Execute on minimum BDT 300 non-judicial stamp paper pursuant to The Stamp Act, 1899, "
        "signed by authorized signatories and witnessed by two independent witnesses."
    )
