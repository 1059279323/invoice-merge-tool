import streamlit  streamlit as st
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
    "选择PDF发票文件",,,,
    type=    type=["pdf"],,
    accept_multiple_files=    accept_multiple_files=True
)

def merge_pdfs(pdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_filespdf_files)::::::::::::::::
    """合并多个PDF文件"""
    merger = PyPDF2.            merger = PyPDF2.        merger = PyPDF2.     = PyPDF2.    merger = PyPDF2. = PyPDF2. = PyPDF2. = PyPDF2.PdfMerger()
    for pdf  pdf  pdf  pdf in pdf_files: pdf_files: pdf_files: pdf_files:
        merger.                merger.        merger..append(pdfpdfpdfpdf)
    
    # 生成合并后的PDF
    output = io.            output = io.        output = io.     = io.    output = io. = io. = io. = io.BytesIO()
    merger.        merger.    merger..write(outputoutputoutputoutput)
    output.                output.            output.        .        output.    .    .    .    output........seek(0)
    merger.            merger.        merger.    .    merger....close()
    return output output output output

# 处理逻辑
if uploaded_files: uploaded_files: uploaded_files: uploaded_files:
    try::::
        # 预览上传文件
        st.        st.success(f"✅ 成功上传 {len(uploaded_filesuploaded_files)} 个PDF文件")
        file_names =         file_names = [f.f.name for f  f in uploaded_files uploaded_files]
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
