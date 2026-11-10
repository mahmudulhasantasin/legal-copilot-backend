"""
Redlined DOCX Generation Service for Advotech / ContractMind.
Converts AI Contract Audit findings into a professional, enterprise-grade,
downloadable Word document (.docx) with redline markup (strikethroughs, insertions,
and legal commentary citing Bangladesh statutes).
"""

import io
import logging
from typing import List, Dict, Any, Optional
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)


def set_cell_background(cell, hex_color: str):
    """Sets background shading color for a docx table cell."""
    tcPr = cell._element.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color)
    tcPr.append(shd)


def build_redlined_docx(
    title: str,
    overall_risk: str,
    risk_score: int,
    executive_summary: str,
    clauses: List[Dict[str, Any]],
    missing_provisions: Optional[List[Dict[str, Any]]] = None,
    stamp_duty_advisory: Optional[str] = None,
    lawyer_name: Optional[str] = None,
    lawyer_notes: Optional[str] = None,
    is_lawyer_verified: bool = False,
) -> io.BytesIO:
    """
    Builds a professional, redlined Word document in memory and returns it as BytesIO.
    """
    doc = docx.Document()

    # 1. Page Setup / Margins (1 inch everywhere)
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # 2. Document Header Banner
    header_para = doc.add_paragraph()
    header_run = header_para.add_run("CONFIDENTIAL ATTORNEY-CLIENT PRIVILEGED WORK PRODUCT\nSOVEREIGN BANGLADESH STATUTORY REDLINE DRAFT")
    header_run.font.size = Pt(8.5)
    header_run.font.bold = True
    header_run.font.color.rgb = RGBColor(120, 120, 120)
    header_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    # 3. Document Title
    doc_title_para = doc.add_paragraph()
    title_run = doc_title_para.add_run(f"REDLINED: {title.upper()}")
    title_run.font.size = Pt(18)
    title_run.font.bold = True
    title_run.font.color.rgb = RGBColor(24, 43, 73)  # Navy Blue
    doc_title_para.alignment = WD_ALIGN_PARAGRAPH.LEFT

    # 4. Status & Audit Metadata Summary Box
    summary_table = doc.add_table(rows=2, cols=3)
    summary_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    summary_table.autofit = False

    # Row 0: Labels
    labels = ["STATUTORY RISK GRADE", "COMPLIANCE SCORE", "VERIFICATION STATUS"]
    for i, label in enumerate(labels):
        cell = summary_table.cell(0, i)
        set_cell_background(cell, "F1F5F9")  # Slate-100
        p = cell.paragraphs[0]
        r = p.add_run(label)
        r.font.size = Pt(8)
        r.font.bold = True
        r.font.color.rgb = RGBColor(100, 116, 139)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Row 1: Values
    risk_color = RGBColor(220, 38, 38) if overall_risk.upper() in ("HIGH", "CRITICAL") else (
        RGBColor(217, 119, 6) if overall_risk.upper() in ("MODERATE", "MEDIUM") else RGBColor(16, 149, 193)
    )
    val_cells = [
        (overall_risk.upper(), risk_color),
        (f"{risk_score} / 100", RGBColor(15, 23, 42)),
        ("LAWYER SEALED" if is_lawyer_verified else "AI STATUTORY AUDITED", RGBColor(5, 150, 105) if is_lawyer_verified else RGBColor(37, 99, 235))
    ]
    for i, (val, color) in enumerate(val_cells):
        cell = summary_table.cell(1, i)
        set_cell_background(cell, "FFFFFF")
        p = cell.paragraphs[0]
        r = p.add_run(val)
        r.font.size = Pt(11)
        r.font.bold = True
        r.font.color.rgb = color
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph()  # Spacing

    # 5. Executive Summary
    if executive_summary:
        h1 = doc.add_heading(level=1)
        h1_run = h1.add_run("1. Executive Statutory Risk Summary")
        h1_run.font.size = Pt(13)
        h1_run.font.color.rgb = RGBColor(24, 43, 73)

        exec_p = doc.add_paragraph()
        exec_p.paragraph_format.left_indent = Inches(0.2)
        exec_p.paragraph_format.right_indent = Inches(0.2)
        exec_p.paragraph_format.space_after = Pt(12)
        exec_run = exec_p.add_run(executive_summary)
        exec_run.font.size = Pt(10)
        exec_run.font.italic = True
        exec_run.font.color.rgb = RGBColor(51, 65, 85)

    # 6. Clause-by-Clause Redlines
    h2 = doc.add_heading(level=1)
    h2_run = h2.add_run("2. Contract Clauses & Statutory Redline Draft")
    h2_run.font.size = Pt(13)
    h2_run.font.color.rgb = RGBColor(24, 43, 73)

    instructions = doc.add_paragraph()
    inst_run = instructions.add_run(
        "Legend: Strikethrough Red = Unfavorable / Statutorily Non-Compliant Original Text. "
        "Underlined Green = Proposed Safe Statutory Redline under Bangladesh Jurisprudence."
    )
    inst_run.font.size = Pt(8.5)
    inst_run.font.italic = True
    inst_run.font.color.rgb = RGBColor(100, 116, 139)

    for idx, cl in enumerate(clauses, start=1):
        clause_num = str(cl.get("clause_number", idx)).replace("§", "").strip()
        clause_title = cl.get("clause_title") or cl.get("title") or f"Clause {clause_num}"
        risk_level = str(cl.get("risk_level", "LOW")).upper()
        original_text = cl.get("original_text", "").strip()
        redline = cl.get("proposed_safe_redline") or cl.get("redline_suggestion", "")
        statute = cl.get("statute_reference") or cl.get("statute_citation", "")
        guidance = cl.get("negotiation_guidance") or cl.get("legal_explanation", "")

        # Heading per clause
        cl_head = doc.add_paragraph()
        cl_head.paragraph_format.space_before = Pt(12)
        cl_head.paragraph_format.space_after = Pt(4)
        
        num_run = cl_head.add_run(f"Clause {clause_num}. {clause_title} ")
        num_run.font.bold = True
        num_run.font.size = Pt(11)
        num_run.font.color.rgb = RGBColor(15, 23, 42)

        # Risk Tag
        tag_run = cl_head.add_run(f"[{risk_level}]")
        tag_run.font.bold = True
        tag_run.font.size = Pt(9)
        if risk_level in ("HIGH", "CRITICAL"):
            tag_run.font.color.rgb = RGBColor(220, 38, 38)
        elif risk_level in ("MODERATE", "MEDIUM", "REVIEW_NEEDED"):
            tag_run.font.color.rgb = RGBColor(217, 119, 6)
        else:
            tag_run.font.color.rgb = RGBColor(16, 149, 193)

        # Body paragraph containing the actual Redline markup
        body_p = doc.add_paragraph()
        body_p.paragraph_format.left_indent = Inches(0.2)
        body_p.paragraph_format.space_after = Pt(4)

        if redline and original_text:
            # 1) Strikethrough Original Text (Red)
            strike_run = body_p.add_run(original_text + " ")
            strike_run.font.strike = True
            strike_run.font.color.rgb = RGBColor(220, 38, 38)
            strike_run.font.size = Pt(10)

            # 2) Inserted Statutory Compliant Text (Green Underline)
            insert_run = body_p.add_run(redline.strip())
            insert_run.font.underline = True
            insert_run.font.bold = True
            insert_run.font.color.rgb = RGBColor(16, 149, 100)
            insert_run.font.size = Pt(10)

        elif original_text:
            # Compliant / Unchanged text
            normal_run = body_p.add_run(original_text)
            normal_run.font.size = Pt(10)
            normal_run.font.color.rgb = RGBColor(51, 65, 85)

        # Statutory Rationale Box (Callout)
        if statute or guidance:
            callout = doc.add_paragraph()
            callout.paragraph_format.left_indent = Inches(0.4)
            callout.paragraph_format.space_after = Pt(8)
            
            c_tag = callout.add_run("▶ Statutory & Negotiation Rationale: ")
            c_tag.font.bold = True
            c_tag.font.size = Pt(8.5)
            c_tag.font.color.rgb = RGBColor(71, 85, 105)

            c_text_parts = []
            if statute:
                c_text_parts.append(f"Statute: {statute}")
            if guidance:
                c_text_parts.append(guidance)

            c_body = callout.add_run(" | ".join(c_text_parts))
            c_body.font.size = Pt(8.5)
            c_body.font.italic = True
            c_body.font.color.rgb = RGBColor(100, 116, 139)

    # 7. Missing Provisions Gap Analysis (if any)
    if missing_provisions:
        doc.add_page_break()
        h3 = doc.add_heading(level=1)
        h3_run = h3.add_run("3. Missing Essential Protective Provisions (Omissions)")
        h3_run.font.size = Pt(13)
        h3_run.font.color.rgb = RGBColor(24, 43, 73)

        warn_p = doc.add_paragraph()
        warn_r = warn_p.add_run(
            "Note: Under Bangladesh law, terms not articulated in writing cannot be enforced later. "
            "The following mandatory safeguards must be inserted before signing:"
        )
        warn_r.font.size = Pt(9.5)
        warn_r.font.italic = True

        for m in missing_provisions:
            mp = doc.add_paragraph(style='List Bullet')
            mp.paragraph_format.space_after = Pt(4)
            m_title = mp.add_run(f"{m.get('title')}: ")
            m_title.font.bold = True
            m_title.font.size = Pt(9.5)

            m_desc = mp.add_run(f"{m.get('risk_explanation')} (Reference: {m.get('statutory_reference')})")
            m_desc.font.size = Pt(9.5)
            m_desc.font.color.rgb = RGBColor(71, 85, 105)

    # 8. Stamp Duty & Formal Execution Formalities
    if stamp_duty_advisory:
        h4 = doc.add_heading(level=1)
        h4_run = h4.add_run("4. Bangladesh Stamp Duty & Execution Formalities")
        h4_run.font.size = Pt(13)
        h4_run.font.color.rgb = RGBColor(24, 43, 73)

        sd_p = doc.add_paragraph()
        sd_p.paragraph_format.left_indent = Inches(0.2)
        sd_run = sd_p.add_run(stamp_duty_advisory)
        sd_run.font.size = Pt(9.5)
        sd_run.font.color.rgb = RGBColor(51, 65, 85)

    # 9. Lawyer Sign-off & Digital Bar Council Seal
    h5 = doc.add_heading(level=1)
    h5_run = h5.add_run("5. Advocate Statutory Verification & Signature Block")
    h5_run.font.size = Pt(13)
    h5_run.font.color.rgb = RGBColor(24, 43, 73)

    seal_p = doc.add_paragraph()
    seal_p.paragraph_format.left_indent = Inches(0.2)
    if is_lawyer_verified and lawyer_name:
        s_run = seal_p.add_run(
            f"Vetted and approved by Advocate {lawyer_name}.\n"
            f"Notes: {lawyer_notes or 'All proposed redlines align with codified statutory provisions of Bangladesh.'}\n"
            f"Status: DIGITALLY SEALED & VERIFIED"
        )
        s_run.font.size = Pt(9.5)
        s_run.font.bold = True
        s_run.font.color.rgb = RGBColor(5, 150, 105)
    else:
        s_run = seal_p.add_run(
            "Draft generated autonomously via Advotech Copilot Statutory Engine.\n"
            "Pending formal review and digital seal from an Advocate of the Supreme Court of Bangladesh."
        )
        s_run.font.size = Pt(9)
        s_run.font.italic = True
        s_run.font.color.rgb = RGBColor(100, 116, 139)

    # Output to in-memory bytes
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer
