from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from datetime import datetime
from html import escape
from io import BytesIO

import pandas as pd
import streamlit as st

try:
    import pymupdf
except ImportError:  # 旧版包名
    import fitz as pymupdf


A4_W, A4_H = 595.276, 841.89
CJK_FONT = "china-s"  # PyMuPDF 内置简体中文字体，无需注册字体文件

TYPE_LABEL = {"trip": "行程单", "invoice": "发票", "support": "附件",
              "ticket": "车票/机票", "blank": "空白", None: "未识别"}
LABEL_TYPE = {v: k for k, v in TYPE_LABEL.items() if k is not None}
LABEL_TYPE.update({"未识别": None})

# CJK Radicals Supplement（U+2E80–U+2EFF）NFKC 不处理，手动补几个常见字形
_RADICAL_FIX = str.maketrans("⻋⻢⻥⻦⻩⻮⻰⻳⻬⻣⻤⻨", "车马鱼鸟黄齿龙龟齐骨鬼麦")


# ═══════════════════════════════ 文本归一化 ═══════════════════════════════
_CJK = r"\u3400-\u9fff\uf900-\ufaff"
_RE_CJK_GAP = re.compile(rf"(?<=[{_CJK}])[ \t\u00a0\u3000]+(?=[{_CJK}])")
_RE_SPACES = re.compile(r"[ \t\u00a0\u3000]{2,}")


def norm_text(raw: str) -> str:
    """归一化 PDF 提取文本：兼容字形 → 正常汉字，全角 → 半角，压缩排版空格。"""
    s = unicodedata.normalize("NFKC", raw or "")
    s = s.translate(_RADICAL_FIX)
    s = s.replace("\u200b", "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    s = _RE_CJK_GAP.sub("", s)          # "单  价" / "合   计" → "单价" / "合计"
    s = _RE_SPACES.sub(" ", s)
    return s.strip()


# ═══════════════════════════════ 金额 / 时间抽取 ═══════════════════════════════
_RE_MONEY = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{1,2})(?![\d])")
_RE_DT = re.compile(
    r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?"
    r"(?:\s*(\d{1,2})\s*[:：时]\s*(\d{1,2})(?:\s*[:：分]\s*(\d{1,2}))?)?"
)
_RE_MD = re.compile(r"(?<!\d)(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?\s+(\d{1,2})\s*[:：]\s*(\d{2})(?!\d)")

_CN_DIGIT = {
    "零": 0, "〇": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "两": 2,
}
_CN_UNIT = {"拾": 10, "十": 10, "佰": 100, "百": 100, "仟": 1000, "千": 1000}
_RE_CN_AMT = re.compile(
    r"[零〇壹贰叁肆伍陆柒捌玖一二三四五六七八九两拾十佰百仟千万亿]{1,12}[圆元]"
    r"[零〇壹贰叁肆伍陆柒捌玖一二三四五六七八九拾十角分整]{0,6}"
)
_RE_CN_DIGIT = r"([零〇壹贰叁肆伍陆柒捌玖一二三四五六七八九])"


def _cn_int(s: str) -> int:
    total = section = num = 0
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            section += (num or 1) * _CN_UNIT[ch]
            num = 0
        elif ch == "万":
            total += (section + num) * 10000
            section = num = 0
        elif ch == "亿":
            total = (total + section + num) * 100000000
            section = num = 0
    return total + section + num


def cn_amount_to_float(s: str):
    """壹拾壹圆肆角玖分 → 11.49；壹拾陆圆整 → 16.00"""
    s = s.replace("元", "圆")
    if "圆" not in s:
        return None
    head, tail = s.split("圆", 1)
    val = float(_cn_int(head))
    m = re.search(_RE_CN_DIGIT + "角", tail)
    if m:
        val += _CN_DIGIT[m.group(1)] * 0.1
    m = re.search(_RE_CN_DIGIT + "分", tail)
    if m:
        val += _CN_DIGIT[m.group(1)] * 0.01
    return round(val, 2)


def find_cn_amount(text: str):
    for m in _RE_CN_AMT.finditer(text):
        val = cn_amount_to_float(m.group(0))
        if val and val > 0:
            return val
    return None


def first_money(seg: str):
    m = _RE_MONEY.search(seg or "")
    return float(m.group(1).replace(",", "")) if m else None


def all_money(text: str):
    return [float(m.group(1).replace(",", "")) for m in _RE_MONEY.finditer(text)]


def _after(text: str, kw: str, span: int = 260) -> str:
    idx = text.find(kw)
    return text[idx + len(kw): idx + len(kw) + span] if idx >= 0 else ""


def parse_dt(seg: str, year_hint: int | None = None):
    """从片段里取第一个日期（可带时分）。返回 (datetime, 原文)。"""
    m = _RE_DT.search(seg or "")
    if m:
        y, mo, d, hh, mi, ss = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d))
        except ValueError:
            return None, ""
        if hh is not None and mi is not None:
            try:
                dt = dt.replace(hour=int(hh), minute=int(mi), second=int(ss or 0))
            except ValueError:
                pass
        return dt, m.group(0).strip()
    m = _RE_MD.search(seg or "")           # 07-22 19:02 这种无年份格式
    if m:
        mo, d, hh, mi = m.groups()
        for y in ([year_hint] if year_hint else []) + [datetime.now().year]:
            try:
                return datetime(int(y), int(mo), int(d), int(hh), int(mi)), m.group(0).strip()
            except ValueError:
                continue
    return None, ""


# ═══════════════════════════════ 分类与解析 ═══════════════════════════════
INVOICE_KW = {
    "价税合计": 6, "发票号码": 4, "电子发票": 4, "开票日期": 3, "税率/征收率": 3,
    "纳税人识别号": 3, "增值税": 3, "数电票": 3, "全电发票": 3, "发票代码": 3,
    "购买方": 2, "销售方": 2, "税额": 2, "统一社会信用代码": 2, "开户银行": 1, "发票": 1,
}
TRIP_KW = {
    "行程单": 5, "行程记录": 4, "上车时间": 4, "行程时间": 4, "行程起止日期": 4,
    "下车时间": 3, "网约车": 3, "打车": 3, "高德": 3, "滴滴": 3, "行程人": 3,
    "里程": 3, "上车地点": 2, "下车站点": 2, "上车站点": 2, "起点": 2, "终点": 2,
    "车牌": 2, "司机": 2, "行程": 2, "订单号": 1, "城市": 1, "车型": 1,
}
SUPPORT_KW = {
    "运单明细": 6, "运单号": 4, "运单": 3, "明细": 3, "寄件时间": 3, "清单": 2,
    "小票": 2, "水单": 2, "寄件地": 2, "收件地": 2, "备注": 0,
}
TICKET_KW = {
    "火车票": 5, "电子客票": 4, "铁路": 4, "车次": 3, "席别": 3, "出发站": 3,
    "到达站": 3, "航空运输电子客票行程单": 6, "登机牌": 3, "航班": 3, "机票": 3,
    "票价": 2, "承运人": 2, "舱位": 2, "旅客": 1,
}

