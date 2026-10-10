"""
Unit tests for Citation & Claim Verifier.
Validates:
1. Real section + correct quote -> verified.
2. Fabricated section number -> caught and rejected (unverified).
3. Real section + wrong quote -> caught and rejected (unverified).
4. Real section not in retrieved context -> caught and rejected (ungrounded).
5. Unsupported claim (entailment failure) -> caught and marked unverified.
"""

import pytest
from unittest.mock import MagicMock

from app.services.verifier import (
    verifier,
    CitationInput,
    ClaimInput,
    clean_for_comparison,
    find_best_quote_match,
)
from app.services.retriever import RetrievedSection, CitationInfo
from app.services.law_store import SectionRecord, law_store


@pytest.fixture(autouse=True)
def setup_mock_store(monkeypatch):
    """Sets up controlled statutory data for deterministic verifier testing."""
    test_contract_sec = SectionRecord(
        id="BD-act-26-sec-10-en",
        pair_id="BD-act-26-sec-10",
        jurisdiction="BD",
        act_name="The Contract Act, 1872",
        act_year=1872,
        act_id="act-26",
        chapter="Chapter II",
        section_no="10",
        section_no_raw="10.",
        section_title="What agreements are contracts",
        text=(
            "All agreements are contracts if they are made by the free consent "
            "of parties competent to contract, for a lawful consideration and "
            "with a lawful object, and are not hereby expressly declared to be void."
        ),
        language="en",
        source_url="https://bdlaws.minlaw.gov.bd/act-26/section-172.html"
    )

    test_labour_sec = SectionRecord(
        id="BD-act-952-sec-46-bn",
        pair_id="BD-act-952-sec-46",
        jurisdiction="BD",
        act_name="The Bangladesh Labour Act, 2006",
        act_name_bn="বাংলাদেশ শ্রম আইন, ২০০৬",
        act_year=2006,
        act_id="act-952",
        chapter="চতুর্থ অধ্যায়",
        section_no="46",
        section_no_raw="৪৬৷",
        section_title="প্রসূতি কল্যাণ সুবিধা প্রাপ্তির অধিকার এবং প্রদানের দায়িত্ব",
        text=(
            "৪৬৷ (১) প্রত্যেক নারী শ্রমিক তাহার মালিকের নিকট হইতে তাহার সন্তান প্রসবের "
            "সম্ভাব্য তারিখের অব্যবহিত পূর্ববর্তী ৬০ (ষাট) দিন এবং সন্তান প্রসবের "
            "অব্যবহিত পরবর্তী ৬০ (ষাট) দিনের জন্য প্রসূতি কল্যাণ সুবিধা পাইবার অধিকারী হইবেন।"
        ),
        language="bn",
        source_url="https://bdlaws.minlaw.gov.bd/act-952/section-26452.html"
    )

    mock_db = {
        ("act-26", "10"): [test_contract_sec],
        ("the contract act, 1872", "10"): [test_contract_sec],
        ("act-952", "46"): [test_labour_sec],
        ("the bangladesh labour act, 2006", "46"): [test_labour_sec],
        ("বাংলাদেশ শ্রম আইন, ২০০৬", "46"): [test_labour_sec],
    }

    def mock_lookup(act_id, sec_no, lang=None):
        key = (act_id.lower().strip(), sec_no.strip())
        return mock_db.get(key, [])

    monkeypatch.setattr(law_store, "lookup_section", mock_lookup)


def test_quote_matching_exact_and_fuzzy():
    statute = (
        "All agreements are contracts if they are made by the free consent "
        "of parties competent to contract, for a lawful consideration."
    )
    exact_quote = "free consent of parties competent to contract"
    matched, score, snippet = find_best_quote_match(exact_quote, statute)
    assert matched is True
    assert score == 1.0

    # Slight punctuation/whitespace difference
    fuzzy_quote = "free  consent of parties, competent to contract"
    matched, score, snippet = find_best_quote_match(fuzzy_quote, statute)
    assert matched is True
    assert score >= 0.85

    # Completely bogus quote
    bogus_quote = "all employers must distribute company stock options every month"
    matched, score, snippet = find_best_quote_match(bogus_quote, statute)
    assert matched is False
    assert score < 0.50


