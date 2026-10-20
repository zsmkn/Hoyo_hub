/* 瀏覽器端 2.5D（Cloudflare 版使用；本機版與 AWS 版仍由伺服器計算）
 * 在使用者的瀏覽器跑 Depth Anything V2（transformers.js / ONNX Runtime Web），
 * 產生與 server/depth.py 相同的三個檔案：image（原圖，保留透明）、depth（白 = 近、黑 = 遠）、thumb（縮圖）。
 */
// 固定版本（npm 版本發布後內容不可變）；Cloudflare 版的 CSP 只允許這個套件的 dist/ 目錄，部署腳本從這行讀版本
const TF_URL = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.5.1/dist/transformers.min.js";
const MODEL = "onnx-community/depth-anything-v2-small";
const MAX_SIDE = 1600;

let estimator = null;

async function getEstimator(progress) {
  if (estimator) return estimator;
  const { pipeline, env } = await import(TF_URL);
  env.allowLocalModels = false;
  const files = {};
  const onProgress = (p) => {
    if (p.status !== "progress" || !p.total) return;
    files[p.file] = [p.loaded, p.total];
    const [l, t] = Object.values(files).reduce(([a, b], [x, y]) => [a + x, b + y], [0, 0]);
    progress(`下載 AI 深度模型（第一次使用才需要）… ${Math.round((l / t) * 100)}%（${(t / 1048576).toFixed(0)}MB）`);
  };
  const gpu = "gpu" in navigator && (await navigator.gpu.requestAdapter().catch(() => null));
  try {
    estimator = await pipeline("depth-estimation", MODEL, gpu
      ? { device: "webgpu", dtype: "fp16", progress_callback: onProgress }
      : { device: "wasm", dtype: "fp32", progress_callback: onProgress });
  } catch (e) {
    if (!gpu) throw e;
    estimator = await pipeline("depth-estimation", MODEL, { device: "wasm", dtype: "fp32", progress_callback: onProgress });
  }
  return estimator;
}

const canvas = (w, h) => Object.assign(document.createElement("canvas"), { width: w, height: h });
const toBlob = (c, type, q) => new Promise((ok, err) => c.toBlob((b) => (b ? ok(b) : err(new Error("圖片編碼失敗"))), type, q));

function percentile(sorted, p) {
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.round((p / 100) * (sorted.length - 1))))];
}

/** file → { image, depth, thumb (Blob), width, height, elapsed } */
export async function processImage(file, progress) {
  let t0 = performance.now();
  const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
  const s = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
  const w = Math.round(bmp.width * s), h = Math.round(bmp.height * s);
  const main = canvas(w, h);
  const mctx = main.getContext("2d");
  mctx.imageSmoothingQuality = "high";
  mctx.drawImage(bmp, 0, 0, w, h);
  const px = mctx.getImageData(0, 0, w, h).data;
  let hasAlpha = false;
  for (let i = 3; i < px.length; i += 4) if (px[i] < 255) { hasAlpha = true; break; }

  // 透明背景先墊灰底再給模型看
  const gray = canvas(w, h);
  const gctx = gray.getContext("2d");
  gctx.fillStyle = "rgb(128,128,128)";
  gctx.fillRect(0, 0, w, h);
  gctx.drawImage(main, 0, 0);

  const est = await getEstimator(progress);
  t0 = performance.now();  // 耗時不含第一次下載模型
  progress("AI 正在估計深度（在你的瀏覽器上運算）…");
  const { RawImage } = await import(TF_URL);
  const out = await est(await RawImage.fromBlob(await toBlob(gray, "image/png")));
  const t = out.predicted_depth;
  const [dh, dw] = t.dims.slice(-2);
  const d = t.data;

  // 去掉最亮、最暗各 1% 再拉到 0~1（同 server/depth.py）
  const sorted = Float32Array.from(d).sort();
  const lo = percentile(sorted, 1), hi = percentile(sorted, 99);
  const small = canvas(dw, dh);
  const sctx = small.getContext("2d");
  const img = sctx.createImageData(dw, dh);
  for (let i = 0; i < d.length; i++) {
    const v = Math.round(Math.min(1, Math.max(0, (d[i] - lo) / (hi - lo + 1e-6))) * 255);
    img.data.set([v, v, v, 255], i * 4);
  }
  sctx.putImageData(img, 0, 0);

  const full = canvas(w, h);
  const fctx = full.getContext("2d");
  fctx.imageSmoothingQuality = "high";
  fctx.drawImage(small, 0, 0, w, h);
  if (hasAlpha) {  // 透明處視為最遠
    const dd = fctx.getImageData(0, 0, w, h);
    for (let i = 0; i < dd.data.length; i += 4) {
      const a = px[i + 3] / 255;
      dd.data[i] = dd.data[i + 1] = dd.data[i + 2] = Math.round(dd.data[i] * a);
    }
    fctx.putImageData(dd, 0, 0);
  }
  const depth = canvas(w, h);
  const dctx = depth.getContext("2d");
  dctx.fillStyle = "#000";
  dctx.fillRect(0, 0, w, h);
  dctx.filter = `blur(${Math.max(1, w / 500)}px)`;
  dctx.drawImage(full, 0, 0);

  const ts = Math.min(1, 480 / Math.max(w, h));
  const thumb = canvas(Math.round(w * ts), Math.round(h * ts));
  const tctx = thumb.getContext("2d");
  tctx.imageSmoothingQuality = "high";
  tctx.drawImage(main, 0, 0, thumb.width, thumb.height);

  progress("壓縮圖片…");
  return {
    image: await toBlob(main, "image/webp", 0.9),
    depth: await toBlob(depth, "image/png"),
    thumb: await toBlob(thumb, "image/webp", 0.8),
    width: w, height: h, elapsed: (performance.now() - t0) / 1000,
  };
}
