/* 所有 API 路由（對應 server/routes.py，路徑、參數、回應格式都相同，前端與 Discord Cog 不用改）。
 * 遊戲相關的 API 都可以加上遊戲前綴：/api/{zzz|gi|hsr}/...；沒有前綴時使用預設遊戲。
 *
 * 與 AWS 版的差別：
 *   背景工作  Lambda 非同步呼叫 → Cloudflare Queues（env.JOBS）
 *   圖片上傳  S3 預簽名網址     → 本 Worker 的 PUT /api/uploads/{job_id}（HMAC 簽章、5 分鐘有效）
 *   OCR       RapidOCR          → Workers AI 視覺模型
 *   2.5D      Lambda + ONNX     → 使用者瀏覽器端計算（depth_client.js），結果用 POST /api/depth/upload 上傳
 *   作品檔案  S3 網站 bucket    → KV（env.FILES），由 /gallery/... 提供
 */
import { ApiError, encrypt, hmac, newId, newToken, nowTs, safeEqual, sha256, signingKey } from "./common.js";
import * as checkin from "./checkin.js";
import * as gacha from "./gacha.js";
import * as ocr from "./ocr.js";
import { score } from "./scoring.js";
import { ALL_GAMES, DEFAULT_GAME, GAMES, getGame, publicMeta } from "./games.js";

const GAME_PREFIX = new RegExp(`^/api/(${Object.keys(ALL_GAMES).join("|")})(/.*)$`);
const ROUTES = [];
const UPLOAD_TTL = 300;
const MAX_UPLOAD = 10 * 1024 * 1024;
const MAX_JSON = 64 * 1024;

/* 每日配額：免費方案的 KV 每天只能寫 1,000 次、Workers AI 每天 10,000 Neurons，
 * 沒有上限的話，有人寫個迴圈幾秒鐘就能把當天額度用完，所有人都不能用。
 *   ip    同一個 IP 每天的上限（教室共用 IP 也夠用）
 *   all   全站每天的上限（保護免費額度：2.5D 每件寫 KV 3 次、OCR 每張 1 次）
 */
const QUOTAS = {
  profile: { ip: 300 },
  import: { ip: 60 },
  ocr: { ip: 40, all: 120 },   // 一張 1080p 截圖約 75 Neurons，免費 10,000 ≈ 130 張
  depth: { ip: 30, all: 200 },
};

/** "2001:db8::1" → "2001:0db8:0000:0000::/64"（先展開 :: 再取前 4 組） */
export function ipv6Prefix64(ip) {
  const [head, tail = ""] = ip.split("%")[0].split("::");
  const h = head ? head.split(":") : [], t = tail ? tail.split(":") : [];
  const full = ip.includes("::") ? [...h, ...Array(Math.max(0, 8 - h.length - t.length)).fill("0"), ...t] : h;
  return full.slice(0, 4).map((x) => x.padStart(4, "0").toLowerCase()).join(":") + "::/64";
}

async function useQuota(c, kind) {
  const q = QUOTAS[kind];
  const day = new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
  const ttl = nowTs() + 2 * 86400;
  // IPv6 一個使用者通常拿到整個 /64，可以任意換位址，所以用前 64 位元當作同一個人
  const who = c.ip.includes(":") ? ipv6Prefix64(c.ip) : c.ip;
  const ipKey = (await sha256(`${day}|${who}`)).slice(0, 24);  // 不存原始 IP
  if ((await c.store.incr(`QUOTA#${day}`, `${kind}#${ipKey}`, ttl)) > q.ip) {
    throw new ApiError(429, "今天的使用次數已達上限，請明天再試");
  }
  if (q.all && (await c.store.incr(`QUOTA#${day}`, `${kind}#ALL`, ttl)) > q.all) {
    throw new ApiError(429, "今天全站的免費額度已用完，請明天再試");
  }
}

