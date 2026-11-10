"""
ContractMind - Sovereign Structural Clause Segmentation & AST Engine
Specialized for Bangladesh Commercial and Statutory Contracts.

Features:
- Regex-based AST structural parser preserving original clause numbers and titles.
- Contract Domain Classifier (Tech SaaS, Employment, Commercial Lease, NDA, General Commercial).
- Missing Mandatory Clause / Gap Detector per domain.
- Bangladesh Stamp Duty (The Stamp Act, 1899) and Formal Execution Advisory.
"""

import re
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# DOMAIN CHECKLISTS: MANDATORY PROTECTIVE CLAUSES
# ──────────────────────────────────────────────────────────

DOMAIN_MANDATORY_PROVISIONS: Dict[str, List[Dict[str, Any]]] = {
    "TECH_SAAS_VENDOR": [
        {
            "provision_id": "IP_ASSIGNMENT",
            "title": "Intellectual Property Assignment & Work Made for Hire",
            "keywords": ["intellectual property", "ownership", "work made for hire", "assignment of copyright", "source code"],
            "statutory_ref": "Copyright Act, 2000 (Sections 15, 17)",
            "risk_if_missing": "Client may not legally own custom software source code, creating copyright clouds and licensing exposure.",
            "severity": "HIGH"
        },
        {
            "provision_id": "DATA_BREACH_NOTICE",
            "title": "Data Security & Breach Incident Notification (72h)",
            "keywords": ["data security", "data privacy", "incident notification", "breach notification", "security measures", "data deletion"],
            "statutory_ref": "Cyber Security Act, 2023 / ICT Act, 2006",
            "risk_if_missing": "Absence of mandatory security breach reporting timelines exposes client databases to regulatory liability.",
            "severity": "HIGH"
        },
        {
            "provision_id": "THIRD_PARTY_OSS_WARRANTY",
            "title": "Third-Party & Open Source Software (OSS) Licensing Warranty",
            "keywords": ["third-party software", "open source", "oss", "gpl", "third-party components", "proprietary methodologies"],
            "statutory_ref": "Copyright Act, 2000 / Commercial Standard",
            "risk_if_missing": "Silence on OSS licenses may force disclosure of client proprietary business logic under viral copyleft licenses.",
            "severity": "MODERATE"
        },
        {
            "provision_id": "ADVANCE_PAYMENT_REFUND",
            "title": "Termination Refund for Unearned Advance Payments",
            "keywords": ["refund", "advance payment", "reimbursement", "unperformed services", "return of deposit"],
            "statutory_ref": "The Contract Act, 1872 (Sections 64, 65)",
            "risk_if_missing": "Service provider may retain unearned advance fees (e.g., 50% advance) upon early termination or material breach.",
            "severity": "HIGH"
        },
        {
            "provision_id": "ACCEPTANCE_TESTING_CRITERIA",
            "title": "Objective Deliverable Acceptance & UAT Window",
            "keywords": ["acceptance testing", "uat", "testing period", "rejection notice", "acceptance criteria", "remedy period"],
            "statutory_ref": "Commercial Standard / General Contract Principle",
            "risk_if_missing": "Deemed acceptance within an unreasonably brief period (e.g. 3 days) forces client to accept buggy or incomplete software.",
            "severity": "MODERATE"
        },
        {
            "provision_id": "MUTUAL_IP_INDEMNIFICATION",
            "title": "Vendor Intellectual Property Infringement Indemnity",
            "keywords": ["indemnify", "indemnity", "hold harmless", "patent infringement", "copyright infringement"],
            "statutory_ref": "The Contract Act, 1872 (Section 124)",
            "risk_if_missing": "Client bears full liability if vendor-developed code infringes third-party patents or proprietary copyrights.",
            "severity": "HIGH"
        }
    ],
    "EMPLOYMENT_SERVICE": [
        {
            "provision_id": "STATUTORY_NOTICE_PERIOD",
            "title": "Statutory Termination Notice & Severance Pay",
            "keywords": ["notice period", "severance", "termination notice", "statutory pay", "compensation"],
            "statutory_ref": "Bangladesh Labour Act, 2006 (Sections 26, 27)",
            "risk_if_missing": "Termination without codified notice (120/60/30 days based on tenure) violates mandatory Bangladesh labour protections.",
            "severity": "HIGH"
        },
        {
            "provision_id": "PROVIDENT_FUND_GRATUITY",
            "title": "Provident Fund, Gratuity & Terminal Benefits",
            "keywords": ["gratuity", "provident fund", "pf", "terminal benefits", "separation pay"],
            "statutory_ref": "Bangladesh Labour Act, 2006 (Sections 27, 28)",
            "risk_if_missing": "Failure to articulate statutory terminal benefits exposes enterprise to Labour Court litigation and penalties.",
            "severity": "MODERATE"
        },
        {
            "provision_id": "RESTRAINT_OF_TRADE_LIMITATION",
            "title": "Lawful Post-Employment Restraint Boundaries",
            "keywords": ["non-compete", "restraint of trade", "competing business", "post-employment"],
            "statutory_ref": "The Contract Act, 1872 (Section 27)",
            "risk_if_missing": "Post-employment non-compete clauses are void ab initio in Bangladesh; agreement must limit covenants to confidentiality and trade secrets.",
            "severity": "HIGH"
        }
    ],
    "COMMERCIAL_LEASE": [
        {
            "provision_id": "SECURITY_DEPOSIT_RETURN",
            "title": "Security Deposit Adjustment & Return Timeline",
            "keywords": ["security deposit", "advance rent", "refund of deposit", "deduction from deposit"],
            "statutory_ref": "Premises Rent Control Act, 1991 / Transfer of Property Act, 1882",
            "risk_if_missing": "Landlord may withhold security deposit indefinitely without objective dilapidation claims.",
            "severity": "HIGH"
        },
        {
            "provision_id": "STAMP_DUTY_REGISTRATION",
            "title": "Mandatory Registration & Stamp Duty Formalities",
            "keywords": ["registration", "sub-registry", "stamp paper", "non-judicial stamp", "stamp duty"],
            "statutory_ref": "Registration Act, 1908 (Section 17) / The Stamp Act, 1899",
            "risk_if_missing": "Lease exceeding one year executed on plain paper without registration is inadmissible in evidence under Section 49.",
            "severity": "HIGH"
        }
    ],
    "NDA_CONFIDENTIALITY": [
        {
            "provision_id": "STANDARD_EXCLUSIONS",
            "title": "Standard Confidentiality Exclusions (Public Domain, Legal Compulsion)",
            "keywords": ["public domain", "prior knowledge", "court order", "required by law", "independently developed"],
            "statutory_ref": "Commercial Standard / The Specific Relief Act, 1877",
            "risk_if_missing": "Absence of carve-outs creates strict liability if information is demanded by law enforcement or regulatory authorities.",
            "severity": "MODERATE"
        },
        {
            "provision_id": "REASONABLE_TERM_SUNSET",
            "title": "Defined Confidentiality Expiration Period (2-5 Years)",
            "keywords": ["survival", "sunset", "years after termination", "duration of confidentiality"],
            "statutory_ref": "The Contract Act, 1872 (Section 27)",
            "risk_if_missing": "Perpetual confidentiality on ordinary business info may be challenged as an unreasonable restraint of trade.",
            "severity": "MODERATE"
        }
    ]
}


