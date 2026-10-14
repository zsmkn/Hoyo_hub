"""雲端：API Gateway (HTTP API) → 這裡 → routes.handle（與本機版共用同一份路由）。"""
import base64
import json

from . import routes


def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method = http.get("method", "GET")
    if method == "OPTIONS":  # CORS 預檢，API Gateway 會補上 Access-Control-* 標頭
        return {"statusCode": 204, "headers": {}, "body": ""}
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    status, data = routes.handle(method, event.get("rawPath", "/"), event.get("headers") or {},
                                 event.get("queryStringParameters") or {}, body)
    return {"statusCode": status, "headers": {"Content-Type": "application/json; charset=utf-8"},
            "body": json.dumps(data, ensure_ascii=False)}