/** 上傳類請求一定要帶 Content-Length（不接受 chunked），超過上限直接拒絕，不讀進記憶體 */
function requireLength(c, max) {
  const len = Number(c.request.headers.get("content-length"));
  if (!Number.isFinite(len) || len <= 0) throw new ApiError(411, "需要 Content-Length");
  if (len > max) throw new ApiError(413, `檔案太大（上限 ${Math.round(max / 1048576)}MB）`);
}

/** auth: null=公開, "user"=需要個人 token, "bot"=需要 Bot API Key；raw=true 時不解析 JSON body */
function route(method, pattern, auth, fn, raw = false) {
  ROUTES.push({ method, re: new RegExp(`^${pattern}$`), auth, fn, raw });
}

// ================= 背景工作 =================
async function newJob(c, pid, kind, extra = {}) {
  const jobId = newId(16);
  await c.store.put(`JOB#${jobId}`, "META", { pid, kind, status: "pending", message: "排隊中…", created: nowTs(), ...extra }, nowTs() + 86400);
  return jobId;
}

export const setJob = (store, jobId, status, message, extra = {}) =>
  store.update(`JOB#${jobId}`, "META", { status, message, updated: nowTs(), ...extra });

/** 佇列消費者：一則訊息 = 一個工作（或匯入的一段） */
export async function runAction(env, store, msg) {
  if (msg.action === "import_url") return runImport(env, store, msg);
  if (msg.action === "process_upload") return processUpload(env, store, msg.job_id);
  if (msg.action === "checkin") return checkin.runForProfile(env, store, getGame(msg.game), msg.pid);
  console.log("unknown action", msg.action);
}

async function runImport(env, store, msg) {
  const { job_id: jobId, pid, url } = msg;
  const state = msg.state || gacha.newImportState();
  try {
    const g = getGame(msg.game);
    if (!msg.state) await setJob(store, jobId, "running", "開始匯入…");
    const done = await gacha.importStep(store, g, pid, url, state, (m) => setJob(store, jobId, "running", m));
    if (!done) {
      await env.JOBS.send({ ...msg, state });  // 還沒抓完：下一段接著抓
      return;
    }
    await gacha.updateGlobal(store, g, pid, await gacha.profileStats(store, g, pid));
    const n = Object.values(state.summary).reduce((a, b) => a + b, 0);
    await setJob(store, jobId, "done", `匯入完成，新增 ${n} 筆紀錄`, { result: state.summary });
  } catch (e) {
    if (!(e instanceof ApiError)) console.log("import failed", e.stack);
    await setJob(store, jobId, "error", e instanceof ApiError ? e.message : `匯入失敗：${e.name}`);
  }
}

async function processUpload(env, store, jobId) {
  const job = await store.get(`JOB#${jobId}`, "META");
  const key = `upload/${jobId}`;
  try {
    if (!job) return;
    const bytes = await env.FILES.get(key, "arrayBuffer");
    if (!bytes) return setJob(store, jobId, "error", "找不到上傳的圖片，請重新上傳");
    const game = getGame(job.game || DEFAULT_GAME);
    await setJob(store, jobId, "running", "AI 辨識中…");
    const lines = await ocr.detectLines(env, new Uint8Array(bytes), job.content_type);
    const raw = lines.map((l) => l.text);
    if (job.kind === "ocr_gacha") {
      const rows = ocr.parseGacha(game, lines);
      const msg = rows.length ? `辨識出 ${rows.length} 筆紀錄，請確認後匯入` : "沒有辨識到紀錄，請確認截圖清晰且包含時間欄位";
      await setJob(store, jobId, "done", msg, { result: { rows, gacha_type: job.gacha_type }, raw });
    } else {
      const d = ocr.parseDisc(game, lines);
      const ok = d.main || d.subs.length;
      await setJob(store, jobId, "done", ok ? "辨識完成，請確認數值" : "沒有辨識到詞條，請手動輸入", { result: d, raw });
    }
  } catch (e) {
    console.log("ocr failed", e.stack);
    await setJob(store, jobId, "error", `OCR 失敗：${e.name}`);
  } finally {
    await env.FILES.delete(key);
  }
}

