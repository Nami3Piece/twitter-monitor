"""
contract_gen.py — eCandle sales contract generator (PDF + Word, CN/EN).
Usage:
    result = generate_contract(params)
    # result = {"cn_pdf": "/tmp/...", "en_pdf": "/tmp/...", "cn_docx": "/tmp/...", "en_docx": "/tmp/..."}
"""

import os
import tempfile
from datetime import datetime
from typing import Dict, Optional


# ── helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _fmt_num(n) -> str:
    try:
        return f"{float(n):,.2f}"
    except Exception:
        return str(n)


# ── contract data builder ─────────────────────────────────────────────────────

def _build_data(params: Dict) -> Dict:
    buyer_name    = params.get("buyer_name", "")
    buyer_address = params.get("buyer_address", "")
    buyer_contact = params.get("buyer_contact", "")
    qty_red       = int(params.get("qty_red", 1))
    qty_green     = int(params.get("qty_green", 1))
    unit_price    = float(params.get("unit_price", 299))
    shipping_per  = float(params.get("shipping_per_unit", 50))
    qty_total     = qty_red + qty_green
    goods_total   = qty_total * unit_price
    shipping_total = qty_total * shipping_per
    grand_total   = goods_total + shipping_total

    return {
        "buyer_name":    buyer_name,
        "buyer_address": buyer_address,
        "buyer_contact": buyer_contact,
        "qty_red":       qty_red,
        "qty_green":     qty_green,
        "qty_total":     qty_total,
        "unit_price":    unit_price,
        "shipping_per":  shipping_per,
        "goods_total":   goods_total,
        "shipping_total": shipping_total,
        "grand_total":   grand_total,
        "date":          _today(),
        "seller_name":   "Arkreen Network Ltd.",
        "seller_address": "Suite 1, 2nd Floor, The Sotheby Building, Rodney Bay, Gros-Islet, Saint Lucia",
        "seller_contact": "nami3piece@gmail.com",
        "product_name_cn": "eCandle 智能绿色蜡烛",
        "product_name_en": "eCandle Smart Green Candle",
    }


# ── PDF generator ─────────────────────────────────────────────────────────────

