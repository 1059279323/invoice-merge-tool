import streamlit as st
import PyPDF2
import pdfplumber
import io
import re
from datetime import datetime
from collections import defaultdict

# 页面配置
st.set_page_config(page_title="发票合并助手", page_icon="📄", layout="wide")
st.title("📄 发票合并助手")
st.subheader("智能配对：按金额匹配行程单+发票，按行程单时间升序排列")

# 侧边栏说明
with st.sidebar:
    st.header("⚙️ 配对规则")
    st.markdown("""
    **配对逻辑：**
    1. 💰 从文件名提取金额作为配对key
    2. 🎫 行程单 + 🧾 发票 金额相同 → 一组
    3. 📅 从行程单PDF内容提取上车时间排序
    4. 📦 最终顺序：`行程单A → 发票A → 行程单B → 发票B → ...`
    5. ❓ 未配对文件排在最后
    
    **文件名示例：**
    ```
    【快来车-10.60元-1个行程】高德打车电子发票.pdf
    【快来车-10.60元-1个行程】高德打车电子行程单.pdf
    ```
    """)

# 上传PDF文件
uploaded_files = st.file_uploader(
    "📁 上传PDF文件（支持多选）",
    type=["pdf"],
    accept_multiple_files=True,
    help="按住 Ctrl/Cmd 多选，或拖拽上传"
)

# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def extract_amount_from_filename(filename):
    """
    从文件名提取金额作为配对key
    匹配格式：10.60元 / 11.49元 / 100元 / 10.6元
    返回标准化字符串，如 '10.60'
    """
    pattern = r'(\d+\.?\d*)[元¥]'
    match = re.search(pattern, filename)
    if match:
        amount_str = match.group(1)
        try:
            # 标准化为float再转字符串，避免 10.6 和 10.60 不匹配
            return str(float(amount_str))
        except ValueError:
            return amount_str
    return None


def classify_file(filename):
    """
    分类文件：行程单 / 发票 / 其他
    根据高德打车文件名规律判断
    """
    name_lower = filename.lower()
    # 行程单关键词
    if any(kw in filename for kw in ['行程单', 'itinerary', '行程']):
        return '行程单'
    # 发票关键词
    elif any(kw in filename for kw in ['发票', 'invoice', 'receipt', 'bill']):
        return '发票'
    return '其他'


def extract_date_from_pdf_content(file):
    """
    从行程单PDF内容提取上车/出发时间
    支持多种格式，三层优先级
    """
    try:
        file.seek(0)
        with pdfplumber.open(file) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

        if not full_text.strip():
            return None, None  # 返回 (date, datetime)

        # ── 第一优先：关键词 + 日期时间 ──
        keyword_patterns = [
            # 上车时间：2024-03-15 08:30
            r'(?:上车|出发|乘车|行程|起程|登机|发车|开车|上车时间|出发时间|乘车时间|行程时间|打车时间)'
            r'[时间：:\s]*'
            r'(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})[日\s]*(\d{1,2}):(\d{2})',

            # 上车时间：2024年03月15日 08:30
            r'(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})',

            # 纯日期+时间：2024-03-15 08:30
            r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s+(\d{1,2}):(\d{2})',
        ]

        for pattern in keyword_patterns:
            match = re.search(pattern, full_text)
            if match:
                groups = match.groups()
                try:
                    if len(groups) == 5:
                        y, mo, d, h, mi = int(groups[0]), int(groups[1]), int(groups[2]), int(groups[3]), int(groups[4])
                        dt = datetime(y, mo, d, h, mi)
                        return dt.date(), dt
                except ValueError:
                    continue

        # ── 第二优先：只有日期无时间 ──
        date_patterns = [
            r'(\d{4})年(\d{1,2})月(\d{1,2})日',
            r'(\d{4})-(\d{1,2})-(\d{1,2})',
            r'(\d{4})\.(\d{1,2})\.(\d{1,2})',
            r'(\d{4})/(\d{1,2})/(\d{1,2})',
        ]

        for pattern in date_patterns:
            for match in re.finditer(pattern, full_text):
                y, mo, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                try:
                    dt = datetime(y, mo, d)
                    if datetime(2000, 1, 1) <= dt <= datetime(2099, 12, 31):
                        return dt.date(), dt
                except ValueError:
                    continue

    except Exception as e:
        pass

    return None, None


