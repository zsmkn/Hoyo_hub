"""所有 API 路由（與框架無關）。本機由 app.py（FastAPI）呼叫，雲端由 lambda_api.py 呼叫。

遊戲相關的 API 都可以加上遊戲前綴：/api/{zzz|gi|hsr}/...；沒有前綴時使用預設遊戲（相容舊版與 Discord Cog）。
"""
import json
import re
import secrets
import time
import traceback

from . import checkin, gacha, ocr, runtime, scoring
from .common import ApiError, new_id, new_token, now_ts, sha256
from .config import BOT_API_KEY, CLOUD, GALLERY, UPLOADS
from .games import ALL_GAMES, DEFAULT_GAME, GAMES, external_games, extra_links, get_game, public_meta
from .store import store

GAME_PREFIX = re.compile(r"^/api/(%s)(/.*)$" % "|".join(ALL_GAMES))
ROUTES = []


def route(method, pattern, auth=None):
    """auth: None=公開, 'user'=需要個人 token, 'bot'=需要 Bot API Key"""
    def deco(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), auth, fn))
        return fn
    return deco


# ================= 背景工作 =================
def new_job(pid, kind, **extra):
    job_id = new_id(16)
    store.put(f"JOB#{job_id}", "META", {"pid": pid, "kind": kind, "status": "pending",
                                         "message": "排隊中…", "created": now_ts(), **extra},
              ttl=now_ts() + 86400)
    return job_id


def set_job(job_id, status, message, **extra):
    store.update(f"JOB#{job_id}", "META", status=status, message=message, updated=now_ts(), **extra)


@runtime.action("import_url")
def run_import_url(job_id, game, pid, url):
    try:
        g = get_game(game)
        set_job(job_id, "running", "開始匯入…")
        summary = gacha.import_from_url(g, pid, url, progress=lambda m: set_job(job_id, "running", m))
        gacha.update_global(g, pid, gacha.profile_stats(g, pid))
        set_job(job_id, "done", f"匯入完成，新增 {sum(summary.values())} 筆紀錄", result=summary)
    except ApiError as e:
        set_job(job_id, "error", e.message)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        set_job(job_id, "error", f"匯入失敗：{type(e).__name__}")


@runtime.action("process_upload")
def process_upload(job_id):
    """圖片已經在 UPLOADS/{job_id}：依工作類型做 OCR 或 2.5D。"""
    job = store.get(f"JOB#{job_id}", "META")
    path = UPLOADS / job_id
    if not job:
        path.unlink(missing_ok=True)
        return
    (run_depth if job["kind"] == "depth" else run_ocr)(job_id, job, path)


def run_ocr(job_id, job, path):
    try:
        game = get_game(job.get("game", DEFAULT_GAME))
        set_job(job_id, "running", "OCR 辨識中…")
        lines = ocr.detect_lines(path)
        raw = [ln["text"] for ln in lines]
        if job["kind"] == "ocr_gacha":
            rows = ocr.parse_gacha(game, lines)
            msg = f"辨識出 {len(rows)} 筆紀錄，請確認後匯入" if rows else "沒有辨識到紀錄，請確認截圖清晰且包含時間欄位"
            set_job(job_id, "done", msg, result={"rows": rows, "gacha_type": job.get("gacha_type")}, raw=raw)
        else:
            d = ocr.parse_disc(game, lines)
            ok = d["main"] or d["subs"]
            set_job(job_id, "done", "辨識完成，請確認數值" if ok else "沒有辨識到詞條，請手動輸入", result=d, raw=raw)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        set_job(job_id, "error", f"OCR 失敗：{type(e).__name__}")
    finally:
        path.unlink(missing_ok=True)


