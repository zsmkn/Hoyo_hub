"""本機 OCR（RapidOCR，支援繁中 / 簡中 / 英文），三款遊戲通用。

- parse_gacha：抽卡紀錄截圖（調頻 / 祈願 / 躍遷紀錄）
- parse_disc：裝備截圖（驅動盤 / 聖遺物 / 遺器）
"""
import re
import threading
from datetime import datetime

from .games import agent_by_name
from .games.base import COMMON_ALIASES

_engine = None
_lock = threading.Lock()


def engine():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
    return _engine


def detect_lines(path):
    """回傳：text / top / left / h（皆為 0~1 的相對座標）。"""
    import numpy as np  # 只有真的要辨識時才載入（雲端的 API Lambda 沒有這些套件）
    from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    result, _ = engine()(np.asarray(img))
    lines = []
    for box, text, score in result or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        lines.append({"text": text.strip(), "top": min(ys) / h, "left": min(xs) / w,
                      "h": (max(ys) - min(ys)) / h, "conf": float(score) * 100})
    return lines


def group_rows(lines):
    """依 Y 座標把同一列的文字合併。"""
    rows = []
    for ln in sorted(lines, key=lambda x: x["top"]):
        center = ln["top"] + ln["h"] / 2
        for row in rows:
            if abs(row["center"] - center) < max(ln["h"], row["h"]) * 0.6:
                row["cells"].append(ln)
                break
        else:
            rows.append({"center": center, "h": ln["h"], "cells": [ln]})
    for row in rows:
        row["cells"].sort(key=lambda c: c["left"])
    return rows


# ---------------- 抽卡紀錄 ----------------
TIME_RE = re.compile(r"(\d{4})[-/.](\d{2})[-/.](\d{2})\s*(\d{2}):(\d{2}):(\d{2})")
HEADER_WORDS = {
    "item name", "item type", "time", "obtained", "time obtained", "time received", "name", "type",
    "signal search", "wish history", "warp history", "history", "previous", "next", "page",
    "物品名稱", "物品名称", "物品類型", "物品类型", "獲取時間", "获取时间", "調頻類型", "调频类型",
    "調頻紀錄", "调频记录", "祈願類型", "祈愿类型", "躍遷類型", "跃迁类型", "名稱", "名称", "類型", "类型", "時間", "时间",
    "上一頁", "下一頁", "上一页", "下一页",
}
STAR = {"5": "S", "4": "A", "3": "B", "五": "S", "四": "A", "三": "B"}
RANK_RES = [
    (re.compile(r"\(?\b([SAB])[- ]?Rank\b\)?"), lambda m: m[1]),
    (re.compile(r"\[([SAB])\]"), lambda m: m[1]),
    (re.compile(r"([SAB])\s*[級级]"), lambda m: m[1]),
    (re.compile(r"[(（]\s*([345五四三])\s*[-\s]?(?:star|星)\s*[)）]", re.I), lambda m: STAR[m[1]]),
]


def _fuzzy(tok, word, threshold):
    """OCR 結果與關鍵字是否「差不多」：允許少字或認錯字（例：角色活動祈 ≈ 角色活動祈願、光 ≈ 光錐）。"""
    if word in tok:
        return True
    if not (CJK.search(tok) and CJK.search(word)) or len(tok) > len(word) + 1:
        return False
    return _cjk_score(tok, word) >= threshold


def parse_gacha(game, lines):
    ocr = game["ocr"]
    channel_labels = list(game["gacha"]["channels"].values())
    out = []
    for row in group_rows(lines):
        text = " ".join(c["text"] for c in row["cells"])
        m = TIME_RE.search(text)
        if not m:
            continue
        t = f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}"
        item_type, name_parts, rank = "", [], None

        def is_noise(s):
            low = s.lower().strip()
            return (any(w in low for w in ocr["channel_words"]) or low in HEADER_WORDS
                    or any(_fuzzy(s, lab, 0.6) for lab in channel_labels))

        for c in row["cells"]:
            cell = TIME_RE.sub(" ", c["text"]).strip()
            for rx, conv in RANK_RES:
                rm = rx.search(cell)
                if rm:
                    rank = conv(rm)
                    cell = rx.sub(" ", cell).strip()
            low = cell.lower()
            # 第一層：整個格子（英文的 "Exclusive Channel" 這類多字詞要整格判斷）
            if not cell or is_noise(cell):
                continue
            if low in ocr["item_types"]:
                item_type = ocr["item_types"][low]
                continue
            # 第二層：OCR 可能把相鄰格子合併（例：「光 輪契」），逐詞判斷
            for tok in cell.split():
                fuzzy_type = next((v for k, v in ocr["item_types"].items() if _fuzzy(tok, k, 0.5)), None)
                if tok.lower() in ocr["item_types"] or (fuzzy_type and len(tok) <= 3):
                    item_type = ocr["item_types"].get(tok.lower(), fuzzy_type)
                elif not is_noise(tok):
                    name_parts.append(tok)
        name = " ".join(name_parts).strip(" -|")
        if not name:
            continue
        agent = agent_by_name(game, name)
        if agent:
            name, rank = agent["name"], rank or agent["rank"]
            item_type = item_type or game["labels"]["agent"]
        out.append({"name": name, "item_type": item_type, "rank": rank or "B", "time": t})
    return out  # 由上到下 = 新到舊