def test_verified_citation_success():
    retrieved = [
        RetrievedSection(
            section_id="BD-act-26-sec-10-en",
            pair_id="BD-act-26-sec-10",
            score=1.0,
            text=(
                "All agreements are contracts if they are made by the free consent "
                "of parties competent to contract."
            ),
            citation=CitationInfo(
                act_name="The Contract Act, 1872",
                act_year=1872,
                act_id="act-26",
                section_no="10",
                section_title="What agreements are contracts",
                source_url="https://bdlaws.minlaw.gov.bd/act-26/section-172.html"
            ),
            language="en",
            via="exact"
        )
    ]

    claim = ClaimInput(
        text="A contract requires free consent of competent parties.",
        citations=[
            CitationInput(
                act="The Contract Act, 1872",
                section_no="10",
                quote="free consent of parties competent to contract"
            )
        ]
    )

    result = verifier.verify_claim(claim, retrieved, check_entailment=False)
    assert result.status == "verified"
    assert len(result.citations) == 1
    assert result.citations[0].verified is True


def test_fabricated_section_number_rejected():
    retrieved = []
    claim = ClaimInput(
        text="Section 999 mandates free housing for all employees.",
        citations=[
            CitationInput(
                act="The Bangladesh Labour Act, 2006",
                section_no="999",  # Fabricated section
                quote="free housing for all employees"
            )
        ]
    )

    result = verifier.verify_claim(claim, retrieved, check_entailment=False)
    assert result.status == "unverified"
    assert len(result.citations) == 1
    assert result.citations[0].verified is False
    assert "does not exist" in result.citations[0].reason


def test_real_section_not_in_retrieved_context_rejected():
    # Retrieved context only has Section 10 of Contract Act
    retrieved = [
        RetrievedSection(
            section_id="BD-act-26-sec-10-en",
            pair_id="BD-act-26-sec-10",
            score=1.0,
            text="All agreements are contracts...",
            citation=CitationInfo(
                act_name="The Contract Act, 1872",
                act_year=1872,
                act_id="act-26",
                section_no="10",
                section_title="What agreements are contracts",
                source_url="https://bdlaws.minlaw.gov.bd/act-26/section-172.html"
            ),
            language="en",
            via="exact"
        )
    ]

    # Claim cites Section 46 of Labour Act (which exists in DB, but was NOT retrieved for this request)
    claim = ClaimInput(
        text="Maternity benefit is payable for 60 days before and after delivery.",
        citations=[
            CitationInput(
                act="The Bangladesh Labour Act, 2006",
                section_no="46",
                quote="৬০ (ষাট) দিন"
            )
        ]
    )

    result = verifier.verify_claim(claim, retrieved, check_entailment=False)
    assert result.status == "unverified"
    assert result.citations[0].verified is False
    assert "not present in the retrieved legal context" in result.citations[0].reason


def test_real_section_with_wrong_quote_rejected():
    retrieved = [
        RetrievedSection(
            section_id="BD-act-26-sec-10-en",
            pair_id="BD-act-26-sec-10",
            score=1.0,
            text="All agreements are contracts if they are made by the free consent of parties...",
            citation=CitationInfo(
                act_name="The Contract Act, 1872",
                act_year=1872,
                act_id="act-26",
                section_no="10",
                section_title="What agreements are contracts",
                source_url="https://bdlaws.minlaw.gov.bd/act-26/section-172.html"
            ),
            language="en",
            via="exact"
        )
    ]

    # Quote does not appear anywhere in Section 10
    claim = ClaimInput(
        text="Section 10 guarantees triple damages in all breach cases.",
        citations=[
            CitationInput(
                act="The Contract Act, 1872",
                section_no="10",
                quote="guarantees triple damages in all breach cases"
            )
        ]
    )

    result = verifier.verify_claim(claim, retrieved, check_entailment=False)
    assert result.status == "unverified"
    assert result.citations[0].verified is False
    assert "Quote could not be verified" in result.citations[0].reason


def test_llm_entailment_rejection(monkeypatch):
    retrieved = [
        RetrievedSection(
            section_id="BD-act-26-sec-10-en",
            pair_id="BD-act-26-sec-10",
            score=1.0,
            text="All agreements are contracts if they are made by the free consent of parties...",
            citation=CitationInfo(
                act_name="The Contract Act, 1872",
                act_year=1872,
                act_id="act-26",
                section_no="10",
                section_title="What agreements are contracts",
                source_url="https://bdlaws.minlaw.gov.bd/act-26/section-172.html"
            ),
            language="en",
            via="exact"
        )
    ]

    # Mock LLM entailment to return "no"
    monkeypatch.setattr(verifier, "check_entailment_llm", lambda claim, ctx: ("no", "Statute says nothing about cryptocurrencies"))

    claim = ClaimInput(
        text="Cryptocurrency contracts are completely banned under Section 10.",
        citations=[
            CitationInput(
                act="The Contract Act, 1872",
                section_no="10",
                quote="All agreements are contracts"
            )
        ]
    )

    result = verifier.verify_claim(claim, retrieved, check_entailment=True)
    assert result.status == "unverified"
    assert result.entailment == "no"
    assert any("does not entail" in r for r in result.reasons)
