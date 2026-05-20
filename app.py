"""
发票合并助手 — Streamlit 应用
行程单 × 发票 PDF 智能配对，按时间顺序合并
"""
import os
import re
import math
import tempfile
import io
from datetime import datetime

from io import BytesIO

import streamlit as st
import pdfplumber
import pikepdf
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ────────────────────────── 页面配置 ──────────────────────────
st.set_page_config(
    page_title="发票合并助手",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ────────────────────────── 中文字体注册 ──────────────────────────
_FONT_REGISTERED = False


def _register_cjk_font():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    candidates = [
        ("C:/Windows/Fonts/msyh.ttc", 0, "SimSun"),
        ("C:/Windows/Fonts/simsun.ttc", 0, "SimSun"),
        ("C:/Windows/Fonts/simhei.ttf", 0, "SimHei"),
        ("/System/Library/Fonts/PingFang.ttc", 0, "PingFang"),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0, "WQY"),
        ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0, "Droid"),
    ]
    for path, index, name in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=index))
                _FONT_REGISTERED = True
                return name
            except Exception:
                continue
    return "Helvetica"


_CJK_FONT = _register_cjk_font()


# ────────────────────────── 自定义 CSS ──────────────────────────
def _inject_css():
    st.markdown(
        """
    <style>
    /* 全局 — 深色基底 */
    .stApp {
        background: #0f1117;
    }
    .main > div:first-child {
        background: #0f1117;
    }
    /* 覆盖 Streamlit 默认亮色容器 */
    section[data-testid="stSidebar"] > div {
        background: #161822;
    }
    div[data-testid="stVerticalBlock"] {
        background: transparent;
    }

    /* 标题区 */
    .hero-title {
        text-align: center;
        padding: 1.2rem 0 0.2rem 0;
    }
    .hero-title h1 {
        font-size: 2.4rem;
        font-weight: 700;
        background: linear-gradient(135deg, #60a5fa, #a78bfa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.15rem;
    }
    .hero-sub {
        text-align: center;
        color: #9ca3af;
        font-size: 0.95rem;
        letter-spacing: 0.05em;
    }

    /* 统计卡片 — 暗色玻璃质感 */
    .stat-card {
        background: linear-gradient(145deg, #1a1d2a, #1f2235);
        border: 1px solid #2a2d3e;
        border-radius: 14px;
        padding: 1.2rem 1rem;
        text-align: center;
        transition: transform 0.15s, border-color 0.2s;
    }
    .stat-card:hover {
        transform: translateY(-2px);
        border-color: #3b3f55;
    }
    .stat-value {
        font-size: 2rem;
        font-weight: 700;
        color: #e2e8f0;
    }
    .stat-label {
        font-size: 0.78rem;
        color: #6b7280;
        margin-top: 0.25rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }
    .stat-accent-blue  .stat-value { color: #60a5fa; }
    .stat-accent-green .stat-value { color: #34d399; }
    .stat-accent-amber .stat-value { color: #fbbf24; }
    .stat-accent-red   .stat-value { color: #f87171; }

    /* 配对卡片 — 暗色玻璃 */
    .pair-card {
        background: linear-gradient(145deg, #1a1d2a, #1f2235);
        border: 1px solid #2a2d3e;
        border-radius: 12px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.6rem;
        border-left: 4px solid #60a5fa;
        transition: border-color 0.2s;
    }
    .pair-card:hover { border-left-color: #818cf8; }
    .pair-card .pair-header {
        font-weight: 600;
        color: #e2e8f0;
        margin-bottom: 0.3rem;
    }
    .pair-card .pair-row {
        display: flex;
        justify-content: space-between;
        font-size: 0.88rem;
        color: #9ca3af;
        padding: 0.15rem 0;
    }
    .pair-card .tag-trip {
        background: rgba(96,165,250,0.15);
        color: #93c5fd;
        padding: 1px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .pair-card .tag-invoice {
        background: rgba(52,211,153,0.15);
        color: #6ee7b7;
        padding: 1px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .pair-card .tag-miss {
        background: rgba(251,191,36,0.15);
        color: #fcd34d;
        padding: 1px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .pair-card .time-text {
        font-family: "SF Mono", "Fira Code", monospace;
        font-size: 0.82rem;
        color: #6b7280;
    }

    /* 下载按钮容器 */
    .download-box {
        background: linear-gradient(135deg, #1e2440, #2d2150);
        border: 1px solid #3b3f55;
        border-radius: 14px;
        padding: 1.5rem 2rem;
        text-align: center;
        color: #e2e8f0;
        margin-top: 1rem;
    }
    .download-box h3 { margin: 0; font-size: 1.15rem; color: #c4b5fd; }

    /* Streamlit 原生组件适配 */
    div[data-testid="stFileUploaderDropzone"] {
        background: #1a1d2a !important;
        border: 2px dashed #2a2d3e !important;
        border-radius: 14px !important;
        transition: all 0.25s !important;
    }
    div[data-testid="stFileUploaderDropzone"]:hover {
        border-color: #60a5fa !important;
    }
    div[data-testid="stFileUploaderDropzone"] p {
        color: #9ca3af !important;
    }
    .stMarkdown, .stCaption, p, span {
        color: #9ca3af;
    }
    h3 { color: #e2e8f0 !important; }
    div[data-testid="stExpander"] {
        background: #1a1d2a;
        border: 1px solid #2a2d3e;
        border-radius: 10px;
    }

    /* 隐藏多余元素 */
    header[data-testid="stHeader"] { display: none; }
    div[data-testid="stDecoration"] { display: none; }
    footer { visibility: hidden; }
    </style>
    """,
        unsafe_allow_html=True,
    )


