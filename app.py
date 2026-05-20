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
st.subheader("智能配对：行程单 + 发票 一对一分组，按时间升序排列")

# 侧边栏说明
with st.sidebar:
    st.header("⚙️ 排序规则")
    st.markdown("""
    **配对逻辑：**
    1. 📅 从PDF内容提取行程单上车时间
    2. 🎫 行程单为主键，按日期升序排列
    3. 🧾 同日期的发票配对到对应行程单后面
    4. 📦 最终顺序：`行程单A → 发票A → 行程单B → 发票B → ...`
    5. ❓ 未能配对的文件排在最后
    """)
    st.info("💡 支持滴滴、出租车、火车、机票等行程单格式")

    st.markdown("---")
    st.header("📋 分类关键词")
    st.markdown("""
    **行程单关键词：**
    行程单, itinerary, flight, train, ticket

    **发票关键词：**
    发票, invoice, receipt, bill
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

def extract_date_from_pdf_content(file):
    """
    从PDF内容中提取日期，支持多种行程单格式
    优先级：上车时间 > 出发时间 > 乘车时间 > 任意日期
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
            return None

        # ── 优先匹配：带关键词的时间字段 ──
        # 匹配格式举例：
        # 上车时间：2024-03-15 08:30
        # 出发时间：2024年03月15日 08:30
        # 乘车时间：2024.03.15 08:30
        # 行程日期：2024-03-15
        keyword_patterns = [
            # 关键词 + 年月日格式
            r'(?:上车|出发|乘车|行程|起程|登机|发车|开车|搭乘|乘坐|行程日期|出行时间|乘机|乘车日期)'
            r'[时间日期：:\s]*'
            r'(\d{4})[年\-./](\d{1,2})[月\-./](\d{1,2})',

            # 关键词 + 中文年月日
            r'(?:上车|出发|乘车|行程|起程|登机|发车|开车|搭乘|乘坐|行程日期|出行时间|乘机|乘车日期)'
            r'[时间日期：:\s]*'
            r'(\d{4})年(\d{1,2})月(\d{1,2})日',

            # 日期在关键词后面（换行或空格隔开）
            r'(?:上车时间|出发时间|乘车时间|行程日期|出行日期|乘机日期|发车时间)[^\d]{0,10}'
            r'(\d{4})[年\-./](\d{1,2})[月\-./](\d{1,2})',
        ]

        for pattern in keyword_patterns:
            match = re.search(pattern, full_text)
            if match:
                y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                try:
                    return datetime(y, m, d).date()
                except ValueError:
                    continue

        # ── 次优匹配：常见日期格式（无关键词） ──
        general_patterns = [
            r'(\d{4})年(\d{1,2})月(\d{1,2})日',      # 2024年3月15日
            r'(\d{4})-(\d{1,2})-(\d{1,2})',           # 2024-03-15
            r'(\d{4})\.(\d{1,2})\.(\d{1,2})',         # 2024.03.15
            r'(\d{4})/(\d{1,2})/(\d{1,2})',           # 2024/03/15
            r'(\d{4})(\d{2})(\d{2})',                  # 20240315
        ]

        candidates = []
        for pattern in general_patterns:
            for match in re.finditer(pattern, full_text):
                y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                try:
                    date = datetime(y, m, d).date()
                    # 过滤明显不合理的日期（太早或未来太远）
                    if datetime(2000, 1, 1).date() <= date <= datetime(2099, 12, 31).date():
                        candidates.append(date)
                except ValueError:
                    continue

        if candidates:
            # 返回最早出现的合理日期
            return candidates[0]

    except Exception as e:
        st.warning(f"⚠️ PDF内容解析失败：{e}")

    return None


def extract_date_from_filename(filename):
    """从文件名提取日期（作为兜底）"""
    patterns = [
        r'(\d{4})[-._]?(\d{2})[-._]?(\d{2})',
        r'(\d{2})[-._](\d{2})[-._](\d{4})',
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            groups = match.groups()
            try:
                if len(groups[0]) == 4:
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2])).date()
                else:
                    return datetime(int(groups[2]), int(groups[0]), int(groups[1])).date()
            except ValueError:
                continue
    return None


def classify_file(filename):
    """分类文件：行程单 / 发票 / 其他"""
    name_lower = filename.lower()
    if any(kw in name_lower for kw in ['行程单', 'itinerary', 'flight', 'train', 'ticket']):
        return '行程单'
    elif any(kw in name_lower for kw in ['发票', 'invoice', 'receipt', 'bill']):
        return '发票'
    return '其他'


def build_file_info(files):
    """
    构建文件信息：
    - 行程单：优先从PDF内容提取日期，失败则从文件名提取
    - 发票：从文件名提取日期
    """
    file_info_list = []
    progress = st.progress(0, text="正在读取文件信息...")

    for i, f in enumerate(files):
        ftype = classify_file(f.name)
        date = None
        date_source = "未识别"

        if ftype == '行程单':
            # 优先从PDF内容提取
            date = extract_date_from_pdf_content(f)
            if date:
                date_source = "PDF内容"
            else:
                # 兜底从文件名提取
                date = extract_date_from_filename(f.name)
                if date:
                    date_source = "文件名"
                else:
                    date_source = "⚠️ 未识别"
        else:
            date = extract_date_from_filename(f.name)
            date_source = "文件名" if date else "⚠️ 未识别"

        file_info_list.append({
            'file': f,
            'name': f.name,
            'date': date,
            'type': ftype,
            'date_source': date_source,
        })

        progress.progress((i + 1) / len(files), text=f"正在读取：{f.name}")

    progress.empty()
    return file_info_list