def run_depth(job_id, job, path):
    from . import depth  # numpy / onnxruntime 只有需要時才載入
    try:
        set_job(job_id, "running", "AI 正在估計深度…")
        t0 = time.time()
        w, h = depth.process(path.read_bytes(), GALLERY / job_id, lambda m: set_job(job_id, "running", m))
        runtime.publish_work(job_id)
        ts = now_ts()
        work = {
            "id": job_id, "title": job.get("title") or "未命名立繪", "public": bool(job.get("public")),
            "width": w, "height": h, "created": ts, "elapsed": round(time.time() - t0, 1),
            "image": f"gallery/{job_id}/image.webp", "depth": f"gallery/{job_id}/depth.png",
            "thumb": f"gallery/{job_id}/thumb.webp",
        }
        store.put(f"DEPTH#{job_id}", "META", {"pid": job["pid"], **work})
        store.put(f"PROFILE#{job['pid']}", f"DEPTH#{ts}#{job_id}", work)
        if work["public"]:
            store.put("GALLERY", f"{ts}#{job_id}", work)
        set_job(job_id, "done", f"完成！AI 推論＋處理耗時 {work['elapsed']} 秒", result=work)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        set_job(job_id, "error", f"處理失敗：{type(e).__name__}")
    finally:
        path.unlink(missing_ok=True)


def accept_upload(job_id, data: bytes):
    """本機版：收到 PUT 上傳的圖片後排進背景工作。回傳 (status, error)。"""
    job = store.get(f"JOB#{job_id}", "META")
    if not job or job.get("status") != "waiting_upload":
        return 404, "上傳網址無效或已使用"
    (UPLOADS / job_id).write_bytes(data)
    set_job(job_id, "pending", "排隊中…")
    runtime.submit("process_upload", job_id=job_id)
    return 200, None


# ================= 個人檔案 =================
@route("POST", "/api/profile")
def create_profile(req):
    pid, token = new_id(12), new_token()
    store.put(f"PROFILE#{pid}", "META", {"created": now_ts()})
    store.put(f"TOKEN#{sha256(token)}", "PROFILE", {"pid": pid})
    return 201, {"profile_id": pid, "token": token}


@route("GET", "/api/profile", auth="user")
def get_profile(req):
    meta = store.get(f"PROFILE#{req['pid']}", "META") or {}
    return 200, {"profile_id": req["pid"], "game_uid": meta.get(f"game_uid_{req['game']['id']}"),
                 "discord_id": meta.get("discord_id")}


@route("POST", "/api/profile/linkcode", auth="user")
def create_link_code(req):
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    store.put(f"LINKCODE#{code}", "PROFILE", {"pid": req["pid"]}, ttl=now_ts() + 600)
    return 200, {"code": code, "expires_in": 600}


@route("GET", "/api/ping")
def ping(req):
    """健康檢查；雲端版這條路由另外設了嚴格限流（每秒 2 個），可用來測試 429。"""
    return 200, {"ok": True, "time": now_ts()}


@route("GET", "/api/games")
def list_games(req):
    return 200, {"games": [{"id": g["id"], "short": g["short"], "name": g["name"], "accent": g["accent"]}
                           for g in GAMES.values()],
                 "external": external_games(), "links": extra_links(), "default": DEFAULT_GAME, "mode": "cloud" if CLOUD else "local"}


@route("GET", "/api/meta")
def game_meta(req):
    return 200, public_meta(req["game"])


@route("GET", "/api/agents")
def list_agents(req):
    """舊版相容：等同 /api/meta 的角色部分。"""
    m = public_meta(req["game"])
    return 200, {"agents": m["agents"], "stat_labels": m["stat_labels"], "specialties": m["specialties"]}


# ================= 抽卡紀錄 =================
@route("POST", "/api/import/url", auth="user")
def import_url(req):
    url = (req["body"].get("url") or "").strip()
    gacha.parse_gacha_url(req["game"], url)
    job_id = new_job(req["pid"], "import_url", game=req["game"]["id"])
    runtime.submit("import_url", job_id=job_id, game=req["game"]["id"], pid=req["pid"], url=url)
    return 202, {"job_id": job_id}


