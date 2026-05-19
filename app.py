import streamlit as st
import PyPDF2
import io
import re
from datetime import datetime
from collections import defaultdict

# 页面配置
st.set_page_config(page_title="发票合并助手", page_icon="📄", layout="wide")
st.title("📄 发票合并助手")
st.subheader("智能排序：行程单在前 + 发票在后 + 按时间分组")

# 侧边栏：高级设置
with st.sidebar:
    st.header("⚙️ 排序规则")
    date_source = st.radio("📅 日期来源", ["文件名提取", "手动指定"], index=0)
    group_by_day = st.checkbox("📆 按天分组", value=True, help="相同日期的行程单+发票归为一组")
    st.info("💡 文件名建议格式：`20240315_行程单_北京-上海.pdf` 或 `发票_2024-03-15_餐饮.pdf`")

# 上传PDF文件
uploaded_files = st.file_uploader(
    "📁 上传PDF文件（支持多选）",
    type=["pdf"],
    accept_multiple_files=True,
    help="按住 Ctrl/Cmd 多选，或拖拽上传"
)

def extract_date_from_filename(filename):
    """从文件名提取日期，支持多种格式"""
    # 匹配格式：20240315 / 2024-03-15 / 2024.03.15 / 03152024
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
    """智能排序：按日期分组，组内行程单在前"""
    # 1. 构建文件信息列表
    file_info = []
    for f in files:
        filename = f.name
        file_date = extract_date_from_filename(filename) or datetime(2000, 1, 1).date()  # 无日期放最前
        file_type = classify_file(filename)
        # 类型排序权重：行程单(0) < 发票(1) < 其他(2)
        type_weight = {'行程单': 0, '发票': 1, '其他': 2}.get(file_type, 2)
        file_info.append({
            'file': f,
            'name': filename,
            'date': file_date,
            'type': file_type,
            'type_weight': type_weight
        })
    
    # 2. 按 (日期, 类型权重, 文件名) 排序
    sorted_files = sorted(file_info, key=lambda x: (x['date'], x['type_weight'], x['name']))
    
    # 3. 如果启用分组，确保同一天内行程单一定在发票前（二次校验）
    if group_by_day:
        result = []
        current_date = None
        day_group = []
        
        for item in sorted_files:
            if item['date'] != current_date and current_date is not None:
                # 处理上一组：确保行程单在前
                day_group.sort(key=lambda x: x['type_weight'])
                result.extend(day_group)
                day_group = []
            current_date = item['date']
            day_group.append(item)
        
        # 处理最后一组
        if day_group:
            day_group.sort(key=lambda x: x['type_weight'])
            result.extend(day_group)
        
        return [item['file'] for item in result]
    
    return [item['file'] for item in sorted_files]

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
    
    # 文件预览 + 手动调整（可选）
    with st.expander("📋 文件预览与手动调整", expanded=False):
        col1, col2 = st.columns([3, 1])
        with col1:
            for i, f in enumerate(uploaded_files):
                file_date = extract_date_from_filename(f.name)
                file_type = classify_file(f.name)
                st.text(f"{i+1}. [{file_type}] {f.name} → {file_date or '未知日期'}")
        with col2:
            if st.button("🔄 按规则自动排序", use_container_width=True):
                st.session_state.sorted = True
                st.rerun()
    
    # 排序 + 合并
    if st.button("🔗 开始智能合并", type="primary", use_container_width=True):
        try:
            with st.spinner("🔄 正在分析文件并排序..."):
                sorted_files = smart_sort_files(uploaded_files)
            
            # 显示排序结果
            with st.expander("✅ 合并顺序预览", expanded=True):
                for i, f in enumerate(sorted_files):
                    file_type = classify_file(f.name)
                    file_date = extract_date_from_filename(f.name)
                    icon = "🎫" if file_type == "行程单" else "🧾" if file_type == "发票" else "📄"
                    st.text(f"{i+1}. {icon} [{file_type}] {f.name} ({file_date or '未知日期'})")
            
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
                st.success("✨ 合并成功！行程单在前 + 发票在后 + 按时间排序")
                
        except Exception as e:
            st.error(f"❌ 处理失败：{str(e)}")
            st.exception(e)
else:
    st.info("👆 请上传PDF格式的文件开始使用")

# 页脚
st.markdown("---")
st.caption("✅ 智能排序逻辑 | 行程单优先 | 按时间分组 | PyPDF2后端")