/** 上傳網址的簽章：job_id + 到期時間 + 檔案類型，用 Worker 的秘密金鑰做 HMAC */
const uploadSig = (c, jobId, exp, ctype) => hmac(signingKey(c.env), `upload\n${jobId}\n${exp}\n${ctype}`);

async function uploadTarget(c, jobId, ctype) {
  const exp = nowTs() + UPLOAD_TTL;
  const q = new URLSearchParams({ expires: String(exp), ttl: String(UPLOAD_TTL), sig: await uploadSig(c, jobId, exp, ctype) });
  return `${c.origin}/api/uploads/${jobId}?${q}`;
}

// ================= 個人檔案 =================
route("POST", "/api/profile", null, async (c) => {
  await useQuota(c, "profile");
  const pid = newId(12), token = newToken();
  await c.store.put(`PROFILE#${pid}`, "META", { created: nowTs() });
  await c.store.put(`TOKEN#${await sha256(token)}`, "PROFILE", { pid });
  return [201, { profile_id: pid, token }];
});

route("GET", "/api/profile", "user", async (c) => {
  const meta = (await c.store.get(`PROFILE#${c.pid}`, "META")) || {};
  return [200, { profile_id: c.pid, game_uid: meta[`game_uid_${c.game.id}`] || null, discord_id: meta.discord_id || null }];
});

route("POST", "/api/profile/linkcode", "user", async (c) => {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const code = [...crypto.getRandomValues(new Uint8Array(6))].map((b) => chars[b % chars.length]).join("");
  await c.store.put(`LINKCODE#${code}`, "PROFILE", { pid: c.pid }, nowTs() + 600);
  return [200, { code, expires_in: 600 }];
});

// 健康檢查；這條路由另外設了嚴格限流，可用來測試 429
route("GET", "/api/ping", null, async () => [200, { ok: true, time: nowTs() }]);

route("GET", "/api/games", null, async () => [200, {
  games: Object.values(GAMES).map((g) => ({ id: g.id, short: g.short, name: g.name, accent: g.accent })),
  external: [], links: [], default: DEFAULT_GAME, mode: "cloudflare", depth_client: true,
}]);

route("GET", "/api/meta", null, async (c) => [200, publicMeta(c.game)]);

route("GET", "/api/agents", null, async (c) => {
  const m = publicMeta(c.game);
  return [200, { agents: m.agents, stat_labels: m.stat_labels, specialties: m.specialties }];
});

// ================= 抽卡紀錄 =================
route("POST", "/api/import/url", "user", async (c) => {
  const url = String(c.body.url || "").trim();
  if (url.length > 4096) throw new ApiError(400, "網址太長");
  gacha.parseGachaUrl(c.game, url);
  await useQuota(c, "import");
  const jobId = await newJob(c, c.pid, "import_url", { game: c.game.id });
  await c.env.JOBS.send({ action: "import_url", job_id: jobId, game: c.game.id, pid: c.pid, url });
  return [202, { job_id: jobId }];
});

route("POST", "/api/upload-url", "user", async (c) => {
  const b = c.body, game = c.game;
  const kind = b.kind;
  if (!["gacha", "disc", "depth"].includes(kind)) throw new ApiError(400, "kind 必須是 gacha、disc 或 depth");
  const ctype = b.content_type || "image/png";
  const allowed = kind === "depth" ? ["image/png", "image/jpeg", "image/webp"] : ["image/png", "image/jpeg"];
  if (!allowed.includes(ctype)) throw new ApiError(400, "不支援的圖片格式");
  if (kind === "depth") throw new ApiError(400, "這個版本的 2.5D 在瀏覽器計算，請重新整理頁面後再試");
  const ch = String(b.gacha_type || game.gacha.featured_channel);
  if (!Object.hasOwn(game.gacha.channels, ch)) throw new ApiError(400, "卡池類型錯誤");
  await useQuota(c, "ocr");
  const jobId = await newJob(c, c.pid, `ocr_${kind}`, { content_type: ctype, game: game.id, gacha_type: ch });
  await setJob(c.store, jobId, "waiting_upload", "等待上傳圖片…");
  return [200, { job_id: jobId, upload_url: await uploadTarget(c, jobId, ctype), content_type: ctype }];
});

