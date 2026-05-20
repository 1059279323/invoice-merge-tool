import streamlit as st
import PyPDF2
import io
import re
from datetime import datetime
from collections import defaultdict

# 页面配置
st.set_page_config(page_title="发票合并助手", page_icon="📄", layout="wide")
st.title("📄 发票合并助手")
st.subheader("智能配对：日期+金额双重匹配，行程单与发票一对一，按时间升序排列")

# 侧边栏说明
with st.sidebar:
    st.header("⚙️ 配对规则")
    st.markdown("""
    **步骤：**
    1. 📅 提取日期（文件名优先，其次PDF内容）
    2. 💰 提取金额（支持中文“价税合计”、英文“Total”等）
    3. 🔗 配对条件：日期相同 **且** 金额一致（允许 ±0.01 元误差）
    4. 🎫 行程单为主键，按日期升序，同日期按文件名排序
    5. 🧾 发票紧跟配对行程单，多余发票/行程单移至末尾
    """)
    st.info("💡 金额提取关键字：`价税合计`、`合计金额`、`总金额`、`¥`、`$`等")
    
    st.markdown("---")
    st.header("📋 分类关键词")
    st.markdown("""
    **行程单：** 行程单, itinerary, flight, train, ticket  
    **发票：** 发票, invoice, receipt, bill
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

def extract_date_from_text(text):
    """从文本提取日期（中英文、多种格式）"""
    patterns = [
        r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
        r'(\d{4})[-./](\d{2})[-./](\d{2})(?!\d)',
        r'(\d{2})[-./](\d{2})[-./](\d{4})',
        r'(\d{1,2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*,?\s*(\d{4})',
        r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})',
    ]
    month_map = {
        'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
        'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12
    }
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            groups = match.groups()
            try:
                if len(groups) == 3:
                    g1,g2,g3 = groups
                    if '年' in pattern:
                        return datetime(int(g1), int(g2), int(g3)).date()
                    if g2.isalpha():
                        if g1.isdigit():
                            return datetime(int(g3), month_map[g2.lower()[:3]], int(g1)).date()
                        else:
                            return datetime(int(g3), month_map[g1.lower()[:3]], int(g2)).date()
                    if len(g1) == 4:
                        return datetime(int(g1), int(g2), int(g3)).date()
                    else:
                        return datetime(int(g3), int(g1), int(g2)).date()
            except (ValueError, KeyError):
                continue
    return None

def extract_amount_from_text(text):
    """
    从文本中提取金额（优先查找明确关键字，再回退到通用金额模式）
    返回 float，若未找到返回 None
    """
    # 优先级1：明确关键字 + 金额
    keyword_patterns = [
        r'(?:价税合计|合计金额|总金额|应付金额|发票金额|金额合计|价税合计人民币大写)?[:\s=]*[¥￥]?\s*([\d,]+\.?\d{0,2})\s*元?',
        r'Total\s*[:\s=]*[¥￥$]?\s*([\d,]+\.?\d{0,2})',
        r'Grand\s*Total\s*[:\s=]*[¥￥$]?\s*([\d,]+\.?\d{0,2})',
    ]
    for pat in keyword_patterns:
        match = re.search(pat, text, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace(',', '')
            try:
                return round(float(amount_str), 2)
            except ValueError:
                continue

    # 回退：查找任意 ¥1234.56 格式（取第一个）
    fallback = re.findall(r'[¥￥]\s*([\d,]+\.?\d{0,2})', text)
    if fallback:
        try:
            return round(float(fallback[0].replace(',', '')), 2)
        except ValueError:
            pass
    return None

def extract_date_from_filename(filename):
    return extract_date_from_text(filename)

def extract_date_from_pdf_content(file_obj):
    """从PDF内容提取日期"""
    try:
        pos = file_obj.tell()
        file_obj.seek(0)
        reader = PyPDF2.PdfReader(file_obj)
        full_text = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
        file_obj.seek(pos)
        if full_text.strip():
            return extract_date_from_text(full_text)
    except Exception:
        pass
    return None

def extract_amount_from_pdf_content(file_obj):
    """从PDF内容提取金额"""
    try:
        pos = file_obj.tell()
        file_obj.seek(0)
        reader = PyPDF2.PdfReader(file_obj)
        full_text = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
        file_obj.seek(pos)
        if full_text.strip():
            return extract_amount_from_text(full_text)
    except Exception:
        pass
    return None

def classify_file(filename):
    """分类：行程单/发票/其他"""
    name_lower = filename.lower()
    if any(kw in name_lower for kw in ['行程单', 'itinerary', 'flight', 'train', 'ticket']):
        return '行程单'
    elif any(kw in name_lower for kw in ['发票', 'invoice', 'receipt', 'bill']):
        return '发票'
    return '其他'

def build_file_info(files):
    """构建文件信息（日期+金额）"""
    file_info_list = []
    for f in files:
        date = extract_date_from_filename(f.name)
        ftype = classify_file(f.name)
        amount = None

        # 日期获取
        if date is None and ftype == '行程单':
            date = extract_date_from_pdf_content(f)
        elif date is None:
            date = extract_date_from_pdf_content(f)

        # 金额获取（优先PDF内容，因文件名通常无金额；也可从文件名尝试）
        # 先从内容提取，若失败可尝试文件名（极少数情况）
        amount = extract_amount_from_pdf_content(f)
        if amount is None:
            amount = extract_amount_from_text(f.name)  # 文件名中可能有金额

        file_info_list.append({
            'file': f,
            'name': f.name,
            'date': date,
            'type': ftype,
            'amount': amount,
        })
    return file_info_list

def pair_and_sort(file_info_list):
    """
    配对逻辑：日期相同 + 金额一致（允许误差0.01）
    行程单为主键，按日期升序，同日期按文件名排序
    多张行程单同日期、同金额：依次配对同金额发票（先到先得）
    发票金额不匹配或日期不匹配 → 未配对
    """
    itineraries = [f for f in file_info_list if f['type'] == '行程单']
    invoices    = [f for f in file_info_list if f['type'] == '发票']
    others      = [f for f in file_info_list if f['type'] == '其他']

    # 行程单排序
    itineraries_sorted = sorted(itineraries, key=lambda x: (x['date'] or datetime.max.date(), x['name']))
    invoices_sorted = sorted(invoices, key=lambda x: (x['date'] or datetime.max.date(), x['name']))

    # 构建可用发票池 (日期,金额) → 列表
    from collections import defaultdict
    invoice_pool = defaultdict(list)  # key: (date, amount)
    for inv in invoices_sorted:
        key = (inv['date'], inv['amount'])
        invoice_pool[key].append(inv)

    result_groups = []
    paired_invoice_ids = set()
    unpaired_invoices = []

    for itin in itineraries_sorted:
        itin_date = itin['date']
        itin_amount = itin['amount']
        paired_inv = None

        # 尝试匹配同日期同金额的发票
        if itin_date and itin_amount is not None:
            key = (itin_date, itin_amount)
            pool = invoice_pool.get(key, [])
            for inv in pool:
                inv_id = id(inv['file'])
                if inv_id not in paired_invoice_ids:
                    paired_inv = inv
                    paired_invoice_ids.add(inv_id)
                    break

        result_groups.append({
            'itinerary': itin,
            'invoice': paired_inv,
            'match_type': 'date+amount' if paired_inv else ('date_only' if False else 'no_match')
        })

    # 未配对发票
    for inv in invoices_sorted:
        if id(inv['file']) not in paired_invoice_ids:
            unpaired_invoices.append(inv)

    others_sorted = sorted(others, key=lambda x: (x['date'] or datetime.max.date(), x['name']))

    ordered_files = []
    group_preview = []

    for group in result_groups:
        itin = group['itinerary']
        inv  = group['invoice']
        ordered_files.append(itin['file'])
        if inv:
            ordered_files.append(inv['file'])
        group_preview.append(group)

    unpaired_preview = unpaired_invoices
    other_preview = others_sorted
    for inv in unpaired_invoices:
        ordered_files.append(inv['file'])
    for o in others_sorted:
        ordered_files.append(o['file'])

    return ordered_files, group_preview, unpaired_preview, other_preview

def merge_pdfs(pdf_files):
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
# 主界面
# ─────────────────────────────────────────────

if uploaded_files:
    st.success(f"✅ 已上传 {len(uploaded_files)} 个文件")

    file_info_list = build_file_info(uploaded_files)

    with st.expander("📋 已上传文件列表", expanded=False):
        header_cols = st.columns([1,4,2,2,2,2])
        header_cols[0].markdown("**序号**")
        header_cols[1].markdown("**文件名**")
        header_cols[2].markdown("**类型**")
        header_cols[3].markdown("**日期**")
        header_cols[4].markdown("**金额**")
        header_cols[5].markdown("**金额来源**")
        st.markdown("---")

        for i, info in enumerate(file_info_list):
            cols = st.columns([1,4,2,2,2,2])
            icon = "🎫" if info['type']=='行程单' else "🧾" if info['type']=='发票' else "📄"
            cols[0].write(i+1)
            cols[1].write(info['name'])
            cols[2].write(f"{icon} {info['type']}")
            cols[3].write(str(info['date']) if info['date'] else "⚠️ 无")
            amt = f"¥{info['amount']:.2f}" if info['amount'] is not None else "—"
            cols[4].write(amt)
            # 来源判断（简化）
            src = "📄PDF" if (extract_amount_from_pdf_content(info['file']) is not None) else "—"
            cols[5].write(src)

    if st.button("🔗 开始智能配对并合并", type="primary", use_container_width=True):
        try:
            with st.spinner("🔄 正在分析配对关系（日期+金额）..."):
                ordered_files, group_preview, unpaired_invoices, other_files = pair_and_sort(file_info_list)

            with st.expander("✅ 配对分组预览", expanded=True):
                if group_preview:
                    st.markdown("### 🗂️ 已配对分组")
                    for i, group in enumerate(group_preview):
                        itin = group['itinerary']
                        inv  = group['invoice']
                        date_str = str(itin['date']) if itin['date'] else '未知日期'
                        itin_amt = f"¥{itin['amount']:.2f}" if itin['amount'] is not None else "?"
                        inv_amt = f"¥{inv['amount']:.2f}" if inv and inv['amount'] is not None else "?"

                        status = ""
                        if inv:
                            if itin['amount'] is not None and inv['amount'] is not None and abs(itin['amount']-inv['amount'])<0.01:
                                status = "✅ 金额匹配"
                            else:
                                status = "❓ 金额可能不匹配或无金额"
                        else:
                            status = "⚠️ 无对应发票"

                        st.markdown(f"**第{i+1}组 · {date_str}**")
                        c1,c2,c3,c4 = st.columns([1,4,2,2])
                        c1.write("🎫")
                        c2.write(itin['name'])
                        c3.write(itin_amt)
                        c4.write(f"`行程单`")

                        c1,c2,c3,c4 = st.columns([1,4,2,2])
                        if inv:
                            c1.write("🧾")
                            c2.write(inv['name'])
                            c3.write(inv_amt)
                            c4.write(status)
                        else:
                            c1.write("—")
                            c2.write("*无匹配发票*")
                            c3.write("—")
                            c4.write(status)
                        st.markdown("---")

                if unpaired_invoices:
                    st.markdown("### 🧾 未配对发票")
                    for inv in unpaired_invoices:
                        c1,c2,c3 = st.columns([1,5,2])
                        c1.write("🧾")
                        c2.write(inv['name'])
                        amt = f"¥{inv['amount']:.2f}" if inv['amount'] is not None else "金额未知"
                        c3.write(amt)

                if other_files:
                    st.markdown("### 📄 其他文件")
                    for o in other_files:
                        st.write(f"📄 {o['name']}")

                st.markdown("### 📑 最终合并顺序")
                for idx, f in enumerate(ordered_files):
                    ftype = classify_file(f.name)
                    fdate = extract_date_from_filename(f.name) or extract_date_from_pdf_content(f)
                    famt = extract_amount_from_pdf_content(f)
                    icon = "🎫" if ftype=='行程单' else "🧾" if ftype=='发票' else "📄"
                    st.text(f"  {idx+1:>2}. {icon} [{ftype}] {f.name} ({fdate or '?date'} | {f'¥{famt:.2f}' if famt else '?¥'})")

            with st.spinner("📦 正在合并 PDF..."):
                merged_pdf = merge_pdfs(ordered_files)

            if merged_pdf:
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                st.download_button(
                    label="📥 下载合并后的发票包",
                    data=merged_pdf,
                    file_name=f"发票合并包_{ts}.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
                st.balloons()
                paired_count = sum(1 for g in group_preview if g['invoice'])
                st.success(f"✨ 合并完成！{len(group_preview)}张行程单，成功配对{paired_count}组，未配对发票{len(unpaired_invoices)}张")

        except Exception as e:
            st.error(f"❌ 处理失败：{str(e)}")
            st.exception(e)

else:
    st.info("👆 请上传 PDF 文件开始使用")
    st.markdown("""
    ### 📌 增强功能
    - 同时提取**日期**和**金额**（价税合计）
    - 配对条件：日期相同 **且** 金额一致（允许 ±0.01 元）
    - 金额提取关键字：`价税合计`、`合计金额`、`¥` 等
    - 预览界面展示每个文件的金额及配对状态
    """)

st.markdown("---")
st.caption("✅ 日期+金额双重匹配 | 行程单在前发票在后 | 按时间升序分组")
