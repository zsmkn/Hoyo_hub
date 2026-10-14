"""Textract OCR：解析調頻紀錄截圖與驅動盤截圖。

注意：Textract 不支援中文，截圖前請把遊戲語言切到英文。
"""
import re
import boto3

from gamedata import agent_by_name, AGENTS

textract = boto3.client("textract")

TIME_RE = re.compile(r"(\d{4})[-/.](\d{2})[-/.](\d{2})\s*(\d{2}):(\d{2}):(\d{2})")
ITEM_TYPES = {"agents": "代理人", "agent": "代理人", "w-engines": "音擎", "w-engine": "音擎", "bangboo": "邦布"}
HEADER_WORDS = {"item name", "item type", "time", "obtained", "time obtained", "name", "type",
                "signal search", "history", "previous", "next", "page"}


def detect_lines(bucket, key):
    r = textract.detect_document_text(Document={"S3Object": {"Bucket": bucket, "Name": key}})
    lines = []
    for b in r["Blocks"]:
        if b["BlockType"] == "LINE":
            bb = b["Geometry"]["BoundingBox"]
            lines.append({"text": b["Text"].strip(), "top": bb["Top"], "left": bb["Left"],
                          "h": bb["Height"], "conf": b.get("Confidence", 0)})
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


# ---------------- 調頻紀錄 ----------------
def parse_gacha(lines):
    out = []
    for row in group_rows(lines):
        text = " ".join(c["text"] for c in row["cells"])
        m = TIME_RE.search(text)
        if not m:
            continue
        t = f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}"
        item_type, name_parts, rank = "", [], None
        for c in row["cells"]:
            tx = c["text"]
            if TIME_RE.search(tx):
                continue
            low = tx.lower()
            if low in ITEM_TYPES:
                item_type = ITEM_TYPES[low]
                continue
            rm = re.search(r"\b([SAB])[- ]?Rank\b|\[([SAB])\]", tx)
            if rm:
                rank = rm[1] or rm[2]
                tx = re.sub(r"\(?\b[SAB][- ]?Rank\b\)?|\[[SAB]\]", "", tx).strip()
            if tx and low not in HEADER_WORDS:
                name_parts.append(tx)
        name = " ".join(name_parts).strip(" -|")
        if not name:
            continue
        agent = agent_by_name(name)
        if agent:
            name, item_type, rank = agent["name"], "代理人", rank or agent["rank"]
        out.append({"name": name, "item_type": item_type, "rank": rank or "B", "time": t})
    return out  # 由上到下 = 新到舊


def rows_to_pulls(rows):
    """幫 OCR 資料產生穩定 id：時間戳 + 同一時間內的序號（越舊序號越小）。"""
    from datetime import datetime
    pulls, seen = [], {}
    for r in reversed(rows):  # 舊到新
        seq = seen.get(r["time"], 0)
        seen[r["time"]] = seq + 1
        ts = int(datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").timestamp())
        pulls.append({**r, "id": f"{ts:010d}{seq:09d}"})
    return pulls


# ---------------- 驅動盤 ----------------
STAT_ALIASES = [
    ("crit rate", "crit_rate"), ("crit dmg", "crit_dmg"),
    ("pen ratio", "pen_ratio"), ("pen", "pen"),
    ("anomaly proficiency", "ap"), ("anomaly mastery", "am"),
    ("energy regen", "er"), ("impact", "impact"),
    ("physical dmg bonus", "dmg_bonus"), ("fire dmg bonus", "dmg_bonus"),
    ("ice dmg bonus", "dmg_bonus"), ("electric dmg bonus", "dmg_bonus"),
    ("ether dmg bonus", "dmg_bonus"),
    ("atk", "atk"), ("hp", "hp"), ("def", "def"),
]
NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%)?")


def _match_stat(text):
    low = text.lower()
    for alias, key in STAT_ALIASES:
        if re.match(rf"^{re.escape(alias)}\b", low):
            return key, text[len(alias):]
    return None, None


def parse_disc(lines):
    main, subs, slot, in_subs = None, [], None, False
    for row in group_rows(lines):
        text = " ".join(c["text"] for c in row["cells"]).strip()
        low = text.lower()
        sm = re.search(r"\[(\d)\]|slot\s*(\d)", low)
        if sm and not slot:
            slot = int(sm[1] or sm[2])
        if "sub-stat" in low or "sub stat" in low or "substat" in low:
            in_subs = True
            continue
        if "main stat" in low:
            in_subs = False
            continue
        key, rest = _match_stat(text)
        if not key:
            continue
        rest = re.sub(r"\+\d\b", "", rest)  # 去掉強化次數標記 +1
        nums = NUM_RE.findall(rest)
        if not nums:
            continue
        val, pct = nums[-1]
        if pct and key in ("atk", "hp", "def"):
            key += "_pct"
        entry = {"stat": key, "value": float(val)}
        if main is None and not in_subs:
            main = entry
        elif len(subs) < 4:
            subs.append(entry)
    return {"slot": slot, "main": main, "subs": subs}


def all_agent_names():
    return [a["name_en"] for a in AGENTS]