// 瀏覽器用 PUT 上傳圖片（取代 S3 預簽名網址）：簽章、期限、檔案類型都不符合就回 403，跟 S3 一樣
route("PUT", "/api/uploads/(?<job_id>[a-f0-9]+)", null, async (c) => {
  const jobId = c.params.job_id;
  const exp = Number(c.query.expires || 0);
  const ctype = (c.request.headers.get("content-type") || "").split(";")[0].trim();
  const expect = await uploadSig(c, jobId, exp, ctype);
  if (!exp || exp < nowTs() || !safeEqual(c.query.sig, expect)) throw new ApiError(403, "上傳網址無效、已過期或檔案類型不符");
  requireLength(c, MAX_UPLOAD);
  // 原子地把狀態從 waiting_upload 改成 uploading：同一個網址只能成功上傳一次（同時送兩次也只有一個成功）
  if (!(await c.store.updateIf(`JOB#${jobId}`, "META", "status", "waiting_upload", { status: "uploading", updated: nowTs() }))) {
    throw new ApiError(404, "上傳網址無效或已使用");
  }
  const bytes = await c.request.arrayBuffer();
  if (!bytes.byteLength || bytes.byteLength > MAX_UPLOAD) throw new ApiError(413, "圖片請小於 10MB");
  await c.env.FILES.put(`upload/${jobId}`, bytes, { expirationTtl: 3600 });
  await setJob(c.store, jobId, "pending", "排隊中…");
  await c.env.JOBS.send({ action: "process_upload", job_id: jobId });
  return [200, { ok: true }];
}, true);

route("GET", "/api/jobs/(?<job_id>[a-f0-9]+)", "user", async (c) => {
  const job = await c.store.get(`JOB#${c.params.job_id}`, "META");
  if (!job || job.pid !== c.pid) throw new ApiError(404, "找不到這個工作");
  const { PK, SK, pid, ...rest } = job;
  return [200, rest];
});

route("POST", "/api/import/rows", "user", async (c) => {
  const game = c.game;
  const ch = String(c.body.gacha_type);
  if (!Object.hasOwn(game.gacha.channels, ch)) throw new ApiError(400, "卡池類型錯誤");
  const rows = Array.isArray(c.body.rows) ? c.body.rows.slice(0, 200) : [];
  const clean = rows.map((r) => {
    r = r || {};
    if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(r.time || "")) throw new ApiError(400, `時間格式錯誤：${r.time}`);
    return { name: String(r.name || "").slice(0, 40), item_type: String(r.item_type || "").slice(0, 10),
             rank: ["S", "A", "B"].includes(r.rank) ? r.rank : "B", time: r.time };
  });
  const n = await gacha.savePulls(c.store, game, c.pid, ch, ocr.rowsToPulls(clean), "ocr");
  await gacha.updateGlobal(c.store, game, c.pid, await gacha.profileStats(c.store, game, c.pid));
  return [200, { imported: n }];
});

route("GET", "/api/stats", "user", async (c) => [200, await gacha.profileStats(c.store, c.game, c.pid)]);

const pullView = (p) => Object.fromEntries(["id", "gacha_type", "name", "item_type", "rank", "time"].map((k) => [k, p[k] ?? null]));

route("GET", "/api/pulls", "user", async (c) => {
  const pulls = await gacha.loadPulls(c.store, c.game, c.pid, c.query.gacha_type || null);
  return [200, { pulls: pulls.reverse().map(pullView) }];
});

