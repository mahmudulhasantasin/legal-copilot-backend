"""Tests for the OCR-default PDF ingestion policy in app.services.ingestion.

Policy under test: every file goes through OCR unless the PDF's embedded text
layer is *positively verified* as clean English digital text. These tests are
hermetic - no network calls and no real OCR engines are invoked.
"""

import pytest

from app.services.ingestion import DocumentProcessor


class _FakePage:
    """Minimal stand-in for a pypdf Page."""

    def __init__(self, text: str, images=None):
        self._text = text
        self.images = images if images is not None else []

    def extract_text(self):
        return self._text


class _ExplodingPage:
    def extract_text(self):
        raise RuntimeError("corrupt page stream")

    images = []


class _FakeReader:
    def __init__(self, pages):
        self.pages = pages


CLEAN_EN = (
    "MASTER SAAS AGREEMENT\n"
    "CLAUSE 1: PAYMENT TERMS\n"
    "Client shall pay BDT 5,00,000 within 30 days of invoice."
)
GARBLED_BN = "জ7মি ক্রয়-7বিক্রয় চুǬক্তিপত্র দলিল রেফারেন্স LSA-2026-002 সহ দলিল রেজিস্ট্রেশন সম্পন্ন"


# --------------------------------------------------------------- quality gate
@pytest.mark.parametrize(
    "text,expected",
    [
        (CLEAN_EN, True),
        ("SCHEDULE B: MILESTONE PAYMENT TABLE\nMilestone 1: Architecture Sign-off | 20% | BDT 4,00,000", True),
        # Bengali script must never be trusted from pypdf (glyphs need OCR).
        ("আইনি নোটিশ প্রতি জনাব করিম আহমেদ গুলশান-২ ঢাকা", False),
        # Garbled legacy Bengali font -> Latin-1 supplement soup.
        (GARBLED_BN, False),
        # Mojibake from a broken encoding.
        ("Agr\u00e9\u00e9ment \u00c0\u00c1\u00c2\u00c3\u00c4\u00c5 \u00ff\u00fe\u00fd payment terms clause", False),
        # Spaced-out letters from a broken text layer.
        ("A g r e e m e n t c l a u s e o n e p a y m e n t t e r m s", False),
        # Sparse / scanned page.
        ("hi", False),
        ("", False),
        ("   \n  ", False),
        # Control characters.
        ("CLAUSE 1 \x00\x01\x02\x03 payment terms and conditions apply herein", False),
        # Replacement characters.
        ("CLAUSE 1 \ufffd\ufffd\ufffd payment terms and conditions apply herein", False),
    ],
)
def test_is_digital_text_trustworthy_decision_table(text, expected):
    trusted, reason = DocumentProcessor._is_digital_text_trustworthy(text)
    assert trusted is expected, f"unexpected decision for {text!r}: {reason}"
    assert isinstance(reason, str) and reason


def test_trusted_text_reason_mentions_english():
    trusted, reason = DocumentProcessor._is_digital_text_trustworthy(CLEAN_EN)
    assert trusted and "English" in reason


# ------------------------------------------------------------ document routing
def test_should_run_pdf_ocr_false_for_all_clean_english_pages():
    reader = _FakeReader([_FakePage(CLEAN_EN), _FakePage(CLEAN_EN)])
    needs_ocr, reason = DocumentProcessor._should_run_pdf_ocr(reader, CLEAN_EN)
    assert needs_ocr is False
    assert "verified" in reason


def test_should_run_pdf_ocr_true_when_any_page_is_untrusted():
    reader = _FakeReader([_FakePage(CLEAN_EN), _FakePage("")])
    needs_ocr, reason = DocumentProcessor._should_run_pdf_ocr(reader, CLEAN_EN)
    assert needs_ocr is True
    assert "p2" in reason


def test_should_run_pdf_ocr_true_for_embedded_images_on_trusted_text():
    """A page with a clean text layer but an embedded image still needs OCR."""
    reader = _FakeReader([_FakePage(CLEAN_EN, images=["img1"])])
    needs_ocr, reason = DocumentProcessor._should_run_pdf_ocr(reader, CLEAN_EN)
    assert needs_ocr is True
    assert "embedded image" in reason


def test_should_run_pdf_ocr_true_when_no_pages():
    needs_ocr, reason = DocumentProcessor._should_run_pdf_ocr(_FakeReader([]), "")
    assert needs_ocr is True
    assert "no readable pages" in reason


def test_should_run_pdf_ocr_true_when_page_extraction_raises():
    reader = _FakeReader([_ExplodingPage()])
    needs_ocr, reason = DocumentProcessor._should_run_pdf_ocr(reader, "")
    assert needs_ocr is True
    assert "failed" in reason


def test_should_run_pdf_ocr_truncates_long_reason():
    reader = _FakeReader([_FakePage("") for _ in range(6)])
    needs_ocr, reason = DocumentProcessor._should_run_pdf_ocr(reader, "")
    assert needs_ocr is True
    assert "+3 more" in reason


