"""ZZZ Proxy Hub — 2D → 3D 模型 worker（跑在 EC2，CPU 推論）

流程：SQS 收到 S3 上傳事件 → 下載圖片 → rembg 動漫去背 → TripoSR 生成 3D
     → 減面 → 匯出 GLB → 上傳到網站 bucket → 更新 DynamoDB

- 每 20 秒寫一次心跳到 DynamoDB，API 據此判斷 worker 是否在線
- 閒置超過 IDLE_MINUTES 分鐘會自動關機（EC2 停止 = 不計費），有新任務時由 API 重新開機
"""
import io
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import types
from decimal import Decimal
from urllib.parse import unquote_plus

import boto3
import numpy as np
import torch
from PIL import Image

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
QUEUE_URL = os.environ["QUEUE_URL"]
TABLE_NAME = os.environ.get("TABLE_NAME", "zzz-hub")
WEB_BUCKET = os.environ["WEB_BUCKET"]
IDLE_MINUTES = float(os.environ.get("IDLE_MINUTES", "20"))
MC_RESOLUTION = int(os.environ.get("MC_RESOLUTION", "192"))
MAX_FACES = int(os.environ.get("MAX_FACES", "60000"))
TRIPOSR_DIR = os.environ.get("TRIPOSR_DIR", "/opt/zzz3d/TripoSR")

sqs = boto3.client("sqs", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_NAME)

state = {"status": "loading", "job": None, "last_active": time.time()}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def to_ddb(obj):
    return json.loads(json.dumps(obj), parse_float=Decimal)


# ---------------- TripoSR 載入 ----------------
# 官方用 torchmcubes（需要編譯）；這裡用 PyMCubes 做一個相容的替身。
# torchmcubes 回傳的頂點軸序是反的，TripoSR 之後會再做 [2,1,0] 交換，所以這裡先反轉。
import mcubes  # noqa: E402

_shim = types.ModuleType("torchmcubes")


def _marching_cubes(vol, thresh):
    v, f = mcubes.marching_cubes(vol.detach().cpu().numpy(), thresh)
    v = np.ascontiguousarray(v[:, ::-1])
    return torch.from_numpy(v).float(), torch.from_numpy(f.astype(np.int64))


_shim.marching_cubes = _marching_cubes
sys.modules["torchmcubes"] = _shim
sys.path.insert(0, TRIPOSR_DIR)

import rembg  # noqa: E402
import trimesh  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from tsr.system import TSR  # noqa: E402
from tsr.utils import remove_background, resize_foreground, to_gradio_3d_orientation  # noqa: E402

torch.set_num_threads(os.cpu_count() or 2)
log("loading TripoSR…")
MODEL = TSR.from_pretrained("stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt")
MODEL.renderer.set_chunk_size(8192)
MODEL.to("cpu")
REMBG_SESSIONS = [rembg.new_session("isnet-anime"), rembg.new_session("isnet-general-use")]
log("models ready")


# ---------------- DynamoDB ----------------
def set_job(job_id, status, message, result=None):
    expr = "SET #s = :s, #m = :m, updated = :u"
    names = {"#s": "status", "#m": "message"}
    vals = {":s": status, ":m": message, ":u": int(time.time())}
    if result is not None:
        expr += ", #r = :r"
        names["#r"] = "result"
        vals[":r"] = to_ddb(result)
    table.update_item(Key={"PK": f"JOB#{job_id}", "SK": "META"}, UpdateExpression=expr,
                      ExpressionAttributeNames=names, ExpressionAttributeValues=vals)


def heartbeat_loop():
    while True:
        try:
            table.put_item(Item={"PK": "WORKER", "SK": "model3d", "status": state["status"],
                                 "job": state["job"] or "", "ts": int(time.time())})
        except Exception as e:  # noqa: BLE001
            log("heartbeat failed", e)
        time.sleep(20)


# ---------------- 生成 ----------------
def cutout(img):
    """先用動漫專用模型去背；主體面積不合理（幾乎全圖或幾乎沒有）時改用通用模型。"""
    first = None
    for sess in REMBG_SESSIONS:
        out = remove_background(img, sess, force=True)
        coverage = float((np.asarray(out.getchannel("A")) > 128).mean())
        log(f"rembg coverage {coverage:.2f}")
        if 0.02 < coverage < 0.85:
            return out
        first = first or out
    return first


