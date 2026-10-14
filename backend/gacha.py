"""抽卡紀錄：從官方紀錄網址抓資料、存取、統計分析。"""
import time
from urllib.parse import urlparse, parse_qsl, urlencode

from common import table, query_all, to_ddb, now_ts, http_json, ApiError
from gamedata import GACHA_TYPES, PITY_MAX, EXPECTED_S_PULLS, is_standard_s

API_HOSTS = {
    "global": "https://public-operation-nap-sg.hoyoverse.com",
    "cn": "https://public-operation-nap.mihoyo.com",
}
API_PATH = "/common/gacha_record/api/getGachaLog"
RANK = {"4": "S", "3": "A", "2": "B"}


# ---------------- 抓取 ----------------
def parse_gacha_url(url):
    url = (url or "").strip()
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    if "authkey" not in q and "?" in p.fragment:
        q = dict(parse_qsl(p.fragment.split("?", 1)[1], keep_blank_values=True))
    if not q.get("authkey"):
        raise ApiError(400, "網址裡找不到 authkey，請確認貼上的是完整的調頻紀錄網址")
    is_cn = q.get("game_biz", "").endswith("_cn") or "mihoyo.com" in p.netloc
    return API_HOSTS["cn" if is_cn else "global"], q


def fetch_type(host, base, gacha_type, stop_at_id=None, on_page=None):
    """往前翻頁抓某一頻段；遇到已存在的 id 就停（增量更新）。"""
    out, end_id = [], "0"
    while True:
        params = dict(base)
        params.update({
            "real_gacha_type": gacha_type, "size": "20", "end_id": end_id,
            "lang": "zh-tw", "authkey_ver": base.get("authkey_ver", "1"),
            "sign_type": base.get("sign_type", "2"),
            "auth_appid": base.get("auth_appid", "webview_gacha"),
        })
        data = http_json(f"{host}{API_PATH}?{urlencode(params)}")
        rc = data.get("retcode")
        if rc == -101:
            raise ApiError(400, "authkey 已過期，請重新開啟遊戲內的調頻紀錄再取得網址")
        if rc != 0:
            raise ApiError(400, f"官方 API 錯誤 {rc}: {data.get('message')}")
        page = (data.get("data") or {}).get("list") or []
        for item in page:
            if stop_at_id and int(item["id"]) <= int(stop_at_id):
                return out
            out.append(item)
        if on_page:
            on_page(len(out))
        if len(page) < 20:
            return out
        end_id = page[-1]["id"]
        time.sleep(0.3)


def latest_id(pid, gacha_type):
    r = table.query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={":pk": f"PROFILE#{pid}", ":sk": f"PULL#{gacha_type}#"},
        ScanIndexForward=False, Limit=1, ProjectionExpression="id, src",
    ).get("Items", [])
    # OCR 匯入的假 id 不能當增量依據
    return r[0]["id"] if r and r[0].get("src") == "api" else None


def save_pulls(pid, gacha_type, items, src):
    with table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as bw:
        for it in items:
            bw.put_item(Item=to_ddb({
                "PK": f"PROFILE#{pid}",
                "SK": f"PULL#{gacha_type}#{it['id']}",
                "id": str(it["id"]),
                "gacha_type": gacha_type,
                "name": it.get("name", ""),
                "item_type": it.get("item_type", ""),
                "rank": RANK.get(str(it.get("rank_type")), it.get("rank", "B")),
                "time": it.get("time", ""),
                "game_uid": str(it.get("uid", "")),
                "src": src,
            }))
    return len(items)


def import_from_url(pid, url, progress=None):
    host, base = parse_gacha_url(url)
    summary, game_uid = {}, None
    for gt, label in GACHA_TYPES.items():
        if progress:
            progress(f"正在讀取 {label}…")
        items = fetch_type(
            host, base, gt, stop_at_id=latest_id(pid, gt),
            on_page=(lambda n, label=label: progress and progress(f"正在讀取 {label}…已取得 {n} 筆")),
        )
        if items and not game_uid:
            game_uid = items[0].get("uid")
        summary[gt] = save_pulls(pid, gt, items, "api")
    if game_uid:
        table.update_item(
            Key={"PK": f"PROFILE#{pid}", "SK": "META"},
            UpdateExpression="SET game_uid = :u",
            ExpressionAttributeValues={":u": str(game_uid)},
        )
    return summary