route("DELETE", "/api/pulls", "user", async (c) => [200, { deleted: await gacha.deletePulls(c.store, c.game, c.pid) }]);

route("GET", "/api/global", null, async (c) => [200, await gacha.globalStats(c.store, c.game)]);

// ================= 裝備評分 =================
route("POST", "/api/disc/score", null, async (c) => {
  const b = c.body;
  return [200, score(c.game, b.character, b.slot, b.main, b.subs)];
});

// ================= 2.5D 立繪（不分遊戲） =================
const DEPTH_FIELDS = ["id", "title", "public", "width", "height", "created", "elapsed", "image", "depth", "thumb"];
const view = (w) => Object.fromEntries(DEPTH_FIELDS.map((k) => [k, w[k] ?? null]));
const FILE_LIMITS = { image: 8 << 20, depth: 4 << 20, thumb: 1 << 20 };

function sniff(bytes) {
  const b = new Uint8Array(bytes.slice(0, 12));
  if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47) return ["image/png", "png"];
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return ["image/jpeg", "jpg"];
  if (String.fromCharCode(...b.slice(0, 4)) === "RIFF" && String.fromCharCode(...b.slice(8, 12)) === "WEBP") return ["image/webp", "webp"];
  return [null, null];
}

// 瀏覽器算好深度後上傳三個檔案（multipart/form-data：image、depth、thumb + 作品資訊）
route("POST", "/api/depth/upload", "user", async (c) => {
  requireLength(c, 14 << 20);
  await useQuota(c, "depth");
  let form;
  try { form = await c.request.formData(); } catch { throw new ApiError(400, "需要 multipart/form-data"); }
  const id = newId(16);
  const files = {};
  for (const [field, limit] of Object.entries(FILE_LIMITS)) {
    const f = form.get(field);
    if (!f || typeof f === "string") throw new ApiError(400, `缺少檔案：${field}`);
    const bytes = await f.arrayBuffer();
    if (bytes.byteLength > limit) throw new ApiError(413, `${field} 檔案太大`);
    const [ctype, ext] = sniff(bytes);
    if (!ctype) throw new ApiError(400, `${field} 不是圖片`);
    files[field] = { bytes, ctype, path: `gallery/${id}/${field}.${ext}` };
  }
  const num = (k, max) => Math.min(Math.max(Number(form.get(k)) || 0, 0), max);
  for (const f of Object.values(files)) await c.env.FILES.put(f.path, f.bytes, { metadata: { ct: f.ctype } });
  const ts = nowTs();
  const work = {
    id, title: String(form.get("title") || "").trim().slice(0, 40) || "未命名立繪", public: form.get("public") === "true",
    width: Math.round(num("width", 4096)), height: Math.round(num("height", 4096)), created: ts, elapsed: Math.round(num("elapsed", 3600) * 10) / 10,
    image: files.image.path, depth: files.depth.path, thumb: files.thumb.path,
  };
  await c.store.put(`DEPTH#${id}`, "META", { pid: c.pid, ...work });
  await c.store.put(`PROFILE#${c.pid}`, `DEPTH#${ts}#${id}`, work);
  if (work.public) await c.store.put("GALLERY", `${ts}#${id}`, work);
  return [200, { ...work, message: `完成！瀏覽器 AI 推論＋處理耗時 ${work.elapsed} 秒` }];
}, true);

route("GET", "/api/depth/mine", "user", async (c) =>
  [200, { works: (await c.store.query(`PROFILE#${c.pid}`, "DEPTH#", { desc: true })).map(view) }]);

route("GET", "/api/depth/gallery", null, async (c) =>
  [200, { works: (await c.store.query("GALLERY", "", { desc: true, limit: 60 })).map(view) }]);

route("GET", "/api/depth/(?<wid>[a-f0-9]+)", null, async (c) => {
  const w = await c.store.get(`DEPTH#${c.params.wid}`, "META");
  if (!w) throw new ApiError(404, "找不到這個作品");
  return [200, view(w)];
});