def build_file_info(files):
    """构建文件信息列表，行程单额外解析PDF内容获取时间"""
    file_info_list = []
    progress = st.progress(0, text="正在读取文件信息...")

    for i, f in enumerate(files):
        ftype = classify_file(f.name)
        amount = extract_amount_from_filename(f.name)
        date = None
        sort_dt = None
        date_source = "未识别"

        if ftype == '行程单':
            # 从PDF内容提取时间（用于排序）
            date, sort_dt = extract_date_from_pdf_content(f)
            if date:
                date_source = "PDF内容"
            else:
                date_source = "⚠️ 未识别"
        else:
            date_source = "文件名（发票无需排序）"

        file_info_list.append({
            'file': f,
            'name': f.name,
            'date': date,
            'sort_dt': sort_dt,   # 精确到分钟，用于排序
            'type': ftype,
            'amount': amount,
            'date_source': date_source,
        })

        progress.progress((i + 1) / len(files), text=f"正在读取：{f.name}")

    progress.empty()
    return file_info_list


def pair_and_sort(file_info_list):
    """
    配对逻辑：
    1. 按金额匹配行程单和发票
    2. 行程单按PDF内提取的上车时间升序排列
    3. 每组：行程单在前，发票在后
    4. 未配对的追加末尾
    """
    itineraries = [f for f in file_info_list if f['type'] == '行程单']
    invoices    = [f for f in file_info_list if f['type'] == '发票']
    others      = [f for f in file_info_list if f['type'] == '其他']

    # 发票按金额建立索引（同金额可能有多张，用队列）
    invoice_by_amount = defaultdict(list)
    for inv in invoices:
        key = inv['amount']
        if key:
            invoice_by_amount[key].append(inv)

    # 行程单按上车时间升序排列（无时间的排最后）
    itineraries_sorted = sorted(
        itineraries,
        key=lambda x: (
            x['sort_dt'] or datetime.max,
            x['name']
        )
    )

    paired_invoice_ids = set()
    result_groups = []

    for itin in itineraries_sorted:
        amount_key = itin['amount']
        paired_inv = None

        if amount_key and amount_key in invoice_by_amount:
            for inv in invoice_by_amount[amount_key]:
                if id(inv['file']) not in paired_invoice_ids:
                    paired_inv = inv
                    paired_invoice_ids.add(id(inv['file']))
                    break

        result_groups.append({
            'itinerary': itin,
            'invoice': paired_inv,
            'amount': amount_key,
        })

    # 未配对的发票
    unpaired_invoices = [
        inv for inv in invoices
        if id(inv['file']) not in paired_invoice_ids
    ]

    # 其他文件
    others_sorted = sorted(others, key=lambda x: x['name'])

    # 构建最终文件列表
    ordered_files = []
    for group in result_groups:
        ordered_files.append(group['itinerary']['file'])
        if group['invoice']:
            ordered_files.append(group['invoice']['file'])

    for inv in unpaired_invoices:
        ordered_files.append(inv['file'])

    for o in others_sorted:
        ordered_files.append(o['file'])

    return ordered_files, result_groups, unpaired_invoices, others_sorted


def merge_pdfs(pdf_files):
    """合并PDF文件列表"""
    if not pdf_files:
        return None
    merger = PyPDF2.PdfMerger()
    for pdf in pdf_files:
        pdf.seek(0)
        merger.append(pdf)
    output = io.BytesIO()
    merger.write(output)
    output.seek(0)
    merger.close()
    return output


# ─────────────────────────────────────────────
# 主逻辑
# ─────────────────────────────────────────────