def rows_to_pulls(rows):
    """幫 OCR 資料產生穩定 id：時間戳 + 同一時間內的序號（越舊序號越小）。"""
    pulls, seen = [], {}
    for r in reversed(rows):
        seq = seen.get(r["time"], 0)
        seen[r["time"]] = seq + 1
        ts = int(datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").timestamp())
        pulls.append({**r, "id": f"{ts:010d}{seq:09d}"})
    return pulls


# ---------------- 裝備 ----------------
CJK = re.compile(r"[぀-ヿ一-鿿]")
NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%)?")
DMG_BONUS_WORDS = ("dmg bonus", "傷害加成", "伤害加成")
BULLETS = "·・•●◆◇-–— "


def _cjk_score(text, alias):
    """逐字比對開頭：OCR 認錯繁體字通常是「替換一個字」（擊→撃、異→巽），看同位置相同字的比例。"""
    seg = text[:len(alias)]
    return sum(a == b for a, b in zip(seg, alias)) / len(alias)


def _match_stat(game, text):
    aliases = game["ocr"]["stat_aliases"] + COMMON_ALIASES
    text = text.lstrip(BULLETS)
    is_cjk = bool(CJK.search(text))
    low = text.lower().replace(" ", "") if is_cjk else text.lower()
    for w in DMG_BONUS_WORDS:
        w = w.replace(" ", "") if is_cjk else w
        if w in low or (is_cjk and "加成" in low and "治" not in low):
            idx = low.find(w) if w in low else low.find("加成")
            return "dmg_bonus", low[idx:]
    if is_cjk:
        best, best_score = None, 0.0
        for alias, key in aliases:
            if not CJK.search(alias):
                continue
            s = _cjk_score(low, alias)
            if s > best_score or (s == best_score and best and len(alias) > len(best[0])):
                best, best_score = (alias, key), s
        if best and best_score >= 0.6:
            return best[1], low[len(best[0]):]
        return None, None
    for alias, key in aliases:
        if not CJK.search(alias) and re.match(rf"^{re.escape(alias)}\b", low):
            return key, low[len(alias):]
    return None, None


def parse_disc(game, lines):
    slot_words = game["ocr"]["slot_words"]
    main, subs, slot, in_subs, pending = None, [], None, False, None

    def add(key, val, pct):
        nonlocal main
        if pct and key in ("atk", "hp", "def"):
            key += "_pct"
        entry = {"stat": key, "value": float(val)}
        if main is None and not in_subs:
            main = entry
        elif len(subs) < 4:
            subs.append(entry)

    for row in group_rows(lines):
        text = " ".join(c["text"] for c in row["cells"]).strip()
        low = text.lower()
        if not slot:
            sm = re.search(r"\[\s*(\d)\s*\]|【(\d)】|slot\s*(\d)", low)
            if sm:
                slot = int(next(g for g in sm.groups() if g))
            else:
                compact = low.replace(" ", "")
                slot = next((v for k, v in slot_words.items()
                             if k in compact or k in low or (len(compact) <= len(k) + 1 and _fuzzy(compact, k, 0.6))), None)
        # 標題列：英文，或「主／副」開頭的短字串（OCR 常漏字，例如「主屬性」→「主性」）
        short_cjk = len(low) <= 4 and not re.search(r"\d", low)
        if any(w in low for w in ("sub-stat", "sub stat", "substat")) or (short_cjk and low.startswith("副")):
            in_subs, pending = True, None
            continue
        if "main stat" in low or (short_cjk and low.startswith("主")):
            in_subs, pending = False, None
            continue
        # 主詞條名稱和數值常常分在上下兩行：上一行只有名稱時，這一行的純數字就是它的值
        bare = re.fullmatch(r"\+?\s*(\d+(?:\.\d+)?)\s*(%)?", text.replace(" ", ""))
        if pending and bare:
            add(pending, bare[1], bare[2])
            pending = None
            continue
        # 先去掉絕區零的強化次數標記「+2 14.4%」，否則去空白後會黏成 +214.4
        key, rest = _match_stat(game, re.sub(r"\+\d(?=\s+\d)", " ", text))
        if not key:
            continue
        nums = NUM_RE.findall(rest)
        if not nums:
            pending = key if main is None else None
            continue
        pending = None
        add(key, *nums[-1])
    return {"slot": slot, "main": main, "subs": subs}
