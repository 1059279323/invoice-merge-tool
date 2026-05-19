import streamlit as st
import PyPDF2
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
    1. 📅 从文件名提取日期
    2. 🎫 行程单为主键，按日期升序排列
    3. 🧾 同日期的发票配对到对应行程单后面
    4. 📦 最终顺序：`行程单A → 发票A → 行程单B → 发票B → ...`
    5. ❓ 未能配对的文件排在最后
    """)
    st.info("💡 文件名建议包含日期，例如：\n`20240315_行程单.pdf`\n`发票_2024-03-15.pdf`")
    
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

def extract_date_from_filename(filename):
    """从文件名提取日期，支持多种格式"""
    patterns = [
        r'(\d{4})[-._]?(\d{2})[-._]?(\d{2})',  # 2024-03-15 / 20240315
        r'(\d{2})[-._](\d{2})[-._](\d{4})',     # 03-15-2024
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            groups = match.groups()
            try:
                if len(groups[0]) == 4:  # YYYY 开头
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2])).date()
                else:  # MM-DD-YYYY
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
    """构建每个文件的信息字典"""
    file_info_list = []
    for f in files:
        date = extract_date_from_filename(f.name)
        ftype = classify_file(f.name)
        file_info_list.append({
            'file': f,
            'name': f.name,
            'date': date,
            'type': ftype,
        })
    return file_info_list


def pair_and_sort(file_info_list):
    """
    配对逻辑：
    - 以行程单为主键，按日期升序排列
    - 同一日期的发票配对到该日期行程单后面
    - 多张行程单同日期：按文件名顺序排，每张行程单各配对一张发票（先来先配）
    - 多张发票同日期：依次配对同日期的行程单，多余发票放到未配对区
    - 未配对文件追加到末尾
    返回：有序的 file 对象列表，以及配对详情（用于预览）
    """

    itineraries = [f for f in file_info_list if f['type'] == '行程单']
    invoices    = [f for f in file_info_list if f['type'] == '发票']
    others      = [f for f in file_info_list if f['type'] == '其他']

    # 按日期升序、同日期按文件名排序
    itineraries_sorted = sorted(
        itineraries,
        key=lambda x: (x['date'] or datetime.max.date(), x['name'])
    )
    invoices_sorted = sorted(
        invoices,
        key=lambda x: (x['date'] or datetime.max.date(), x['name'])
    )

    # 用日期为 key，把发票按日期分桶（队列形式，先进先出）
    invoice_bucket = defaultdict(list)
    for inv in invoices_sorted:
        invoice_bucket[inv['date']].append(inv)

    # 已使用的发票集合（避免重复配对）
    used_invoices = set()

    result_groups  = []   # 每组：{'itinerary': info, 'invoice': info or None}
    paired_invoice_ids = set()

    # 遍历行程单，尝试在同日期发票桶里取一张配对
    for itin in itineraries_sorted:
        itin_date = itin['date']
        paired_inv = None

        bucket = invoice_bucket.get(itin_date, [])
        for inv in bucket:
            inv_id = id(inv['file'])
            if inv_id not in paired_invoice_ids:
                paired_inv = inv
                paired_invoice_ids.add(inv_id)
                break

        result_groups.append({
            'itinerary': itin,
            'invoice': paired_inv
        })

    # 未配对的发票（没有对应日期行程单，或同日期发票多于行程单）
    unpaired_invoices = [
        inv for inv in invoices_sorted
        if id(inv['file']) not in paired_invoice_ids
    ]

    # 未配对的其他文件
    others_sorted = sorted(others, key=lambda x: (x['date'] or datetime.max.date(), x['name']))

    # ── 最终文件列表 ──
    ordered_files = []
    group_preview = []   # 用于界面预览

    for group in result_groups:
        itin = group['itinerary']
        inv  = group['invoice']

        ordered_files.append(itin['file'])
        preview_entry = {
            'itinerary': itin,
            'invoice': inv,
            'paired': inv is not None
        }

        if inv:
            ordered_files.append(inv['file'])

        group_preview.append(preview_entry)

    # 追加未配对发票
    unpaired_preview = []
    for inv in unpaired_invoices:
        ordered_files.append(inv['file'])
        unpaired_preview.append(inv)

    # 追加其他文件
    other_preview = []
    for o in others_sorted:
        ordered_files.append(o['file'])
        other_preview.append(o)

    return ordered_files, group_preview, unpaired_preview, other_preview


def merge_pdfs(pdf_files):
    """合并PDF文件列表"""
    if not pdf_files:
        return None
    merger = PyPDF2.PdfMerger()
    for pdf in pdf_files:
        pdf.seek(0)   # 确保指针在头部
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

    # ── 上传文件概览 ──
    with st.expander("📋 已上传文件列表", expanded=False):
        header_cols = st.columns([1, 4, 2, 2])
        header_cols[0].markdown("**序号**")
        header_cols[1].markdown("**文件名**")
        header_cols[2].markdown("**识别类型**")
        header_cols[3].markdown("**识别日期**")
        st.markdown("---")

        for i, info in enumerate(file_info_list):
            cols = st.columns([1, 4, 2, 2])
            icon = "🎫" if info['type'] == '行程单' else "🧾" if info['type'] == '发票' else "📄"
            cols[0].write(i + 1)
            cols[1].write(info['name'])
            cols[2].write(f"{icon} {info['type']}")
            cols[3].write(str(info['date']) if info['date'] else "⚠️ 未识别")

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
                        paired = group['paired']

                        # 分组标题
                        date_str = str(itin['date']) if itin['date'] else '未知日期'
                        st.markdown(f"**第 {i+1} 组 · {date_str}**")

                        # 行程单行
                        c1, c2, c3 = st.columns([1, 5, 2])
                        c1.write("🎫")
                        c2.write(itin['name'])
                        c3.write(f"`行程单`")

                        # 发票行
                        c1, c2, c3 = st.columns([1, 5, 2])
                        if paired:
                            c1.write("🧾")
                            c2.write(inv['name'])
                            c3.write(f"`发票`")
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

                # 完整顺序编号
                st.markdown("### 📑 最终合并顺序")
                for idx, f in enumerate(ordered_files):
                    ftype = classify_file(f.name)
                    fdate = extract_date_from_filename(f.name)
                    icon  = "🎫" if ftype == '行程单' else "🧾" if ftype == '发票' else "📄"
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

                # 统计摘要
                paired_count   = sum(1 for g in group_preview if g['paired'])
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
    # 空状态引导
    st.info("👆 请上传 PDF 格式的文件开始使用")
    st.markdown("""
    ### 📌 使用说明
    
    | 步骤 | 操作 |
    |------|------|
    | 1️⃣ | 上传所有行程单和发票 PDF |
    | 2️⃣ | 查看文件识别结果是否正确 |
    | 3️⃣ | 点击「开始智能配对并合并」 |
    | 4️⃣ | 预览配对分组结果 |
    | 5️⃣ | 下载合并后的 PDF |
    
    ### 📝 文件命名建议
    ```
    20240315_行程单_北京-上海.pdf   ← 行程单
    20240315_发票_机票.pdf          ← 对应发票（同日期自动配对）
    
    20240320_行程单_上海-广州.pdf
    20240320_发票_机票.pdf
    ```
    """)

# 页脚
st.markdown("---")
st.caption("✅ 一对一配对 | 行程单在前发票在后 | 按时间升序分组 | PyPDF2 后端")
