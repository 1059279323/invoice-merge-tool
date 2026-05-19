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
            "1️⃣ 按日期升序分组（未识别日期排最后）\n"
            "2️⃣ 同日期内：行程单与发票按文件名排序后交替配对\n"
            "   `行程单[0] → 发票[0] → 行程单[1] → 发票[1] ...`\n"
            "3️⃣ **命名建议**：同一次行程的行程单和发票保持相同标识，如：\n"
            "   `20240425_上午_行程单_机票.pdf`\n"
            "   `20240425_上午_发票_机票.pdf`")

# 上传PDF文件
uploaded_files = st.file_uploader(
    "📁 上传PDF文件（支持多选）",
    type=["pdf"],
    accept_multiple_files=True,
    help="按住 Ctrl/Cmd 多选，或拖拽上传"
)

def extract_date_from_filename(filename):
    """从文件名提取日期，兼容多种格式与中文"""
    # 统一替换中文日期标识，便于正则匹配
    clean_name = re.sub(r'[年月日]', '-', filename)
    
    # 1. 优先匹配 YYYY-MM-DD / YYYY.MM.DD / YYYYMMDD
    m = re.search(r'(\d{4})[-.](\d{1,2})[-.](\d{1,2})', clean_name) or \
        re.search(r'(\d{4})(\d{2})(\d{2})', clean_name)
    if m:
        try: return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except: pass
        
    # 2. 匹配 DD-MM-YYYY / MM-DD-YYYY / DDMYYYY 等
    m = re.search(r'(\d{1,2})[-.](\d{1,2})[-.](\d{4})', clean_name) or \
        re.search(r'(\d{2})(\d{2})(\d{4})', clean_name)
    if m:
        p1, p2, p3 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # 智能推断：>12 的必然是日或月
        if p1 > 12: d, mon, y = p1, p2, p3
        elif p2 > 12: d, mon, y = p2, p1, p3
        else: d, mon, y = p2, p1, p3  # 默认按 MMDDYYYY 处理
        try: return datetime(y, mon, d).date()
        except: pass
        
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
    file_info = []
    unknown_dates = []
    
    for f in files:
        filename = f.name
        file_date = extract_date_from_filename(filename)
        file_type = classify_file(filename)
        
        if file_date is None:
            unknown_dates.append({'file': f, 'name': filename, 'type': file_type, 'date': None})
        else:
            file_info.append({'file': f, 'name': filename, 'type': file_type, 'date': file_date})

    # 按日期排序（有效日期在前，按时间升序）
    file_info.sort(key=lambda x: x['date'])
    
    result = []
    # 按日期分组处理
    for date, group in groupby(file_info, key=lambda x: x['date']):
        group_list = list(group)
        
        itineraries = sorted([i for i in group_list if i['type'] == '行程单'], key=lambda x: x['name'])
        invoices = sorted([i for i in group_list if i['type'] == '发票'], key=lambda x: x['name'])
        others = sorted([i for i in group_list if i['type'] == '其他'], key=lambda x: x['name'])
        
        # 🔄 核心：交替合并行程单和发票
        max_len = max(len(itineraries), len(invoices))
        for i in range(max_len):
            if i < len(itineraries): result.append(itineraries[i])
            if i < len(invoices): result.append(invoices[i])
        result.extend(others)
        
    # 未识别日期的文件固定排在最后
    unknown_dates.sort(key=lambda x: x['name'])
    result.extend(unknown_dates)
    
    return [item['file'] for item in result]

def merge_pdfs(pdf_files):
    """合并PDF文件"""
    if not pdf_files: return None
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
                        'date': extract_date_from_filename(f.name),
                        'type': classify_file(f.name)
                    })
                
                # 按日期分组渲染
                has_valid = [p for p in preview_info if p['date'] is not None]
                has_unknown = [p for p in preview_info if p['date'] is None]
                
                for date, group in groupby(has_valid, key=lambda x: x['date']):
                    st.markdown(f"**📅 {date}**")
                    for item in group:
                        icon = "🎫" if item['type'] == "行程单" else "🧾" if item['type'] == "发票" else "📄"
                        st.text(f"   {icon} [{item['type']}] {item['file'].name}")
                    st.divider()
                    
                if has_unknown:
                    st.markdown("**📅 未知日期（排最后）**")
                    for item in has_unknown:
                        icon = "🎫" if item['type'] == "行程单" else "🧾" if item['type'] == "发票" else "📄"
                        st.text(f"   {icon} [{item['type']}] {item['file'].name}")
                    st.warning("⚠️ 以上文件名未提取到有效日期，建议改为 `YYYYMMDD_xxx.pdf` 格式")

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
                st.success("✨ 合并成功！按日期升序分组，组内行程单+发票交替排列")

        except Exception as e:
            st.error(f"❌ 处理失败：{str(e)}")
            st.exception(e)
else:
    st.info("👆 请上传PDF格式的文件开始使用")

# 页脚
st.markdown("---")
st.caption("✅ 智能日期解析 | 按天分组 | 组内行程单在前+发票在后 | PyPDF2后端")
