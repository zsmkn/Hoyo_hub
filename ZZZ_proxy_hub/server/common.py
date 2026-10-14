"""共用工具：錯誤、ID、外部 HTTP 請求。"""
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def now_ts():
    return int(time.time())


def sha256(s):
    return hashlib.sha256(s.encode()).hexdigest()


def new_token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


def new_id(n=10):
    return secrets.token_hex(n // 2)


def http_json(url, method="GET", headers=None, body=None, timeout=15):
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError(502, f"外部服務錯誤 HTTP {e.code}")
    return json.loads(raw) if raw else {}


def post_discord_webhook(webhook_url, content):
    """發送 Discord Webhook 通知（失敗不影響主流程）。"""
    if not webhook_url:
        return
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps({"content": content[:1900]}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # noqa: BLE001
        print("webhook failed:", e)