# ──────────────────────────────────────────────────────────
# CLAUSE SPLITTER & AST PARSER
# ──────────────────────────────────────────────────────────

def extract_clauses_from_text(raw_text: str) -> List[Dict[str, Any]]:
    """
    Splits contract text into distinct structured clauses preserving original
    numbering and headings. Supports standard Bangladesh and English legal drafting formats.
    """
    if not raw_text or not raw_text.strip():
        return []

    lines = raw_text.splitlines()
    clauses: List[Dict[str, Any]] = []

    # Regex patterns for numbered clause headers
    # Examples:
    # "1. PARTIES", "2. PURPOSE AND SCOPE OF SERVICES", "Clause 4. PAYMENT TERMS"
    # "Article IV - INDEMNIFICATION", "SECTION 10: LIMITATION OF LIABILITY"
    # "১. পক্ষসমূহ", "ধারা ৪: চুক্তি মূল্য"
    clause_header_pattern = re.compile(
        r"^(?:\s*(?:Clause|Section|Article|ধারা|অনুচ্ছেদ)?\s*([0-9]{1,2}|[IVXLCDM]+)[\.\:\-]\s*([^\n\r]{2,80}))\s*$",
        re.IGNORECASE
    )

    current_number = None
    current_title = None
    current_lines = []

    for line in lines:
        stripped = line.strip()
        match = clause_header_pattern.match(stripped)

        if match and len(stripped) < 90:
            # Found a new clause header
            if current_number is not None and current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    clauses.append({
                        "source_clause_number": current_number,
                        "clause_title": current_title,
                        "clause_text": body
                    })
            current_number = match.group(1).strip()
            current_title = match.group(2).strip()
            current_lines = [stripped]
        else:
            if current_number is not None:
                current_lines.append(line)
            else:
                # Preamble / Recitals before Clause 1
                current_lines.append(line)

    # Append trailing clause
    if current_number is not None and current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            clauses.append({
                "source_clause_number": current_number,
                "clause_title": current_title,
                "clause_text": body
            })

    # Fallback: If regex matched fewer than 3 clauses, try section/paragraph splitting
    if len(clauses) < 3:
        paragraphs = re.split(r"\n\s*\n", raw_text)
        fallback_clauses = []
        for idx, p in enumerate(paragraphs, start=1):
            p_strip = p.strip()
            if not p_strip or len(p_strip) < 30:
                continue
            first_line = p_strip.splitlines()[0][:60]
            fallback_clauses.append({
                "source_clause_number": str(idx),
                "clause_title": first_line,
                "clause_text": p_strip
            })
        if len(fallback_clauses) > len(clauses):
            return fallback_clauses

    return clauses