@route("POST", "/api/upload-url", auth="user")
def upload_url(req):
    """先拿上傳網址，再用 PUT 上傳圖片（本機：自己的伺服器；雲端：S3 預簽名網址）。"""
    b, game = req["body"], req["game"]
    kind = b.get("kind")
    if kind not in ("gacha", "disc", "depth"):
        raise ApiError(400, "kind 必須是 gacha、disc 或 depth")
    ctype = b.get("content_type", "image/png")
    allowed = ("image/png", "image/jpeg", "image/webp") if kind == "depth" else ("image/png", "image/jpeg")
    if ctype not in allowed:
        raise ApiError(400, "不支援的圖片格式")
    if kind == "depth":
        job_id = new_job(req["pid"], "depth", content_type=ctype, title=(b.get("title") or "").strip()[:40],
                         public=bool(b.get("public")))
    else:
        ch = str(b.get("gacha_type") or game["gacha"]["featured_channel"])
        job_id = new_job(req["pid"], f"ocr_{kind}", content_type=ctype, game=game["id"], gacha_type=ch)
    set_job(job_id, "waiting_upload", "等待上傳圖片…")
    return 200, {"job_id": job_id, "upload_url": runtime.upload_target(job_id, ctype), "content_type": ctype}


@route("GET", r"/api/jobs/(?P<job_id>[a-f0-9]+)", auth="user")
def get_job(req):
    job = store.get(f"JOB#{req['params']['job_id']}", "META")
    if not job or job["pid"] != req["pid"]:
        raise ApiError(404, "找不到這個工作")
    return 200, {k: v for k, v in job.items() if k not in ("PK", "SK", "pid")}


@route("POST", "/api/import/rows", auth="user")
def import_rows(req):
    game = req["game"]
    ch = str(req["body"].get("gacha_type"))
    if ch not in game["gacha"]["channels"]:
        raise ApiError(400, "卡池類型錯誤")
    clean = []
    for r in (req["body"].get("rows") or [])[:200]:
        if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", r.get("time", "")):
            raise ApiError(400, f"時間格式錯誤：{r.get('time')}")
        clean.append({"name": str(r.get("name", ""))[:40], "item_type": str(r.get("item_type", ""))[:10],
                      "rank": r.get("rank") if r.get("rank") in ("S", "A", "B") else "B", "time": r["time"]})
    n = gacha.save_pulls(game, req["pid"], ch, ocr.rows_to_pulls(clean), "ocr")
    gacha.update_global(game, req["pid"], gacha.profile_stats(game, req["pid"]))
    return 200, {"imported": n}


@route("GET", "/api/stats", auth="user")
def my_stats(req):
    return 200, gacha.profile_stats(req["game"], req["pid"])


def _pull_view(p):
    return {k: p.get(k) for k in ("id", "gacha_type", "name", "item_type", "rank", "time")}


@route("GET", "/api/pulls", auth="user")
def my_pulls(req):
    pulls = gacha.load_pulls(req["game"], req["pid"], req["query"].get("gacha_type"))
    return 200, {"pulls": [_pull_view(p) for p in reversed(pulls)]}


@route("DELETE", "/api/pulls", auth="user")
def clear_pulls(req):
    return 200, {"deleted": gacha.delete_pulls(req["game"], req["pid"])}


@route("GET", "/api/global")
def global_stats(req):
    return 200, gacha.global_stats(req["game"])


# ================= 裝備評分 =================
@route("POST", "/api/disc/score")
def disc_score(req):
    b = req["body"]
    return 200, scoring.score(req["game"], b.get("character"), b.get("slot"), b.get("main"), b.get("subs"))


# ================= 2.5D 立繪（不分遊戲） =================
DEPTH_FIELDS = ("id", "title", "public", "width", "height", "created", "elapsed", "image", "depth", "thumb")


def _view(w):
    return {k: w.get(k) for k in DEPTH_FIELDS}


@route("GET", "/api/depth/mine", auth="user")
def depth_mine(req):
    return 200, {"works": [_view(w) for w in store.query(f"PROFILE#{req['pid']}", "DEPTH#", desc=True)]}


@route("GET", "/api/depth/gallery")
def depth_gallery(req):
    return 200, {"works": [_view(w) for w in store.query("GALLERY", desc=True, limit=60)]}


@route("GET", r"/api/depth/(?P<wid>[a-f0-9]+)")
def depth_get(req):
    w = store.get(f"DEPTH#{req['params']['wid']}", "META")
    if not w:
        raise ApiError(404, "找不到這個作品")
    return 200, _view(w)


