import streamlit as st
import PyPDF2
import io
import re
from datetime import datetime
from itertools import groupby

# 页面配置
st.set_page_config(page_title="发票合并助手", page_icon="📄", layout="wide")
st.title("📄 发票合并助手")
st.subheader("智能排序：按日期分组 → 组内行程单+发票交替配对")

# 侧边栏：使用说明
with st.sidebar:
    st.header("⚙️ 排序规则")
    st.info("💡 **核心逻辑**：\n"
            "1️⃣ 按日期升序分组（无日期排最后）\n"
            "2️⃣ 同日期内：行程单与发票按文件名排序后交替配对\n"
            "   `行程单[0] → 发票[0] → 行程单[1] → 发票[1] ...`\n"
            "3️⃣ **命名建议**：保持行程单与发票非类型部分一致，如：\n"
            "   `20240315_上午_行程单_机票.pdf`\n"
            "   `20240315_上午_发票_机票.pdf`")

# 上传PDF文件
uploaded_files = st.file_uploader(
    "📁 上传PDF文件（支持多选）",
    type=["pdf"],
    accept_multiple_files=True,
    help="按住 Ctrl/Cmd 多选，或拖拽上传"
)

def extract_date_from_filename(filename):
    """从文件名提取日期，支持多种格式"""
    patterns = [
        r'(\d{4})[-.]?(\d{2})[-.]?(\d{2})',  # 2024-03-15 或 20240315
        r'(\d{2})[-.]?(\d{2})[-.]?(\d{4})',  # 03-15-2024
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            groups = match.groups()
            if len(groups[0]) == 4:  # YYYYMMDD
                try:
                    return datetime(int(groups[0]), int(groups[1]), int(groups[2])).date()
                except:
                    continue
            else:  # MMDDYYYY
                try:
                    return datetime(int(groups[2]), int(groups[0]), int(groups[1])).date()
                except:
                    continue
    return None

def classify_file(filename):
    """分类文件：行程单 / 发票 / 其他"""
    filename_lower = filename.lower()
    if any(kw in filename_lower for kw in ['行程单', 'itinerary', 'flight', 'train', 'ticket']):
        return '行程单'
    elif any(kw in filename_lower for kw in ['发票', 'invoice', 'receipt', 'bill']):
        return '发票'
    else:
        return '其他'

def smart_sort_files(files):
    """智能排序：按日期分组，每组内行程单+发票交替配对排列"""
    # 1. 构建文件信息列表
    file_info = []
    for f in files:
        filename = f.name
        # 无日期的文件排到最后（用 9999-12-31 占位）
        file_date = extract_date_from_filename(filename) or datetime(9999, 12, 31).date()
        file_type = classify_file(filename)
        file_info.append({
            'file': f,
            'name': filename,
            'date': file_date,
            'type': file_type
        })

    # 2. 先按 (日期, 文件名) 排序，为分组做准备
    sorted_by_date_name = sorted(file_info, key=lambda x: (x['date'], x['name']))

    result = []
    # 3. 按日期分组处理
    for date, group in groupby(sorted_by_date_name, key=lambda x: x['date']):
        group_list = list(group)

        # 分离三类文件
        itineraries = [item for item in group_list if item['type'] == '行程单']
        invoices = [item for item in group_list if item['type'] == '发票']
        others = [item for item in group_list if item['type'] == '其他']

        # 确保组内按文件名排序（保证配对顺序一致）
        itineraries.sort(key=lambda x: x['name'])
        invoices.sort(key=lambda x: x['name'])
        others.sort(key=lambda x: x['name'])

        # 🔄 核心：交替合并行程单和发票（模拟"上午一对+下午一对"）
        max_len = max(len(itineraries), len(invoices))
        for i in range(max_len):
            if i < len(itineraries):
                result.append(itineraries[i])
            if i < len(invoices):
                result.append(invoices[i])

        # 剩余的"其他"文件放该日期组最后
        result.extend(others)

    return [item['file'] for item in result]

def merge_pdfs(pdf_files):
    """合并PDF文件"""
    if not pdf_files:
        return None
    merger = PyPDF2.PdfMerger()
    for pdf in pdf_files:
        merger.append(pdf)
    output = io.BytesIO()
    merger.write(output)
    output.seek(0)
    merger.close()
    return output

# 主逻辑
if uploaded_files:
    st.success(f"✅ 已上传 {len(uploaded_files)} 个文件")

    # 原始文件预览
    with st.expander("📋 上传文件原始列表", expanded=False):
        for i, f in enumerate(uploaded_files):
            file_date = extract_date_from_filename(f.name)
            file_type = classify_file(f.name)
            st.text(f"{i+1}. [{file_type}] {f.name} → {file_date or '未知日期'}")

    # 排序 + 合并按钮
    if st.button("🔗 开始智能合并", type="primary", use_container_width=True):
        try:
            with st.spinner("🔄 正在分析文件并排序..."):
                sorted_files = smart_sort_files(uploaded_files)

            # 显示排序结果（按天分组展示）
            with st.expander("✅ 合并顺序预览", expanded=True):
                preview_info = []
                for f in sorted_files:
                    preview_info.append({
                        'file': f,
                        'date': extract_date_from_filename(f.name) or datetime(9999, 12, 31).date(),
                        'type': classify_file(f.name)
                    })

                for date, group in groupby(preview_info, key=lambda x: x['date']):
                    group_list = list(group)
                    date_str = str(date) if date.year < 9999 else "📅 未知日期"
                    st.markdown(f"**{date_str}**")
                    for item in group_list:
                        f, file_type = item['file'], item['type']
                        icon = "🎫" if file_type == "行程单" else "🧾" if file_type == "发票" else "📄"
                        st.text(f"   {icon} [{file_type}] {f.name}")
                    st.divider()

            # 执行合并
            with st.spinner("📦 正在合并PDF..."):
                merged_pdf = merge_pdfs(sorted_files)

            if merged_pdf:
                st.download_button(
                    label="📥 下载合并后的发票包",
                    data=merged_pdf,
                    file_name=f"发票合并包_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                st.balloons()
                st.success("✨ 合并成功！按日期分组，组内行程单在前+发票在后交替排列")

        except Exception as e:
            st.error(f"❌ 处理失败：{str(e)}")
            st.exception(e)
else:
    st.info("👆 请上传PDF格式的文件开始使用")

# 页脚
st.markdown("---")
st.caption("✅ 智能排序逻辑 | 按日期分组 | 组内行程单在前+发票在后 | PyPDF2后端")
