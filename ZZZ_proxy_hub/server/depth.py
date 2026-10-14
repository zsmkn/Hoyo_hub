"""2D → 2.5D 立繪：Depth Anything V2 (ONNX) 估計深度圖。

輸出到 data/gallery/{id}/
  image.webp  原圖（保留透明背景）
  depth.png   深度圖（白 = 近、黑 = 遠）
  thumb.webp  縮圖
前端用 WebGL shader 依深度做視差位移。
"""
import io
import os
import threading

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .config import MODELS  # 也會設定 HF_HOME

MODEL_REPO = "onnx-community/depth-anything-v2-small"
MODEL_FILE = "onnx/model.onnx"
MAX_SIDE = 1600
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_session = None
_lock = threading.Lock()


def get_session(progress=None):
    """第一次使用時下載模型（約 95MB）並建立推論 session。"""
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            if os.environ.get("MODEL_BUCKET"):
                # 雲端：冷啟動時從自己的 S3 下載到 /tmp（比 Hugging Face 快，也不依賴外部服務）
                import boto3
                path = str(MODELS / "depth.onnx")
                if not os.path.exists(path):
                    boto3.client("s3").download_file(os.environ["MODEL_BUCKET"], os.environ["MODEL_KEY"], path)
            else:
                from huggingface_hub import hf_hub_download, try_to_load_from_cache
                if progress and not isinstance(try_to_load_from_cache(MODEL_REPO, MODEL_FILE), str):
                    progress("第一次使用，正在下載 AI 深度模型（約 95MB）…")
                path = hf_hub_download(MODEL_REPO, MODEL_FILE)  # 支援續傳，存在 models/hf
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = os.cpu_count() or 4
            _session = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
    return _session


def _multiple_of_14(x):
    return max(14, int(round(x / 14)) * 14)


def estimate_depth(rgb, progress=None):
    """rgb: PIL RGB → 0~1 的 numpy 深度（越大越近），尺寸與輸入相同。"""
    w, h = rgb.size
    scale = 518 / min(w, h)
    nw, nh = _multiple_of_14(w * scale), _multiple_of_14(h * scale)
    if max(nw, nh) > 924:
        s = 924 / max(nw, nh)
        nw, nh = _multiple_of_14(nw * s), _multiple_of_14(nh * s)
    x = np.asarray(rgb.resize((nw, nh), Image.BICUBIC), dtype=np.float32) / 255.0
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)

    sess = get_session(progress)
    if progress:
        progress("AI 正在估計深度…")
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    d = out.reshape(out.shape[-2], out.shape[-1])
    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
    d = np.clip((d - lo) / (hi - lo + 1e-6), 0, 1)
    img = Image.fromarray((d * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(img, dtype=np.float32) / 255.0


def process(src_bytes, out_dir, progress=None):
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(src_bytes)))
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA")
    alpha = np.asarray(img.getchannel("A"), dtype=np.float32) / 255.0

    # 透明背景先墊灰底再給模型看
    rgb = Image.alpha_composite(Image.new("RGBA", img.size, (128, 128, 128, 255)), img).convert("RGB")
    depth = estimate_depth(rgb, progress)
    if has_alpha:
        depth = depth * alpha  # 透明處視為最遠

    blur = max(1.0, img.width / 500)
    depth_img = Image.fromarray((depth * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))

    main = img if has_alpha else img.convert("RGB")
    thumb = main.copy()
    thumb.thumbnail((480, 480), Image.LANCZOS)
    out_dir.mkdir(parents=True, exist_ok=True)
    main.save(out_dir / "image.webp", "WEBP", quality=90)
    depth_img.save(out_dir / "depth.png", "PNG", optimize=True)
    thumb.save(out_dir / "thumb.webp", "WEBP", quality=80)
    return img.size