TRIP_AMOUNT_KW = ("合计", "实付", "实收", "支付金额", "订单金额", "总金额", "车费", "总计")
INVOICE_AMOUNT_KW = ("价税合计", "发票总金额", "合计金额")
TICKET_AMOUNT_KW = ("合计", "总价", "票价", "金额")


def _score(compact: str, table: dict):
    hits, score = [], 0
    for kw, w in table.items():
        if kw and kw in compact:
            score += w
            hits.append(kw)
    return score, hits


def classify(text: str):
    """返回 (type, 命中关键词字符串, 四项得分)"""
    compact = re.sub(r"\s+", "", text)
    inv, inv_hits = _score(compact, INVOICE_KW)
    trip, trip_hits = _score(compact, TRIP_KW)
    sup, sup_hits = _score(compact, SUPPORT_KW)
    tk, tk_hits = _score(compact, TICKET_KW)

    has_tax_total = "价税合计" in compact
    if has_tax_total or inv >= 6:
        if sup >= inv and not has_tax_total:
            return "support", "、".join(sup_hits[:6]), (inv, trip, sup)
        return "invoice", "、".join(inv_hits[:6]), (inv, trip, sup)
    if tk >= 5 and tk > trip:
        return "ticket", "、".join(tk_hits[:6]), (inv, trip, sup)
    if trip >= 5:
        return "trip", "、".join(trip_hits[:6]), (inv, trip, sup)
    if sup >= 5:
        return "support", "、".join(sup_hits[:6]), (inv, trip, sup)
    return None, "、".join((trip_hits + inv_hits + sup_hits + tk_hits)[:6]), (inv, trip, sup)


def _invoice_amount(text: str):
    """发票金额：小写 > 大写 > 价税合计附近 > 全页最大值"""
    lower = upper = None
    note = ""

    for m in re.finditer(r"小写", text):
        val = first_money(text[m.end(): m.end() + 28])
        if val and val > 0:
            lower = val
            break
    upper = find_cn_amount(text)

    if lower is not None and upper is not None and abs(lower - upper) > 0.02:
        note = "小写与大写金额不一致，已采用大写"
        return upper, note
    if lower is not None:
        return lower, note
    if upper is not None:
        return upper, note

    for kw in INVOICE_AMOUNT_KW:
        seg = _after(text, kw, 60)
        val = first_money(seg)
        if val and val > 0:
            return val, note

    money = [v for v in all_money(text) if v > 0]
    return (max(money) if money else None), note


def _trip_amount(text: str):
    for kw in TRIP_AMOUNT_KW:
        for m in re.finditer(re.escape(kw), text):
            seg = text[m.end(): m.end() + 24]
            val = first_money(seg)
            if val and val > 0:
                return val
    money = [v for v in all_money(text) if v > 0]
    return max(money) if money else None


def _support_amount(text: str):
    for kw in ("发票总金额", "运单金额", "总金额", "合计", "金额"):
        seg = _after(text, kw, 40)
        val = first_money(seg)
        if val and val > 0:
            return val
    money = [v for v in all_money(text) if v > 0]
    return max(money) if money else None


def _ticket_amount(text: str):
    for kw in TICKET_AMOUNT_KW:
        for m in re.finditer(re.escape(kw), text):
            val = first_money(text[m.end(): m.end() + 30])
            if val and val > 0:
                return val
    money = [v for v in all_money(text) if v > 0]
    return max(money) if money else None


def _trip_time(text: str, year_hint=None):
    # 优先级：真实行程时间 > 申请日期（开票日）
    for kw in ("行程时间", "行程起止日期", "用车时间", "服务时间", "出发时间", "乘车日期",
               "出发日期", "航班日期", "上车时间", "行程记录"):
        seg = _after(text, kw, 200)
        if seg:
            dt, raw = parse_dt(seg, year_hint)
            if dt:
                return dt, raw.replace("\n", " ").strip()
    dt, raw = parse_dt(text, year_hint)
    if dt:
        return dt, raw.replace("\n", " ").strip()
    for kw in ("申请时间", "申请日期"):
        dt, raw = parse_dt(_after(text, kw, 40))
        if dt:
            return dt, raw
    return None, ""


def _invoice_time(text: str):
    for kw in ("开票日期", "开具日期", "开票时间"):
        seg = _after(text, kw, 40)
        dt, raw = parse_dt(seg)
        if dt:
            return dt, raw.replace("\n", " ")
    dt, raw = parse_dt(text)
    return (dt, raw.replace("\n", " ")) if dt else (None, "")


def _fmt_time(dt) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M") if (dt.hour or dt.minute) else dt.strftime("%Y-%m-%d")


def parse_page_text(raw_text: str) -> dict:
    """单页文本 → 结构化信息"""
    text = norm_text(raw_text)
    info = {
        "type": None, "time": None, "time_str": "", "amount": None,
        "signals": "", "note": "", "scores": (0, 0, 0), "text": text, "chars": len(text),
    }
    if len(re.sub(r"\s", "", text)) < 4:
        info["type"] = "blank"
        return info

    info["type"], info["signals"], info["scores"] = classify(text)

    if info["type"] == "trip":
        info["amount"] = _trip_amount(text)
        info["time"], _ = _trip_time(text)
    elif info["type"] == "ticket":
        info["amount"] = _ticket_amount(text)
        info["time"], _ = _trip_time(text)
        if info["time"] is None:
            info["time"], _ = _invoice_time(text)
    elif info["type"] == "invoice":
        info["amount"], info["note"] = _invoice_amount(text)
        info["time"], _ = _invoice_time(text)
    elif info["type"] == "support":
        info["amount"] = _support_amount(text)
        info["time"], _ = _invoice_time(text)
    else:
        info["amount"] = _invoice_amount(text)[0] or _trip_amount(text)
        info["time"], _ = _invoice_time(text)

    if info["amount"] is not None:
        info["amount"] = round(float(info["amount"]), 2)
    info["time_str"] = _fmt_time(info["time"])
    return info