# ──────────────────────────────────────────────────────────
# CONTRACT DOMAIN CLASSIFIER
# ──────────────────────────────────────────────────────────

def classify_contract_domain(text: str) -> Dict[str, Any]:
    """
    Classifies the contract domain and detects contracting parties.
    """
    lower = text.lower()

    # Domain keyword heuristics
    if any(k in lower for k in ["software development", "saas", "source code", "platform data", "api integration", "cloud-based", "deliverable"]):
        domain = "TECH_SAAS_VENDOR"
        domain_name = "Technology / SaaS & Software Services Agreement"
    elif any(k in lower for k in ["employment agreement", "appointment letter", "probation period", "gratuity", "provident fund", "employee", "salary per month"]):
        domain = "EMPLOYMENT_SERVICE"
        domain_name = "Employment & Professional Services Agreement"
    elif any(k in lower for k in ["lease agreement", "tenancy", "premises rent", "landlord", "tenant", "security deposit"]):
        domain = "COMMERCIAL_LEASE"
        domain_name = "Commercial Real Estate & Premises Lease"
    elif any(k in lower for k in ["non-disclosure", "confidentiality agreement", "receiving party", "disclosing party", "trade secret"]):
        domain = "NDA_CONFIDENTIALITY"
        domain_name = "Non-Disclosure & Confidentiality Agreement"
    elif any(k in lower for k in ["procurement", "purchase order", "supply agreement", "bill of lading", "delivery of goods"]):
        domain = "PROCUREMENT_SUPPLY"
        domain_name = "Procurement & Commercial Supply Agreement"
    else:
        domain = "GENERAL_COMMERCIAL"
        domain_name = "General Commercial Contract"

    # Party extraction
    party_a = "Client / First Party"
    party_b = "Service Provider / Second Party"

    p_a_match = re.search(r"(?:Party\s*A|Client|Employer|First\s*Party)\s*[\:\—\-]\s*([^\n\r,]+)", text, re.IGNORECASE)
    if p_a_match:
        party_a = p_a_match.group(1).strip()

    p_b_match = re.search(r"(?:Party\s*B|Service\s*Provider|Vendor|Employee|Second\s*Party)\s*[\:\—\-]\s*([^\n\r,]+)", text, re.IGNORECASE)
    if p_b_match:
        party_b = p_b_match.group(1).strip()

    return {
        "domain": domain,
        "domain_name": domain_name,
        "party_a": party_a,
        "party_b": party_b
    }


