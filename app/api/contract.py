import json
from io import BytesIO
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Response
from pypdf import PdfReader
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

# ReportLab Imports for Legal PDF Formatting
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User, SubscriptionTier, UserRole
from app.models.memo import ContractMemo
from app.agents.contract_graph import analyze_contract_text as analyze_contract_risk

router = APIRouter(prefix="/api/contract", tags=["Contract Analysis"])


# --- PDF Generator Function ---
def build_legal_memo_pdf(memo: ContractMemo, lawyer_name: str = "Unassigned") -> bytes:
    """মেমোর ডেটা প্রসেস করে প্রফেশনাল লিগ্যাল অডিট PDF রিটার্ন করে।"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=6
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor("#4A5568"),
        spaceAfter=15
    )
    h2_style = ParagraphStyle(
        'H2Style',
        parent=styles['Heading2'],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=12,
        spaceAfter=6
    )
    body_style = ParagraphStyle(
        'BodyDark',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#2D3748")
    )
    risk_title_style = ParagraphStyle(
        'RiskTitle',
        parent=styles['Normal'],
        fontSize=10,
        leading=13,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#9B2C2C")
    )

    story = []

    # হেডার ও মেটাডাটা
    story.append(Paragraph("CONTRACTMIND LEGAL AUDIT MEMORANDUM", title_style))
    story.append(Paragraph(f"Generated on {datetime.utcnow().strftime('%B %d, %Y')} | Bangladesh Statutory Jurisdiction", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2B6CB0"), spaceAfter=15))

    # সামারি টেবিল
    meta_data = [
        [Paragraph("<b>Contract Document:</b>", body_style), Paragraph(memo.filename, body_style)],
        [Paragraph("<b>Audit Status:</b>", body_style), Paragraph(memo.status, body_style)],
        [Paragraph("<b>Overall Statutory Risk:</b>", body_style), Paragraph(f"<b>{memo.overall_risk_score}</b>", body_style)],
        [Paragraph("<b>Reviewing Advocate:</b>", body_style), Paragraph(lawyer_name, body_style)],
    ]
    meta_table = Table(meta_data, colWidths=[150, 380])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F7FAFC")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E0")),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 15))

    # নির্বাহী সারাংশ
    story.append(Paragraph("1. Executive Summary", h2_style))
    story.append(Paragraph(memo.memo_summary or "No summary available.", body_style))
    story.append(Spacer(1, 12))

    # চিহ্নিত আইনি ঝুঁকি ও ধারা
    story.append(Paragraph("2. Detected Statutory Risks & Unfair Terms", h2_style))
    risks = json.loads(memo.risks_json) if memo.risks_json else []

    if risks:
        for idx, risk in enumerate(risks, start=1):
            clause_header = f"{idx}. {risk.get('clause_title', 'Clause')} — [Severity: {risk.get('severity', 'MODERATE')}]"
            story.append(Paragraph(clause_header, risk_title_style))
            story.append(Paragraph(f"<b>Flagged Text:</b> <i>\"{risk.get('flagged_text', '')}\"</i>", body_style))
            story.append(Paragraph(f"<b>Violated Provision:</b> {risk.get('violated_law', 'Statutory Provision')}", body_style))
            story.append(Paragraph(f"<b>Legal Analysis:</b> {risk.get('explanation', '')}", body_style))
            story.append(Paragraph(f"<b>Recommendation:</b> {risk.get('recommendation', '')}", body_style))
            story.append(Spacer(1, 8))
    else:
        story.append(Paragraph("No critical statutory risks detected.", body_style))

    story.append(Spacer(1, 10))

    # আইনজীবীর পর্যালোচনা ও মতামত
    story.append(Paragraph("3. Legal Counsel Review & Redlining Notes", h2_style))
    story.append(Paragraph(memo.lawyer_notes or "Pending final manual advocate observations.", body_style))
    story.append(Spacer(1, 15))

    # ডিজিটাল স্বাক্ষর বক্স
    story.append(Paragraph("4. Digital Authentication & Execution", h2_style))
    sig_text = memo.digital_signature or "NOT YET SIGNED"
    reviewed_at_str = memo.reviewed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if memo.reviewed_at else "N/A"

    sig_data = [
        [Paragraph("<b>Digital Signature:</b>", body_style), Paragraph(sig_text, body_style)],
        [Paragraph("<b>Authentication Time:</b>", body_style), Paragraph(reviewed_at_str, body_style)]
    ]
    sig_table = Table(sig_data, colWidths=[150, 380])
    sig_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#EDF2F7")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(sig_table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# --- Endpoint ---
@router.get("/generate-doc/{memo_id}")
async def generate_memo_document(
    memo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """মেমোর প্রফেশনাল লিগ্যাল PDF ডাউনলোড করার এন্ডপয়েন্ট।"""
    memo = db.query(ContractMemo).filter(ContractMemo.id == memo_id).first()
    if not memo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contract memo not found."
        )

    # এক্সেস কন্ট্রোল: শুধু মেমোর মালিক, অ্যাডমিন অথবা রিভিউয়ার আইনজীবী ডাউনলোড করতে পারবে
    if memo.user_id != current_user.id and current_user.role not in [UserRole.ADMIN, UserRole.LAWYER]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to download this legal document."
        )

    # আইনজীবী এসাইন করা থাকলে তার নাম ফেচ করা
    lawyer_name = "Unassigned / AI Only"
    if memo.lawyer_id:
        lawyer = db.query(User).filter(User.id == memo.lawyer_id).first()
        if lawyer:
            lawyer_name = f"{lawyer.full_name} ({lawyer.bar_council_id or 'Advocate'})"

    # PDF তৈরি
    pdf_bytes = await run_in_threadpool(build_legal_memo_pdf, memo, lawyer_name)

    filename = f"ContractMind_Memo_{memo.id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )