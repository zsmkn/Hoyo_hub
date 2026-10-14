"""本機 / 雲端的差異都集中在這裡，路由程式碼不用知道自己跑在哪裡。

| 動作           | 本機                          | 雲端（AWS）                                  |
|----------------|-------------------------------|---------------------------------------------|
| 背景工作       | 執行緒池                      | 非同步呼叫 worker Lambda                     |
| 圖片上傳網址   | PUT /api/uploads/{job_id}     | S3 預簽名網址（上傳後 S3 事件觸發 worker）     |
| 作品檔案       | data/gallery/{id}/            | 網站 bucket 的 gallery/{id}/                  |
"""
import json
import shutil
from concurrent.futures import ThreadPoolExecutor

from .config import CLOUD, GALLERY, UPLOAD_BUCKET, WEB_BUCKET, WORKER_FN

ACTIONS = {}  # 背景工作名稱 → 函式（由 routes 註冊）
_pool = None


def action(name):
    def deco(fn):
        ACTIONS[name] = fn
        return fn
    return deco


def _clients():
    import boto3
    from botocore.config import Config
    return (boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"})),
            boto3.client("lambda"))


def submit(name, **payload):
    """丟一個背景工作出去。payload 必須能轉成 JSON。"""
    global _pool
    if CLOUD:
        _, lam = _clients()
        lam.invoke(FunctionName=WORKER_FN, InvocationType="Event",
                   Payload=json.dumps({"action": name, **payload}))
    else:
        _pool = _pool or ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")
        _pool.submit(ACTIONS[name], **payload)


def upload_target(job_id, content_type):
    if not CLOUD:
        return f"/api/uploads/{job_id}"
    s3, _ = _clients()
    return s3.generate_presigned_url("put_object", ExpiresIn=300, Params={
        "Bucket": UPLOAD_BUCKET, "Key": f"uploads/{job_id}", "ContentType": content_type})


def publish_work(work_id):
    """把 GALLERY/{id}/ 底下產生好的檔案公開出去。"""
    if not CLOUD:
        return
    s3, _ = _clients()
    types = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}
    for f in (GALLERY / work_id).iterdir():
        s3.upload_file(str(f), WEB_BUCKET, f"gallery/{work_id}/{f.name}", ExtraArgs={
            "ContentType": types.get(f.suffix, "application/octet-stream"),
            "CacheControl": "public, max-age=31536000"})
    shutil.rmtree(GALLERY / work_id, ignore_errors=True)


def delete_work_files(work_id):
    if not CLOUD:
        shutil.rmtree(GALLERY / work_id, ignore_errors=True)
        return
    s3, _ = _clients()
    keys = s3.list_objects_v2(Bucket=WEB_BUCKET, Prefix=f"gallery/{work_id}/").get("Contents", [])
    if keys:
        s3.delete_objects(Bucket=WEB_BUCKET, Delete={"Objects": [{"Key": k["Key"]} for k in keys]})