# ═══════════════════════════════ PDF 读取 ═══════════════════════════════
def _open_doc(src):
    """打开磁盘路径或内存中的 PDF 字节流"""
    if hasattr(src, "read"):
        data = src.getvalue() if hasattr(src, "getvalue") else src.read()
        doc = pymupdf.open(stream=data, filetype="pdf")
    else:
        doc = pymupdf.open(src)
    if doc.needs_pass:
        doc.authenticate("")
    return doc


def extract_pages(pdf_paths, progress=None):
    """遍历所有 PDF 的所有页面，返回页面信息列表"""
    pages, total, done = [], len(pdf_paths), 0
    for path, shown_name in pdf_paths:
        try:
            doc = _open_doc(path)
        except Exception as exc:
            st.warning(f"打开 {shown_name} 失败：{exc}")
            done += 1
            continue

        try:
            for i, page in enumerate(doc):
                text = page.get_text("text") or ""
                if len(re.sub(r"\s", "", text)) < 4:
                    alt = page.get_text("text", sort=True) or ""
                    if len(re.sub(r"\s", "", alt)) > len(re.sub(r"\s", "", text)):
                        text = alt
                info = parse_page_text(text)
                info.update(
                    page_num=i + 1,
                    pdf_file=path,
                    pdf_name=shown_name,
                    has_image=bool(page.get_images()),
                )
                pages.append(info)
        finally:
            doc.close()

        done += 1
        if progress is not None:
            progress.progress(done / total, f"🔎 解析中… {done}/{total}")

    return pages


# ═══════════════════════════════ 配对 ═══════════════════════════════
def _sort_key(p):
    return (p.get("time") is None, p.get("time") or datetime.max)


def _date_gap(inv, trip):
    if inv.get("time") is None or trip.get("time") is None:
        return 10 ** 6
    gap = (inv["time"] - trip["time"]).total_seconds() / 86400
    # 发票一般开在行程之后，早于行程的扣分更重
    return gap + 30 if gap < 0 else abs(gap)


def match_pages(pages, tolerance=0.05):
    """
    行程单按时间排序逐个配对同额发票；同额多张时取开票日最接近的一张。
    明细附件按金额贴到对应发票后面；没行程的发票（如邮寄发票）也带上自己的附件。
    火车票 / 机票本身就是报销凭证，单独成组（self=True，不需要配发票）。
    返回 (groups, unmatched_inv, unmatched_sup)
        groups        : [{"trip":…, "invoice":…, "supports":[…], "self": bool}]
        unmatched_inv : [(invoice, [support, …]), …]  —— 有发票但没对应行程
        unmatched_sup : [support, …]                 —— 连发票都找不到的附件
    """
    heads = [(p, False) for p in pages if p["type"] == "trip"]
    heads += [(p, True) for p in pages if p["type"] == "ticket"]
    heads.sort(key=lambda pair: _sort_key(pair[0]))

    invoices = [p for p in pages if p["type"] == "invoice"]
    supports = [p for p in pages if p["type"] == "support"]

    used_inv, used_sup = set(), set()

    def take_supports(amount):
        got = []
        if amount is None:
            return got
        for j, sup in enumerate(supports):
            if j in used_sup or sup.get("amount") is None:
                continue
            if abs(sup["amount"] - amount) <= tolerance:
                used_sup.add(j)
                got.append(sup)
        return got

    groups = []
    for head, is_ticket in heads:
        if is_ticket:
            groups.append({"trip": head, "invoice": None, "supports": [], "self": True})
            continue

        best, best_gap = None, None
        if head.get("amount") is not None:
            for i, inv in enumerate(invoices):
                if i in used_inv or inv.get("amount") is None:
                    continue
                if abs(inv["amount"] - head["amount"]) <= tolerance:
                    gap = _date_gap(inv, head)
                    if best_gap is None or gap < best_gap:
                        best, best_gap = i, gap

        inv = None
        if best is not None:
            used_inv.add(best)
            inv = invoices[best]

        attached = take_supports(inv.get("amount") if inv else None)
        groups.append({"trip": head, "invoice": inv, "supports": attached, "self": False})

    unmatched_inv = []
    for i, inv in enumerate(invoices):
        if i in used_inv:
            continue
        unmatched_inv.append((inv, take_supports(inv.get("amount"))))
    unmatched_inv.sort(key=lambda pair: _sort_key(pair[0]))

    unmatched_sup = [p for j, p in enumerate(supports) if j not in used_sup]
    unmatched_sup.sort(key=_sort_key)
    return groups, unmatched_inv, unmatched_sup