def preprocess(raw):
    img = Image.open(io.BytesIO(raw))
    img.thumbnail((1024, 1024))
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        img = img.convert("RGBA")
        # 至少 5% 像素透明才算「已去背」，避免只有邊緣半透明的圖被誤判
        if (np.asarray(img.getchannel("A")) < 128).mean() < 0.05:
            img, has_alpha = img.convert("RGB"), False
    if not has_alpha:
        img = cutout(img.convert("RGB"))
    img = resize_foreground(img, 0.85)
    arr = np.asarray(img).astype(np.float32) / 255.0
    arr = arr[:, :, :3] * arr[:, :, 3:4] + (1 - arr[:, :, 3:4]) * 0.5
    return Image.fromarray((arr * 255).astype(np.uint8)), img


def simplify(mesh):
    if len(mesh.faces) <= MAX_FACES:
        return mesh
    colors = mesh.visual.vertex_colors
    small = mesh.simplify_quadric_decimation(face_count=MAX_FACES)
    # 減面後顏色會遺失，用最近頂點補回
    _, idx = cKDTree(mesh.vertices).query(small.vertices)
    small.visual.vertex_colors = colors[idx]
    return small


def generate(job_id, raw):
    t0 = time.time()
    set_job(job_id, "running", "去背與前處理中…")
    model_input, cutout = preprocess(raw)

    set_job(job_id, "running", "AI 正在推測 3D 結構（約 1 分鐘）…")
    with torch.no_grad():
        scene_codes = MODEL([model_input], device="cpu")

    set_job(job_id, "running", "建立 3D 網格與上色中…")
    mesh = MODEL.extract_mesh(scene_codes, True, resolution=MC_RESOLUTION)[0]
    mesh = simplify(mesh)
    mesh.fix_normals()
    mesh = to_gradio_3d_orientation(mesh)
    glb = mesh.export(file_type="glb")

    preview = io.BytesIO()
    thumb = cutout.copy()
    thumb.thumbnail((480, 480))
    thumb.save(preview, "WEBP", quality=85)
    return glb, preview.getvalue(), len(mesh.faces), round(time.time() - t0, 1)


def handle(bucket, key):
    parts = key.split("/")  # uploads/model3d/{pid}/{job_id}
    if len(parts) != 4 or parts[1] != "model3d":
        return
    _, _, pid, job_id = parts
    job = table.get_item(Key={"PK": f"JOB#{job_id}", "SK": "META"}).get("Item")
    if not job or job["pid"] != pid:
        log("unknown job", key)
        return
    state.update(status="busy", job=job_id)
    try:
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        glb, preview, faces, elapsed = generate(job_id, raw)
        base = f"gallery3d/{job_id}"
        s3.put_object(Bucket=WEB_BUCKET, Key=f"{base}/model.glb", Body=glb,
                      ContentType="model/gltf-binary", CacheControl="public, max-age=31536000")
        s3.put_object(Bucket=WEB_BUCKET, Key=f"{base}/thumb.webp", Body=preview,
                      ContentType="image/webp", CacheControl="public, max-age=31536000")
        ts = int(time.time())
        work = {
            "id": job_id, "title": job.get("title") or "未命名模型", "public": bool(job.get("public")),
            "created": ts, "elapsed": elapsed, "faces": faces, "size": len(glb),
            "model": f"{base}/model.glb", "thumb": f"{base}/thumb.webp",
        }
        table.put_item(Item=to_ddb({"PK": f"MODEL3D#{job_id}", "SK": "META", "pid": pid, **work}))
        table.put_item(Item=to_ddb({"PK": f"PROFILE#{pid}", "SK": f"MODEL3D#{ts}#{job_id}", **work}))
        if work["public"]:
            table.put_item(Item=to_ddb({"PK": "GALLERY3D", "SK": f"{ts}#{job_id}", **work}))
        set_job(job_id, "done", f"完成！耗時 {elapsed} 秒，{faces:,} 個面", result=work)
        log("done", job_id, elapsed, "s")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        set_job(job_id, "error", f"生成失敗：{type(e).__name__}")
    finally:
        state.update(status="idle", job=None, last_active=time.time())


def main():
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    state.update(status="idle", last_active=time.time())
    while True:
        r = sqs.receive_message(QueueUrl=QUEUE_URL, MaxNumberOfMessages=1, WaitTimeSeconds=20)
        for msg in r.get("Messages", []):
            try:
                body = json.loads(msg["Body"])
                for rec in body.get("Records", []):
                    handle(rec["s3"]["bucket"]["name"], unquote_plus(rec["s3"]["object"]["key"]))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
        idle = (time.time() - state["last_active"]) / 60
        if idle > IDLE_MINUTES:
            log(f"idle {idle:.0f} min → shutdown")
            state["status"] = "stopping"
            table.put_item(Item={"PK": "WORKER", "SK": "model3d", "status": "stopping",
                                 "job": "", "ts": int(time.time())})
            subprocess.run(["shutdown", "-h", "now"])
            time.sleep(120)


if __name__ == "__main__":
    main()
