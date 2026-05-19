import streamlit as st
import PyPDF2
import io
import re
from datetime import datetime

# 页面配置
st.set_page_config(page_title="发票合并助手", page_icon="📄", layout="wide")
st.title("📄 发票合并助手")
st.subheader("稳定排序：按日期升序 → 同日按上午/下午分组 → 行程单优先 → 按文件名")

# 侧边栏说明
with st.sidebar:
    st.header("⚙️ 排序逻辑说明")
    st.info("💡 **排序规则（新版）**：\n"
            "1️⃣ 第一优先级：日期升序（25号一定在27号前）\n"
            "2️⃣ 第二优先级：同一天内按时间段（上午 → 下午 → 未知）\n"
            "3️⃣ 第三优先级：文件类型（行程单 → 发票 → 其他）\n"
            "4️⃣ 第四优先级：文件名自然排序\n"
            "✨ 文件名请包含“上午/下午”或时间（如 08:30）以便自动识别")

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

def extract_period_from_filename(filename):
    """从文件名提取时间段：上午/下午/未知"""
    # 优先匹配中文或英文上午/下午关键字
    if re.search(r'(上午|am|a\.m\.)', filename, re.IGNORECASE):
        return '上午'
    if re.search(r'(下午|pm|p\.m\.)', filename, re.IGNORECASE):
        return '下午'
    # 尝试匹配 HH:MM 格式时间
    time_match = re.search(r'(\d{1,2}):(\d{2})', filename)
    if time_match:
        hour = int(time_match.group(1))
        if 0 <= hour < 12:
            return '上午'
        elif 12 <= hour < 18:
            return '下午'
        else:
            return '下午'  # 晚间也归为下午处理
    return '未知'

def classify_file(filename):
    """文件类型分类"""
    name_lower = filename.lower()
    if any(k in name_lower for k in ['行程单', 'itinerary', 'flight', 'train', 'ticket']):
        return '行程单'
    if any(k in name_lower for k in ['发票', 'invoice', 'receipt', 'bill']):
        return '发票'
    return '其他'

def smart_sort_files(files):
    """核心排序函数：按日期、时间段、类型、文件名排序"""
    info_list = []
    period_weight_map = {'上午': 0, '下午': 1, '未知': 2}
    type_weight_map = {'行程单': 0, '发票': 1, '其他': 2}

    for f in files:
        date_obj = extract_date_from_filename(f.name)
        period = extract_period_from_filename(f.name)
        ftype = classify_file(f.name)
        weight = type_weight_map.get(ftype, 2)
        p_weight = period_weight_map.get(period, 2)
        sort_date = date_obj if date_obj else datetime(9999, 12, 31).date()
        
        info_list.append({
            'file': f,
            'name': f.name,
            'date': sort_date,
            'period': period,
            'type': ftype,
            'weight': weight,
            'period_weight': p_weight,
            'has_date': date_obj is not None
        })

    # 排序键：(日期, 时间段权重, 类型权重, 文件名)
    sorted_list = sorted(info_list, key=lambda x: (x['date'], x['period_weight'], x['weight'], x['name']))
    return sorted_list

def merge_pdfs(pdf_files):
    """合并PDF"""
    if not pdf_files:
        return None
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

            # 预览排序结果（按日期+时间段分组显示，直观验证）
            with st.expander("✅ 最终合并顺序预览", expanded=True):
                current_group = None
                for item in sorted_info:
                    date_str = str(item['date']) if item['has_date'] else "📅 未知日期"
                    period_str = item['period']
                    group_key = (date_str, period_str)
                    if group_key != current_group:
                        current_group = group_key
                        st.markdown(f"**{date_str}  {period_str}**")
                    icon = "🎫" if item['type'] == "行程单" else "🧾" if item['type'] == "发票" else "📄"
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
                st.success("✨ 合并完成！顺序：日期升序 → 上午/下午 → 行程单优先 → 文件名")

        except Exception as e:
            st.error(f"❌ 处理失败：{e}")
            st.exception(e)
else:
    st.info("👆 请上传PDF文件开始使用")

st.markdown("---")
st.caption("✅ 采用复合稳定排序键 | 支持上下午分组 | PyPDF2后端")
