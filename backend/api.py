"""API Gateway (HTTP API) 進入點：所有 /api/* 路由。"""
import os
import re
import json
import base64
import secrets

import boto3
from botocore.config import Config
from boto3.dynamodb.conditions import Attr

from common import (
    table, ApiError, resp, to_ddb, from_ddb, now_ts, sha256, new_token, new_id,
    get_item, query_all,
)
import gacha
import checkin
from disc import score_disc
from gamedata import AGENTS, AGENTS_BY_ID, GACHA_TYPES, STAT_LABELS, ROLE_WEIGHTS, SPECIALTIES

UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "")
WEB_BUCKET = os.environ.get("WEB_BUCKET", "")
WORKER_FN = os.environ.get("WORKER_FN", "")
BOT_API_KEY = os.environ.get("BOT_API_KEY", "")

s3 = boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
lambda_client = boto3.client("lambda")
ec2 = boto3.client("ec2")
sqs = boto3.client("sqs")
QUEUE_URL = os.environ.get("QUEUE_URL", "")
WORKER_INSTANCE_ID = os.environ.get("WORKER_INSTANCE_ID", "")

ROUTES = []


def route(method, pattern, auth=None):
    """auth: None=公開, 'user'=需要個人 token, 'bot'=需要 Bot API Key"""
    def deco(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), auth, fn))
        return fn
    return deco


# ================= 個人檔案 =================
@route("POST", "/api/profile")
def create_profile(req):
    pid, token = new_id(12), new_token()
    table.put_item(Item={"PK": f"PROFILE#{pid}", "SK": "META", "created": now_ts()})
    table.put_item(Item={"PK": f"TOKEN#{sha256(token)}", "SK": "PROFILE", "pid": pid})
    return 201, {"profile_id": pid, "token": token}


@route("GET", "/api/profile", auth="user")
def get_profile(req):
    meta = get_item(f"PROFILE#{req['pid']}", "META") or {}
    hl = get_item(f"PROFILE#{req['pid']}", "HOYOLAB")
    return 200, {
        "profile_id": req["pid"],
        "game_uid": meta.get("game_uid"),
        "discord_id": meta.get("discord_id"),
        "hoyolab_enabled": bool(hl and hl.get("enabled")),
    }


@route("POST", "/api/profile/linkcode", auth="user")
def create_link_code(req):
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    table.put_item(Item={"PK": f"LINKCODE#{code}", "SK": "PROFILE", "pid": req["pid"],
                         "ttl": now_ts() + 600})
    return 200, {"code": code, "expires_in": 600}


# ================= 抽卡紀錄 =================
def _new_job(pid, kind, **extra):
    job_id = new_id(16)
    table.put_item(Item=to_ddb({
        "PK": f"JOB#{job_id}", "SK": "META", "pid": pid, "kind": kind,
        "status": "pending", "message": "排隊中…", "created": now_ts(),
        "ttl": now_ts() + 86400, **extra,
    }))
    return job_id


@route("POST", "/api/import/url", auth="user")
def import_url(req):
    url = (req["body"].get("url") or "").strip()
    gacha.parse_gacha_url(url)  # 先驗證格式
    job_id = _new_job(req["pid"], "import_url")
    lambda_client.invoke(
        FunctionName=WORKER_FN, InvocationType="Event",
        Payload=json.dumps({"action": "import_url", "job_id": job_id, "pid": req["pid"], "url": url}),
    )
    return 202, {"job_id": job_id}


@route("POST", "/api/upload-url", auth="user")
def upload_url(req):
    kind = req["body"].get("kind")
    if kind not in ("gacha", "disc", "depth", "model3d"):
        raise ApiError(400, "kind 必須是 gacha、disc、depth 或 model3d")
    ctype = req["body"].get("content_type", "image/png")
    art = kind in ("depth", "model3d")
    allowed = ("image/png", "image/jpeg", "image/webp") if art else ("image/png", "image/jpeg")
    if ctype not in allowed:
        raise ApiError(400, "不支援的圖片格式")
    if art:
        extra = {"title": (req["body"].get("title") or "").strip()[:40],
                 "public": bool(req["body"].get("public"))}
        msg = "等待上傳圖片…"
        if kind == "model3d":
            # 防濫用：3D 由 EC2 運算，佇列太長就先拒絕，避免有人灌任務讓主機一直開著
            if _worker_info()["queue"] >= MODEL3D_QUEUE_MAX:
                raise ApiError(429, "目前排隊的 3D 任務太多，請稍後再試")
            msg = "排隊中，" + _wake_worker()
        job_id = _new_job(req["pid"], kind, message=msg, **extra)
    else:
        gt = str(req["body"].get("gacha_type", "2"))
        job_id = _new_job(req["pid"], f"ocr_{kind}", gacha_type=gt, message="等待上傳圖片…")
    key = f"uploads/{kind}/{req['pid']}/{job_id}"
    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": UPLOAD_BUCKET, "Key": key, "ContentType": ctype},
        ExpiresIn=300,
    )
    return 200, {"job_id": job_id, "upload_url": url, "content_type": ctype}