@route("DELETE", r"/api/depth/(?P<wid>[a-f0-9]+)", auth="user")
def depth_delete(req):
    wid, pid = req["params"]["wid"], req["pid"]
    w = store.get(f"DEPTH#{wid}", "META")
    if not w or w["pid"] != pid:
        raise ApiError(404, "找不到這個作品")
    runtime.delete_work_files(wid)
    store.delete(f"DEPTH#{wid}", "META")
    store.delete(f"PROFILE#{pid}", f"DEPTH#{w['created']}#{wid}")
    store.delete("GALLERY", f"{w['created']}#{wid}")
    return 200, {"ok": True}


# ================= HoYoLAB 簽到 =================
def _mask(s):
    s = str(s or "")
    return s[:2] + "*" * max(len(s) - 4, 0) + s[-2:] if len(s) > 4 else s


@route("GET", "/api/hoyolab", auth="user")
def hoyolab_status(req):
    gid = req["game"]["id"]
    cfg = store.get(f"PROFILE#{req['pid']}", "HOYOLAB")
    if not cfg:
        return 200, {"configured": False}
    history = store.query(f"PROFILE#{req['pid']}", f"CHECKIN#{gid}#", desc=True, limit=14)
    return 200, {
        "configured": True, "enabled": bool((cfg.get("games") or {}).get(gid)),
        "enabled_games": [g for g, on in (cfg.get("games") or {}).items() if on],
        "ltuid": _mask(cfg.get("ltuid", "")), "has_webhook": bool(cfg.get("webhook")),
        "last_result": (cfg.get("last") or {}).get(gid),
        "history": [{k: h.get(k) for k in ("date", "status", "message", "reward", "total_days")} for h in history],
    }


@route("PUT", "/api/hoyolab", auth="user")
def hoyolab_save(req):
    b, pid, gid = req["body"], req["pid"], req["game"]["id"]
    existing = store.get(f"PROFILE#{pid}", "HOYOLAB") or {}
    item = {**{k: v for k, v in existing.items() if k not in ("PK", "SK")}, "updated": now_ts()}
    if b.get("cookie"):
        cookie, ltuid = checkin.normalize_cookie(b["cookie"])
        if not cookie:
            raise ApiError(400, "Cookie 裡需要包含 ltoken_v2 與 ltuid_v2")
        item.update(cookie=cookie, ltuid=ltuid)
    elif not existing.get("cookie"):
        raise ApiError(400, "請貼上 HoYoLAB Cookie")
    if b.get("webhook") is not None:
        if b["webhook"] and not re.match(r"^https://(discord|discordapp)\.com/api/webhooks/", b["webhook"]):
            raise ApiError(400, "Discord Webhook 網址格式錯誤")
        item["webhook"] = b["webhook"]
    games = dict(item.get("games") or {})
    games[gid] = bool(b.get("enabled", True))
    item["games"] = games
    store.put(f"PROFILE#{pid}", "HOYOLAB", item)
    for g, on in games.items():
        if on:
            store.put(f"CHECKIN_SET#{g}", pid, {})
        else:
            store.delete(f"CHECKIN_SET#{g}", pid)
    return 200, {"ok": True, "ltuid": _mask(item["ltuid"])}


@route("DELETE", "/api/hoyolab", auth="user")
def hoyolab_delete(req):
    store.delete(f"PROFILE#{req['pid']}", "HOYOLAB")
    for g in ALL_GAMES:
        store.delete(f"CHECKIN_SET#{g}", req["pid"])
    return 200, {"ok": True}


@route("POST", "/api/hoyolab/checkin", auth="user")
def hoyolab_run(req):
    return 200, checkin.run_for_profile(req["game"], req["pid"])


# ================= Discord Bot API =================
def _pid_by_discord(discord_id):
    link = store.get(f"DISCORD#{discord_id}", "PROFILE")
    if not link:
        raise ApiError(404, "這個 Discord 帳號還沒綁定，請先到網站取得綁定碼並使用 /link")
    return link["pid"]


@route("POST", "/api/bot/link", auth="bot")
def bot_link(req):
    code = (req["body"].get("code") or "").strip().upper()
    discord_id = str(req["body"].get("discord_id") or "").strip()
    if not code or not discord_id.isdigit():
        raise ApiError(400, "需要 code 與 discord_id")
    lc = store.get(f"LINKCODE#{code}", "PROFILE")
    if not lc:
        raise ApiError(404, "綁定碼無效或已過期")
    store.put(f"DISCORD#{discord_id}", "PROFILE", {"pid": lc["pid"]})
    store.update(f"PROFILE#{lc['pid']}", "META", discord_id=discord_id)
    store.delete(f"LINKCODE#{code}", "PROFILE")
    return 200, {"ok": True}


