"""
发票合并助手 - 核心业务逻辑
负责 PDF 解析、行程单/发票匹配、PDF 合并
"""

import os
import re
import math
import tempfile
from datetime import datetime

import pdfplumber

try:
    import pikepdf

    USE_PIKEPDF = True
except ImportError:
    from PyPDF2 import PdfReader, PdfWriter  # noqa: F401

    USE_PIKEPDF = False

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4


# -------- 占位页生成 --------
def make_placeholder_pdf(message_lines, out_path):
    """生成占位说明页 PDF"""
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(48, height - 50, "占位说明页")
    c.setFont("Helvetica", 11)
    y = height - 80
    for line in message_lines:
        c.drawString(48, y, str(line))
        y -= 16
        if y < 48:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = height - 48
    c.showPage()
    c.save()


# -------- 提取单个页面的信息 --------
def extract_page_info(text):
    """从单个页面文本中识别类型和提取信息"""
    page_type = None
    info = {"time": None, "amount": None, "type": None}

    # 判断是行程单还是发票
    is_trip = "上车时间" in text and "高德地图" in text
    is_invoice = "电子发票" in text or ("发票号码" in text and "价税合计" in text)

    if is_trip:
        page_type = "trip"
        t_match = re.search(
            r"上车时间[:：]?\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text
        )
        if t_match:
            try:
                info["time"] = datetime.strptime(
                    t_match.group(1), "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                pass
        amt_match = re.search(r"合计\s*([0-9]+\.[0-9]{1,2})\s*元", text)
        if amt_match:
            info["amount"] = round(float(amt_match.group(1)), 2)

    elif is_invoice:
        page_type = "invoice"
        inv_match = re.search(r"发票号码[:：]?\s*(\d+)", text)
        if inv_match:
            info["invoice_no"] = inv_match.group(1)
        amt_match = re.search(
            r"价税合计.*?[¥￥]\s*([0-9]+\.[0-9]{1,2})", text, re.DOTALL
        )
        if amt_match:
            info["amount"] = round(float(amt_match.group(1)), 2)

    info["type"] = page_type
    return info


# -------- 从PDF提取所有页面信息 --------
def extract_pdf_pages_info(pdf_path):
    """从PDF中提取每个页面的详细信息"""
    pages_info = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                info = extract_page_info(text)
                info["page_num"] = page_num + 1
                info["pdf_file"] = pdf_path
                pages_info.append(info)
    except Exception as e:
        raise RuntimeError(f"读取 {os.path.basename(pdf_path)} 失败: {e}")

    return pages_info


# -------- 从 PDF 批量提取 --------
def extract_all_pdfs(pdf_paths):
    """批量提取多个 PDF 的所有页面信息"""
    all_pages = []
    for path in pdf_paths:
        pages = extract_pdf_pages_info(path)
        all_pages.extend(pages)
    return all_pages


# -------- 匹配行程单和发票 --------
def match_trips_invoices(pages_info_list, tol=0.01):
    """匹配行程单和发票页面"""
    trips = [p for p in pages_info_list if p.get("type") == "trip"]
    invoices = [p for p in pages_info_list if p.get("type") == "invoice"]

    trips_sorted = sorted(
        trips, key=lambda x: (x.get("time") is None, x.get("time"))
    )

    matched = []
    used_inv = set()

    for trip in trips_sorted:
        trip_amt = trip.get("amount")
        found_idx = None

        if trip_amt is not None:
            for i, inv in enumerate(invoices):
                if i in used_inv:
                    continue
                inv_amt = inv.get("amount")
                if inv_amt is not None and math.isclose(
                    trip_amt, inv_amt, abs_tol=tol
                ):
                    found_idx = i
                    break

        if found_idx is not None:
            used_inv.add(found_idx)
            matched.append({"trip": trip, "invoice": invoices[found_idx]})
        else:
            matched.append({"trip": trip, "invoice": None})

    unmatched_invoices = [
        inv for i, inv in enumerate(invoices) if i not in used_inv
    ]
    return matched, unmatched_invoices


# -------- 统计信息 --------
def get_statistics(pages_info_list):
    """获取页面统计信息"""
    trips = [p for p in pages_info_list if p["type"] == "trip"]
    invoices = [p for p in pages_info_list if p["type"] == "invoice"]
    unknown = [p for p in pages_info_list if p["type"] is None]
    return {"trips": len(trips), "invoices": len(invoices), "unknown": len(unknown)}


# -------- PDF 合并 --------
def merge_pdfs_with_placeholders(
    matched_pairs,
    unmatched_invoices,
    out_path,
    progress_callback=None,
):
    """合并 PDF，缺失项自动生成占位页"""
    tmpdir = tempfile.mkdtemp()
    total_items = len(matched_pairs) * 2 + len(unmatched_invoices)
    current_item = 0

    if USE_PIKEPDF:
        pdf_merger = pikepdf.Pdf.new()

        for pair in matched_pairs:
            for key in ["trip", "invoice"]:
                item = pair.get(key)
                if item is None:
                    ph_path = os.path.join(
                        tmpdir, f"placeholder_{key}_{len(pdf_merger.pages)}.pdf"
                    )
                    make_placeholder_pdf([f"{key} 缺失"], ph_path)
                    with pikepdf.open(ph_path) as ph_pdf:
                        pdf_merger.pages.extend(ph_pdf.pages)
                else:
                    try:
                        page_num = item["page_num"]
                        with pikepdf.open(item["pdf_file"]) as src_pdf:
                            page_to_add = src_pdf.pages[page_num - 1]
                            pdf_merger.pages.append(page_to_add)
                    except Exception as e:
                        ph_path = os.path.join(
                            tmpdir,
                            f"placeholder_error_{len(pdf_merger.pages)}.pdf",
                        )
                        make_placeholder_pdf(
                            [f"{key} 读取失败", f"错误: {str(e)[:50]}"], ph_path
                        )
                        with pikepdf.open(ph_path) as ph_pdf:
                            pdf_merger.pages.extend(ph_pdf.pages)

                current_item += 1
                if progress_callback:
                    progress_callback(current_item / total_items * 100)

        for inv in unmatched_invoices:
            try:
                page_num = inv["page_num"]
                with pikepdf.open(inv["pdf_file"]) as src_pdf:
                    page_to_add = src_pdf.pages[page_num - 1]
                    pdf_merger.pages.append(page_to_add)
            except Exception as e:
                ph_path = os.path.join(
                    tmpdir, f"unmatched_error_{len(pdf_merger.pages)}.pdf"
                )
                make_placeholder_pdf(
                    ["未匹配发票读取失败", f"错误: {str(e)[:50]}"], ph_path
                )
                with pikepdf.open(ph_path) as ph_pdf:
                    pdf_merger.pages.extend(ph_pdf.pages)

            current_item += 1
            if progress_callback:
                progress_callback(current_item / total_items * 100)

        pdf_merger.save(out_path)
        pdf_merger.close()
    else:
        from PyPDF2 import PdfReader, PdfWriter

        writer = PdfWriter()

        for pair in matched_pairs:
            for key in ["trip", "invoice"]:
                item = pair.get(key)
                if item is None:
                    ph_path = os.path.join(
                        tmpdir, f"placeholder_{key}_{len(writer.pages)}.pdf"
                    )
                    make_placeholder_pdf([f"{key} 缺失"], ph_path)
                    reader = PdfReader(ph_path)
                    for page in reader.pages:
                        writer.add_page(page)
                else:
                    try:
                        page_num = item["page_num"]
                        reader = PdfReader(item["pdf_file"])
                        writer.add_page(reader.pages[page_num - 1])
                    except Exception as e:
                        ph_path = os.path.join(
                            tmpdir,
                            f"placeholder_error_{len(writer.pages)}.pdf",
                        )
                        make_placeholder_pdf(
                            [f"{key} 读取失败", f"错误: {str(e)[:50]}"], ph_path
                        )
                        reader = PdfReader(ph_path)
                        for page in reader.pages:
                            writer.add_page(page)

                current_item += 1
                if progress_callback:
                    progress_callback(current_item / total_items * 100)

        for inv in unmatched_invoices:
            try:
                page_num = inv["page_num"]
                reader = PdfReader(inv["pdf_file"])
                writer.add_page(reader.pages[page_num - 1])
            except Exception as e:
                ph_path = os.path.join(
                    tmpdir, f"unmatched_error_{len(writer.pages)}.pdf"
                )
                make_placeholder_pdf(
                    ["未匹配发票读取失败", f"错误: {str(e)[:50]}"], ph_path
                )
                reader = PdfReader(ph_path)
                for page in reader.pages:
                    writer.add_page(page)

            current_item += 1
            if progress_callback:
                progress_callback(current_item / total_items * 100)

        with open(out_path, "wb") as f:
            writer.write(f)