@route("GET", r"/api/jobs/(?P<job_id>[a-f0-9]+)", auth="user")
def get_job(req):
    job = get_item(f"JOB#{req['params']['job_id']}", "META")
    if not job or job["pid"] != req["pid"]:
        raise ApiError(404, "找不到這個工作")
    return 200, {k: v for k, v in job.items() if k not in ("PK", "SK", "pid", "ttl")}


@route("POST", "/api/import/rows", auth="user")
def import_rows(req):
    """匯入 OCR 結果（使用者確認/修正後送回）。"""
    from ocr import rows_to_pulls
    gt = str(req["body"].get("gacha_type"))
    if gt not in GACHA_TYPES:
        raise ApiError(400, "gacha_type 錯誤")
    rows = req["body"].get("rows") or []
    clean = []
    for r in rows[:200]:
        if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", r.get("time", "")):
            raise ApiError(400, f"時間格式錯誤：{r.get('time')}")
        clean.append({"name": str(r.get("name", ""))[:40], "item_type": str(r.get("item_type", ""))[:10],
                      "rank": r.get("rank") if r.get("rank") in ("S", "A", "B") else "B",
                      "time": r["time"]})
    n = gacha.save_pulls(req["pid"], gt, rows_to_pulls(clean), "ocr")
    stats = gacha.profile_stats(req["pid"])
    gacha.update_global(req["pid"], stats)
    return 200, {"imported": n}


@route("GET", "/api/stats", auth="user")
def my_stats(req):
    stats = gacha.profile_stats(req["pid"])
    return 200, stats


@route("GET", "/api/pulls", auth="user")
def my_pulls(req):
    gt = req["query"].get("gacha_type")
    pulls = gacha.load_pulls(req["pid"], gt)
    return 200, {"pulls": [_pull_view(p) for p in reversed(pulls)]}


@route("DELETE", "/api/pulls", auth="user")
def clear_pulls(req):
    items = query_all(f"PROFILE#{req['pid']}", "PULL#", ProjectionExpression="PK, SK")
    with table.batch_writer() as bw:
        for it in items:
            bw.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
    table.delete_item(Key={"PK": "GLOBAL", "SK": f"P#{req['pid']}"})
    return 200, {"deleted": len(items)}


@route("GET", "/api/global")
def global_stats(req):
    return 200, gacha.global_stats()


def _pull_view(p):
    return {k: p.get(k) for k in ("id", "gacha_type", "name", "item_type", "rank", "time")}


# ================= 驅動盤 / 角色 / 配隊 =================
@route("GET", "/api/agents")
def list_agents(req):
    return 200, {"agents": AGENTS, "stat_labels": STAT_LABELS,
                 "specialties": SPECIALTIES, "role_weights": ROLE_WEIGHTS}


@route("POST", "/api/disc/score")
def disc_score(req):
    b = req["body"]
    return 200, score_disc(b.get("character"), b.get("role"), b.get("slot"), b.get("main"), b.get("subs"))


@route("GET", "/api/teams")
def list_teams(req):
    teams = query_all("TEAMS", "TEAM#")
    teams.sort(key=lambda t: -int(t.get("votes", 0)))
    out = []
    for t in teams:
        out.append({
            "id": t["SK"].split("#", 1)[1], "name": t["name"], "note": t.get("note", ""),
            "members": [AGENTS_BY_ID[m] for m in t["members"] if m in AGENTS_BY_ID],
            "votes": t.get("votes", 0), "author": t.get("author", "玩家"),
        })
    return 200, {"teams": out}