def _gen_pdf_cn(d: Dict, path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # Try to register a CJK font; fall back to Helvetica if unavailable
    _cn_font = "Helvetica"
    for font_path in [
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont("CJK", font_path))
                _cn_font = "CJK"
            except Exception:
                pass
            break

    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", fontName=_cn_font, fontSize=16,
                                 spaceAfter=6, alignment=1, leading=22)
    h2_style    = ParagraphStyle("h2", fontName=_cn_font, fontSize=12,
                                 spaceAfter=4, spaceBefore=12, leading=18)
    body_style  = ParagraphStyle("body", fontName=_cn_font, fontSize=10,
                                 spaceAfter=4, leading=16)

    story = []
    story.append(Paragraph("eCandle 产品销售合同", title_style))
    story.append(Paragraph(f"合同编号：ECA-{d['date'].replace('-','')}-001", body_style))
    story.append(Paragraph(f"签订日期：{d['date']}", body_style))
    story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("一、甲乙双方信息", h2_style))
    story.append(Paragraph(f"<b>甲方（卖方）：</b>{d['seller_name']}", body_style))
    story.append(Paragraph(f"<b>地址：</b>{d['seller_address']}", body_style))
    story.append(Paragraph(f"<b>联系方式：</b>{d['seller_contact']}", body_style))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(f"<b>乙方（买方）：</b>{d['buyer_name']}", body_style))
    story.append(Paragraph(f"<b>地址：</b>{d['buyer_address']}", body_style))
    story.append(Paragraph(f"<b>联系方式：</b>{d['buyer_contact']}", body_style))

    story.append(Paragraph("二、产品信息", h2_style))
    tdata = [
        ["产品名称", "规格/颜色", "数量", "单价（USD）", "小计（USD）"],
        [d["product_name_cn"], "红色款", str(d["qty_red"]),
         _fmt_num(d["unit_price"]), _fmt_num(d["qty_red"] * d["unit_price"])],
        [d["product_name_cn"], "绿色款", str(d["qty_green"]),
         _fmt_num(d["unit_price"]), _fmt_num(d["qty_green"] * d["unit_price"])],
        ["运费", f"每件 USD {_fmt_num(d['shipping_per'])}", str(d["qty_total"]),
         _fmt_num(d["shipping_per"]), _fmt_num(d["shipping_total"])],
        ["", "", "", "合计", _fmt_num(d["grand_total"])],
    ]
    t = Table(tdata, colWidths=[4.5*cm, 3*cm, 2*cm, 3*cm, 3*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,-1), _cn_font),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ("ALIGN",      (2,0), (-1,-1), "CENTER"),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#f0fdf4")),
        ("FONTNAME",   (0,-1), (-1,-1), _cn_font),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    story.append(t)

    story.append(Paragraph("三、付款方式", h2_style))
    story.append(Paragraph("乙方应在合同签订后 <b>7 个工作日内</b>完成付款，支持 USDT（Polygon 网络）或银行电汇。", body_style))

    story.append(Paragraph("四、交货条款", h2_style))
    story.append(Paragraph("甲方在收到全额货款后 <b>15 个工作日内</b>安排发货，运输方式为国际快递（DHL/FedEx）。", body_style))

    story.append(Paragraph("五、质量保证", h2_style))
    story.append(Paragraph("产品自交货之日起享有 <b>12 个月</b>质量保修，因产品质量问题导致的损失由甲方承担。", body_style))

    story.append(Paragraph("六、违约责任", h2_style))
    story.append(Paragraph("任何一方违约，应向守约方支付合同总金额 <b>10%</b> 的违约金，并赔偿实际损失。", body_style))

    story.append(Paragraph("七、争议解决", h2_style))
    story.append(Paragraph("本合同适用中华人民共和国法律，争议提交合同签订地仲裁委员会仲裁解决。", body_style))

    story.append(Spacer(1, 1*cm))
    story.append(Paragraph("八、签署", h2_style))
    sig_data = [
        ["甲方（卖方）签字/盖章", "乙方（买方）签字/盖章"],
        ["\n\n\n", "\n\n\n"],
        [f"日期：{d['date']}", "日期：___________"],
    ]
    sig_t = Table(sig_data, colWidths=[8*cm, 8*cm])
    sig_t.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), _cn_font),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("BOX",      (0,0), (0,-1), 0.5, colors.HexColor("#334155")),
        ("BOX",      (1,0), (1,-1), 0.5, colors.HexColor("#334155")),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(sig_t)

    doc.build(story)


