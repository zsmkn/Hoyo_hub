"""2D → 2.5D 立繪：用 Depth Anything V2 (ONNX) 在 Lambda 上估計深度圖。

S3 uploads/depth/{pid}/{job_id} → 本函式 → 網站 bucket gallery/{id}/
  image.webp  原圖（保留透明背景）
  depth.png   深度圖（白 = 近、黑 = 遠）
  thumb.webp  縮圖
前端再用 WebGL shader 依深度做視差位移，產生 2.5D 效果。
"""
import io
import os
import time
from urllib.parse import unquote_plus

import boto3
import numpy as np
from PIL import Image, ImageFilter, ImageOps

from common import table, get_item, to_ddb, now_ts

s3 = boto3.client("s3")
WEB_BUCKET = os.environ.get("WEB_BUCKET", "")
MODEL_BUCKET = os.environ.get("MODEL_BUCKET", "")
MODEL_KEY = os.environ.get("MODEL_KEY", "models/depth_anything_v2_small.onnx")
MODEL_PATH = "/tmp/depth_model.onnx"
MAX_BYTES = 10 * 1024 * 1024
MAX_SIDE = 1600
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_session = None


def get_session():
    """冷啟動時從 S3 下載模型並建立推論 session，之後重複使用。"""
    global _session
    if _session is None:
        import onnxruntime as ort
        if not os.path.exists(MODEL_PATH):
            s3.download_file(MODEL_BUCKET, MODEL_KEY, MODEL_PATH)
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = os.cpu_count() or 2
        _session = ort.InferenceSession(MODEL_PATH, opts, providers=["CPUExecutionProvider"])
    return _session


def _multiple_of_14(x):
    return max(14, int(round(x / 14)) * 14)


def estimate_depth(rgb):
    """rgb: PIL RGB → 0~1 的 numpy 深度（越大越近），尺寸與輸入相同。"""
    w, h = rgb.size
    scale = 518 / min(w, h)
    nw, nh = _multiple_of_14(w * scale), _multiple_of_14(h * scale)
    if max(nw, nh) > 924:  # 控制推論時間
        s = 924 / max(nw, nh)
        nw, nh = _multiple_of_14(nw * s), _multiple_of_14(nh * s)
    x = np.asarray(rgb.resize((nw, nh), Image.BICUBIC), dtype=np.float32) / 255.0
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)

    sess = get_session()
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    d = out.reshape(out.shape[-2], out.shape[-1])
    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
    d = np.clip((d - lo) / (hi - lo + 1e-6), 0, 1)
    img = Image.fromarray((d * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(img, dtype=np.float32) / 255.0


def process(src_bytes):
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(src_bytes)))
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA")
    alpha = np.asarray(img.getchannel("A"), dtype=np.float32) / 255.0

    # 透明背景先墊灰底再給模型看
    rgb = Image.alpha_composite(Image.new("RGBA", img.size, (128, 128, 128, 255)), img).convert("RGB")
    depth = estimate_depth(rgb)
    if has_alpha:
        depth = depth * alpha  # 透明處視為最遠

    blur = max(1.0, img.width / 500)
    depth_img = Image.fromarray((depth * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))

    def encode(im, fmt, **kw):
        buf = io.BytesIO()
        im.save(buf, fmt, **kw)
        return buf.getvalue()

    main = img if has_alpha else img.convert("RGB")
    thumb = main.copy()
    thumb.thumbnail((480, 480), Image.LANCZOS)
    return {
        "image.webp": (encode(main, "WEBP", quality=90), "image/webp"),
        "depth.png": (encode(depth_img, "PNG", optimize=True), "image/png"),
        "thumb.webp": (encode(thumb, "WEBP", quality=80), "image/webp"),
    }, img.size


def set_job(job_id, status, message, result=None):
    expr = "SET #s = :s, #m = :m, updated = :u"
    vals = {":s": status, ":m": message, ":u": now_ts()}
    if result is not None:
        expr += ", #r = :r"
        vals[":r"] = to_ddb(result)
    table.update_item(
        Key={"PK": f"JOB#{job_id}", "SK": "META"}, UpdateExpression=expr,
        ExpressionAttributeNames={"#s": "status", "#m": "message", **({"#r": "result"} if result is not None else {})},
        ExpressionAttributeValues=vals,
    )


def handle(bucket, key):
    parts = key.split("/")  # uploads/depth/{pid}/{job_id}
    if len(parts) != 4 or parts[1] != "depth":
        return
    _, _, pid, job_id = parts
    job = get_item(f"JOB#{job_id}", "META")
    if not job or job["pid"] != pid:
        print("unknown job", key)
        return
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        if obj["ContentLength"] > MAX_BYTES:
            return set_job(job_id, "error", "圖片超過 10MB")
        set_job(job_id, "running", "AI 正在估計深度…")
        t0 = time.time()
        files, (w, h) = process(obj["Body"].read())
        elapsed = round(time.time() - t0, 1)

        work_id = job_id
        for name, (data, ctype) in files.items():
            s3.put_object(Bucket=WEB_BUCKET, Key=f"gallery/{work_id}/{name}", Body=data,
                          ContentType=ctype, CacheControl="public, max-age=31536000")
        ts = now_ts()
        work = {
            "id": work_id, "title": job.get("title") or "未命名立繪", "public": bool(job.get("public")),
            "width": w, "height": h, "created": ts, "elapsed": elapsed,
            "image": f"gallery/{work_id}/image.webp", "depth": f"gallery/{work_id}/depth.png",
            "thumb": f"gallery/{work_id}/thumb.webp",
        }
        table.put_item(Item=to_ddb({"PK": f"DEPTH#{work_id}", "SK": "META", "pid": pid, **work}))
        table.put_item(Item=to_ddb({"PK": f"PROFILE#{pid}", "SK": f"DEPTH#{ts}#{work_id}", **work}))
        if work["public"]:
            table.put_item(Item=to_ddb({"PK": "GALLERY", "SK": f"{ts}#{work_id}", **work}))
        set_job(job_id, "done", f"完成！AI 推論＋處理耗時 {elapsed} 秒", result=work)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        set_job(job_id, "error", f"處理失敗：{type(e).__name__}")


def handler(event, context):
    for rec in event.get("Records", []):
        if rec.get("eventSource") == "aws:s3":
            handle(rec["s3"]["bucket"]["name"], unquote_plus(rec["s3"]["object"]["key"]))
    return {"ok": True}
