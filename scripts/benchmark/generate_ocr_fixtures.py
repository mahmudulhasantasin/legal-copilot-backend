"""Generates OCR benchmark fixture files from ocr_cases.json ground truth.

Single source of truth: the `ground_truth_text` field of each case in
scripts/benchmark/datasets/ocr_cases.json. Existing fixtures are never
overwritten unless --force is given.

Fixture rendering per doc_type (all via Microsoft Edge headless so Bengali
gets proper OpenType shaping through Nirmala UI; PIL/reportlab have no raqm
shaping on Windows and break conjuncts):
  digital_pdf  -> Edge print-to-pdf (real text layer, shaped glyphs)
  docx         -> python-docx paragraphs (extraction reads XML text directly)
  scanned_pdf  -> Edge screenshots wrapped in an image-only PDF (no text layer,
                  forces the hybrid OCR path in app.services.ingestion)
  scanned_image-> Edge screenshot saved as JPG/PNG
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import pathlib
import subprocess
import tempfile

logger = logging.getLogger("fixture_generator")

DATASETS_DIR = pathlib.Path(__file__).resolve().parent / "datasets"
OCR_CASES_FILE = DATASETS_DIR / "ocr_cases.json"

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

# doc_types this script can reproduce from ground truth. Anything else
# (e.g. 'garbled_font_bengali_pdf') is a hand-crafted fixture and is
# never generated or overwritten, even with --force.
GENERATABLE_DOC_TYPES = {
    "digital_pdf",
    "docx",
    "scanned_pdf",
    "scanned_pdf_long",
    "scanned_image",
}

# Extra clause pages for OCR-SCAN-010 (a 10-page MSA). Ground-truth lines are
# interleaved verbatim; filler headings let extract_clauses_from_text reach the
# expected_min_clauses threshold while CER stays anchored to GT lines only.
LONG_MSA_PLAN = [
    ("title", 0),   # MASTER SERVICES AGREEMENT
    ("clause", 1),  # CLAUSE 1: DEFINITIONS
    ("filler", "The terms defined in this Clause apply throughout this Agreement."),
    ("filler_clause", "CLAUSE 2: TERM", "The initial term is two (2) years from the Effective Date."),
    ("filler_clause", "CLAUSE 3: PAYMENT", "Invoices are payable within thirty (30) days of receipt."),
    ("clause", 2),  # CLAUSE 7: LIMITATION OF LIABILITY AND NON-COMPETE
    ("filler", 3),  # Vendor shall not compete for 5 years and late penalty is 5% per month.
    ("filler_clause", "CLAUSE 8: INDEMNITY", "Each party shall indemnify the other against third-party claims."),
    ("filler_clause", "CLAUSE 9: DISPUTE RESOLUTION", "Disputes shall be referred to arbitration under the Arbitration Act, 2001."),
    ("filler_clause", "CLAUSE 10: GOVERNING LAW", "This Agreement is governed by the laws of Bangladesh."),
]

# Full document bodies for cases whose expected_min_clauses exceed the ground
# truth line count. GT lines are embedded verbatim (CER anchors only on GT
# lines); filler clauses let the AST parser reach the required clause count,
# mirroring the original hand-made fixtures.
FULL_DOC_LINES: dict[str, list[str]] = {
    "OCR-PDF-012": [
        "SOFTWARE DEVELOPMENT & TECHNOLOGY SERVICE AGREEMENT",
        "Agreement Reference: SDA-2026-011",
        "1. PARTIES TO THE AGREEMENT",
        "Party A – Client",
        "Company Name: Nexora Digital Solutions Ltd.",
        "Party B – Service Provider",
        "Company Name: ByteCraft Technologies Ltd.",
        "2. SCOPE OF SERVICES",
        "The Service Provider shall design, develop, and deliver the software modules described in Schedule A.",
        "3. CONTRACT VALUE AND PAYMENT TERMS",
        "The total contract value shall be BDT 12,00,000 (Twelve Lakh Bangladeshi Taka).",
        "In case of delayed payment, the Client shall pay a late payment charge of 5% per day on the outstanding amount.",
        "4. DELIVERY SCHEDULE",
        "Milestone deliverables shall be accepted within ten (10) business days of submission.",
        "5. INTELLECTUAL PROPERTY RIGHTS",
        "All custom-developed source code shall vest in the Client upon full payment.",
        "6. CONFIDENTIALITY",
        "Each party shall keep confidential all technical and commercial information of the other party.",
        "7. WARRANTIES AND REPRESENTATIONS",
        "The Service Provider warrants that deliverables shall materially conform to the agreed specification.",
        "8. TERM AND TERMINATION",
        "This Agreement remains effective for twenty-four (24) months from the Effective Date.",
        "9. LIMITATION OF LIABILITY",
        "The Service Provider's total liability under this Agreement shall not exceed BDT 50,000 under any circumstances.",
        "10. INDEMNIFICATION",
        "The Service Provider shall indemnify the Client against third-party intellectual property infringement claims.",
        "11. DATA PROTECTION",
        "Personal data shall be processed in compliance with applicable Bangladesh law.",
        "12. FORCE MAJEURE",
        "Neither party is liable for delay caused by events beyond its reasonable control.",
        "13. DISPUTE RESOLUTION",
        "Disputes shall be resolved through arbitration in Dhaka under the Arbitration Act, 2001.",
        "14. GOVERNING LAW",
        "This Agreement is governed by the laws of Bangladesh.",
        "15. AMENDMENTS",
        "Any amendment must be made in writing and signed by both parties.",
        "16. ENTIRE AGREEMENT",
        "This document supersedes all prior negotiations between the parties.",
    ],
    "OCR-PDF-013": [
        "NEXORA GROUP",
        "CORPORATE CONTRACT GOVERNANCE PLAYBOOK",
        "Document ID: NEX-CP-001",
        "1. Purpose and Application",
        "This playbook defines the company's preferred contract positions for all procurement engagements.",
        "2. Definitions",
        "Capitalized terms follow the meanings assigned in the master procurement policy.",
        "3. Payment Terms",
        "Rule CP-003 — Payment Terms",
        "The company's preferred payment structure is:",
        "30% advance.",
        "40% upon milestone completion.",
        "30% after final acceptance.",
        "4. Approval Thresholds",
        "Contracts above BDT 50,00,000 require board-level approval.",
        "5. Liability Caps",
        "Rule CP-006",
        "The company's preferred aggregate liability cap is the total contract value.",
        "6. Confidentiality",
        "Rule CP-007",
        "The preferred confidentiality period is three years after termination, subject to longer protection where appropriate.",
        "7. Warranty Periods",
        "Rule CP-009",
        "The preferred software defect correction period is 60 days after final acceptance.",
        "8. Termination Rights",
        "The company requires termination for convenience with thirty (30) days written notice.",
        "9. Indemnification",
        "Preferred indemnity covers third-party claims arising from negligent performance.",
        "10. Insurance Requirements",
        "Vendors shall maintain comprehensive general liability insurance.",
        "11. Subcontracting",
        "Subcontracting requires prior written consent from the company.",
        "12. Governing Law and Jurisdiction",
        "All agreements are governed by the laws of Bangladesh.",
        "13. Dispute Resolution Preference",
        "The preferred resolution mechanism is arbitration seated in Dhaka.",
        "14. Anti-Corruption Compliance",
        "Vendors shall comply with all applicable anti-bribery laws.",
        "15. Audit Rights",
        "The company may audit vendor records once per contract year.",
        "16. Dual-Source Legal Audit Requirement",
        "Source A — Company Playbook",
        "Source B — Bangladesh Law Database (BDLaw)",
    ],
}


def _gt_lines(case: dict) -> list[str]:
    return [ln for ln in case.get("ground_truth_text", "").splitlines() if ln.strip()]


def _find_edge() -> str:
    for candidate in EDGE_CANDIDATES:
        if pathlib.Path(candidate).exists():
            return candidate
    raise RuntimeError("Microsoft Edge not found; required for shaped fixture rendering.")


def _page_html(lines: list[str], lang: str) -> str:
    """Contract-styled page: headings bold, generous line height for OCR legibility."""
    if "bn" in str(lang):
        font_family = "'Nirmala UI', 'Nirmala'"
    else:
        font_family = "Arial, 'Nirmala UI'"
    body_rows = []
    for ln in lines:
        is_heading = ln.isupper() or ln.upper().startswith("CLAUSE")
        body_rows.append(
            f'<p class="{"heading" if is_heading else "body"}">{html.escape(ln)}</p>'
        )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body {{ margin: 0; padding: 56px 64px; background: #fcfcfa;
         font-family: {font_family}; }}
  p {{ margin: 0 0 26px 0; white-space: pre-wrap; }}
  .heading {{ font-weight: 700; font-size: 34px; color: #141414; }}
  .body {{ font-size: 30px; color: #1e1e1e; line-height: 1.6; }}
</style></head><body>{''.join(body_rows)}</body></html>"""


