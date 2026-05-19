import streamlit as st
import PyPDF2
import io
import re
from datetime import datetime

# 页面配置
st.set_page_config(page_title="发票合并助手", page_icon="📄", layout="wide")
st.title("📄 发票合并助手")
st.subheader("稳定排序：按日期升序 → 同日期行程单在前 → 按文件名")

# 侧边栏说明
with st.sidebar:
    st.header("⚙️ 排序逻辑说明")
    st.info("💡 **采用财务标准稳定排序法**：\n"
            "1️⃣ 第一优先级：日期升序（25号一定在27号前）\n"
            "2️⃣ 第二优先级：文件类型（行程单 → 发票 → 其他）\n"
            "3️⃣ 第三优先级：文件名自然排序\n"
            "✅ 同一天内：所有行程单集中排在前，所有发票集中在后，彻底避免错位。")

# 上传区域
uploaded_files = st.file_uploader(
    "📁 上传PDF文件（支持多选）",
    type=["pdf"],
    accept_multiple_files=True,
    help="按住 Ctrl/Cmd 多选，或拖拽上传"
)

def extract_date_from_filename(filename):
    """高精度日期提取，避免过度推断导致错乱"""
    # 匹配 2024-04-25 / 20240425 / 2024年04月25日 / 2024.04.25
    match = re.search(r'(\d{4})[-年.月]?(\d{2})[-月.日]?(\d{2})', filename)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except ValueError:
            pass
    return None

def classify_file(filename):
    """文件类型分类"""
    name_lower = filename.lower()
    if any(k in name_lower for k in ['行程单', 'itinerary', 'flight', 'train', 'ticket']):
        return '行程单'
    if any(k in name_lower for k in ['发票', 'invoice', 'receipt', 'bill']):
        return '发票'
    return '其他'

def smart_sort_files(files):
    """核心排序函数：单一稳定键排序"""
    info_list = []
    for f in files:
        date_obj = extract_date_from_filename(f.name)
        ftype = classify_file(f.name)
        # 类型权重：行程单(0) < 发票(1) < 其他(2)
        weight = {'行程单': 0, '发票': 1, '其他': 2}.get(ftype, 2)
        # 无日期文件统一排在最后
        sort_date = date_obj if date_obj else datetime(9999, 12, 31).date()
        
        info_list.append({
            'file': f,
            'name': f.name,
            'date': sort_date,
            'type': ftype,
            'weight': weight,
            'has_date': date_obj is not None
        })

    # 【核心】一行完成稳定排序：(日期, 类型权重, 文件名)
    sorted_list = sorted(info_list, key=lambda x: (x['date'], x['weight'], x['name']))
    return sorted_list

def merge_pdfs(pdf_files):
    """合并PDF"""
    if not pdf_files: return None
    merger = PyPDF2.PdfMerger()
    for pdf in pdf_files:
        merger.append(pdf)
    out = io.BytesIO()
    merger.write(out)
    out.seek(0)
    merger.close()
    return out

# 主逻辑
if uploaded_files:
    st.success(f"✅ 已上传 {len(uploaded_files)} 个文件")

    if st.button("🔗 开始智能合并", type="primary", use_container_width=True):
        try:
            with st.spinner("🔄 正在按规则排序..."):
                sorted_info = smart_sort_files(uploaded_files)

            # 预览排序结果（按天分组显示，直观验证）
            with st.expander("✅ 最终合并顺序预览", expanded=True):
                current_date = None
                for item in sorted_info:
                    if item['date'] != current_date:
                        current_date = item['date']
                        date_str = str(current_date) if item['has_date'] else "📅 未知日期（固定排最后）"
                        st.markdown(f"**{date_str}**")
                    icon = "🎫" if item['type']=="行程单" else "🧾" if item['type']=="发票" else "📄"
                    st.text(f"   {icon} [{item['type']}] {item['name']}")
                st.divider()

            # 执行合并
            with st.spinner("📦 正在合并PDF..."):
                sorted_files = [item['file'] for item in sorted_info]
                merged_pdf = merge_pdfs(sorted_files)

            if merged_pdf:
                st.download_button(
                    label="📥 下载合并后的PDF",
                    data=merged_pdf,
                    file_name=f"发票合并包_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                st.balloons()
                st.success("✨ 合并完成！顺序稳定：日期升序 → 行程单优先 → 文件名排序")
                
        except Exception as e:
            st.error(f"❌ 处理失败：{e}")
            st.exception(e)
else:
    st.info("👆 请上传PDF文件开始使用")

st.markdown("---")
st.caption("✅ 采用单一稳定排序键 | 彻底避免逻辑冲突导致错乱 | PyPDF2后端")
