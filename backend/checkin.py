"""HoYoLAB 絕區零每日簽到。

- 排程：EventBridge 每天 00:10 (UTC+8) 觸發 handler
- 也可由 API / Discord bot 手動觸發 run_for_profile
"""
import re
from datetime import datetime, timezone, timedelta

from common import table, get_item, query_all, to_ddb, now_ts, http_json, post_discord_webhook

ACT_ID = "e202406031448091"
BASE = "https://sg-public-api.hoyolab.com/event/luna/zzz/os"
TZ8 = timezone(timedelta(hours=8))


def _headers(cookie):
    return {
        "Cookie": cookie,
        "Origin": "https://act.hoyolab.com",
        "Referer": "https://act.hoyolab.com/",
        "x-rpc-signgame": "zzz",
        "x-rpc-app_version": "2.34.1",
        "x-rpc-client_type": "4",
    }


def normalize_cookie(raw):
    """接受整串 cookie 或只有 ltuid_v2 / ltoken_v2 的格式，只保留簽到需要的欄位。"""
    # 最小化保存：簽到只需要這三個；cookie_token_v2 等權限較大的欄位一律丟掉，不存進資料庫
    keep = ("ltoken_v2", "ltuid_v2", "ltmid_v2")
    pairs = dict(re.findall(r"([A-Za-z0-9_]+)=([^;\s]+)", raw or ""))
    picked = {k: pairs[k] for k in keep if k in pairs}
    if "ltoken_v2" not in picked or "ltuid_v2" not in picked:
        return None, None
    return "; ".join(f"{k}={v}" for k, v in picked.items()), picked["ltuid_v2"]


def do_checkin(cookie):
    h = _headers(cookie)
    q = f"?lang=zh-tw&act_id={ACT_ID}"
    info = http_json(f"{BASE}/info{q}", headers=h)
    if info.get("retcode") != 0:
        return {"status": "error", "message": f"Cookie 無效或過期（{info.get('retcode')}: {info.get('message')}）"}

    if info["data"].get("is_sign"):
        status = "already"
        days = info["data"].get("total_sign_day", 0)
    else:
        sign = http_json(f"{BASE}/sign?lang=zh-tw", method="POST", headers=h, body={"act_id": ACT_ID})
        rc = sign.get("retcode")
        gt = (sign.get("data") or {}).get("gt_result") or {}
        if rc == -5003:
            status = "already"
        elif rc != 0:
            return {"status": "error", "message": f"簽到失敗（{rc}: {sign.get('message')}）"}
        elif gt.get("is_risk") or gt.get("risk_code") not in (None, 0):
            return {"status": "captcha", "message": "HoYoLAB 要求人機驗證，請手動到網頁簽到一次"}
        else:
            status = "signed"
        days = info["data"].get("total_sign_day", 0) + (1 if status == "signed" else 0)

    reward = None
    home = http_json(f"{BASE}/home{q}", headers=h)
    awards = (home.get("data") or {}).get("awards") or []
    if 0 < days <= len(awards):
        a = awards[days - 1]
        reward = f"{a.get('name')} x{a.get('cnt')}"
    msg = {"signed": "簽到成功", "already": "今天已經簽到過了"}[status]
    return {"status": status, "message": msg, "total_days": days, "reward": reward}


def run_for_profile(pid, notify=True):
    cfg = get_item(f"PROFILE#{pid}", "HOYOLAB")
    if not cfg:
        return {"status": "error", "message": "尚未設定 HoYoLAB Cookie"}
    try:
        result = do_checkin(cfg["cookie"])
    except Exception as e:  # noqa: BLE001
        result = {"status": "error", "message": f"連線失敗：{e}"}
    today = datetime.now(TZ8).strftime("%Y-%m-%d")
    result["date"] = today
    result["at"] = now_ts()
    table.put_item(Item=to_ddb({
        "PK": f"PROFILE#{pid}", "SK": f"CHECKIN#{today}", **result,
        "ttl": now_ts() + 90 * 86400,
    }))
    table.update_item(
        Key={"PK": f"PROFILE#{pid}", "SK": "HOYOLAB"},
        UpdateExpression="SET last_result = :r",
        ExpressionAttributeValues={":r": to_ddb(result)},
    )
    if notify and cfg.get("webhook"):
        icon = {"signed": "✅", "already": "☑️"}.get(result["status"], "⚠️")
        text = f"{icon} 絕區零 HoYoLAB 簽到｜{result['message']}"
        if result.get("reward"):
            text += f"\n🎁 今日獎勵：{result['reward']}（本月第 {result['total_days']} 天）"
        post_discord_webhook(cfg["webhook"], text)
    return result


def handler(event, context):
    """EventBridge 排程進入點：幫所有啟用自動簽到的人簽到。"""
    members = query_all("CHECKIN_SET")
    results = {}
    for m in members:
        pid = m["SK"]
        results[pid] = run_for_profile(pid)["status"]
    print("checkin results:", results)
    return {"count": len(members), "results": results}
