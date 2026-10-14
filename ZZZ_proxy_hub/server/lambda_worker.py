"""雲端：背景工作 Lambda。
- S3 上傳事件（uploads/{job_id}）→ OCR 或 2.5D
- 非同步呼叫 {"action": "import_url", ...} → 從官方網址匯入抽卡紀錄
"""
import os
import sys
import zipfile
from urllib.parse import unquote_plus

import boto3

from . import routes, runtime
from .config import UPLOADS

s3 = boto3.client("s3")
DEPS_DIR = "/tmp/deps"


def ensure_deps():
    """numpy / onnxruntime / OpenCV / RapidOCR 加起來約 330MB，超過 Lambda 250MB 的套件上限，
    所以放在 S3，冷啟動時下載解壓到 /tmp（同區域 S3 很快，只有冷啟動會多幾秒）。"""
    key = os.environ.get("DEPS_KEY")
    if not key:
        return
    marker = os.path.join(DEPS_DIR, ".ok-" + os.path.basename(key))
    if not os.path.exists(marker):
        archive = "/tmp/deps.zip"
        s3.download_file(os.environ["DEPS_BUCKET"], key, archive)
        with zipfile.ZipFile(archive) as z:
            z.extractall(DEPS_DIR)
        os.remove(archive)
        open(marker, "w").close()
    if DEPS_DIR not in sys.path:
        sys.path.insert(0, DEPS_DIR)


def handler(event, context):
    ensure_deps()
    for rec in event.get("Records", []):
        if rec.get("eventSource") != "aws:s3":
            continue
        key = unquote_plus(rec["s3"]["object"]["key"])
        job_id = key.rsplit("/", 1)[-1]
        if not key.startswith("uploads/") or not job_id.isalnum():
            continue
        s3.download_file(rec["s3"]["bucket"]["name"], key, str(UPLOADS / job_id))
        s3.delete_object(Bucket=rec["s3"]["bucket"]["name"], Key=key)
        routes.set_job(job_id, "pending", "排隊中…")
        routes.process_upload(job_id)
    if event.get("action"):
        payload = {k: v for k, v in event.items() if k != "action"}
        runtime.ACTIONS[event["action"]](**payload)
    return {"ok": True}
