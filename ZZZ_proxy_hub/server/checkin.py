"""HoYoLAB 每日簽到（三款遊戲通用；同一組 Cookie 共用）。

設定：PROFILE#{pid} / HOYOLAB  {cookie, ltuid, webhook, games: {zzz: bool, gi: bool, hsr: bool}, last: {game: 結果}}
紀錄：PROFILE#{pid} / CHECKIN#{game}#{date}
名單：CHECKIN_SET#{game} / {pid}
排程：伺服器開著時每 30 分鐘檢查一次，00:10（UTC+8）後幫還沒簽到的人簽；電腦沒開的日子下次啟動自動補簽。
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from .common import http_json, now_ts, post_discord_webhook
from .games import GAMES
from .store import store

TZ8 = timezone(timedelta(hours=8))


def _headers(game, cookie):
    return {"Cookie": cookie, "Origin": "https://act.hoyolab.com", "Referer": "https://act.hoyolab.com/",
            "x-rpc-app_version": "2.34.1", "x-rpc-client_type": "4", **game["checkin"]["headers"]}


def normalize_cookie(raw):
    """接受整串 cookie 或只有 ltuid_v2 / ltoken_v2 的格式，只保留簽到需要的欄位。"""
    # 最小化保存：簽到只需要這三個；cookie_token_v2 等權限較大的欄位一律丟掉，不存進資料庫
    keep = ("ltoken_v2", "ltuid_v2", "ltmid_v2")
    pairs = dict(re.findall(r"([A-Za-z0-9_]+)=([^;\s]+)", raw or ""))
    picked = {k: pairs[k] for k in keep if k in pairs}
    if "ltoken_v2" not in picked or "ltuid_v2" not in picked:
        return None, None
    return "; ".join(f"{k}={v}" for k, v in picked.items()), picked["ltuid_v2"]


def do_checkin(game, cookie):
    c = game["checkin"]
    h = _headers(game, cookie)
    q = f"?lang=zh-tw&act_id={c['act_id']}"
    info = http_json(f"{c['base']}/info{q}", headers=h)
    if info.get("retcode") != 0:
        return {"status": "error", "message": f"Cookie 無效或過期（{info.get('retcode')}: {info.get('message')}）"}

    if info["data"].get("is_sign"):
        status = "already"
        days = info["data"].get("total_sign_day", 0)
    else:
        sign = http_json(f"{c['base']}/sign?lang=zh-tw", method="POST", headers=h, body={"act_id": c["act_id"]})
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
    home = http_json(f"{c['base']}/home{q}", headers=h)
    awards = (home.get("data") or {}).get("awards") or []
    if 0 < days <= len(awards):
        a = awards[days - 1]
        reward = f"{a.get('name')} x{a.get('cnt')}"
    msg = {"signed": "簽到成功", "already": "今天已經簽到過了"}[status]
    return {"status": status, "message": msg, "total_days": days, "reward": reward}


def today():
    return datetime.now(TZ8).strftime("%Y-%m-%d")


def run_for_profile(game, pid, notify=True):
    cfg = store.get(f"PROFILE#{pid}", "HOYOLAB")
    if not cfg:
        return {"status": "error", "message": "尚未設定 HoYoLAB Cookie"}
    try:
        result = do_checkin(game, cfg["cookie"])
    except Exception as e:  # noqa: BLE001
        result = {"status": "error", "message": f"連線失敗：{e}"}
    result.update(date=today(), at=now_ts(), game=game["id"])
    key = f"CHECKIN#{game['id']}#{result['date']}"
    prev = store.get(f"PROFILE#{pid}", key) or {}
    store.put(f"PROFILE#{pid}", key, {**result, "attempts": int(prev.get("attempts", 0)) + 1},
              ttl=now_ts() + 90 * 86400)
    last = cfg.get("last") or {}
    last[game["id"]] = result
    store.update(f"PROFILE#{pid}", "HOYOLAB", last=last)
    if notify and cfg.get("webhook"):
        icon = {"signed": "✅", "already": "☑️"}.get(result["status"], "⚠️")
        text = f"{icon} {game['name']} HoYoLAB 簽到｜{result['message']}"
        if result.get("reward"):
            text += f"\n🎁 今日獎勵：{result['reward']}（本月第 {result['total_days']} 天）"
        post_discord_webhook(cfg["webhook"], text)
    return result


def run_due():
    """幫所有啟用自動簽到、今天還沒成功簽到的人簽到（每遊戲每天最多重試 3 次）。"""
    results = {}
    for gid, game in GAMES.items():
        for m in store.query(f"CHECKIN_SET#{gid}"):
            pid = m["SK"]
            done = store.get(f"PROFILE#{pid}", f"CHECKIN#{gid}#{today()}") or {}
            if done.get("status") in ("signed", "already") or int(done.get("attempts", 0)) >= 3:
                continue
            results[f"{gid}:{pid}"] = run_for_profile(game, pid)["status"]
    if results:
        print("[checkin]", results, flush=True)
    return results


def _scheduler():
    time.sleep(15)
    while True:
        now = datetime.now(TZ8)
        try:
            if now.hour > 0 or now.minute >= 10:
                run_due()
        except Exception as e:  # noqa: BLE001
            print("[checkin] scheduler error:", e, flush=True)
        time.sleep(30 * 60)


def start_scheduler():
    threading.Thread(target=_scheduler, daemon=True, name="checkin-scheduler").start()