route("DELETE", "/api/depth/(?<wid>[a-f0-9]+)", "user", async (c) => {
  const wid = c.params.wid, pid = c.pid;
  const w = await c.store.get(`DEPTH#${wid}`, "META");
  if (!w || w.pid !== pid) throw new ApiError(404, "找不到這個作品");
  for (const k of ["image", "depth", "thumb"]) if (w[k]) await c.env.FILES.delete(w[k]);
  await c.store.delete(`DEPTH#${wid}`, "META");
  await c.store.delete(`PROFILE#${pid}`, `DEPTH#${w.created}#${wid}`);
  await c.store.delete("GALLERY", `${w.created}#${wid}`);
  return [200, { ok: true }];
});

// ================= HoYoLAB 簽到 =================
const mask = (s) => {
  s = String(s || "");
  return s.length > 4 ? s.slice(0, 2) + "*".repeat(Math.max(s.length - 4, 0)) + s.slice(-2) : s;
};

route("GET", "/api/hoyolab", "user", async (c) => {
  const gid = c.game.id;
  const cfg = await c.store.get(`PROFILE#${c.pid}`, "HOYOLAB");
  if (!cfg) return [200, { configured: false }];
  const history = await c.store.query(`PROFILE#${c.pid}`, `CHECKIN#${gid}#`, { desc: true, limit: 14 });
  const games = cfg.games || {};
  return [200, {
    configured: true, enabled: Boolean(games[gid]),
    enabled_games: Object.entries(games).filter(([, on]) => on).map(([g]) => g),
    ltuid: mask(cfg.ltuid), has_webhook: Boolean(cfg.webhook),
    last_result: (cfg.last || {})[gid] || null,
    history: history.map((h) => Object.fromEntries(["date", "status", "message", "reward", "total_days"].map((k) => [k, h[k] ?? null]))),
  }];
});

route("PUT", "/api/hoyolab", "user", async (c) => {
  const b = c.body, pid = c.pid, gid = c.game.id;
  const existing = (await c.store.get(`PROFILE#${pid}`, "HOYOLAB")) || {};
  const { PK, SK, ...item } = existing;
  item.updated = nowTs();
  if (b.cookie) {
    if (typeof b.cookie !== "string" || b.cookie.length > 8192) throw new ApiError(400, "Cookie 格式錯誤");
    const [cookie, ltuid] = checkin.normalizeCookie(b.cookie);
    if (!cookie) throw new ApiError(400, "Cookie 裡需要包含 ltoken_v2 與 ltuid_v2");
    // Cookie 加密後才存（AES-GCM，金鑰只在 Worker secret 裡）
    Object.assign(item, { cookie: await encrypt(signingKey(c.env), cookie), ltuid });
  } else if (!existing.cookie) {
    throw new ApiError(400, "請貼上 HoYoLAB Cookie");
  }
  if (b.webhook !== undefined && b.webhook !== null) {
    if (typeof b.webhook !== "string" || b.webhook.length > 300) throw new ApiError(400, "Discord Webhook 網址格式錯誤");
    if (b.webhook && !/^https:\/\/(discord|discordapp)\.com\/api\/webhooks\/\d+\/[\w-]+$/.test(b.webhook)) throw new ApiError(400, "Discord Webhook 網址格式錯誤");
    item.webhook = b.webhook;
  }
  const games = { ...(item.games || {}), [gid]: b.enabled === undefined ? true : Boolean(b.enabled) };
  item.games = games;
  await c.store.put(`PROFILE#${pid}`, "HOYOLAB", item);
  for (const [g, on] of Object.entries(games)) {
    if (on) await c.store.put(`CHECKIN_SET#${g}`, pid, {});
    else await c.store.delete(`CHECKIN_SET#${g}`, pid);
  }
  return [200, { ok: true, ltuid: mask(item.ltuid) }];
});