def _edge_html_path(lines: list[str], lang: str) -> str:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    tmp.write(_page_html(lines, lang))
    tmp.close()
    return tmp.name


def _edge_run(args: list[str]) -> None:
    subprocess.run(
        [_find_edge(), "--headless=new", "--disable-gpu", "--no-first-run", *args],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _crop_whitespace(png_path: pathlib.Path, pad: int = 40) -> "PIL.Image.Image":
    from PIL import Image, ImageChops

    img = Image.open(png_path).convert("RGB")
    bg = Image.new("RGB", img.size, (252, 252, 250))
    diff = ImageChops.difference(img, bg).convert("L")
    if bbox := diff.getbbox():
        left = max(0, bbox[0] - pad)
        top = max(0, bbox[1] - pad)
        right = min(img.width, bbox[2] + pad)
        bottom = min(img.height, bbox[3] + pad)
        img = img.crop((left, top, right, bottom))
    return img


# ---------------------------------------------------------------- digital PDF
def make_digital_pdf(path: pathlib.Path, lines: list[str], lang: str) -> None:
    html_path = _edge_html_path(lines, lang)
    try:
        _edge_run([f"--print-to-pdf={path}", "--no-pdf-header-footer", pathlib.Path(html_path).as_uri()])
    finally:
        pathlib.Path(html_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------- DOCX
def make_docx(path: pathlib.Path, lines: list[str]) -> None:
    import docx

    doc = docx.Document()
    for ln in lines:
        doc.add_paragraph(ln)
    doc.save(str(path))


# --------------------------------------------------------------- scanned PDF
def make_scanned_pdf(path: pathlib.Path, pages: list[list[str]], lang: str) -> None:
    from PIL import Image

    images: list[Image.Image] = []
    try:
        for page_lines in pages:
            html_path = _edge_html_path(page_lines, lang)
            png_path = pathlib.Path(html_path).with_suffix(".png")
            try:
                _edge_run(
                    [
                        f"--screenshot={png_path}",
                        "--window-size=1700,2400",
                        "--force-device-scale-factor=1.5",
                        "--hide-scrollbars",
                        pathlib.Path(html_path).as_uri(),
                    ]
                )
                images.append(_crop_whitespace(png_path))
            finally:
                pathlib.Path(html_path).unlink(missing_ok=True)
                png_path.unlink(missing_ok=True)
    except Exception:
        for img in images:
            img.close()
        raise

    images[0].save(
        str(path),
        format="PDF",
        save_all=True,
        append_images=images[1:],
        resolution=150.0,
    )
    for img in images:
        img.close()


# ------------------------------------------------------------- scanned image
def make_scanned_image(path: pathlib.Path, lines: list[str], lang: str) -> None:
    from PIL import Image

    html_path = _edge_html_path(lines, lang)
    png_path = pathlib.Path(html_path).with_suffix(".png")
    try:
        _edge_run(
            [
                f"--screenshot={png_path}",
                "--window-size=1700,2400",
                "--force-device-scale-factor=1.5",
                "--hide-scrollbars",
                pathlib.Path(html_path).as_uri(),
            ]
        )
        img = _crop_whitespace(png_path)
        if path.suffix.lower() == ".jpg":
            img.save(str(path), format="JPEG", quality=92)
        else:
            img.save(str(path), format="PNG")
        img.close()
    finally:
        pathlib.Path(html_path).unlink(missing_ok=True)
        png_path.unlink(missing_ok=True)


# ------------------------------------------------------- long-MSA page plan
def _pages_for_case(case: dict) -> list[list[str]]:
    gt = _gt_lines(case)
    if case.get("id") != "OCR-SCAN-010":
        return [gt]

    pages: list[list[str]] = []
    for entry in LONG_MSA_PLAN:
        kind, idx = entry[0], entry[1]
        if kind in ("title", "clause"):
            pages.append([gt[idx]])
        elif kind == "filler":
            # filler second element is either a GT line index or literal text
            pages.append([idx if isinstance(idx, str) else gt[idx]])
        else:  # filler_clause
            pages.append([entry[1], entry[2]])
    assert len(pages) == 10, f"long MSA plan must yield 10 pages, got {len(pages)}"
    return pages


def generate(force: bool = False) -> None:
    cases = json.loads(OCR_CASES_FILE.read_text(encoding="utf-8"))["cases"]
    created, skipped, handmade = [], [], []
    for case in cases:
        fixture_rel = case.get("fixture_path", "")
        if not fixture_rel:
            continue
        doc_type = case.get("doc_type", "")
        if doc_type not in GENERATABLE_DOC_TYPES:
            handmade.append(case["id"])
            continue

        fixture = DATASETS_DIR / fixture_rel
        if fixture.exists() and not force:
            skipped.append(case["id"])
            continue

        fixture.parent.mkdir(parents=True, exist_ok=True)
        lines = FULL_DOC_LINES.get(case["id"]) or _gt_lines(case)
        lang = case.get("language", "en")

        if doc_type == "digital_pdf":
            make_digital_pdf(fixture, lines, lang)
        elif doc_type == "docx":
            make_docx(fixture, lines)
        elif doc_type in ("scanned_pdf", "scanned_pdf_long"):
            make_scanned_pdf(fixture, _pages_for_case(case), lang)
        elif doc_type == "scanned_image":
            make_scanned_image(fixture, lines, lang)
        created.append(f"{case['id']} -> {fixture_rel} ({fixture.stat().st_size} bytes)")

    for row in created:
        logger.info(f"CREATED {row}")
    if skipped:
        logger.info(f"Skipped (already exist): {', '.join(skipped)}")
    if handmade:
        logger.info(f"Hand-crafted fixtures kept untouched: {', '.join(handmade)}")
    print(f"Done. created={len(created)} skipped={len(skipped)} handmade={len(handmade)}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Generate OCR benchmark fixtures.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if the fixture exists")
    generate(force=parser.parse_args().force)


if __name__ == "__main__":
    main()