# ──────────────────────────────────────────────────────────
# MISSING PROVISIONS DETECTOR
# ──────────────────────────────────────────────────────────

def detect_missing_mandatory_provisions(
    clauses: List[Dict[str, Any]],
    domain: str,
    full_text: str
) -> List[Dict[str, Any]]:
    """
    Compares the contract's parsed clauses against mandatory protective provisions
    for that specific domain under Bangladesh law.
    """
    mandatory_list = DOMAIN_MANDATORY_PROVISIONS.get(domain, DOMAIN_MANDATORY_PROVISIONS.get("TECH_SAAS_VENDOR", []))
    missing: List[Dict[str, Any]] = []

    lower_full = full_text.lower()

    for item in mandatory_list:
        keywords = item["keywords"]
        # Check if any keyword exists in the entire document
        found = any(kw in lower_full for kw in keywords)

        # Contextual check: even if keyword exists, does it lack essential protections?
        # e.g., if "incident notification" keyword exists, but text says "No specific incident notification period is established":
        is_specifically_negated = False
        if item["provision_id"] == "DATA_BREACH_NOTICE":
            if "no specific incident notification" in lower_full or "no incident notification" in lower_full:
                is_specifically_negated = True
        elif item["provision_id"] == "ADVANCE_PAYMENT_REFUND":
            if "no specific refund procedure" in lower_full or "no refund" in lower_full:
                is_specifically_negated = True
        elif item["provision_id"] == "THIRD_PARTY_OSS_WARRANTY":
            if "does not expressly establish ownership or licensing terms for third-party" in lower_full:
                is_specifically_negated = True

        if not found or is_specifically_negated:
            missing.append({
                "provision_id": item["provision_id"],
                "title": item["title"],
                "statutory_reference": item["statutory_ref"],
                "risk_explanation": item["risk_if_missing"],
                "severity": item["severity"]
            })

    return missing


# ──────────────────────────────────────────────────────────
# STAMP DUTY & FORMAL EXECUTION ADVISORY
# ──────────────────────────────────────────────────────────

def generate_stamp_duty_advisory(domain: str, contract_text: str) -> str:
    """
    Provides precise Bangladesh Stamp Act, 1899 and Registration Act, 1908
    guidance based on contract domain and transaction value.
    """
    # Look for value (e.g. BDT 12,00,000)
    val_match = re.search(r"(?:BDT|TK|Taka|Tk\.)\s*([\d,]+)", contract_text, re.IGNORECASE)
    contract_value = val_match.group(0) if val_match else None

    if domain == "COMMERCIAL_LEASE":
        return (
            "Under Schedule 1, Article 35 of The Stamp Act, 1899, commercial leases in Bangladesh require "
            "ad valorem stamp duty calculated on average annual rent. Furthermore, leases exceeding one (1) year "
            "must be registered with the relevant Sub-Registry Office under Section 17 of the Registration Act, 1908; "
            "unregistered leases are inadmissible in evidence under Section 49."
        )
    elif domain in ("TECH_SAAS_VENDOR", "GENERAL_COMMERCIAL", "PROCUREMENT_SUPPLY"):
        val_str = f" for contracts of value {contract_value}" if contract_value else ""
        return (
            f"Under Schedule 1, Article 5 of The Stamp Act, 1899, standard commercial agreements{val_str} "
            "must be executed on non-judicial stamp paper of appropriate value (typically BDT 300 non-judicial stamp). "
            "To ensure full evidentiary enforceability under Section 35 of The Stamp Act in Bangladesh courts, "
            "both parties must sign each page in the presence of at least two (2) attesting witnesses."
        )
    elif domain == "EMPLOYMENT_SERVICE":
        return (
            "Employment contracts must comply with the mandatory appointment letter and service record requirements "
            "under Section 5 of the Bangladesh Labour Act, 2006. Standard non-judicial stamp duty applies if executed "
            "as a formal bilateral covenant."
        )
    else:
        return (
            "Execute on minimum BDT 300 non-judicial stamp paper pursuant to The Stamp Act, 1899, "
            "signed by authorized signatories and witnessed by two independent witnesses."
        )