if uploaded_files:
    st.success(f"✅ 已上传 {len(uploaded_files)} 个文件")

    # 构建文件信息
    file_info_list = build_file_info(uploaded_files)

    # ── 文件识别概览 ──
    with st.expander("📋 文件识别结果", expanded=True):
        header_cols = st.columns([1, 5, 2, 2, 2])
        header_cols[0].markdown("**序号**")
        header_cols[1].markdown("**文件名**")
        header_cols[2].markdown("**类型**")
        header_cols[3].markdown("**金额(配对key)**")
        header_cols[4].markdown("**识别日期**")
        st.markdown("---")

        for i, info in enumerate(file_info_list):
            cols = st.columns([1, 5, 2, 2, 2])
            icon = "🎫" if info['type'] == '行程单' else "🧾" if info['type'] == '发票' else "📄"
            cols[0].write(i + 1)
            cols[1].write(info['name'])
            cols[2].write(f"{icon} {info['type']}")
            cols[3].write(f"💰 {info['amount'] or '⚠️ 未识别'}")
            cols[4].write(
                str(info['sort_dt'].strftime('%Y-%m-%d %H:%M') if info['sort_dt'] else
                    str(info['date']) if info['date'] else '⚠️ 未识别')
            )

    # ── 合并按钮 ──
    if st.button("🔗 开始智能配对并合并", type="primary", use_container_width=True):
        try:
            with st.spinner("🔄 正在按金额配对并排序..."):
                ordered_files, group_preview, unpaired_invoices, other_files = pair_and_sort(file_info_list)

            # ── 配对结果预览 ──
            with st.expander("✅ 配对分组预览（最终合并顺序）", expanded=True):

                if group_preview:
                    st.markdown("### 🗂️ 配对分组（按行程单时间升序）")
                    for i, group in enumerate(group_preview):
                        itin   = group['itinerary']
                        inv    = group['invoice']
                        amount = group['amount']
                        paired = inv is not None

                        # 时间显示
                        time_str = (
                            itin['sort_dt'].strftime('%Y-%m-%d %H:%M')
                            if itin['sort_dt'] else
                            str(itin['date']) if itin['date'] else '未知时间'
                        )

                        st.markdown(
                            f"**第 {i+1} 组 · ⏰ {time_str} · 💰 {amount or '未知金额'}元**"
                        )

                        # 行程单行
                        c1, c2, c3 = st.columns([1, 6, 2])
                        c1.write("🎫")
                        c2.write(itin['name'])
                        c3.write("`行程单`")

                        # 发票行
                        c1, c2, c3 = st.columns([1, 6, 2])
                        if paired:
                            c1.write("🧾")
                            c2.write(inv['name'])
                            c3.write("`发票`")
                        else:
                            c1.write("⚠️")
                            c2.write("*无对应发票*")
                            c3.write("`未配对`")

                        st.markdown("---")

                if unpaired_invoices:
                    st.markdown("### 🧾 未配对发票（追加到末尾）")
                    for inv in unpaired_invoices:
                        c1, c2 = st.columns([1, 7])
                        c1.write("🧾")
                        c2.write(f"{inv['name']}  ·  💰 {inv['amount'] or '未知金额'}元")

                if other_files:
                    st.markdown("### 📄 其他文件（追加到末尾）")
                    for o in other_files:
                        c1, c2 = st.columns([1, 7])
                        c1.write("📄")
                        c2.write(o['name'])

                # 完整顺序
                st.markdown("### 📑 最终合并顺序")
                for idx, f in enumerate(ordered_files):
                    info = next((x for x in file_info_list if x['file'] == f), None)
                    ftype = info['type'] if info else '其他'
                    icon  = "🎫" if ftype == '行程单' else "🧾" if ftype == '发票' else "📄"
                    time_str = (
                        info['sort_dt'].strftime('%Y-%m-%d %H:%M')
                        if info and info['sort_dt'] else
                        str(info['date']) if info and info['date'] else '未知时间'
                    )
                    st.text(f"  {idx+1:>2}. {icon} [{ftype}]  {f.name}  ({time_str})")

            # ── 合并 PDF ──
            with st.spinner("📦 正在合并 PDF..."):
                merged_pdf = merge_pdfs(ordered_files)

            if merged_pdf:
                filename_ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                st.download_button(
                    label="📥 下载合并后的发票包",
                    data=merged_pdf,
                    file_name=f"发票合并包_{filename_ts}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                st.balloons()

                paired_count   = sum(1 for g in group_preview if g['invoice'] is not None)
                unpaired_itin  = sum(1 for g in group_preview if g['invoice'] is None)
                unpaired_inv   = len(unpaired_invoices)
                st.success(
                    f"✨ 合并成功！共 {len(group_preview)} 张行程单，"
                    f"成功配对 {paired_count} 组，"
                    f"行程单未配对 {unpaired_itin} 张，"
                    f"发票未配对 {unpaired_inv} 张"
                )

        except Exception as e:
            st.error(f"❌ 处理失败：{str(e)}")
            st.exception(e)

else:
    st.info("👆 请上传 PDF 格式的文件开始使用")
    st.markdown("""
    ### 📌 使用说明

    | 步骤 | 操作 |
    |------|------|
    | 1️⃣ | 上传所有行程单和发票 PDF |
    | 2️⃣ | 系统自动按金额配对，从行程单PDF提取上车时间 |
    | 3️⃣ | 点击「开始智能配对并合并」|
    | 4️⃣ | 预览配对分组结果 |
    | 5️⃣ | 下载合并后的 PDF |

    ### 💡 文件命名规则
    ```
    【快来车-10.60元-1个行程】高德打车电子发票.pdf   ← 发票
    【快来车-10.60元-1个行程】高德打车电子行程单.pdf  ← 行程单
    （金额相同 → 自动配对为一组）
    ```
    """)

# 页脚
st.markdown("---")
st.caption("✅ 金额匹配配对 | 行程单时间排序 | 行程单在前发票在后 | pdfplumber解析")