@route("DELETE", r"/api/bot/users/(?P<did>\d+)/link", auth="bot")
def bot_unlink(req):
    pid = _pid_by_discord(req["params"]["did"])
    store.delete(f"DISCORD#{req['params']['did']}", "PROFILE")
    store.update(f"PROFILE#{pid}", "META", discord_id=None)
    return 200, {"ok": True}


@route("GET", r"/api/bot/users/(?P<did>\d+)/summary", auth="bot")
def bot_summary(req):
    game, pid = req["game"], _pid_by_discord(req["params"]["did"])
    stats, glob = gacha.profile_stats(game, pid), gacha.global_stats(game)
    return 200, {"game": game["id"], "stats": stats, "global": glob, "llm_context": _llm_context(game, stats, glob)}


@route("GET", r"/api/bot/users/(?P<did>\d+)/pulls", auth="bot")
def bot_pulls(req):
    pid = _pid_by_discord(req["params"]["did"])
    rank = req["query"].get("rank")
    limit = min(int(req["query"].get("limit", 50)), 500)
    pulls = list(reversed(gacha.load_pulls(req["game"], pid, req["query"].get("gacha_type"))))
    if rank:
        pulls = [p for p in pulls if p.get("rank") == rank.upper()]
    return 200, {"pulls": [_pull_view(p) for p in pulls[:limit]]}


@route("GET", "/api/bot/global", auth="bot")
def bot_global(req):
    return 200, gacha.global_stats(req["game"])


@route("GET", r"/api/bot/users/(?P<did>\d+)/checkin", auth="bot")
def bot_checkin_status(req):
    cfg = store.get(f"PROFILE#{_pid_by_discord(req['params']['did'])}", "HOYOLAB") or {}
    gid = req["game"]["id"]
    return 200, {"configured": bool(cfg), "enabled": bool((cfg.get("games") or {}).get(gid)),
                 "last_result": (cfg.get("last") or {}).get(gid)}


@route("POST", r"/api/bot/users/(?P<did>\d+)/checkin", auth="bot")
def bot_checkin_run(req):
    return 200, checkin.run_for_profile(req["game"], _pid_by_discord(req["params"]["did"]))


def _llm_context(game, stats, glob):
    """給 Discord bot 的 AI 模型當作上下文的純文字摘要。"""
    L = game["labels"]
    lines = [f"遊戲：{game['name']}。玩家總抽數 {stats['total_pulls']} 抽（約 {stats['polychrome_spent']} {L['currency']}）。"]
    for c in stats["channels"].values():
        if not c["total"]:
            continue
        g = glob["channels"].get(c["gacha_type"], {})
        s_names = "、".join(f"{s['name']}({s['pulls']}抽{'/歪' if s['result'] == 'lost' else ''})"
                           for s in c["s_history"][:10])
        lines.append(
            f"【{c['label']}】共 {c['total']} 抽，{L['rank_S']} {c['s_count']} 個，平均 {c['avg_s_pulls']} 抽出金"
            f"（全站平均 {g.get('avg_s_pulls')}），目前已墊 {c['current_pity']}/{c['pity_max']} 抽"
            + (f"，小保底歪率 {c['lose_rate']}%" if c["lose_rate"] is not None else "")
            + ("，下一隻為大保底" if c["guaranteed"] else "")
            + f"，歐非評價：{c['luck_label']}。近期 {L['rank_S']}：{s_names or '無'}"
        )
    return "\n".join(lines)