@route("POST", "/api/teams", auth="user")
def create_team(req):
    b = req["body"]
    members = b.get("members") or []
    if len(members) != 3 or len(set(members)) != 3 or any(m not in AGENTS_BY_ID for m in members):
        raise ApiError(400, "配隊需要 3 位不重複的代理人")
    name = (b.get("name") or "").strip()[:30]
    if not name:
        raise ApiError(400, "請輸入隊伍名稱")
    tid = new_id(10)
    table.put_item(Item={
        "PK": "TEAMS", "SK": f"TEAM#{tid}", "name": name, "members": members,
        "note": (b.get("note") or "").strip()[:200], "votes": 0,
        "author": "玩家", "owner": req["pid"], "created": now_ts(),
    })
    return 201, {"id": tid}


@route("POST", r"/api/teams/(?P<tid>[a-f0-9]+)/vote", auth="user")
def vote_team(req):
    tid = req["params"]["tid"]
    if not get_item("TEAMS", f"TEAM#{tid}"):
        raise ApiError(404, "找不到隊伍")
    try:
        table.put_item(Item={"PK": f"TEAMVOTE#{tid}", "SK": req["pid"], "at": now_ts()},
                       ConditionExpression=Attr("PK").not_exists())
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        raise ApiError(409, "你已經投過這個隊伍了")
    r = table.update_item(Key={"PK": "TEAMS", "SK": f"TEAM#{tid}"},
                          UpdateExpression="ADD votes :one",
                          ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW")
    return 200, {"votes": r["Attributes"]["votes"]}


# ================= 2.5D 立繪 =================
DEPTH_FIELDS = ("id", "title", "public", "width", "height", "created", "elapsed", "image", "depth", "thumb")


def _work_view(w):
    return {k: w.get(k) for k in DEPTH_FIELDS}


@route("GET", "/api/depth/mine", auth="user")
def depth_mine(req):
    items = query_all(f"PROFILE#{req['pid']}", "DEPTH#")
    return 200, {"works": [_work_view(w) for w in reversed(items)]}


@route("GET", "/api/depth/gallery")
def depth_gallery(req):
    r = table.query(KeyConditionExpression="PK = :p", ExpressionAttributeValues={":p": "GALLERY"},
                    ScanIndexForward=False, Limit=60)
    return 200, {"works": [_work_view(w) for w in r.get("Items", [])]}


@route("GET", r"/api/depth/(?P<wid>[a-f0-9]+)")
def depth_get(req):
    w = get_item(f"DEPTH#{req['params']['wid']}", "META")
    if not w:
        raise ApiError(404, "找不到這個作品")
    return 200, _work_view(w)


@route("DELETE", r"/api/depth/(?P<wid>[a-f0-9]+)", auth="user")
def depth_delete(req):
    wid = req["params"]["wid"]
    w = get_item(f"DEPTH#{wid}", "META")
    if not w or w["pid"] != req["pid"]:
        raise ApiError(404, "找不到這個作品")
    s3.delete_objects(Bucket=WEB_BUCKET, Delete={"Objects": [
        {"Key": w[k]} for k in ("image", "depth", "thumb")]})
    created = int(w["created"])
    table.delete_item(Key={"PK": f"DEPTH#{wid}", "SK": "META"})
    table.delete_item(Key={"PK": f"PROFILE#{req['pid']}", "SK": f"DEPTH#{created}#{wid}"})
    table.delete_item(Key={"PK": "GALLERY", "SK": f"{created}#{wid}"})
    return 200, {"ok": True}


# ================= 2D → 3D 模型 =================
MODEL3D_QUEUE_MAX = 10
MODEL3D_FIELDS = ("id", "title", "public", "created", "elapsed", "faces", "size", "model", "thumb")


def _worker_info():
    hb = get_item("WORKER", "model3d") or {}
    state = "unknown"
    if WORKER_INSTANCE_ID:
        try:
            r = ec2.describe_instances(InstanceIds=[WORKER_INSTANCE_ID])
            state = r["Reservations"][0]["Instances"][0]["State"]["Name"]
        except Exception as e:  # noqa: BLE001
            print("describe_instances failed:", e)
    queue = 0
    if QUEUE_URL:
        try:
            a = sqs.get_queue_attributes(QueueUrl=QUEUE_URL, AttributeNames=[
                "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
            queue = int(a["ApproximateNumberOfMessages"]) + int(a["ApproximateNumberOfMessagesNotVisible"])
        except Exception as e:  # noqa: BLE001
            print("get_queue_attributes failed:", e)
    # 心跳要新鮮且 EC2 真的在跑才算在線（被外部關機時心跳可能還沒過期）
    online = (bool(hb) and now_ts() - int(hb.get("ts", 0)) < 70
              and hb.get("status") != "stopping" and state in ("running", "unknown"))
    return {"instance": state, "online": online, "worker_status": hb.get("status") if online else None,
            "queue": queue}


def _wake_worker(info=None):
    """有新任務時把停止中的 EC2 開起來。"""
    info = info or _worker_info()
    if info["online"]:
        return f"運算主機在線（佇列中 {info['queue']} 個任務）"
    if info["instance"] == "stopped":
        try:
            ec2.start_instances(InstanceIds=[WORKER_INSTANCE_ID])
            return "正在喚醒 3D 運算主機（約 2～3 分鐘）…"
        except Exception as e:  # noqa: BLE001
            print("start_instances failed:", e)
            return "無法自動啟動運算主機，請聯絡管理員"
    if info["instance"] == "stopping":
        return "運算主機正在關機，稍後會自動重新啟動…"
    return "運算主機啟動中…"


@route("GET", "/api/model3d/status")
def model3d_status(req):
    info = _worker_info()
    # 自我修復：佇列有任務但主機停著（例如 Lab session 重開）就順手喚醒
    if info["queue"] and info["instance"] == "stopped":
        _wake_worker(info)
        info["instance"] = "pending"
    return 200, info


def _model_view(w):
    return {k: w.get(k) for k in MODEL3D_FIELDS}


@route("GET", "/api/model3d/mine", auth="user")
def model3d_mine(req):
    items = query_all(f"PROFILE#{req['pid']}", "MODEL3D#")
    return 200, {"works": [_model_view(w) for w in reversed(items)]}


@route("GET", "/api/model3d/gallery")
def model3d_gallery(req):
    r = table.query(KeyConditionExpression="PK = :p", ExpressionAttributeValues={":p": "GALLERY3D"},
                    ScanIndexForward=False, Limit=60)
    return 200, {"works": [_model_view(w) for w in r.get("Items", [])]}


@route("GET", r"/api/model3d/(?P<wid>[a-f0-9]+)")
def model3d_get(req):
    w = get_item(f"MODEL3D#{req['params']['wid']}", "META")
    if not w:
        raise ApiError(404, "找不到這個模型")
    return 200, _model_view(w)


@route("DELETE", r"/api/model3d/(?P<wid>[a-f0-9]+)", auth="user")
def model3d_delete(req):
    wid = req["params"]["wid"]
    w = get_item(f"MODEL3D#{wid}", "META")
    if not w or w["pid"] != req["pid"]:
        raise ApiError(404, "找不到這個模型")
    s3.delete_objects(Bucket=WEB_BUCKET, Delete={"Objects": [{"Key": w["model"]}, {"Key": w["thumb"]}]})
    created = int(w["created"])
    table.delete_item(Key={"PK": f"MODEL3D#{wid}", "SK": "META"})
    table.delete_item(Key={"PK": f"PROFILE#{req['pid']}", "SK": f"MODEL3D#{created}#{wid}"})
    table.delete_item(Key={"PK": "GALLERY3D", "SK": f"{created}#{wid}"})
    return 200, {"ok": True}


# ================= HoYoLAB 簽到 =================
@route("GET", "/api/hoyolab", auth="user")
def hoyolab_status(req):
    cfg = get_item(f"PROFILE#{req['pid']}", "HOYOLAB")
    if not cfg:
        return 200, {"configured": False}
    history = query_all(f"PROFILE#{req['pid']}", "CHECKIN#", ScanIndexForward=False, Limit=14)
    return 200, {
        "configured": True,
        "enabled": cfg.get("enabled", False),
        "ltuid": _mask(cfg.get("ltuid", "")),
        "has_webhook": bool(cfg.get("webhook")),
        "last_result": cfg.get("last_result"),
        "history": [{k: h.get(k) for k in ("date", "status", "message", "reward", "total_days")}
                    for h in history[:14]],
    }


@route("PUT", "/api/hoyolab", auth="user")
def hoyolab_save(req):
    b = req["body"]
    pid = req["pid"]
    existing = get_item(f"PROFILE#{pid}", "HOYOLAB") or {}
    item = {"PK": f"PROFILE#{pid}", "SK": "HOYOLAB", "updated": now_ts(),
            "enabled": bool(b.get("enabled", True))}
    if b.get("cookie"):
        cookie, ltuid = checkin.normalize_cookie(b["cookie"])
        if not cookie:
            raise ApiError(400, "Cookie 裡需要包含 ltoken_v2 與 ltuid_v2")
        item.update(cookie=cookie, ltuid=ltuid)
    elif existing.get("cookie"):
        item.update(cookie=existing["cookie"], ltuid=existing.get("ltuid"))
    else:
        raise ApiError(400, "請貼上 HoYoLAB Cookie")
    webhook = b.get("webhook")
    if webhook is None:
        webhook = existing.get("webhook", "")
    if webhook and not re.match(r"^https://(discord|discordapp)\.com/api/webhooks/", webhook):
        raise ApiError(400, "Discord Webhook 網址格式錯誤")
    item["webhook"] = webhook
    if existing.get("last_result"):
        item["last_result"] = existing["last_result"]
    table.put_item(Item=to_ddb(item))
    if item["enabled"]:
        table.put_item(Item={"PK": "CHECKIN_SET", "SK": pid})
    else:
        table.delete_item(Key={"PK": "CHECKIN_SET", "SK": pid})
    return 200, {"ok": True, "ltuid": _mask(item["ltuid"])}


@route("DELETE", "/api/hoyolab", auth="user")
def hoyolab_delete(req):
    table.delete_item(Key={"PK": f"PROFILE#{req['pid']}", "SK": "HOYOLAB"})
    table.delete_item(Key={"PK": "CHECKIN_SET", "SK": req["pid"]})
    return 200, {"ok": True}


@route("POST", "/api/hoyolab/checkin", auth="user")
def hoyolab_run(req):
    return 200, checkin.run_for_profile(req["pid"])


def _mask(s):
    s = str(s or "")
    return s[:2] + "*" * max(len(s) - 4, 0) + s[-2:] if len(s) > 4 else s


# ================= Discord Bot API =================
def _pid_by_discord(discord_id):
    link = get_item(f"DISCORD#{discord_id}", "PROFILE")
    if not link:
        raise ApiError(404, "這個 Discord 帳號還沒綁定，請先到網站取得綁定碼並使用 /link")
    return link["pid"]


@route("POST", "/api/bot/link", auth="bot")
def bot_link(req):
    code = (req["body"].get("code") or "").strip().upper()
    discord_id = str(req["body"].get("discord_id") or "").strip()
    if not code or not discord_id.isdigit():
        raise ApiError(400, "需要 code 與 discord_id")
    lc = get_item(f"LINKCODE#{code}", "PROFILE")
    if not lc or int(lc.get("ttl", 0)) < now_ts():
        raise ApiError(404, "綁定碼無效或已過期")
    pid = lc["pid"]
    table.put_item(Item={"PK": f"DISCORD#{discord_id}", "SK": "PROFILE", "pid": pid})
    table.update_item(Key={"PK": f"PROFILE#{pid}", "SK": "META"},
                      UpdateExpression="SET discord_id = :d", ExpressionAttributeValues={":d": discord_id})
    table.delete_item(Key={"PK": f"LINKCODE#{code}", "SK": "PROFILE"})
    return 200, {"ok": True}


@route("DELETE", r"/api/bot/users/(?P<did>\d+)/link", auth="bot")
def bot_unlink(req):
    pid = _pid_by_discord(req["params"]["did"])
    table.delete_item(Key={"PK": f"DISCORD#{req['params']['did']}", "SK": "PROFILE"})
    table.update_item(Key={"PK": f"PROFILE#{pid}", "SK": "META"}, UpdateExpression="REMOVE discord_id")
    return 200, {"ok": True}


@route("GET", r"/api/bot/users/(?P<did>\d+)/summary", auth="bot")
def bot_summary(req):
    pid = _pid_by_discord(req["params"]["did"])
    stats = gacha.profile_stats(pid)
    glob = gacha.global_stats()
    return 200, {"stats": stats, "global": glob, "llm_context": _llm_context(stats, glob)}


@route("GET", r"/api/bot/users/(?P<did>\d+)/pulls", auth="bot")
def bot_pulls(req):
    pid = _pid_by_discord(req["params"]["did"])
    gt = req["query"].get("gacha_type")
    rank = req["query"].get("rank")
    limit = min(int(req["query"].get("limit", 50)), 500)
    pulls = list(reversed(gacha.load_pulls(pid, gt)))
    if rank:
        pulls = [p for p in pulls if p.get("rank") == rank.upper()]
    return 200, {"pulls": [_pull_view(p) for p in pulls[:limit]]}


@route("GET", "/api/bot/global", auth="bot")
def bot_global(req):
    return 200, gacha.global_stats()


@route("GET", r"/api/bot/users/(?P<did>\d+)/checkin", auth="bot")
def bot_checkin_status(req):
    pid = _pid_by_discord(req["params"]["did"])
    cfg = get_item(f"PROFILE#{pid}", "HOYOLAB")
    return 200, {"configured": bool(cfg), "enabled": bool(cfg and cfg.get("enabled")),
                 "last_result": (cfg or {}).get("last_result")}


@route("POST", r"/api/bot/users/(?P<did>\d+)/checkin", auth="bot")
def bot_checkin_run(req):
    pid = _pid_by_discord(req["params"]["did"])
    return 200, checkin.run_for_profile(pid)


def _llm_context(stats, glob):
    """給 Discord bot 的 AI 模型當作上下文的純文字摘要。"""
    lines = [f"玩家總抽數 {stats['total_pulls']} 抽（約 {stats['polychrome_spent']} 菲林）。"]
    for gt, c in stats["channels"].items():
        if not c["total"]:
            continue
        g = glob["channels"].get(gt, {})
        s_names = "、".join(f"{s['name']}({s['pulls']}抽{'/歪' if s['result'] == 'lost' else ''})"
                           for s in c["s_history"][:10])
        lines.append(
            f"【{c['label']}】共 {c['total']} 抽，S級 {c['s_count']} 個，平均 {c['avg_s_pulls']} 抽出金"
            f"（全服平均 {g.get('avg_s_pulls')}），目前已墊 {c['current_pity']}/{c['pity_max']} 抽"
            + (f"，小保底歪率 {c['lose_rate']}%" if c["lose_rate"] is not None else "")
            + ("，下一隻 S 為大保底" if c["guaranteed"] else "")
            + f"，歐非評價：{c['luck_label']}。近期 S 級：{s_names or '無'}"
        )
    return "\n".join(lines)


# ================= 進入點 =================
def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if method == "OPTIONS":
        # CORS 預檢：回 204，API Gateway 會自動補上 Access-Control-* 標頭
        return {"statusCode": 204, "headers": {}, "body": ""}
    try:
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode()
        try:
            body = json.loads(body) if body else {}
        except ValueError:
            raise ApiError(400, "Body 必須是 JSON")
        if not isinstance(body, dict):
            raise ApiError(400, "Body 必須是 JSON 物件")

        for m, pattern, auth, fn in ROUTES:
            match = pattern.match(path)
            if m != method or not match:
                continue
            req = {"body": body, "query": event.get("queryStringParameters") or {},
                   "params": match.groupdict(), "headers": headers}
            if auth == "user":
                token = headers.get("authorization", "").removeprefix("Bearer ").strip()
                link = get_item(f"TOKEN#{sha256(token)}", "PROFILE") if token else None
                if not link:
                    raise ApiError(401, "未登入或 token 無效")
                req["pid"] = link["pid"]
            elif auth == "bot":
                key = headers.get("x-bot-key", "")
                if not BOT_API_KEY or not secrets.compare_digest(key, BOT_API_KEY):
                    raise ApiError(401, "Bot API Key 錯誤")
            status, data = fn(req)
            return resp(status, from_ddb(data))
        raise ApiError(404, f"找不到路由 {method} {path}")
    except ApiError as e:
        return resp(e.status, {"error": e.message})
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return resp(500, {"error": f"伺服器錯誤：{type(e).__name__}"})