# ────────────────────────── 占位页生成 ──────────────────────────
def _make_placeholder_pdf(message_lines, out_path):
    """生成占位说明页 PDF（含中文支持）"""
    c = canvas.Canvas(out_path, pagesize=A4)
    w, h = A4
    font_name = _CJK_FONT if _FONT_REGISTERED else "Helvetica"
    c.setFont(font_name, 16)
    c.drawString(48, h - 50, "占位说明页")
    c.setFont(font_name, 11)
    y = h - 80
    for line in message_lines:
        c.drawString(48, y, str(line)[:100])
        y -= 18
        if y < 50:
            c.showPage()
            c.setFont(font_name, 11)
            y = h - 50
    c.showPage()
    c.save()


# ────────────────────────── 页面解析 ──────────────────────────
def _parse_page(text):
    """从单页文本识别行程单 / 发票，提取时间和金额"""
    info = {"type": None, "time": None, "amount": None}

    # ── 行程单 ──
    if "上车时间" in text and "高德地图" in text:
        info["type"] = "trip"
        # 优先：行程时间（带时分）
        m = re.search(r"行程时间[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
        if m:
            try:
                info["time"] = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M")
                info["time_str"] = m.group(1)
            except ValueError:
                pass
        # 降级：上车时间（仅日期）
        if info["time"] is None:
            m = re.search(r"上车时间[：:]\s*(\d{4}-\d{2}-\d{2})", text)
            if m:
                try:
                    info["time"] = datetime.strptime(m.group(1), "%Y-%m-%d")
                    info["time_str"] = m.group(1)
                except ValueError:
                    pass
        # 降级：上车时间（带时分秒）
        if info["time"] is None:
            m = re.search(
                r"上车时间[：:]\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", text
            )
            if m:
                try:
                    info["time"] = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    info["time_str"] = m.group(1)
                except ValueError:
                    pass
        # 金额
        m = re.search(r"合计\s*([0-9]+\.[0-9]{1,2})\s*元", text)
        if m:
            info["amount"] = round(float(m.group(1)), 2)
        return info

    # ── 发票 ──
    if "电子发票" in text or ("发票号码" in text and "价税合计" in text):
        info["type"] = "invoice"
        # 金额
        m = re.search(r"价税合计.*?[¥￥]\s*([0-9]+\.[0-9]{1,2})", text, re.DOTALL)
        if m:
            info["amount"] = round(float(m.group(1)), 2)
        # 日期 — 多种格式
        date_parsers = [
            (
                r"开票日期[：:]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
                lambda g: datetime(int(g[0]), int(g[1]), int(g[2])),
            ),
            (
                r"开票日期[：:]\s*(\d{4}-\d{2}-\d{2})",
                lambda g: datetime.strptime(g[0], "%Y-%m-%d"),
            ),
            (
                r"开票日期[：:]\s*(\d{4}/\d{2}/\d{2})",
                lambda g: datetime.strptime(g[0], "%Y/%m/%d"),
            ),
        ]
        for pat, fn in date_parsers:
            m = re.search(pat, text)
            if m:
                try:
                    info["time"] = fn(m.groups())
                    info["time_str"] = info["time"].strftime("%Y-%m-%d")
                except (ValueError, IndexError):
                    pass
                break
        return info

    return info


# ────────────────────────── 批量提取 ──────────────────────────
def _extract_all(pdf_paths, progress_placeholder=None):
    """批量提取 PDF 中所有页面信息"""
    all_pages = []
    total = len(pdf_paths)
    for idx, path in enumerate(pdf_paths):
        fname = os.path.basename(path)
        try:
            with pdfplumber.open(path) as pdf:
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    info = _parse_page(text)
                    info["page_num"] = i + 1
                    info["pdf_file"] = path
                    info["pdf_name"] = fname
                    all_pages.append(info)
        except Exception as e:
            st.warning(f"读取 {fname} 失败: {e}")
        if progress_placeholder is not None:
            progress_placeholder.progress(
                (idx + 1) / total, f"🔎 解析中… {idx + 1}/{total}"
            )
    return all_pages


# ────────────────────────── 配对逻辑 ──────────────────────────
def _match_pairs(pages, amount_tolerance=0.01):
    """
    行程单按时间排序，逐个匹配同金额发票。
    返回 (matched_pairs, unmatched_invoices)
    matched_pairs: [(trip, invoice|None), ...]
    """
    trips = [p for p in pages if p["type"] == "trip"]
    invoices = [p for p in pages if p["type"] == "invoice"]

    trips.sort(key=lambda x: (x["time"] is None, x["time"] or datetime.max))

    matched = []
    used_inv = set()

    for trip in trips:
        trip_amt = trip.get("amount")
        best_idx = None

        if trip_amt is not None:
            for i, inv in enumerate(invoices):
                if i in used_inv:
                    continue
                inv_amt = inv.get("amount")
                if inv_amt is not None and math.isclose(
                    trip_amt, inv_amt, abs_tol=amount_tolerance
                ):
                    best_idx = i
                    break

        if best_idx is not None:
            used_inv.add(best_idx)
            matched.append((trip, invoices[best_idx]))
        else:
            matched.append((trip, None))

    unmatched = [inv for i, inv in enumerate(invoices) if i not in used_inv]
    return matched, unmatched


# ────────────────────────── PDF 合并 ──────────────────────────
def _merge_pdfs(matched_pairs, unmatched_invoices, tmpdir):
    """
    合并 PDF：每组 [行程单, 发票]，缺失项生成占位页。
    返回合并后的 PDF 字节数据。
    """
    pdf_out = pikepdf.Pdf.new()

    def _append_page(item):
        try:
            with pikepdf.open(item["pdf_file"]) as src:
                pdf_out.pages.append(src.pages[item["page_num"] - 1])
        except Exception:
            ph_path = os.path.join(tmpdir, f"ph_{len(pdf_out.pages)}.pdf")
            _make_placeholder_pdf(
                ["页面读取失败",
                 f"文件: {item.get('pdf_name', '?')}",
                 f"页码: {item.get('page_num', '?')}"],
                ph_path,
            )
            with pikepdf.open(ph_path) as ph:
                pdf_out.pages.extend(ph.pages)

    for pair_idx, (trip, inv) in enumerate(matched_pairs):
        _append_page(trip)
        if inv is not None:
            _append_page(inv)
        else:
            ph_path = os.path.join(tmpdir, f"ph_miss_{pair_idx}.pdf")
            t_str = trip.get("time_str", "未知时间")
            amt = trip.get("amount", "?")
            _make_placeholder_pdf(
                ["⚠️ 行程单缺少对应发票",
                 f"时间: {t_str}",
                 f"金额: {amt} 元"],
                ph_path,
            )
            with pikepdf.open(ph_path) as ph:
                pdf_out.pages.extend(ph.pages)

    for inv in unmatched_invoices:
        _append_page(inv)

    out_buf = io.BytesIO()
    pdf_out.save(out_buf)
    pdf_out.close()
    out_buf.seek(0)
    return out_buf


# ────────────────────────── 图片转 PDF ──────────────────────────
def _images_to_pdf(image_files, page_size="A4", fit_mode="fit"):
    """
    将多张图片合并为一个 PDF。
    page_size: "A4" | "A4-L" | "original"
    fit_mode: "fit" 等比缩放适应 | "fill" 拉伸铺满
    返回 PDF 字节数据。
    """
    if not image_files:
        return None

    # 确定页面尺寸（72 DPI 下的 reportlab 点数）
    sizes = {
        "A4": (595.27, 841.89),
        "A4-L": (841.89, 595.27),
    }
    page_w, page_h = sizes.get(page_size, (595.27, 841.89))

    images = []
    for f in image_files:
        try:
            img = Image.open(f).convert("RGB")
            images.append((f, img))
        except Exception as e:
            st.warning(f"跳过无法识别的图片 {getattr(f, 'name', f)}: {e}")

    if not images:
        return None

    pdf_buf = BytesIO()

    if page_size == "original":
        # 每张图片作为独立页面，保持原始尺寸
        first = images[0][1]
        first.save(
            pdf_buf, "PDF", save_all=True,
            append_images=[img for _, img in images[1:]],
        )
    else:
        # 统一页面尺寸
        c = canvas.Canvas(pdf_buf, pagesize=(page_w, page_h))
        for f, img in images:
            iw, ih = img.size
            if fit_mode == "fit":
                scale = min(page_w / iw, page_h / ih)
            else:
                scale = max(page_w / iw, page_h / ih)
            dw, dh = iw * scale, ih * scale
            x = (page_w - dw) / 2
            y = (page_h - dh) / 2

            # 将 PIL Image 临时保存再绘制
            tmp_path = os.path.join(tempfile.gettempdir(), f"_img2pdf_{id(img)}.jpg")
            img.save(tmp_path, "JPEG", quality=92)
            c.drawImage(tmp_path, x, y, dw, dh)
            c.showPage()
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        c.save()

    pdf_buf.seek(0)
    return pdf_buf


# ────────────────────────── PDF 任意插页 ──────────────────────────
def _insert_pdf_pages(base_pdf_buf, insert_pdf_buf, position, page_range=None):
    """
    在 base PDF 的指定位置插入 insert PDF 的页面。
    position: 插入位置（0=开头, 1=第1页后, ..., N=末尾）
    page_range: None 全部页面，或 (start, end) 1-based 闭区间
    返回合并后的 PDF 字节数据。
    """
    base = pikepdf.open(base_pdf_buf)
    insert = pikepdf.open(insert_pdf_buf)

    total_base = len(base.pages)
    total_insert = len(insert.pages)

    # 确定要插入的页面索引
    if page_range is not None:
        start, end = page_range
        start = max(0, start - 1)
        end = min(total_insert, end)
    else:
        start = 0
        end = total_insert

    # 归一化插入位置
    pos = max(0, min(position, total_base))

    # 构建新 PDF：base[:pos] + insert[start:end] + base[pos:]
    out = pikepdf.Pdf.new()
    # 前半部分
    for i in range(pos):
        out.pages.append(base.pages[i])
    # 插入部分
    for i in range(start, end):
        out.pages.append(insert.pages[i])
    # 后半部分
    for i in range(pos, total_base):
        out.pages.append(base.pages[i])

    out_buf = BytesIO()
    out.save(out_buf)
    out.close()
    base.close()
    insert.close()
    out_buf.seek(0)
    return out_buf


# ────────────────────────── 主界面 ──────────────────────────
def _invoice_merge_tab():
    """Tab 1: 发票合并"""
    # ── 说明区 ──
    with st.expander("📖 使用说明", expanded=False):
        st.markdown(
            """
        **功能说明：**
        1. 上传包含行程单和发票的 PDF 文件
        2. 系统自动识别每页是行程单还是发票
        3. 按金额（误差 ≤ ¥0.01）自动配对：金额相同的行程单和发票编为一组
        4. 每组按行程时间排序，行程单在前、发票在后
        5. 点击合并下载，得到一份按时间顺序排列的完整 PDF

        **识别依据：**
        - 行程单：包含「上车时间」和「高德地图」关键字
        - 发票：包含「电子发票」或「发票号码 + 价税合计」关键字
        """
        )

    # ── 上传区 ──
    uploaded = st.file_uploader(
        "📂 拖拽或点击选择 PDF 文件",
        type="pdf",
        accept_multiple_files=True,
        help="支持多个 PDF，每页独立识别",
        key="tab1_uploader",
    )

    if not uploaded:
        st.info("👆 请上传行程单和发票的 PDF 文件")
        return

    st.caption(f"✅ 已选择 {len(uploaded)} 个文件")

    # ── 执行按钮 ──
    col_l, col_m, col_r = st.columns([1, 1, 1])
    with col_m:
        run = st.button(
            "🔍 分析并合并",
            type="primary",
            use_container_width=True,
            key="tab1_run",
        )

    if not run:
        return

    # ── 处理流程 ──
    tmpdir = tempfile.mkdtemp()
    status = st.status("🔄 处理中…", expanded=True)

    # 1. 保存文件
    status.update(label="📂 正在保存文件…")
    paths = []
    with st.spinner("保存上传文件…"):
        for f in uploaded:
            p = os.path.join(tmpdir, f.name)
            with open(p, "wb") as fh:
                fh.write(f.getbuffer())
            paths.append(p)

    # 2. 提取内容
    status.update(label="🔎 正在提取 PDF 内容…")
    progress_bar = st.progress(0, "准备解析…")
    all_pages = _extract_all(paths, progress_placeholder=progress_bar)

    trips = [p for p in all_pages if p["type"] == "trip"]
    invoices = [p for p in all_pages if p["type"] == "invoice"]
    unknown = [p for p in all_pages if p["type"] is None]

    # 3. 配对
    status.update(label="🔗 正在配对行程单与发票…")
    matched, unmatched_inv = _match_pairs(all_pages)
    matched_cnt = sum(1 for _, inv in matched if inv is not None)
    unmatched_trip_cnt = sum(1 for _, inv in matched if inv is None)

    # 4. 合并
    status.update(label="📄 正在合并 PDF…")
    merged_buf = _merge_pdfs(matched, unmatched_inv, tmpdir)
    status.update(label="✅ 处理完成！", state="complete")

    # ── 统计卡片 ──
    st.markdown("### 📊 分析概览")
    cols = st.columns(6)
    stats = [
        ("行程单", len(trips), "blue"),
        ("发票", len(invoices), "green"),
        ("✅ 已配对", matched_cnt, "blue"),
        ("⚠️ 缺发票", unmatched_trip_cnt, "amber"),
        ("📄 未匹配发票", len(unmatched_inv), "amber"),
        ("❓ 无法识别", len(unknown), "red"),
    ]
    for col, (label, val, accent) in zip(cols, stats):
        with col:
            st.markdown(
                f'<div class="stat-card stat-accent-{accent}">'
                f'<div class="stat-value">{val}</div>'
                f'<div class="stat-label">{label}</div>'
                f"</div>",
                unsafe_allow_html=True,
            )

    # ── 配对明细 ──
    st.markdown("### 🔗 配对明细")
    st.caption(f"共 {len(matched)} 组，按行程时间排序")

    for idx, (trip, inv) in enumerate(matched):
        t_str = trip.get("time_str", "无时间")
        t_amt = trip.get("amount", "?")
        t_file = trip.get("pdf_name", "?")
        t_page = trip.get("page_num", "?")

        if inv is not None:
            i_str = inv.get("time_str", "无时间")
            i_amt = inv.get("amount", "?")
            i_file = inv.get("pdf_name", "?")
            i_page = inv.get("page_num", "?")
            st.markdown(
                f'<div class="pair-card">'
                f'<div class="pair-header">第 {idx + 1} 组 · 金额 ¥{t_amt}</div>'
                f'<div class="pair-row"><span><span class="tag-trip">行程单</span> {t_file} (p.{t_page})</span>'
                f'<span class="time-text">{t_str}</span></div>'
                f'<div class="pair-row"><span><span class="tag-invoice">发　票</span> {i_file} (p.{i_page})</span>'
                f'<span class="time-text">{i_str}</span></div>'
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="pair-card" style="border-left-color:#f59e0b;">'
                f'<div class="pair-header">第 {idx + 1} 组 · 金额 ¥{t_amt}</div>'
                f'<div class="pair-row"><span><span class="tag-trip">行程单</span> {t_file} (p.{t_page})</span>'
                f'<span class="time-text">{t_str}</span></div>'
                f'<div class="pair-row"><span><span class="tag-miss">⚠️ 缺少发票</span></span>'
                f'<span class="time-text">—</span></div>'
                f"</div>",
                unsafe_allow_html=True,
            )

    # ── 下载区 ──
    st.markdown(
        '<div class="download-box">'
        "<h3>✅ 合并完成，点击下载</h3>"
        '<p style="margin:0.3rem 0 0.5rem;font-size:0.85rem;opacity:0.9;">'
        "文件已按时间顺序排列，缺失项已自动插入占位说明页</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.download_button(
        label="📥 下载合并 PDF",
        data=merged_buf,
        file_name="合并报销文件.pdf",
        mime="application/pdf",
        type="primary",
        use_container_width=True,
        key="tab1_dl",
    )

    # 清理临时文件
    try:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


def _img_to_pdf_tab():
    """Tab 2: 图片转 PDF"""
    st.caption("上传多张图片，一键合并为 PDF 文件")

    img_files = st.file_uploader(
        "🖼️ 选择图片文件",
        type=["jpg", "jpeg", "png", "bmp", "webp"],
        accept_multiple_files=True,
        help="支持 JPG / PNG / BMP / WebP",
        key="tab2_uploader",
    )

    if not img_files:
        st.info("👆 请上传图片文件")
        return

    st.caption(f"✅ 已选择 {len(img_files)} 张图片")

    col1, col2 = st.columns(2)
    with col1:
        page_size = st.selectbox(
            "📐 页面尺寸",
            options=["A4", "A4-L", "original"],
            format_func=lambda x: {"A4": "A4 纵向", "A4-L": "A4 横向", "original": "原始尺寸"}[x],
            key="tab2_size",
        )
    with col2:
        fit_mode = st.selectbox(
            "📏 缩放方式",
            options=["fit", "fill"],
            format_func=lambda x: "等比适应" if x == "fit" else "拉伸铺满",
            key="tab2_fit",
            disabled=(page_size == "original"),
        )

    if st.button("🖼️ 转换为 PDF", type="primary", use_container_width=True, key="tab2_conv"):
        # 保存上传图片到临时目录
        tmpdir = tempfile.mkdtemp()
        img_paths = []
        for f in img_files:
            p = os.path.join(tmpdir, f.name)
            with open(p, "wb") as fh:
                fh.write(f.getbuffer())
            img_paths.append(p)

        with st.spinner("🔄 正在转换…"):
            pdf_buf = _images_to_pdf(img_paths, page_size=page_size, fit_mode=fit_mode)

        if pdf_buf:
            st.success(f"✅ 转换完成 — 共 {len(img_files)} 张图片")
            st.download_button(
                label="📥 下载 PDF",
                data=pdf_buf,
                file_name="图片转PDF.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
                key="tab2_dl",
            )
        else:
            st.error("转换失败，请检查图片格式")

        # 清理
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def _pdf_insert_tab():
    """Tab 3: PDF 任意插页"""
    st.caption("在 PDF 的任意位置插入另一个 PDF 的页面")

    col_a, col_b = st.columns(2)
    with col_a:
        base_file = st.file_uploader(
            "📄 主 PDF 文件",
            type="pdf",
            accept_multiple_files=False,
            key="tab3_base",
        )
    with col_b:
        insert_file = st.file_uploader(
            "📑 要插入的 PDF 文件",
            type="pdf",
            accept_multiple_files=False,
            key="tab3_insert",
        )

    if not base_file or not insert_file:
        st.info("👆 请分别上传主 PDF 和要插入的 PDF")
        return

    # 读取页数
    base_buf_io = BytesIO(base_file.getbuffer())
    insert_buf_io = BytesIO(insert_file.getbuffer())

    try:
        with pikepdf.open(base_buf_io) as b:
            base_pages = len(b.pages)
        with pikepdf.open(insert_buf_io) as ins:
            insert_pages = len(ins.pages)
    except Exception as e:
        st.error(f"PDF 读取失败: {e}")
        return

    st.markdown(
        f'<div style="display:flex;gap:1.5rem;margin:0.5rem 0;">'
        f'<div class="stat-card" style="flex:1;"><div class="stat-value">{base_pages}</div>'
        f'<div class="stat-label">主 PDF 页数</div></div>'
        f'<div class="stat-card" style="flex:1;"><div class="stat-value">{insert_pages}</div>'
        f'<div class="stat-label">待插入页数</div></div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        position = st.number_input(
            "📍 插入位置",
            min_value=0,
            max_value=base_pages,
            value=base_pages,
            help=f"0=开头, 1=第1页后, {base_pages}=末尾",
            key="tab3_pos",
        )
    with col2:
        insert_start = st.number_input(
            "📌 起始页 (1-based)",
            min_value=1,
            max_value=insert_pages,
            value=1,
            key="tab3_start",
        )
    with col3:
        insert_end = st.number_input(
            "📌 结束页 (1-based)",
            min_value=1,
            max_value=insert_pages,
            value=insert_pages,
            key="tab3_end",
        )

    insert_all = st.checkbox("插入全部页面", value=True, key="tab3_all")
    if insert_all:
        page_range = None
    else:
        page_range = (int(insert_start), int(insert_end))

    if st.button("📑 执行插入", type="primary", use_container_width=True, key="tab3_run"):
        base_buf_in = BytesIO(base_file.getbuffer())
        insert_buf_in = BytesIO(insert_file.getbuffer())

        with st.spinner("🔄 正在合并…"):
            try:
                out_buf = _insert_pdf_pages(base_buf_in, insert_buf_in, int(position), page_range)
                st.success("✅ 插入完成")
                st.download_button(
                    label="📥 下载合并 PDF",
                    data=out_buf,
                    file_name="插入后PDF.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True,
                    key="tab3_dl",
                )
            except Exception as e:
                st.error(f"插入失败: {e}")


def main():
    _inject_css()

    # ── 顶部标题 ──
    st.markdown(
        '<div class="hero-title"><h1>📄 发票合并助手</h1></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="hero-sub">行程单合并 · 图片转PDF · PDF插页</div>',
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3 = st.tabs(["📄 发票合并", "🖼️ 图片转PDF", "📑 PDF插页"])

    with tab1:
        _invoice_merge_tab()
    with tab2:
        _img_to_pdf_tab()
    with tab3:
        _pdf_insert_tab()


if __name__ == "__main__":
    main()