def pair_and_sort(file_info_list):
    """
    配对逻辑：
    - 以行程单为主键，按日期升序排列
    - 同一日期的发票配对到该日期行程单后面
    - 未配对文件追加到末尾
    """
    itineraries = [f for f in file_info_list if f['type'] == '行程单']
    invoices    = [f for f in file_info_list if f['type'] == '发票']
    others      = [f for f in file_info_list if f['type'] == '其他']

    # 升序排列，无日期的放最后
    itineraries_sorted = sorted(
        itineraries,
        key=lambda x: (x['date'] or datetime.max.date(), x['name']),
        reverse=False
    )
    invoices_sorted = sorted(
        invoices,
        key=lambda x: (x['date'] or datetime.max.date(), x['name']),
        reverse=False
    )

    # 发票按日期分桶
    invoice_bucket = defaultdict(list)
    for inv in invoices_sorted:
        invoice_bucket[inv['date']].append(inv)

    paired_invoice_ids = set()
    result_groups = []

    for itin in itineraries_sorted:
        itin_date = itin['date']
        paired_inv = None

        bucket = invoice_bucket.get(itin_date, [])
        for inv in bucket:
            if id(inv['file']) not in paired_invoice_ids:
                paired_inv = inv
                paired_invoice_ids.add(id(inv['file']))
                break

        result_groups.append({
            'itinerary': itin,
            'invoice': paired_inv
        })

    # 未配对发票
    unpaired_invoices = [
        inv for inv in invoices_sorted
        if id(inv['file']) not in paired_invoice_ids
    ]

    # 其他文件
    others_sorted = sorted(
        others,
        key=lambda x: (x['date'] or datetime.max.date(), x['name'])
    )

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

    # 构建文件信息（含PDF内容解析）
    file_info_list = build_file_info(uploaded_files)

    # ── 上传文件概览 ──
    with st.expander("📋 已上传文件列表", expanded=True):
        header_cols = st.columns([1, 4, 2, 2, 2])
        header_cols[0].markdown("**序号**")
        header_cols[1].markdown("**文件名**")
        header_cols[2].markdown("**识别类型**")
        header_cols[3].markdown("**识别日期**")
        header_cols[4].markdown("**日期来源**")
        st.markdown("---")

        for i, info in enumerate(file_info_list):
            cols = st.columns([1, 4, 2, 2, 2])
            icon = "🎫" if info['type'] == '行程单' else "🧾" if info['type'] == '发票' else "📄"
            cols[0].write(i + 1)
            cols[1].write(info['name'])
            cols[2].write(f"{icon} {info['type']}")
            cols[3].write(str(info['date']) if info['date'] else "⚠️ 未识别")
            cols[4].write(info['date_source'])

    # ── 合并按钮 ──
    if st.button("🔗 开始智能配对并合并", type="primary", use_container_width=True):
        try:
            with st.spinner("🔄 正在分析配对关系..."):
                ordered_files, group_preview, unpaired_invoices, other_files = pair_and_sort(file_info_list)

            # ── 配对结果预览 ──
            with st.expander("✅ 配对分组预览（最终合并顺序）", expanded=True):

                if group_preview:
                    st.markdown("### 🗂️ 已配对分组")
                    for i, group in enumerate(group_preview):
                        itin = group['itinerary']
                        inv  = group['invoice']
                        paired = group['invoice'] is not None

                        date_str = str(itin['date']) if itin['date'] else '未知日期'
                        st.markdown(f"**第 {i+1} 组 · {date_str}**")

                        c1, c2, c3 = st.columns([1, 5, 2])
                        c1.write("🎫")
                        c2.write(itin['name'])
                        c3.write("`行程单`")

                        c1, c2, c3 = st.columns([1, 5, 2])
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
                        c2.write(f"{inv['name']}  —  {inv['date'] or '未知日期'}")

                if other_files:
                    st.markdown("### 📄 其他文件（追加到末尾）")
                    for o in other_files:
                        c1, c2 = st.columns([1, 7])
                        c1.write("📄")
                        c2.write(f"{o['name']}  —  {o['date'] or '未知日期'}")

                st.markdown("### 📑 最终合并顺序")
                for idx, f in enumerate(ordered_files):
                    ftype = classify_file(f.name)
                    icon  = "🎫" if ftype == '行程单' else "🧾" if ftype == '发票' else "📄"
                    # 从已解析的info里取日期
                    info = next((x for x in file_info_list if x['file'] == f), None)
                    fdate = info['date'] if info else None
                    st.text(f"  {idx+1:>2}. {icon} [{ftype}]  {f.name}  ({fdate or '未知日期'})")

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
                unpaired_count = len(unpaired_invoices)
                itin_count     = len(group_preview)
                st.success(
                    f"✨ 合并成功！共 {itin_count} 张行程单，"
                    f"成功配对 {paired_count} 组，"
                    f"未配对发票 {unpaired_count} 张"
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
    | 2️⃣ | 系统自动解析行程单PDF内容提取上车时间 |
    | 3️⃣ | 点击「开始智能配对并合并」 |
    | 4️⃣ | 预览配对分组结果 |
    | 5️⃣ | 下载合并后的 PDF |

    ### 🚗 支持的行程单类型
    - 滴滴出行行程单
    - 出租车票
    - 火车票行程单
    - 机票行程单
    - 其他包含日期的行程单
    """)

# 页脚
st.markdown("---")
st.caption("✅ PDF内容解析日期 | 一对一配对 | 行程单在前发票在后 | 按时间升序分组")
