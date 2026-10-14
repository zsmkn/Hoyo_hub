"""抽卡紀錄（三款遊戲通用）：從官方紀錄網址抓資料、存取、統計分析。

資料鍵：PROFILE#{pid} / PULL#{game}#{頻段}#{id}；全站統計：GLOBAL#{game} / P#{pid}
"""
import time
from urllib.parse import parse_qsl, urlencode, urlparse

from .common import ApiError, http_json, now_ts
from .games import is_standard
from .store import store


def _pk(pid):
    return f"PROFILE#{pid}"


# ---------------- 抓取 ----------------
def parse_gacha_url(game, url):
    g = game["gacha"]
    url = (url or "").strip()
    p = urlparse(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    if "authkey" not in q and "?" in p.fragment:
        q = dict(parse_qsl(p.fragment.split("?", 1)[1], keep_blank_values=True))
    if not q.get("authkey"):
        raise ApiError(400, f"網址裡找不到 authkey，請確認貼上的是完整的{game['labels']['record_name']}網址")
    is_cn = q.get("game_biz", "").endswith(g["cn_biz_suffix"]) or g["cn_domain"] in p.netloc
    return g["hosts"]["cn" if is_cn else "global"], q


def fetch_type(game, host, base, gacha_type, stop_at_id=None, on_page=None):
    """往前翻頁抓某一卡池；遇到已存在的 id 就停（增量更新）。"""
    g = game["gacha"]
    out, end_id, page_no = [], "0", 1
    while True:
        params = dict(base)
        params.update({
            g["type_param"]: gacha_type, "size": "20", "end_id": end_id, "page": str(page_no),
            "lang": "zh-tw", "authkey_ver": base.get("authkey_ver", "1"), "sign_type": base.get("sign_type", "2"),
        })
        data = http_json(f"{host}{g['path']}?{urlencode(params)}")
        rc = data.get("retcode")
        if rc == -101:
            raise ApiError(400, f"authkey 已過期，請重新開啟遊戲內的{game['labels']['record_name']}再取得網址")
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
        end_id, page_no = page[-1]["id"], page_no + 1
        time.sleep(0.3)


def channel_of(game, fetched_type):
    return game["gacha"]["channel_of"].get(fetched_type, fetched_type)


def latest_api_id(game, pid, fetched_type):
    """某個 API 卡池類型目前存到的最新 id（OCR 匯入的假 id 不算）。"""
    ch = channel_of(game, fetched_type)
    ids = [int(p["id"]) for p in store.query(_pk(pid), f"PULL#{game['id']}#{ch}#")
           if p.get("src") == "api" and p.get("ft", ch) == fetched_type]
    return str(max(ids)) if ids else None


def save_pulls(game, pid, channel, items, src, fetched_type=None):
    rank_map = game["gacha"]["rank_map"]
    rows = [(_pk(pid), f"PULL#{game['id']}#{channel}#{it['id']}", {
        "id": str(it["id"]), "gacha_type": channel, "ft": fetched_type or channel,
        "name": it.get("name", ""), "item_type": it.get("item_type", ""),
        "rank": rank_map.get(str(it.get("rank_type")), it.get("rank", "B")),
        "time": it.get("time", ""), "game_uid": str(it.get("uid", "")), "src": src,
    }) for it in items]
    store.put_many(rows)
    return len(rows)


def import_from_url(game, pid, url, progress=None):
    host, base = parse_gacha_url(game, url)
    channels = game["gacha"]["channels"]
    summary, game_uid = {}, None
    for ft in game["gacha"]["fetch_types"]:
        ch = channel_of(game, ft)
        label = channels[ch]
        if progress:
            progress(f"正在讀取 {label}…")
        items = fetch_type(game, host, base, ft, stop_at_id=latest_api_id(game, pid, ft),
                           on_page=(lambda n, label=label: progress and progress(f"正在讀取 {label}…已取得 {n} 筆")))
        if items and not game_uid:
            game_uid = items[0].get("uid")
        summary[ch] = summary.get(ch, 0) + save_pulls(game, pid, ch, items, "api", ft)
    if game_uid:
        store.update(_pk(pid), "META", **{f"game_uid_{game['id']}": str(game_uid)})
    return summary


# ---------------- 統計 ----------------
def load_pulls(game, pid, channel=None):
    prefix = f"PULL#{game['id']}#{channel}#" if channel else f"PULL#{game['id']}#"
    items = store.query(_pk(pid), prefix)
    items.sort(key=lambda p: (p.get("time", ""), int(p["id"])))
    return items


def delete_pulls(game, pid):
    n = store.delete_prefix(_pk(pid), f"PULL#{game['id']}#")
    store.delete(f"GLOBAL#{game['id']}", f"P#{pid}")
    return n


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


def channel_stats(game, channel, pulls):
    g = game["gacha"]
    has_5050 = channel in g["standard"]
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
        if has_5050:
            if guaranteed:
                result, guaranteed = "guaranteed", False
            elif is_standard(game, channel, p.get("name")):
                result, guaranteed = "lost", True
                lost += 1
            else:
                result = "won"
                won += 1
        s_hist.append({"name": p.get("name"), "time": p.get("time"), "pulls": counter, "result": result})
        counter = 0

    s_count = len(s_hist)
    avg = round(sum(s["pulls"] for s in s_hist) / s_count, 1) if s_count else None
    exp = g["expected"][channel]
    luck = round((exp - avg) / exp * 100) if avg else None
    attempts = won + lost
    return {
        "gacha_type": channel, "label": g["channels"][channel], "total": len(pulls),
        "s_count": s_count, "a_count": a_count, "b_count": b_count,
        "current_pity": counter, "pity_max": g["pity"][channel], "a_pity": a_counter, "guaranteed": guaranteed,
        "avg_s_pulls": avg, "expected_s_pulls": exp, "won_5050": won, "lost_5050": lost,
        "lose_rate": round(lost / attempts * 100, 1) if attempts else None,
        "luck_index": luck, "luck_label": luck_label(luck), "s_history": list(reversed(s_hist)),
    }


def profile_stats(game, pid):
    channels = game["gacha"]["channels"]
    by_ch = {ch: [] for ch in channels}
    for p in load_pulls(game, pid):
        by_ch.setdefault(p["gacha_type"], []).append(p)
    stats = {ch: channel_stats(game, ch, ps) for ch, ps in by_ch.items() if ch in channels}
    total = sum(c["total"] for c in stats.values())
    return {"total_pulls": total, "currency": game["labels"]["currency"], "polychrome_spent": total * 160,
            "channels": stats}


def update_global(game, pid, stats):
    """把個人統計的匿名摘要寫進 GLOBAL#{game} 分區，供全站統計彙整。"""
    entry = {ch: {"s_count": c["s_count"], "s_pulls_sum": sum(s["pulls"] for s in c["s_history"]),
                  "won": c["won_5050"], "lost": c["lost_5050"], "total": c["total"]}
             for ch, c in stats["channels"].items()}
    store.put(f"GLOBAL#{game['id']}", f"P#{pid}", {"channels": entry, "updated": now_ts()})


def global_stats(game):
    rows = store.query(f"GLOBAL#{game['id']}", "P#")
    out = {}
    for ch, label in game["gacha"]["channels"].items():
        def total_of(key):
            return sum(int(r["channels"].get(ch, {}).get(key, 0)) for r in rows)
        s, ps, won, lost = total_of("s_count"), total_of("s_pulls_sum"), total_of("won"), total_of("lost")
        out[ch] = {"label": label, "total_pulls": total_of("total"), "s_count": s,
                   "avg_s_pulls": round(ps / s, 1) if s else None,
                   "lose_rate": round(lost / (won + lost) * 100, 1) if won + lost else None}
    return {"players": len(rows), "channels": out}