# ═══════════════════════════════ PDF 输出 ═══════════════════════════════
def _placeholder_page(doc, title, lines):
    page = doc.new_page(width=A4_W, height=A4_H)
    page.insert_text((56, 76), title, fontname=CJK_FONT, fontsize=18, color=(0.65, 0.15, 0.1))
    page.draw_line((56, 88), (A4_W - 56, 88), color=(0.8, 0.8, 0.8), width=0.8)
    y = 112
    for line in lines:
        rect = pymupdf.Rect(56, y, A4_W - 56, y + 60)
        page.insert_textbox(rect, str(line), fontname=CJK_FONT, fontsize=11,
                            color=(0.15, 0.15, 0.15), align=0)
        y += 20 + max(0, (len(str(line)) // 55)) * 16
    page.insert_text((56, A4_H - 60), "— 占位说明页 · 由发票合并助手自动生成 —",
                     fontname=CJK_FONT, fontsize=9, color=(0.5, 0.5, 0.5))


def _cover_page(doc, groups, title, extra_inv=0, extra_sup=0):
    page = doc.new_page(width=A4_W, height=A4_H)
    page.insert_text((56, 80), title or "报销材料汇总", fontname=CJK_FONT, fontsize=22)
    page.insert_text((56, 104), f"生成时间：{datetime.now():%Y-%m-%d %H:%M}",
                     fontname=CJK_FONT, fontsize=10, color=(0.4, 0.4, 0.4))
    page.draw_line((56, 118), (A4_W - 56, 118), color=(0.75, 0.75, 0.75), width=0.8)

    total = sum(g["trip"].get("amount") or 0 for g in groups)
    page.insert_text((56, 146), f"共 {len(groups)} 组行程，合计 ¥{total:,.2f}",
                     fontname=CJK_FONT, fontsize=13)
    note = []
    if extra_inv:
        note.append(f"无行程发票 {extra_inv} 张")
    if extra_sup:
        note.append(f"游离附件 {extra_sup} 张")
    if note:
        page.insert_text((56, 166), "另附：" + "、".join(note) + "（已排在文末）",
                         fontname=CJK_FONT, fontsize=9, color=(0.45, 0.45, 0.45))

    y = 200
    for idx, g in enumerate(groups, 1):
        trip, inv = g["trip"], g["invoice"]
        amount = trip.get("amount")
        amt_txt = f"¥{amount:,.2f}" if amount is not None else "金额未知"
        parts = [f"{idx:>2}. {trip.get('time_str') or '时间未知':<18} {amt_txt:>12}",
                 "行程单"]
        if g.get("self"):
            parts.append("车票/机票")
        elif inv:
            parts.append("发票")
        else:
            parts.append("缺发票")
        if g["supports"]:
            parts.append(f"附件×{len(g['supports'])}")
        page.insert_text((56, y), "  ".join(parts), fontname=CJK_FONT, fontsize=10.5)
        y += 22
        if y > A4_H - 80:
            page = doc.new_page(width=A4_W, height=A4_H)
            y = 80


def build_merged_pdf(groups, unmatched_inv, unmatched_sup, cover=False, title=""):
    """按 行程单 → 发票 → 附件 的顺序输出；缺失项插入占位页。"""
    doc = pymupdf.open()
    cache = {}

    def src_doc(path):
        if path not in cache:
            cache[path] = _open_doc(path)
        return cache[path]

    def add(item):
        try:
            src = src_doc(item["pdf_file"])
            idx = item["page_num"] - 1
            doc.insert_pdf(src, from_page=idx, to_page=idx)
            return True
        except Exception:
            _placeholder_page(doc, "页面读取失败", [
                f"文件：{item.get('pdf_name', '?')}",
                f"页码：{item.get('page_num', '?')}",
                "该页可能被加密、损坏或格式特殊，已用本说明页代替。",
            ])
            return False

    try:
        if cover:
            _cover_page(doc, groups, title,
                        extra_inv=len(unmatched_inv), extra_sup=len(unmatched_sup))

        for g in groups:
            trip, inv = g["trip"], g["invoice"]
            add(trip)
            if g.get("self"):
                for sup in g["supports"]:
                    add(sup)
                continue
            if inv is not None:
                add(inv)
            else:
                _placeholder_page(doc, "缺发票", [
                    f"行程时间：{trip.get('time_str') or '未识别'}",
                    f"行程金额：{'¥%.2f' % trip['amount'] if trip.get('amount') is not None else '未识别'}",
                    f"来源文件：{trip.get('pdf_name', '?')} 第 {trip.get('page_num', '?')} 页",
                    "未找到金额一致的发票，请人工补交后重新合并。",
                ])
            for sup in g["supports"]:
                add(sup)

        for inv, sup_list in unmatched_inv:
            add(inv)
            for sup in sup_list:
                add(sup)
        for sup in unmatched_sup:
            add(sup)

        return doc.tobytes(garbage=3, deflate=True)
    finally:
        for d in cache.values():
            try:
                d.close()
            except Exception:
                pass
        doc.close()


# ═══════════════════════════════ 图片 → PDF ═══════════════════════════════
def images_to_pdf(image_paths, page_size="A4", fit_mode="fit"):
    if not image_paths:
        return None
    doc = pymupdf.open()

    if page_size == "original":
        for path in image_paths:
            try:
                pix = pymupdf.Pixmap(path)
                page = doc.new_page(width=max(1, pix.width), height=max(1, pix.height))
                page.insert_image(page.rect, filename=path, keep_proportion=True)
            except Exception as exc:
                st.warning(f"跳过无法识别的图片 {os.path.basename(path)}：{exc}")
    else:
        w, h = (A4_W, A4_H) if page_size == "A4" else (A4_H, A4_W)
        for path in image_paths:
            try:
                page = doc.new_page(width=w, height=h)
                page.insert_image(page.rect, filename=path,
                                  keep_proportion=(fit_mode == "fit"))
            except Exception as exc:
                st.warning(f"跳过无法识别的图片 {os.path.basename(path)}：{exc}")

    if doc.page_count == 0:
        doc.close()
        return None
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


# ═══════════════════════════════ PDF 任意插页 ═══════════════════════════════
def insert_pdf_pages(base_buf, insert_buf, position, page_range=None):
    base, ins = _open_doc(base_buf), _open_doc(insert_buf)
    try:
        n_base, n_ins = base.page_count, ins.page_count
        start, end = (0, n_ins) if page_range is None else (
            max(0, page_range[0] - 1), min(n_ins, page_range[1])
        )
        if end <= start:
            raise ValueError("页码范围无效：结束页不能小于起始页")
        pos = max(0, min(int(position), n_base))

        out = pymupdf.open()
        if pos > 0:
            out.insert_pdf(base, from_page=0, to_page=pos - 1)
        out.insert_pdf(ins, from_page=start, to_page=end - 1)
        if pos < n_base:
            out.insert_pdf(base, from_page=pos, to_page=n_base - 1)

        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data
    finally:
        base.close()
        ins.close()


# ═══════════════════════════════ 样式 ═══════════════════════════════
def _inject_css():
    """浅色办公风：只覆盖自定义卡片，尽量不碰 Streamlit 原生控件（避免和主题打架）。"""
    st.markdown(
        """
    <style>
    /* ── 页面骨架 ── */
    .stApp { background: #f6f7fb; }
    .block-container { padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1180px; }
    header[data-testid="stHeader"] { background: transparent; }
    footer, div[data-testid="stDecoration"] { display: none; }

    /* ── 顶部标题 ── */
    .hero { display: flex; align-items: center; gap: .8rem; padding: .2rem 0 1rem; }
    .hero-icon {
        width: 44px; height: 44px; border-radius: 12px; flex: 0 0 auto;
        display: flex; align-items: center; justify-content: center;
        background: linear-gradient(135deg, #2563eb, #7c3aed);
        color: #fff; font-size: 1.3rem;
        box-shadow: 0 6px 16px rgba(37, 99, 235, .25);
    }
    .hero h1 { font-size: 1.55rem; margin: 0; color: #111827; letter-spacing: -.01em; }
    .hero p { margin: .15rem 0 0; color: #6b7280; font-size: .84rem; }

    /* ── KPI 卡片 ── */
    .kpi {
        background: #fff; border: 1px solid #e6e9f0; border-radius: 12px;
        padding: .75rem .95rem; box-shadow: 0 1px 2px rgba(16, 24, 40, .04);
        margin-bottom: .5rem;
    }
    .kpi .v {
        font-size: 1.5rem; font-weight: 700; line-height: 1.2;
        color: #111827; font-variant-numeric: tabular-nums;
    }
    .kpi .l { font-size: .74rem; color: #6b7280; margin-top: .1rem; }
    .kpi.info .v { color: #2563eb; }
    .kpi.ok   .v { color: #059669; }
    .kpi.warn .v { color: #d97706; }
    .kpi.bad  .v { color: #dc2626; }

    /* ── 配对分组卡片 ── */
    .grp {
        background: #fff; border: 1px solid #e6e9f0; border-left: 3px solid #2563eb;
        border-radius: 10px; padding: .6rem .85rem; margin-bottom: .5rem;
        box-shadow: 0 1px 2px rgba(16, 24, 40, .03);
    }
    .grp.ok   { border-left-color: #10b981; }
    .grp.miss { border-left-color: #f59e0b; }
    .grp.self { border-left-color: #7c3aed; }
    .grp-head { display: flex; align-items: center; justify-content: space-between; gap: .5rem; }
    .grp-head .idx { font-size: .78rem; color: #9ca3af; }
    .grp-head .right { display: flex; align-items: baseline; gap: .55rem; }
    .grp-head .amt {
        font-size: .98rem; font-weight: 700; color: #111827;
        font-variant-numeric: tabular-nums;
    }
    .st-pill { font-size: .7rem; font-weight: 600; padding: 1px 8px; border-radius: 999px; }
    .st-pill.ok   { background: #ecfdf5; color: #047857; }
    .st-pill.miss { background: #fffbeb; color: #b45309; }
    .st-pill.self { background: #f5f3ff; color: #6d28d9; }

    .line { display: flex; align-items: center; gap: .5rem; font-size: .83rem; padding: .2rem 0; }
    .line + .line { border-top: 1px solid #f3f4f8; }
    .tag { flex: 0 0 auto; font-size: .69rem; font-weight: 600; padding: 1px 7px; border-radius: 5px; }
    .tag.trip { background: #eff6ff; color: #1d4ed8; }
    .tag.inv  { background: #ecfdf5; color: #047857; }
    .tag.sup  { background: #f5f3ff; color: #6d28d9; }
    .tag.miss { background: #fffbeb; color: #b45309; }
    .fname {
        flex: 1 1 auto; min-width: 0; color: #374151;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .pg { flex: 0 0 auto; color: #9aa3b2; font-size: .74rem; }
    .tm {
        flex: 0 0 auto; color: #4b5563; font-size: .76rem; margin-left: .15rem;
        font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
    }

    /* ── 上传区 ── */
    div[data-testid="stFileUploaderDropzone"] {
        background: #fff !important; border: 1.5px dashed #cbd5e1 !important;
        border-radius: 12px !important; transition: border-color .2s !important;
    }
    div[data-testid="stFileUploaderDropzone"]:hover { border-color: #2563eb !important; }

    /* ── 兜底：即使没读到 .streamlit/config.toml，主按钮也是蓝色而不是默认红 ── */
    button[kind="primary"], div[data-testid="stDownloadButton"] button {
        background-color: #2563eb !important; border-color: #2563eb !important;
        color: #fff !important;
    }
    button[kind="primary"]:hover, div[data-testid="stDownloadButton"] button:hover {
        background-color: #1d4ed8 !important; border-color: #1d4ed8 !important;
    }

    /* ── 小节标题 ── */
    h4 { color: #111827; font-weight: 600; margin: .9rem 0 .5rem; }
    </style>
    """,
        unsafe_allow_html=True,
    )


def kpi_grid(items, per_row=4):
    """KPI 卡片，按每行 4 个排布"""
    for i in range(0, len(items), per_row):
        chunk = items[i:i + per_row]
        cols = st.columns(len(chunk), gap="small")
        for col, (label, val, tone) in zip(cols, chunk):
            with col:
                st.markdown(
                    f'<div class="kpi {tone}"><div class="v">{val}</div>'
                    f'<div class="l">{label}</div></div>',
                    unsafe_allow_html=True,
                )


def _file_line(tag, tag_cls, name, page, extra=""):
    name = escape(str(name or "?"))
    return (
        f'<div class="line"><span class="tag {tag_cls}">{tag}</span>'
        f'<span class="fname" title="{name}">{name}</span>'
        f'<span class="pg">p.{page}</span><span class="tm">{escape(str(extra or ""))}</span></div>'
    )


# ═══════════════════════════════ Tab 1：发票合并 ═══════════════════════════════
def _save_uploads(files, tmpdir):
    """保存上传文件，避免同名覆盖；返回 [(磁盘路径, 原始文件名)]"""
    saved = []
    for i, f in enumerate(files):
        safe = re.sub(r'[\\/:*?"<>|]', "_", f.name)
        path = os.path.join(tmpdir, f"{i:03d}_{safe}")
        with open(path, "wb") as fh:
            fh.write(f.getbuffer())
        saved.append((path, f.name))
    return saved


def _signature(files, tolerance):
    h = hashlib.md5()
    for f in files:
        h.update(f.name.encode("utf-8", "ignore"))
        h.update(str(len(f.getvalue())).encode())
    h.update(str(tolerance).encode())
    return h.hexdigest()


def _pages_to_frame(pages):
    rows = []
    for idx, p in enumerate(pages):
        rows.append({
            "序号": idx,
            "文件": p.get("pdf_name", "?"),
            "页": p.get("page_num", "?"),
            "类型": TYPE_LABEL.get(p["type"], "未识别"),
            "时间": p.get("time_str") or "",
            "金额": p.get("amount"),
            "命中": p.get("signals", ""),
            "摘要": re.sub(r"\s+", " ", p.get("text", ""))[:60],
        })
    return pd.DataFrame(rows)


def _apply_frame(pages, df):
    """把用户在表格里的修正写回 pages（行被删掉即视为忽略该页）"""
    out = []
    for _, row in df.iterrows():
        idx = int(row["序号"])
        if not 0 <= idx < len(pages):
            continue
        p = dict(pages[idx])
        p["type"] = LABEL_TYPE.get(row["类型"], p["type"])

        amount = row["金额"]
        p["amount"] = None if pd.isna(amount) else round(float(amount), 2)

        tstr = str(row["时间"] or "").strip()
        p["time"], _ = parse_dt(tstr)
        p["time_str"] = tstr
        out.append(p)
    return out


def _render_result(state):
    pages = state["pages"]
    groups = state["groups"]
    unmatched_inv, unmatched_sup = state["unmatched_inv"], state["unmatched_sup"]

    trips = [p for p in pages if p["type"] == "trip"]
    tickets = [p for p in pages if p["type"] == "ticket"]
    invoices = [p for p in pages if p["type"] == "invoice"]
    supports = [p for p in pages if p["type"] == "support"]
    unknown = [p for p in pages if p["type"] in (None, "blank")]

    matched_cnt = sum(1 for g in groups if g["invoice"] is not None)
    miss_cnt = sum(1 for g in groups if not g.get("self") and g["invoice"] is None)
    total = sum((g["trip"].get("amount") or 0) for g in groups)

    # ── 结果条（结论 + 下载，放在最显眼的位置）──
    with st.container(border=True):
        c_left, c_right = st.columns([3, 1.2], vertical_alignment="center")
        with c_left:
            st.markdown(
                f'<div style="font-size:1.02rem;font-weight:600;color:#111827;">'
                f'✅ 合并完成 · {len(groups)} 组 · 合计 ¥{total:,.2f}</div>'
                f'<div style="font-size:.8rem;color:#6b7280;margin-top:.15rem;">'
                f'共 {state["page_count"]} 页，按行程时间排列；缺失项已自动插入占位说明页</div>',
                unsafe_allow_html=True,
            )
        with c_right:
            st.download_button(
                "📥 下载合并 PDF",
                data=state["pdf"],
                file_name="合并报销文件.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
                key="tab1_dl",
            )

    kpi_grid([
        ("行程单", len(trips), "info"),
        ("发票", len(invoices), "info"),
        ("车票 / 机票", len(tickets), "info"),
        ("明细附件", len(supports), "info"),
        ("已配对", matched_cnt, "ok"),
        ("缺发票", miss_cnt, "warn" if miss_cnt else "ok"),
        ("未用发票", len(unmatched_inv), "warn" if unmatched_inv else "ok"),
        ("未识别", len(unknown), "bad" if unknown else "ok"),
    ])

    if unknown:
        st.warning(
            f"有 {len(unknown)} 页未能自动识别，已排除在合并结果之外。"
            "展开下方「识别诊断」可查看每页提取到的文字，或在「手动修正」里直接指定类型。"
        )
    if miss_cnt:
        st.info(f"有 {miss_cnt} 组行程单没有找到金额相符的发票，PDF 里已用占位页标注，方便补交。")

    # ── 配对明细 ──
    st.markdown("#### 🔗 配对明细")
    st.caption(f"每组顺序：行程单/车票 → 发票 → 明细附件。共 {len(groups)} 组。")

    for idx, g in enumerate(groups):
        trip, inv = g["trip"], g["invoice"]
        amt = trip.get("amount")
        if g.get("self"):
            cls, pill, pill_cls, head_tag = "self", "车票/机票", "self", "车票"
        elif inv is not None:
            cls, pill, pill_cls, head_tag = "ok", "已配对", "ok", "行程单"
        else:
            cls, pill, pill_cls, head_tag = "miss", "缺发票", "miss", "行程单"

        amt_txt = f"¥{amt:,.2f}" if amt is not None else "金额未识别"
        lines = [_file_line(head_tag, "trip", trip.get("pdf_name"),
                            trip.get("page_num"), trip.get("time_str") or "无时间")]

        if g.get("self"):
            lines.append('<div class="line"><span class="tag sup">本身即凭证</span>'
                         '<span class="fname">车票 / 机票无需另配发票</span>'
                         '<span class="pg"></span><span class="tm"></span></div>')
        elif inv is not None:
            lines.append(_file_line("发票", "inv", inv.get("pdf_name"),
                                    inv.get("page_num"), inv.get("time_str") or "无时间"))
        else:
            lines.append('<div class="line"><span class="tag miss">缺发票</span>'
                         '<span class="fname">未找到金额相符的发票</span>'
                         '<span class="pg"></span><span class="tm"></span></div>')

        for sup in g["supports"]:
            lines.append(_file_line("附件", "sup", sup.get("pdf_name"),
                                    sup.get("page_num"), sup.get("time_str") or ""))

        st.markdown(
            f'<div class="grp {cls}"><div class="grp-head">'
            f'<span class="idx">第 {idx + 1} 组</span>'
            f'<span class="right"><span class="st-pill {pill_cls}">{pill}</span>'
            f'<span class="amt">{amt_txt}</span></span></div>'
            + "".join(lines) + "</div>",
            unsafe_allow_html=True,
        )

    if unmatched_inv or unmatched_sup:
        with st.expander(
            f"📎 未参与配对的文件（{len(unmatched_inv)} 张发票 / {len(unmatched_sup)} 张附件，已排在文末）",
            expanded=False,
        ):
            for inv, sup_list in unmatched_inv:
                amt = inv.get("amount")
                st.markdown(
                    _file_line("发票", "inv", inv.get("pdf_name"), inv.get("page_num"),
                               f"¥{amt:.2f}" if amt is not None else "金额未识别")
                    + "".join(
                        _file_line("附件", "sup", s.get("pdf_name"), s.get("page_num"), "")
                        for s in sup_list
                    ),
                    unsafe_allow_html=True,
                )
            for sup in unmatched_sup:
                st.markdown(
                    _file_line("附件", "sup", sup.get("pdf_name"), sup.get("page_num"),
                               sup.get("time_str") or ""),
                    unsafe_allow_html=True,
                )
            st.caption("这些文件找不到金额相符的行程单/发票，通常是邮寄、代订等没有行程记录的费用。")

    # ── 识别诊断 ──
    with st.expander("🔍 识别诊断（识别不出来时看这里）", expanded=False):
        df = _pages_to_frame(pages)
        st.dataframe(
            df[["文件", "页", "类型", "时间", "金额", "命中", "摘要"]],
            use_container_width=True, hide_index=True, height=320,
        )
        dump = []
        for i, p in enumerate(pages):
            dump.append(
                f"===== [{i}] {p.get('pdf_name')} 第{p.get('page_num')}页 "
                f"类型={TYPE_LABEL.get(p['type'])} 时间={p.get('time_str')} 金额={p.get('amount')} "
                f"得分(inv/trip/sup)={p.get('scores')} 命中={p.get('signals')} =====\n"
                f"{p.get('text', '')}\n"
            )
        st.download_button(
            "⬇️ 导出全部页面文本（排错用）",
            data="\n".join(dump).encode("utf-8"),
            file_name="识别诊断.txt",
            mime="text/plain",
            key="tab1_diag_dl",
        )
        st.caption(
            "识别规则：给「价税合计 / 发票号码 / 电子发票…」和「行程单 / 上车时间 / 行程时间…」"
            "分别加权打分，行程单分高于发票分判为行程单，反之判为发票；"
            "带「运单明细」且没有「价税合计」的判为附件。"
        )

    # ── 手动修正 ──
    with st.expander("✏️ 手动修正（自动识别不准时用）", expanded=bool(unknown)):
        st.caption("改类型 / 时间 / 金额后点下方按钮，会按修正结果重新配对并重新生成 PDF。")
        df = _pages_to_frame(pages)
        edited = st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            height=320,
            disabled=["序号", "文件", "页", "命中", "摘要"],
            column_config={
                "序号": st.column_config.NumberColumn(width="small"),
                "类型": st.column_config.SelectboxColumn(
                    options=["行程单", "发票", "附件", "车票/机票", "未识别", "空白"],
                    required=True, width="small"),
                "时间": st.column_config.TextColumn(help="格式 2026-08-03 或 2026-08-03 12:08"),
                "金额": st.column_config.NumberColumn(format="%.2f", step=0.01),
            },
            key="tab1_editor",
        )
        if st.button("✅ 应用修正并重新生成", key="tab1_apply", use_container_width=True):
            new_pages = _apply_frame(pages, edited)
            new_groups, new_inv, new_sup = match_pages(new_pages, state["tolerance"])
            new_pdf = build_merged_pdf(new_groups, new_inv, new_sup,
                                       cover=state.get("cover", False),
                                       title=state.get("title", ""))
            st.session_state["t1"] = {
                **state,
                "pages": new_pages,
                "groups": new_groups,
                "unmatched_inv": new_inv,
                "unmatched_sup": new_sup,
                "pdf": new_pdf,
                "page_count": _page_count(new_pdf),
            }
            st.rerun()


def _page_count(pdf_bytes):
    doc = pymupdf.open("pdf", pdf_bytes)
    try:
        return doc.page_count
    finally:
        doc.close()


def _invoice_merge_tab():
    with st.expander("📖 使用说明", expanded=False):
        st.markdown(
            """
        **流程：** 上传 PDF → 自动识别每页是行程单 / 发票 / 附件 → 按金额配对 → 按行程时间排序合并。

        **识别依据（都是打分，不是硬性条件）**
        - 行程单：行程单 / 上车时间 / 行程时间 / 里程 / 起点终点 / 滴滴 / 高德 …
        - 发票：价税合计 / 发票号码 / 电子发票 / 开票日期 / 销售方购买方 …
        - 附件：运单明细 / 运单号 / 明细单，且没有「价税合计」

        **金额取值优先级**
        1. 发票：`(小写)¥11.49`（¥ 在前或在后都认）→ 2. 大写金额 `壹拾壹圆肆角玖分` 反算
        → 3. `价税合计` 附近 → 4. 全页最大金额
        2. 行程单：`合计11.49元` / `共计1单行程，合计：¥39.90元`

        **时间取值优先级**：行程时间 / 行程起止日期 / 上车时间 →（申请日期只作兜底，它是开票日）

        **配对规则**：金额相同（默认容差 ±0.05 元）；同额多张时取开票日与行程日最接近的一张。
        """
        )

    uploaded = st.file_uploader(
        "📂 拖拽或点击选择 PDF 文件",
        type="pdf",
        accept_multiple_files=True,
        help="支持多个 PDF，每页独立识别",
        key="tab1_uploader",
    )
    if not uploaded:
        st.info("👆 请上传行程单和发票的 PDF 文件")
        return

    total_mb = sum(len(f.getvalue()) for f in uploaded) / 1048576
    with st.expander(f"✅ 已选择 {len(uploaded)} 个文件 · {total_mb:.1f} MB（点开可核对）",
                     expanded=False):
        rows = "".join(
            f'<div class="line"><span class="tag sup">PDF</span>'
            f'<span class="fname" title="{escape(f.name)}">{escape(f.name)}</span>'
            f'<span class="pg"></span>'
            f'<span class="tm">{len(f.getvalue()) / 1024:.0f} KB</span></div>'
            for f in uploaded
        )
        st.markdown(rows, unsafe_allow_html=True)

    c1, c2, c3 = st.columns([1, 1, 1.4])
    with c1:
        tolerance = st.number_input("金额容差（元）", 0.0, 5.0, 0.05, 0.01,
                                    help="行程单与发票金额差在此范围内即视为同一笔", key="tab1_tol")
    with c2:
        cover = st.checkbox("生成汇总封面页", value=False, key="tab1_cover")
    with c3:
        title = st.text_input("封面标题", value="报销材料汇总", key="tab1_title", disabled=not cover)

    run = st.button("🔍 分析并合并", type="primary", use_container_width=True, key="tab1_run")

    sig = _signature(uploaded, tolerance)

    if run:
        tmpdir = tempfile.mkdtemp(prefix="invoice_")
        try:
            progress = st.progress(0.0, "准备解析…")
            saved = _save_uploads(uploaded, tmpdir)
            pages = extract_pages(saved, progress=progress)
            progress.progress(1.0, "解析完成")

            groups, unmatched_inv, unmatched_sup = match_pages(pages, tolerance)
            if not groups and not unmatched_inv and not unmatched_sup:
                st.error(
                    "所有页面都没能识别成行程单或发票。"
                    "请展开「识别诊断」看每页提取到的文字——如果是空白或乱码，"
                    "说明这几份 PDF 是扫描图片，需要 OCR 才能识别。"
                )
                return

            pdf_bytes = build_merged_pdf(groups, unmatched_inv, unmatched_sup,
                                         cover=cover, title=title)

            st.session_state["t1"] = {
                "sig": sig, "pages": pages, "groups": groups,
                "unmatched_inv": unmatched_inv, "unmatched_sup": unmatched_sup,
                "pdf": pdf_bytes, "page_count": _page_count(pdf_bytes),
                "tolerance": tolerance, "cover": cover, "title": title,
            }
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    state = st.session_state.get("t1")
    if not state or state.get("sig") != sig:
        if not run:
            st.info("点击【分析并合并】开始处理")
        return

    _render_result(state)


# ═══════════════════════════════ Tab 2：图片转 PDF ═══════════════════════════════
def _img_to_pdf_tab():
    st.caption("上传多张图片，一键合并为 PDF 文件")

    img_files = st.file_uploader(
        "🖼️ 选择图片文件",
        type=["jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff"],
        accept_multiple_files=True,
        help="支持 JPG / PNG / BMP / WebP / TIFF",
        key="tab2_uploader",
    )
    if not img_files:
        st.info("👆 请上传图片文件")
        return

    st.caption(f"✅ 已选择 {len(img_files)} 张图片")

    c1, c2 = st.columns(2)
    with c1:
        page_size = st.selectbox(
            "📐 页面尺寸", ["A4", "A4-L", "original"],
            format_func=lambda x: {"A4": "A4 纵向", "A4-L": "A4 横向", "original": "原始尺寸"}[x],
            key="tab2_size",
        )
    with c2:
        fit_mode = st.selectbox(
            "📏 缩放方式", ["fit", "fill"],
            format_func=lambda x: "等比适应" if x == "fit" else "拉伸铺满",
            key="tab2_fit", disabled=(page_size == "original"),
        )

    if st.button("🖼️ 转换为 PDF", type="primary", use_container_width=True, key="tab2_conv"):
        tmpdir = tempfile.mkdtemp(prefix="img2pdf_")
        try:
            paths = []
            for i, f in enumerate(img_files):
                safe = re.sub(r'[\\/:*?"<>|]', "_", f.name)
                p = os.path.join(tmpdir, f"{i:03d}_{safe}")
                with open(p, "wb") as fh:
                    fh.write(f.getbuffer())
                paths.append(p)

            with st.spinner("🔄 正在转换…"):
                data = images_to_pdf(paths, page_size=page_size, fit_mode=fit_mode)

            if data:
                st.success(f"✅ 转换完成 — 共 {len(img_files)} 张图片")
                st.download_button(
                    "📥 下载 PDF", data=data, file_name="图片转PDF.pdf",
                    mime="application/pdf", type="primary",
                    use_container_width=True, key="tab2_dl",
                )
            else:
                st.error("转换失败，请检查图片格式")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════ Tab 3：PDF 任意插页 ═══════════════════════════════
def _pdf_insert_tab():
    st.caption("在 PDF 的任意位置插入另一个 PDF 的页面")

    c1, c2 = st.columns(2)
    with c1:
        base_file = st.file_uploader("📄 主 PDF 文件", type="pdf",
                                     accept_multiple_files=False, key="tab3_base")
    with c2:
        insert_file = st.file_uploader("📑 要插入的 PDF 文件", type="pdf",
                                       accept_multiple_files=False, key="tab3_insert")

    if not base_file or not insert_file:
        st.info("👆 请分别上传主 PDF 和要插入的 PDF")
        return

    try:
        with _open_doc(BytesIO(base_file.getbuffer())) as b:
            base_pages = b.page_count
        with _open_doc(BytesIO(insert_file.getbuffer())) as ins:
            insert_pages = ins.page_count
    except Exception as exc:
        st.error(f"PDF 读取失败：{exc}")
        return

    c_info1, c_info2 = st.columns(2)
    for col, label, val in ((c_info1, "主 PDF 页数", base_pages), (c_info2, "待插入页数", insert_pages)):
        with col:
            st.markdown(
                f'<div class="kpi info"><div class="v">{val}</div><div class="l">{label}</div></div>',
                unsafe_allow_html=True,
            )

    c1, c2, c3 = st.columns(3)
    with c1:
        position = st.number_input("📍 插入位置", 0, base_pages, base_pages,
                                   help=f"0=开头, 1=第1页后, {base_pages}=末尾", key="tab3_pos")
    with c2:
        insert_start = st.number_input("📌 起始页", 1, insert_pages, 1, key="tab3_start")
    with c3:
        insert_end = st.number_input("📌 结束页", 1, insert_pages, insert_pages, key="tab3_end")

    insert_all = st.checkbox("插入全部页面", value=True, key="tab3_all")
    page_range = None if insert_all else (int(insert_start), int(insert_end))

    if st.button("📑 执行插入", type="primary", use_container_width=True, key="tab3_run"):
        with st.spinner("🔄 正在合并…"):
            try:
                out = insert_pdf_pages(
                    BytesIO(base_file.getbuffer()),
                    BytesIO(insert_file.getbuffer()),
                    int(position), page_range,
                )
                st.success("✅ 插入完成")
                st.download_button(
                    "📥 下载合并 PDF", data=out, file_name="插入后PDF.pdf",
                    mime="application/pdf", type="primary",
                    use_container_width=True, key="tab3_dl",
                )
            except Exception as exc:
                st.error(f"插入失败：{exc}")


# ═══════════════════════════════ 入口 ═══════════════════════════════
def main():
    st.set_page_config(
        page_title="发票合并助手",
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_css()

    st.markdown(
        '<div class="hero"><div class="hero-icon">📄</div>'
        '<div><h1>发票合并助手</h1>'
        '<p>行程单 × 发票 自动配对 · 图片转 PDF · PDF 任意插页</p></div></div>',
        unsafe_allow_html=True,
    )

    tab1, tab2, tab3 = st.tabs(["📄 发票合并", "🖼️ 图片转PDF", "📑 PDF插页"])
    with tab1:
        _invoice_merge_tab()
    with tab2:
        _img_to_pdf_tab()
    with tab3:
        _pdf_insert_tab()


if __name__ == "__main__":
    main()