# ---------------- 統計 ----------------
def load_pulls(pid, gacha_type=None):
    prefix = f"PULL#{gacha_type}#" if gacha_type else "PULL#"
    items = query_all(f"PROFILE#{pid}", prefix)
    items.sort(key=lambda p: (p.get("time", ""), int(p["id"])))
    return items


def luck_label(idx):
    if idx is None:
        return "資料不足"
    if idx >= 25:
        return "超級歐皇"
    if idx >= 10:
        return "歐洲人"
    if idx > -10:
        return "普通人"
    if idx > -25:
        return "非洲人"
    return "非酋本酋"


def channel_stats(gacha_type, pulls):
    counter = a_counter = 0
    s_hist, guaranteed = [], False
    won = lost = a_count = b_count = 0
    for p in pulls:
        counter += 1
        a_counter += 1
        rank = p.get("rank")
        if rank == "A":
            a_count += 1
            a_counter = 0
        elif rank == "B":
            b_count += 1
        if rank != "S":
            continue
        result = None
        if gacha_type in ("2", "3"):
            if guaranteed:
                result, guaranteed = "guaranteed", False
            elif is_standard_s(gacha_type, p.get("name")):
                result, guaranteed = "lost", True
                lost += 1
            else:
                result = "won"
                won += 1
        s_hist.append({"name": p.get("name"), "time": p.get("time"), "pulls": counter, "result": result})
        counter = 0

    s_count = len(s_hist)
    avg = round(sum(s["pulls"] for s in s_hist) / s_count, 1) if s_count else None
    exp = EXPECTED_S_PULLS[gacha_type]
    luck = round((exp - avg) / exp * 100) if avg else None
    attempts = won + lost
    return {
        "gacha_type": gacha_type,
        "label": GACHA_TYPES[gacha_type],
        "total": len(pulls),
        "s_count": s_count,
        "a_count": a_count,
        "b_count": b_count,
        "current_pity": counter,
        "pity_max": PITY_MAX[gacha_type],
        "a_pity": a_counter,
        "guaranteed": guaranteed,
        "avg_s_pulls": avg,
        "expected_s_pulls": exp,
        "won_5050": won,
        "lost_5050": lost,
        "lose_rate": round(lost / attempts * 100, 1) if attempts else None,
        "luck_index": luck,
        "luck_label": luck_label(luck),
        "s_history": list(reversed(s_hist)),
    }


def profile_stats(pid):
    pulls = load_pulls(pid)
    by_type = {gt: [] for gt in GACHA_TYPES}
    for p in pulls:
        by_type.setdefault(p["gacha_type"], []).append(p)
    channels = {gt: channel_stats(gt, ps) for gt, ps in by_type.items() if gt in GACHA_TYPES}
    total = sum(c["total"] for c in channels.values())
    return {
        "total_pulls": total,
        "polychrome_spent": total * 160,  # 每抽 160 菲林
        "channels": channels,
    }


def update_global(pid, stats):
    """把個人統計的匿名摘要寫進 GLOBAL 分區，供全服統計彙整。"""
    entry = {
        gt: {
            "s_count": c["s_count"],
            "s_pulls_sum": sum(s["pulls"] for s in c["s_history"]),
            "won": c["won_5050"], "lost": c["lost_5050"], "total": c["total"],
        }
        for gt, c in stats["channels"].items()
    }
    table.put_item(Item=to_ddb({"PK": "GLOBAL", "SK": f"P#{pid}", "channels": entry, "updated": now_ts()}))


def global_stats():
    rows = query_all("GLOBAL", "P#")
    out = {}
    for gt, label in GACHA_TYPES.items():
        s = sum(int(r["channels"].get(gt, {}).get("s_count", 0)) for r in rows)
        ps = sum(int(r["channels"].get(gt, {}).get("s_pulls_sum", 0)) for r in rows)
        won = sum(int(r["channels"].get(gt, {}).get("won", 0)) for r in rows)
        lost = sum(int(r["channels"].get(gt, {}).get("lost", 0)) for r in rows)
        total = sum(int(r["channels"].get(gt, {}).get("total", 0)) for r in rows)
        out[gt] = {
            "label": label,
            "total_pulls": total,
            "s_count": s,
            "avg_s_pulls": round(ps / s, 1) if s else None,
            "lose_rate": round(lost / (won + lost) * 100, 1) if won + lost else None,
        }
    return {"players": len(rows), "channels": out}