route("DELETE", "/api/hoyolab", "user", async (c) => {
  await c.store.delete(`PROFILE#${c.pid}`, "HOYOLAB");
  for (const g of Object.keys(ALL_GAMES)) await c.store.delete(`CHECKIN_SET#${g}`, c.pid);
  return [200, { ok: true }];
});

route("POST", "/api/hoyolab/checkin", "user", async (c) => [200, await checkin.runForProfile(c.env, c.store, c.game, c.pid)]);

// ================= Discord Bot API =================
async function pidByDiscord(c, discordId) {
  const link = await c.store.get(`DISCORD#${discordId}`, "PROFILE");
  if (!link) throw new ApiError(404, "這個 Discord 帳號還沒綁定，請先到網站取得綁定碼並使用 /link");
  return link.pid;
}

route("POST", "/api/bot/link", "bot", async (c) => {
  const code = String(c.body.code || "").trim().toUpperCase();
  const discordId = String(c.body.discord_id || "").trim();
  if (!code || !/^\d+$/.test(discordId)) throw new ApiError(400, "需要 code 與 discord_id");
  const lc = await c.store.get(`LINKCODE#${code}`, "PROFILE");
  if (!lc) throw new ApiError(404, "綁定碼無效或已過期");
  await c.store.put(`DISCORD#${discordId}`, "PROFILE", { pid: lc.pid });
  await c.store.update(`PROFILE#${lc.pid}`, "META", { discord_id: discordId });
  await c.store.delete(`LINKCODE#${code}`, "PROFILE");
  return [200, { ok: true }];
});

route("DELETE", "/api/bot/users/(?<did>\\d+)/link", "bot", async (c) => {
  const pid = await pidByDiscord(c, c.params.did);
  await c.store.delete(`DISCORD#${c.params.did}`, "PROFILE");
  await c.store.update(`PROFILE#${pid}`, "META", { discord_id: null });
  return [200, { ok: true }];
});

route("GET", "/api/bot/users/(?<did>\\d+)/summary", "bot", async (c) => {
  const game = c.game, pid = await pidByDiscord(c, c.params.did);
  const stats = await gacha.profileStats(c.store, game, pid), glob = await gacha.globalStats(c.store, game);
  return [200, { game: game.id, stats, global: glob, llm_context: llmContext(game, stats, glob) }];
});

route("GET", "/api/bot/users/(?<did>\\d+)/pulls", "bot", async (c) => {
  const pid = await pidByDiscord(c, c.params.did);
  const rank = c.query.rank;
  const limit = Math.min(Number.parseInt(c.query.limit, 10) || 50, 500);
  let pulls = (await gacha.loadPulls(c.store, c.game, pid, c.query.gacha_type || null)).reverse();
  if (rank) pulls = pulls.filter((p) => p.rank === rank.toUpperCase());
  return [200, { pulls: pulls.slice(0, limit).map(pullView) }];
});

route("GET", "/api/bot/global", "bot", async (c) => [200, await gacha.globalStats(c.store, c.game)]);

route("GET", "/api/bot/users/(?<did>\\d+)/checkin", "bot", async (c) => {
  const cfg = (await c.store.get(`PROFILE#${await pidByDiscord(c, c.params.did)}`, "HOYOLAB")) || {};
  const gid = c.game.id;
  return [200, { configured: Boolean(cfg.cookie), enabled: Boolean((cfg.games || {})[gid]), last_result: (cfg.last || {})[gid] || null }];
});

route("POST", "/api/bot/users/(?<did>\\d+)/checkin", "bot", async (c) =>
  [200, await checkin.runForProfile(c.env, c.store, c.game, await pidByDiscord(c, c.params.did))]);