def _gen_pdf_en(d: Dict, path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors

    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    title_style = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=16,
                                 spaceAfter=6, alignment=1, leading=22)
    h2_style    = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12,
                                 spaceAfter=4, spaceBefore=12, leading=18)
    body_style  = ParagraphStyle("body", fontName="Helvetica", fontSize=10,
                                 spaceAfter=4, leading=16)

    story = []
    story.append(Paragraph("SALES CONTRACT — eCandle Product", title_style))
    story.append(Paragraph(f"Contract No.: ECA-{d['date'].replace('-','')}-001", body_style))
    story.append(Paragraph(f"Date: {d['date']}", body_style))
    story.append(Spacer(1, 0.4*cm))

    story.append(Paragraph("1. Parties", h2_style))
    story.append(Paragraph(f"<b>Seller:</b> {d['seller_name']}", body_style))
    story.append(Paragraph(f"<b>Address:</b> {d['seller_address']}", body_style))
    story.append(Paragraph(f"<b>Contact:</b> {d['seller_contact']}", body_style))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(f"<b>Buyer:</b> {d['buyer_name']}", body_style))
    story.append(Paragraph(f"<b>Address:</b> {d['buyer_address']}", body_style))
    story.append(Paragraph(f"<b>Contact:</b> {d['buyer_contact']}", body_style))

    story.append(Paragraph("2. Products", h2_style))
    tdata = [
        ["Product", "Variant", "Qty", "Unit Price (USD)", "Subtotal (USD)"],
        [d["product_name_en"], "Red", str(d["qty_red"]),
         _fmt_num(d["unit_price"]), _fmt_num(d["qty_red"] * d["unit_price"])],
        [d["product_name_en"], "Green", str(d["qty_green"]),
         _fmt_num(d["unit_price"]), _fmt_num(d["qty_green"] * d["unit_price"])],
        ["Shipping", f"USD {_fmt_num(d['shipping_per'])} / unit", str(d["qty_total"]),
         _fmt_num(d["shipping_per"]), _fmt_num(d["shipping_total"])],
        ["", "", "", "TOTAL", _fmt_num(d["grand_total"])],
    ]
    t = Table(tdata, colWidths=[4.5*cm, 2.5*cm, 2*cm, 3.5*cm, 3*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
        ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTNAME",   (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",   (0,0), (-1,-1), 9),
        ("GRID",       (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ("ALIGN",      (2,0), (-1,-1), "CENTER"),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#f0fdf4")),
        ("FONTNAME",   (3,-1), (-1,-1), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    story.append(t)

    story.append(Paragraph("3. Payment", h2_style))
    story.append(Paragraph("Buyer shall complete payment within <b>7 business days</b> of contract signing. Accepted methods: USDT (Polygon network) or bank wire transfer.", body_style))

    story.append(Paragraph("4. Delivery", h2_style))
    story.append(Paragraph("Seller shall arrange shipment within <b>15 business days</b> after receipt of full payment via international courier (DHL/FedEx).", body_style))

    story.append(Paragraph("5. Warranty", h2_style))
    story.append(Paragraph("Products carry a <b>12-month</b> quality warranty from the date of delivery. Defects attributable to the Seller will be remedied at Seller's expense.", body_style))

    story.append(Paragraph("6. Liability", h2_style))
    story.append(Paragraph("The breaching party shall pay the non-breaching party a penalty equal to <b>10%</b> of the total contract value, plus actual damages.", body_style))

    story.append(Paragraph("7. Dispute Resolution", h2_style))
    story.append(Paragraph("This contract is governed by the laws of Saint Lucia. Disputes shall be submitted to arbitration at the agreed arbitration body.", body_style))

    story.append(Spacer(1, 1*cm))
    story.append(Paragraph("8. Signatures", h2_style))
    sig_data = [
        ["Seller Signature / Stamp", "Buyer Signature / Stamp"],
        ["\n\n\n", "\n\n\n"],
        [f"Date: {d['date']}", "Date: ___________"],
    ]
    sig_t = Table(sig_data, colWidths=[8*cm, 8*cm])
    sig_t.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("BOX",      (0,0), (0,-1), 0.5, colors.HexColor("#334155")),
        ("BOX",      (1,0), (1,-1), 0.5, colors.HexColor("#334155")),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(sig_t)

    doc.build(story)


# ── Word generator ────────────────────────────────────────────────────────────

def _gen_docx_cn(d: Dict, path: str) -> None:
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    # Page margins
    for section in doc.sections:
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)
        section.top_margin    = Cm(2.5)
        section.bottom_margin = Cm(2.5)

    title = doc.add_heading("eCandle 产品销售合同", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"合同编号：ECA-{d['date'].replace('-','')}-001")
    doc.add_paragraph(f"签订日期：{d['date']}")

    doc.add_heading("一、甲乙双方信息", 1)
    doc.add_paragraph(f"甲方（卖方）：{d['seller_name']}")
    doc.add_paragraph(f"地址：{d['seller_address']}")
    doc.add_paragraph(f"联系方式：{d['seller_contact']}")
    doc.add_paragraph("")
    doc.add_paragraph(f"乙方（买方）：{d['buyer_name']}")
    doc.add_paragraph(f"地址：{d['buyer_address']}")
    doc.add_paragraph(f"联系方式：{d['buyer_contact']}")

    doc.add_heading("二、产品信息", 1)
    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, h in enumerate(["产品名称", "规格/颜色", "数量", "单价（USD）", "小计（USD）"]):
        hdr[i].text = h
    rows_data = [
        [d["product_name_cn"], "红色款", str(d["qty_red"]),
         _fmt_num(d["unit_price"]), _fmt_num(d["qty_red"] * d["unit_price"])],
        [d["product_name_cn"], "绿色款", str(d["qty_green"]),
         _fmt_num(d["unit_price"]), _fmt_num(d["qty_green"] * d["unit_price"])],
        ["运费", f"每件 USD {_fmt_num(d['shipping_per'])}", str(d["qty_total"]),
         _fmt_num(d["shipping_per"]), _fmt_num(d["shipping_total"])],
        ["", "", "", "合计", _fmt_num(d["grand_total"])],
    ]
    for row_data in rows_data:
        row = table.add_row().cells
        for i, val in enumerate(row_data):
            row[i].text = val

    doc.add_heading("三、付款方式", 1)
    doc.add_paragraph("乙方应在合同签订后 7 个工作日内完成付款，支持 USDT（Polygon 网络）或银行电汇。")

    doc.add_heading("四、交货条款", 1)
    doc.add_paragraph("甲方在收到全额货款后 15 个工作日内安排发货，运输方式为国际快递（DHL/FedEx）。")

    doc.add_heading("五、质量保证", 1)
    doc.add_paragraph("产品自交货之日起享有 12 个月质量保修，因产品质量问题导致的损失由甲方承担。")

    doc.add_heading("六、违约责任", 1)
    doc.add_paragraph("任何一方违约，应向守约方支付合同总金额 10% 的违约金，并赔偿实际损失。")

    doc.add_heading("七、争议解决", 1)
    doc.add_paragraph("本合同适用中华人民共和国法律，争议提交合同签订地仲裁委员会仲裁解决。")

    doc.add_heading("八、签署", 1)
    sig_table = doc.add_table(rows=3, cols=2)
    sig_table.style = "Table Grid"
    sig_table.cell(0, 0).text = "甲方（卖方）签字/盖章"
    sig_table.cell(0, 1).text = "乙方（买方）签字/盖章"
    sig_table.cell(1, 0).text = "\n\n"
    sig_table.cell(1, 1).text = "\n\n"
    sig_table.cell(2, 0).text = f"日期：{d['date']}"
    sig_table.cell(2, 1).text = "日期：___________"

    doc.save(path)


def _gen_docx_en(d: Dict, path: str) -> None:
    from docx import Document
    from docx.shared import Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    for section in doc.sections:
        section.left_margin   = Cm(2.5)
        section.right_margin  = Cm(2.5)
        section.top_margin    = Cm(2.5)
        section.bottom_margin = Cm(2.5)

    title = doc.add_heading("SALES CONTRACT — eCandle Product", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"Contract No.: ECA-{d['date'].replace('-','')}-001")
    doc.add_paragraph(f"Date: {d['date']}")

    doc.add_heading("1. Parties", 1)
    doc.add_paragraph(f"Seller: {d['seller_name']}")
    doc.add_paragraph(f"Address: {d['seller_address']}")
    doc.add_paragraph(f"Contact: {d['seller_contact']}")
    doc.add_paragraph("")
    doc.add_paragraph(f"Buyer: {d['buyer_name']}")
    doc.add_paragraph(f"Address: {d['buyer_address']}")
    doc.add_paragraph(f"Contact: {d['buyer_contact']}")

    doc.add_heading("2. Products", 1)
    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for i, h in enumerate(["Product", "Variant", "Qty", "Unit Price (USD)", "Subtotal (USD)"]):
        hdr[i].text = h
    rows_data = [
        [d["product_name_en"], "Red", str(d["qty_red"]),
         _fmt_num(d["unit_price"]), _fmt_num(d["qty_red"] * d["unit_price"])],
        [d["product_name_en"], "Green", str(d["qty_green"]),
         _fmt_num(d["unit_price"]), _fmt_num(d["qty_green"] * d["unit_price"])],
        ["Shipping", f"USD {_fmt_num(d['shipping_per'])} / unit", str(d["qty_total"]),
         _fmt_num(d["shipping_per"]), _fmt_num(d["shipping_total"])],
        ["", "", "", "TOTAL", _fmt_num(d["grand_total"])],
    ]
    for row_data in rows_data:
        row = table.add_row().cells
        for i, val in enumerate(row_data):
            row[i].text = val

    doc.add_heading("3. Payment", 1)
    doc.add_paragraph("Buyer shall complete payment within 7 business days of contract signing. Accepted: USDT (Polygon) or bank wire.")

    doc.add_heading("4. Delivery", 1)
    doc.add_paragraph("Seller shall ship within 15 business days after receipt of full payment via DHL/FedEx.")

    doc.add_heading("5. Warranty", 1)
    doc.add_paragraph("12-month quality warranty from delivery date. Seller bears cost of defect remediation.")

    doc.add_heading("6. Liability", 1)
    doc.add_paragraph("Breaching party pays 10% of contract value as penalty plus actual damages.")

    doc.add_heading("7. Dispute Resolution", 1)
    doc.add_paragraph("Governed by the laws of Saint Lucia. Disputes submitted to agreed arbitration body.")

    doc.add_heading("8. Signatures", 1)
    sig_table = doc.add_table(rows=3, cols=2)
    sig_table.style = "Table Grid"
    sig_table.cell(0, 0).text = "Seller Signature / Stamp"
    sig_table.cell(0, 1).text = "Buyer Signature / Stamp"
    sig_table.cell(1, 0).text = "\n\n"
    sig_table.cell(1, 1).text = "\n\n"
    sig_table.cell(2, 0).text = f"Date: {d['date']}"
    sig_table.cell(2, 1).text = "Date: ___________"

    doc.save(path)


# ── public API ────────────────────────────────────────────────────────────────

def generate_contract(params: Dict) -> Dict[str, str]:
    """
    Generate contract files based on params.
    Returns dict of {key: filepath} for requested formats/languages.
    Keys: cn_pdf, en_pdf, cn_docx, en_docx (subset based on lang/format params).
    """
    d      = _build_data(params)
    lang   = params.get("lang", "both")    # "cn" | "en" | "both"
    fmt    = params.get("format", "both")  # "pdf" | "docx" | "both"
    tmpdir = tempfile.mkdtemp(prefix="contract_")
    result: Dict[str, str] = {}

    want_cn   = lang in ("cn", "both")
    want_en   = lang in ("en", "both")
    want_pdf  = fmt in ("pdf", "both")
    want_docx = fmt in ("docx", "both")

    if want_cn and want_pdf:
        p = os.path.join(tmpdir, "eCandle_合同_CN.pdf")
        _gen_pdf_cn(d, p)
        result["cn_pdf"] = p

    if want_en and want_pdf:
        p = os.path.join(tmpdir, "eCandle_Contract_EN.pdf")
        _gen_pdf_en(d, p)
        result["en_pdf"] = p

    if want_cn and want_docx:
        p = os.path.join(tmpdir, "eCandle_合同_CN.docx")
        _gen_docx_cn(d, p)
        result["cn_docx"] = p

    if want_en and want_docx:
        p = os.path.join(tmpdir, "eCandle_Contract_EN.docx")
        _gen_docx_en(d, p)
        result["en_docx"] = p

    return result