# ================= 取得抽卡網址的 PowerShell 腳本 =================
SCRIPT_TEMPLATE = r"""# HoYo Proxy Hub - get __NAME__ gacha history URL (read-only)
# Finds the game folder from the game log and reads the newest gacha URL from the in-game browser cache.
# Usage: open the gacha history page in game first, then run this script.
$ErrorActionPreference = "Stop"
$roots = @(__ROOTS__) | ForEach-Object { Join-Path "$env:USERPROFILE\AppData\LocalLow" $_ } | Where-Object { Test-Path $_ }
$logs = $roots | ForEach-Object { Get-ChildItem $_ -Recurse -Include __LOGS__ -ErrorAction SilentlyContinue } | Select-Object -ExpandProperty FullName
$gameData = $null
foreach ($log in $logs) {
    $m = Select-String -Path $log -Pattern '__REGEX__' | Select-Object -First 1
    if ($m) { $gameData = $m.Matches[0].Groups[1].Value; break }
}
if (-not $gameData) { Write-Host "Game folder not found. Please launch the game first." -ForegroundColor Red; return }
$latest = Get-ChildItem (Join-Path $gameData "webCaches") -Directory | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$data2 = Join-Path $latest.FullName "Cache\Cache_Data\data_2"
if (-not (Test-Path $data2)) { Write-Host "Cache file not found: $data2" -ForegroundColor Red; return }
$tmp = Join-Path $env:TEMP "hoyo_data_2.tmp"
Copy-Item $data2 $tmp -Force
$text = [System.Text.Encoding]::UTF8.GetString([System.IO.File]::ReadAllBytes($tmp))
Remove-Item $tmp -Force
$found = [regex]::Matches($text, 'https://[^\s"\x00]+?getGachaLog\?[^\s"\x00]+')
if ($found.Count -eq 0) { $found = [regex]::Matches($text, 'https://[^\s"\x00]+?authkey=[^\s"\x00]+') }
if ($found.Count -eq 0) { Write-Host "URL not found. Open the gacha history page in game, then run again." -ForegroundColor Yellow; return }
$url = $found[$found.Count - 1].Value
Set-Clipboard -Value $url
Write-Host ""; Write-Host $url; Write-Host ""
Write-Host "Copied to clipboard! Paste it into HoYo Proxy Hub." -ForegroundColor Green
"""


def render_script(game_id):
    """回傳某遊戲的取網址腳本；不支援的遊戲回傳 None。"""
    if game_id not in ALL_GAMES:
        return None
    g = ALL_GAMES[game_id]
    s = g["script"]
    return (SCRIPT_TEMPLATE.replace("__NAME__", g["name_en"])
            .replace("__ROOTS__", ", ".join(f'"{r}"' for r in s["log_roots"]))
            .replace("__LOGS__", ", ".join(s["log_names"]))
            .replace("__REGEX__", s["data_regex"]))


# ================= 路由分派 =================
def _dispatch(method, path, headers, query, body):
    gid = DEFAULT_GAME
    m = GAME_PREFIX.match(path)
    if m:
        gid, path = m[1], "/api" + m[2]
    game = get_game(gid)
    for meth, pattern, auth, fn in ROUTES:
        match = pattern.match(path)
        if meth != method or not match:
            continue
        req = {"body": body, "query": query, "params": match.groupdict(), "headers": headers, "game": game}
        if auth == "user":
            token = headers.get("authorization", "").removeprefix("Bearer ").strip()
            link = store.get(f"TOKEN#{sha256(token)}", "PROFILE") if token else None
            if not link:
                raise ApiError(401, "未登入或 token 無效")
            req["pid"] = link["pid"]
        elif auth == "bot":
            if not BOT_API_KEY or not secrets.compare_digest(headers.get("x-bot-key", ""), BOT_API_KEY):
                raise ApiError(401, "Bot API Key 錯誤")
        return fn(req)
    raise ApiError(404, f"找不到路由 {method} {path}")


def handle(method, path, headers, query, raw_body):
    """完整處理一個 API 請求，回傳 (HTTP 狀態碼, JSON 物件)。"""
    try:
        try:
            body = json.loads(raw_body) if raw_body else {}
        except ValueError:
            raise ApiError(400, "Body 必須是 JSON")
        if not isinstance(body, dict):
            raise ApiError(400, "Body 必須是 JSON 物件")
        return _dispatch(method, path, {k.lower(): v for k, v in headers.items()}, query or {}, body)
    except ApiError as e:
        return e.status, {"error": e.message}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return 500, {"error": f"伺服器錯誤：{type(e).__name__}"}