def test_page_has_embedded_images_is_crash_safe():
    class _Boom:
        @property
        def images(self):
            raise RuntimeError("no decoder")

    assert DocumentProcessor._page_has_embedded_images(_Boom()) is False


# -------------------------------------------------------- extract_pdf_text flow
@pytest.fixture
def processor():
    """DocumentProcessor with OCR side-effects stubbed out."""
    return DocumentProcessor()


async def test_extract_pdf_text_english_fast_path_skips_ocr(monkeypatch, processor):
    """The core requirement: clean English PDFs must NOT be OCR'd."""
    monkeypatch.setattr(
        "app.services.ingestion.PdfReader", lambda _p: _FakeReader([_FakePage(CLEAN_EN)])
    )
    ocr_calls = []

    async def _fake_ocr(_api, image_path):
        ocr_calls.append(image_path)
        return "SHOULD NOT BE USED"

    monkeypatch.setattr(processor, "_run_hybrid_ocr", _fake_ocr)

    result = await processor.extract_pdf_text("clean_en.pdf")

    assert result == CLEAN_EN.strip()
    assert ocr_calls == [], "OCR must not run for verified clean English digital text"


async def test_extract_pdf_text_scanned_page_goes_through_ocr(monkeypatch, processor):
    """Empty text layer (scanned) must route to OCR."""
    monkeypatch.setattr(
        "app.services.ingestion.PdfReader", lambda _p: _FakeReader([_FakePage("")])
    )
    monkeypatch.setattr(processor, "_render_pdf_to_images", lambda *a, **k: ["page_001.webp"])
    monkeypatch.setattr(processor, "_create_lens_api", lambda: object())

    ocr_calls = []

    async def _fake_ocr(_api, image_path):
        ocr_calls.append(image_path)
        return "COMMERCIAL LEASE AGREEMENT\nMonthly rent is BDT 1,50,000."

    monkeypatch.setattr(processor, "_run_hybrid_ocr", _fake_ocr)

    result = await processor.extract_pdf_text("scanned.pdf")

    assert ocr_calls == ["page_001.webp"]
    assert "BDT 1,50,000" in result


async def test_extract_pdf_text_mixed_document_only_ocrs_bad_pages(monkeypatch, processor):
    """Clean English pages stay direct; only the untrusted page is re-read via OCR."""
    pages = [_FakePage(CLEAN_EN), _FakePage("")]
    monkeypatch.setattr("app.services.ingestion.PdfReader", lambda _p: _FakeReader(pages))
    monkeypatch.setattr(
        processor, "_render_pdf_to_images", lambda *a, **k: ["p1.webp", "p2.webp"]
    )
    monkeypatch.setattr(processor, "_create_lens_api", lambda: object())

    ocr_calls = []

    async def _fake_ocr(_api, image_path):
        ocr_calls.append(image_path)
        return "PAGE TWO OCR TEXT RECOVERED FROM IMAGE"

    monkeypatch.setattr(processor, "_run_hybrid_ocr", _fake_ocr)

    result = await processor.extract_pdf_text("mixed.pdf")

    assert ocr_calls == ["p2.webp"], "only the untrusted page should be OCR'd"
    assert CLEAN_EN.splitlines()[0] in result
    assert "PAGE TWO OCR TEXT RECOVERED FROM IMAGE" in result


async def test_extract_pdf_text_falls_back_to_pypdf_when_ocr_fails(monkeypatch, processor):
    """If OCR fails entirely, untrusted text is still returned rather than dropped."""
    monkeypatch.setattr(
        "app.services.ingestion.PdfReader", lambda _p: _FakeReader([_FakePage(GARBLED_BN)])
    )

    def _boom(*_a, **_k):
        raise RuntimeError("Poppler missing")

    monkeypatch.setattr(processor, "_render_pdf_to_images", _boom)

    result = await processor.extract_pdf_text("garbled.pdf")

    assert result == GARBLED_BN.strip()


async def test_extract_pdf_text_returns_direct_text_when_ocr_returns_empty(monkeypatch, processor):
    """OCR yielding empty text must not erase otherwise-usable extracted text."""
    monkeypatch.setattr(
        "app.services.ingestion.PdfReader", lambda _p: _FakeReader([_FakePage(GARBLED_BN)])
    )
    monkeypatch.setattr(processor, "_render_pdf_to_images", lambda *a, **k: ["p1.webp"])
    monkeypatch.setattr(processor, "_create_lens_api", lambda: object())

    async def _empty_ocr(_api, _image_path):
        return ""

    monkeypatch.setattr(processor, "_run_hybrid_ocr", _empty_ocr)

    result = await processor.extract_pdf_text("garbled.pdf")

    assert result == GARBLED_BN.strip()


async def test_extract_pdf_text_empty_document_returns_empty(monkeypatch, processor):
    monkeypatch.setattr("app.services.ingestion.PdfReader", lambda _p: _FakeReader([]))
    assert await processor.extract_pdf_text("nothing.pdf") == ""
