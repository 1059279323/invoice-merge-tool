""""""
发票合并助手 - FastAPI Web 后端发票合并助手 - FastAPI Web 后端
"""

import os
import uuid
import shutil
import tempfile
import logging
from datetime import datetime
from typing import List

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# -------- 日志配置 --------
LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LOG_DIR, "app.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("invoice_merger")

app = FastAPI(title="发票合并助手 API", version="1.0")

# -------- 会话管理（内存） --------
# session_dir / session_id / 存放上传的PDF
BASE_SESSION_DIR = os.path.join(tempfile.gettempdir(), "invoice_merger_sessions")
os.makedirs(BASE_SESSION_DIR, exist_ok=True)

# 清理过期会话：存储 session_id -> { created_at, dir }
_active_sessions: dict = {}


def _cleanup_old_sessions():
    """删除超过2小时的会话"""
    now = datetime.now()
    expired = [
        sid
        for sid, info in _active_sessions.items()
        if (now - info["created_at"]).total_seconds() > 7200
    ]
    for sid in expired:
        shutil.rmtree(_active_sessions[sid]["dir"], ignore_errors=True)
        del _active_sessions[sid]
    if expired:
        logger.info("清理过期会话 %d 个: %s", len(expired), expired)


def _create_session():
    """创建新会话"""
    _cleanup_old_sessions()
    sid = uuid.uuid4().hex[:12]
    sess_dir = os.path.join(BASE_SESSION_DIR, sid)
    os.makedirs(sess_dir, exist_ok=True)
    _active_sessions[sid] = {"created_at": datetime.now(), "dir": sess_dir}
    logger.info("创建会话 %s | 目录: %s", sid, sess_dir)
    return sid


def _get_session_dir(sid: str) -> str:
    """获取会话目录，不存在则抛 404"""
    info = _active_sessions.get(sid)
    if not info:
        logger.warning("会话 %s 不存在或已过期", sid)
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return info["dir"]


# -------- API --------

@app.get("/api/status")
def get_status():
    """服务状态"""
    return {
        "status": "ok",
        "pdf_engine": "pikepdf" if USE_PIKEPDF else "PyPDF2",
    }


@app.post("/api/session")
def create_session():
    """创建上传会话"""
    sid = _create_session()
    return {"session_id": sid}


@app.post("/api/session/{session_id}/upload")
async def upload_files(session_id: str, files: List[UploadFile] = File(...)):
    """上传 PDF 文件到会话"""
    sess_dir = _get_session_dir(session_id)
    saved = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            continue
        dest = os.path.join(sess_dir, f.filename)
        content = await f.read()
        with open(dest, "wb") as fh:
            fh.write(content)
        saved.append(f.filename)

    logger.info("会话 %s 上传了 %d 个文件: %s", session_id, len(saved), saved)
    return {"uploaded": saved, "count": len(saved)}


@app.get("/api/session/{session_id}/files")
def list_session_files(session_id: str):
    """列出会话中的文件"""
    sess_dir = _get_session_dir(session_id)
    files = [f for f in os.listdir(sess_dir) if f.lower().endswith(".pdf")]
    return {"files": files, "count": len(files)}


@app.post("/api/session/{session_id}/analyze")
def analyze_session(session_id: str):
    """分析会话中所有 PDF 文件"""
    sess_dir = _get_session_dir(session_id)
    pdf_files = [
        os.path.join(sess_dir, f)
        for f in os.listdir(sess_dir)
        if f.lower().endswith(".pdf")
    ]
    if not pdf_files:
        logger.warning("会话 %s 分析失败：没有 PDF 文件", session_id)
        raise HTTPException(status_code=400, detail="会话中没有 PDF 文件")

    all_pages = extract_all_pdfs(pdf_files)
    stats = get_statistics(all_pages)
    matched, unmatched_inv = match_trips_invoices(all_pages)
    logger.info(
        "会话 %s 分析完成 | 总页数: %d, 匹配: %d, 未匹配行程: %d, 未匹配发票: %d",
        session_id, len(all_pages),
        sum(1 for m in matched if m["invoice"] is not None),
        sum(1 for m in matched if m["invoice"] is None),
        len(unmatched_inv),
    )

    # 构建返回结构（datetime 需要序列化）
    def _serialize_page(p):
        info = {k: v for k, v in p.items() if k != "pdf_file"}
        info["pdf_name"] = os.path.basename(p.get("pdf_file", ""))
        if isinstance(info.get("time"), datetime):
            info["time"] = info["time"].strftime("%Y-%m-%d %H:%M:%S")
        info["amount"] = info.get("amount")
        return info

    results = []
    for m in matched:
        entry = {"trip": _serialize_page(m["trip"])}
        entry["invoice"] = (
            _serialize_page(m["invoice"]) if m["invoice"] else None
        )
        results.append(entry)

    unmatched_data = [_serialize_page(inv) for inv in unmatched_inv]

    return {
        "statistics": stats,
        "matched": results,
        "matched_count": sum(1 for m in matched if m["invoice"] is not None),
        "unmatched_trip_count": sum(1 for m in matched if m["invoice"] is None),
        "unmatched_invoices": unmatched_data,
        "unmatched_invoice_count": len(unmatched_inv),
        "total_pages": len(all_pages),
    }


@app.post("/api/session/{session_id}/merge")
def merge_session(session_id: str, background_tasks: BackgroundTasks):
    """合并会话中的 PDF"""
    sess_dir = _get_session_dir(session_id)
    pdf_files = [
        os.path.join(sess_dir, f)
        for f in os.listdir(sess_dir)
        if f.lower().endswith(".pdf")
    ]
    if not pdf_files:
        raise HTTPException(status_code=400, detail="会话中没有 PDF 文件")

    all_pages = extract_all_pdfs(pdf_files)
    matched, unmatched_inv = match_trips_invoices(all_pages)

    out_path = os.path.join(sess_dir, "merged_output.pdf")
    merge_pdfs_with_placeholders(matched, unmatched_inv, out_path)

    if not os.path.exists(out_path):
        logger.error("会话 %s 合并失败：未生成输出文件", session_id)
        raise HTTPException(status_code=500, detail="合并失败")

    logger.info("会话 %s 合并成功 → %s", session_id, out_path)

    # 异步清理会话（延迟 60 秒，确保下载能完成）
    background_tasks.add_task(_delayed_cleanup, session_id, delay_seconds=60)

    return FileResponse(
        out_path,
        media_type="application/pdf",
        filename="merged_output.pdf",
    )


@app.post("/api/merge")
async def merge_direct(files: List[UploadFile] = File(...)):
    """
    一键上传并合并（不保存会话）
    直接返回合并后的 PDF 文件
    """
    tmpdir = tempfile.mkdtemp()
    try:
        pdf_paths = []
        for f in files:
            if not f.filename.lower().endswith(".pdf"):
                continue
            dest = os.path.join(tmpdir, f.filename)
            content = await f.read()
            with open(dest, "wb") as fh:
                fh.write(content)
            pdf_paths.append(dest)

        if not pdf_paths:
            raise HTTPException(status_code=400, detail="未上传有效的 PDF 文件")

        all_pages = extract_all_pdfs(pdf_paths)
        matched, unmatched_inv = match_trips_invoices(all_pages)

        out_path = os.path.join(tmpdir, "merged_output.pdf")
        merge_pdfs_with_placeholders(matched, unmatched_inv, out_path)

        logger.info(
            "一键合并完成 | 文件数: %d, 匹配: %d, 未匹配发票: %d",
            len(pdf_paths),
            sum(1 for m in matched if m["invoice"] is not None),
            len(unmatched_inv),
        )

        return FileResponse(
            out_path,
            media_type="application/pdf",
            filename="merged_output.pdf",
        )
    finally:
        # 临时文件在响应后清理
        background_tasks = BackgroundTasks()
        background_tasks.add_task(shutil.rmtree, tmpdir, ignore_errors=True)


def _delayed_cleanup(session_id: str, delay_seconds: int = 60):
    """延迟清理会话"""
    import time

    time.sleep(delay_seconds)
    info = _active_sessions.pop(session_id, None)
    if info:
        shutil.rmtree(info["dir"], ignore_errors=True)
        logger.info("清理会话 %s（延迟 %ds）", session_id, delay_seconds)


# -------- 静态文件服务 --------
# 挂载 static 目录，提供 Web 前端
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


# -------- 启动 --------
if __name__ == "__main__":
    import uvicorn
    import socket

    # 获取本机局域网 IP
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname + ".local")
    if local_ip.startswith("127."):
        # fallback: 尝试连接外部获取实际 IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            local_ip = "无法获取"

    logger.info("启动完成 | PDF 引擎: %s", "pikepdf" if USE_PIKEPDF else "PyPDF2")
    logger.info("本机访问: http://127.0.0.1:8000  |  局域网访问: http://%s:8000", local_ip)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
