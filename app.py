import streamlit as st
import pandas as pd
import PyPDF2
import io
import os
from datetime import datetime

# 页面配置
st.set_page_config(page_title="发票合并助手", page_icon="📄")
st.title("📄 发票合并助手")
st.subheader("上传多个PDF发票文件，一键合并")

# 上传PDF文件
uploaded_files = st.file_uploader(
    "选择PDF发票文件",
    type=["pdf"],
    accept_multiple_files=True
)

def merge_pdfs(pdf_files):
    """合并多个PDF文件"""
    merger = PyPDF2.PdfMerger()
    for pdf in pdf_files:
        merger.append(pdf)
    
    # 生成合并后的PDF
    output = io.BytesIO()
    merger.write(output)
    output.seek(0)
    merger.close()
    return output

# 处理逻辑
if uploaded_files:
    try:
        # 预览上传文件
        st.success(f"✅ 成功上传 {len(uploaded_files)} 个PDF文件")
        file_names = [f.name for f in uploaded_files]
        st.write("上传文件列表：", file_names)

        # 合并PDF
        merged_pdf = merge_pdfs(uploaded_files)
        
        # 下载按钮
        st.download_button(
            label="📥 下载合并后的发票PDF",
            data=merged_pdf,
            file_name=f"合并发票_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf",
            mime="application/pdf"
        )
        
        st.balloons()
        
    except Exception as e:
        st.error(f"❌ 处理失败：{str(e)}")
else:
    st.info("👆 请上传PDF格式的发票文件")

st.markdown("---")
st.caption("✅ 纯Streamlit运行 | 无需FastAPI | 无依赖报错")