/** 給 Discord bot 的 AI 模型當作上下文的純文字摘要 */
function llmContext(game, stats, glob) {
  const L = game.labels;
  const lines = [`遊戲：${game.name}。玩家總抽數 ${stats.total_pulls} 抽（約 ${stats.polychrome_spent} ${L.currency}）。`];
  for (const ch of game.gacha.channel_order) {
    const c = stats.channels[ch];
    if (!c || !c.total) continue;
    const g = glob.channels[c.gacha_type] || {};
    const sNames = c.s_history.slice(0, 10).map((s) => `${s.name}(${s.pulls}抽${s.result === "lost" ? "/歪" : ""})`).join("、");
    lines.push(
      `【${c.label}】共 ${c.total} 抽，${L.rank_S} ${c.s_count} 個，平均 ${c.avg_s_pulls} 抽出金` +
      `（全站平均 ${g.avg_s_pulls}），目前已墊 ${c.current_pity}/${c.pity_max} 抽` +
      (c.lose_rate !== null ? `，小保底歪率 ${c.lose_rate}%` : "") +
      (c.guaranteed ? "，下一隻為大保底" : "") +
      `，歐非評價：${c.luck_label}。近期 ${L.rank_S}：${sNames || "無"}`,
    );
  }
  return lines.join("\n");
}

// ================= 取得抽卡網址的 PowerShell 腳本 =================
const SCRIPT_TEMPLATE = String.raw`# HoYo Proxy Hub - get __NAME__ gacha history URL (read-only)
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
`;

export function renderScript(gameId) {
  if (!Object.hasOwn(ALL_GAMES, gameId)) return null;
  const g = ALL_GAMES[gameId], s = g.script;
  return SCRIPT_TEMPLATE.replace("__NAME__", g.name_en)
    .replace("__ROOTS__", s.log_roots.map((r) => `"${r}"`).join(", "))
    .replace("__LOGS__", s.log_names.join(", "))
    .replace("__REGEX__", () => s.data_regex)
    .replace(/\n/g, "\r\n");
}

// ================= 路由分派 =================
export async function handleApi(request, env, store, url) {
  let path = url.pathname, gid = DEFAULT_GAME;
  const m = path.match(GAME_PREFIX);
  if (m) [gid, path] = [m[1], "/api" + m[2]];
  try {
    const game = getGame(gid);
    for (const r of ROUTES) {
      const match = path.match(r.re);
      if (r.method !== request.method || !match) continue;
      const c = { env, store, request, game, params: match.groups || {}, query: Object.fromEntries(url.searchParams), origin: url.origin, body: {},
                  ip: request.headers.get("cf-connecting-ip") || "local" };
      if (!r.raw) {
        if (Number(request.headers.get("content-length") || 0) > MAX_JSON) throw new ApiError(413, "請求太大");
        const raw = await request.text();
        if (raw.length > MAX_JSON) throw new ApiError(413, "請求太大");
        try { c.body = raw ? JSON.parse(raw) : {}; } catch { throw new ApiError(400, "Body 必須是 JSON"); }
        if (!c.body || typeof c.body !== "object" || Array.isArray(c.body)) throw new ApiError(400, "Body 必須是 JSON 物件");
      }
      if (r.auth === "user") {
        const token = (request.headers.get("authorization") || "").replace(/^Bearer /, "").trim();
        const link = token ? await store.get(`TOKEN#${await sha256(token)}`, "PROFILE") : null;
        if (!link) throw new ApiError(401, "未登入或 token 無效");
        c.pid = link.pid;
      } else if (r.auth === "bot") {
        if (!env.BOT_API_KEY || !safeEqual(request.headers.get("x-bot-key"), env.BOT_API_KEY)) throw new ApiError(401, "Bot API Key 錯誤");
      }
      return await r.fn(c);
    }
    throw new ApiError(404, `找不到路由 ${request.method} ${path}`);
  } catch (e) {
    if (e instanceof ApiError) return [e.status, { error: e.message }];
    console.log("server error", e.stack);
    return [500, { error: `伺服器錯誤：${e.name}` }];
  }
}
